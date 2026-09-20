from __future__ import annotations

import json
import re
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.chains.base import load_prompt, run_llm_chat
from app.db.models import AiTutorMessage, AiTutorSession
from app.db.session import SessionLocal
from app.providers.llm.base import LlmMessage
from app.schemas.tutor import TutorChatRequest, TutorChatResponse, TutorQuiz
from app.security.context import RequestContext
from app.services.mastery import MasteryService
from app.services.retrieval import RetrievalService


class TutorState(TypedDict, total=False):
    ctx: RequestContext
    payload: TutorChatRequest
    session: AiTutorSession
    history: list[dict[str, str]]
    retrieval: str
    explain_text: str
    quiz_json: dict[str, Any] | None
    provider_id: str
    model: str


def _parse_quiz(content: str) -> dict[str, Any] | None:
    match = re.search(r"\{[\s\S]*\}", content.strip())
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        if parsed.get("question") and parsed.get("options"):
            if parsed.get("correctValue") is None and parsed.get("correct_value") is not None:
                parsed["correctValue"] = parsed["correct_value"]
            if parsed.get("correctValue"):
                return parsed
    except json.JSONDecodeError:
        return None
    return None


class TutorGraph:
    def __init__(
        self,
        retrieval: RetrievalService | None = None,
        mastery: MasteryService | None = None,
    ) -> None:
        self._retrieval = retrieval or RetrievalService()
        self._mastery = mastery or MasteryService()
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(TutorState)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("explain", self._explain_node)
        graph.add_node("quiz", self._quiz_node)
        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "explain")
        graph.add_edge("explain", "quiz")
        graph.add_edge("quiz", END)
        return graph.compile()

    async def _retrieve_node(self, state: TutorState) -> TutorState:
        session = state["session"]
        query = session.topic or state["payload"].message
        sources = await self._retrieval.retrieve(tenant_id=state["ctx"].tenant_id, query=query)
        block = "\n".join(f"- {s.excerpt}" for s in sources[:8]) or "No documents."
        return {**state, "retrieval": block}

    async def _explain_node(self, state: TutorState) -> TutorState:
        session = state["session"]
        payload = state["payload"]
        history_lines = "\n".join(f"{m['role']}: {m['content']}" for m in state["history"][-8:])
        system = (
            f"{load_prompt('tutor')}\n\n"
            f"Topic: {session.topic}\n"
            f"Retrieved notes:\n{state['retrieval']}\n\n"
            f"Prior turns:\n{history_lines or 'None'}"
        )
        result = await run_llm_chat(
            ctx=state["ctx"],
            system_prompt=system,
            messages=[LlmMessage(role="user", content=payload.message)],
            provider=payload.provider,
        )
        return {
            **state,
            "explain_text": result.content.strip(),
            "provider_id": result.provider_id,
            "model": result.model,
        }

    async def _quiz_node(self, state: TutorState) -> TutorState:
        session = state["session"]
        prompt = (
            f"Create ONE multiple-choice question to check understanding of '{session.topic}'. "
            'Return JSON only: {"question":"...","options":[{"label":"A","value":"a"},...],'
            '"correctValue":"a"} with exactly 4 options.'
        )
        result = await run_llm_chat(
            ctx=state["ctx"],
            system_prompt=load_prompt("tutor"),
            messages=[
                LlmMessage(role="assistant", content=state["explain_text"]),
                LlmMessage(role="user", content=prompt),
            ],
            provider=state["payload"].provider,
        )
        quiz = _parse_quiz(result.content)
        return {**state, "quiz_json": quiz, "provider_id": result.provider_id, "model": result.model}

    async def run_turn(
        self,
        ctx: RequestContext,
        payload: TutorChatRequest,
        *,
        db: Session | None = None,
    ) -> TutorChatResponse:
        session_db = db or SessionLocal()
        owns = db is None
        try:
            tutor_session = self._get_or_create_session(session_db, ctx, payload)
            self._save_message(session_db, tutor_session, ctx.tenant_id, "user", payload.message)
            history = self._load_history(session_db, tutor_session.id)
            final_state = await self._graph.ainvoke(
                {
                    "ctx": ctx,
                    "payload": payload,
                    "session": tutor_session,
                    "history": history,
                }
            )
            explain = final_state["explain_text"]
            self._save_message(session_db, tutor_session, ctx.tenant_id, "assistant", explain, "chat")
            quiz_model: TutorQuiz | None = None
            if final_state.get("quiz_json"):
                tutor_session.quiz_json = final_state["quiz_json"]
                tutor_session.state = "quiz"
                quiz_model = TutorQuiz.model_validate(final_state["quiz_json"])
            else:
                tutor_session.state = "explain"
            tutor_session.updated_at = tutor_session.updated_at
            session_db.commit()
            session_db.refresh(tutor_session)
            mastery_pct = self._session_mastery(session_db, ctx, tutor_session)
            return TutorChatResponse(
                sessionId=str(tutor_session.id),
                content=explain,
                state=tutor_session.state,
                masteryPct=mastery_pct,
                quiz=quiz_model,
                providerId=final_state["provider_id"],
                model=final_state["model"],
                disclaimer="AI tutor support — verify with your class teacher before exams.",
            )
        except Exception:
            session_db.rollback()
            raise
        finally:
            if owns:
                session_db.close()

    async def grade_quiz(
        self,
        ctx: RequestContext,
        *,
        session_id: str,
        answer: str,
        db: Session | None = None,
    ) -> TutorChatResponse:
        session_db = db or SessionLocal()
        owns = db is None
        try:
            tutor_session = (
                session_db.query(AiTutorSession)
                .filter(
                    AiTutorSession.id == uuid.UUID(session_id),
                    AiTutorSession.tenant_id == ctx.tenant_id,
                    AiTutorSession.student_id == ctx.actor_id,
                )
                .one_or_none()
            )
            if tutor_session is None or not tutor_session.quiz_json:
                raise ValueError("Tutor session or quiz not found")
            quiz = tutor_session.quiz_json
            correct_value = str(quiz.get("correctValue", "")).strip().lower()
            is_correct = answer.strip().lower() == correct_value
            feedback = (
                "Great work — you got it right!"
                if is_correct
                else f"Not quite. The correct answer was {correct_value.upper()}. Review the explanation and try another question."
            )
            mastery_pct = self._mastery.record_quiz_result(
                tenant_id=ctx.tenant_id,
                student_id=tutor_session.student_id,
                subject_id=tutor_session.subject_id,
                topic=tutor_session.topic,
                correct=is_correct,
                db=session_db,
            )
            tutor_session.mastery_pct = mastery_pct
            tutor_session.state = "feedback"
            self._save_message(session_db, tutor_session, ctx.tenant_id, "user", answer, "quiz")
            self._save_message(session_db, tutor_session, ctx.tenant_id, "assistant", feedback, "feedback")
            session_db.commit()
            return TutorChatResponse(
                sessionId=str(tutor_session.id),
                content=feedback,
                state=tutor_session.state,
                masteryPct=mastery_pct,
                quiz=None,
                providerId="rule",
                model="tutor-feedback",
                disclaimer="AI tutor support — verify with your class teacher before exams.",
            )
        except Exception:
            session_db.rollback()
            raise
        finally:
            if owns:
                session_db.close()

    def _get_or_create_session(
        self,
        db: Session,
        ctx: RequestContext,
        payload: TutorChatRequest,
    ) -> AiTutorSession:
        if payload.session_id:
            existing = (
                db.query(AiTutorSession)
                .filter(
                    AiTutorSession.id == uuid.UUID(payload.session_id),
                    AiTutorSession.tenant_id == ctx.tenant_id,
                    AiTutorSession.student_id == ctx.actor_id,
                )
                .one_or_none()
            )
            if existing:
                return existing
        topic = (payload.topic or payload.message[:120]).strip() or "General revision"
        row = AiTutorSession(
            id=uuid.uuid4(),
            tenant_id=ctx.tenant_id,
            student_id=ctx.actor_id,
            subject_id=payload.subject_id,
            topic=topic,
            state="explain",
            mastery_pct=0,
        )
        db.add(row)
        db.flush()
        return row

    def _save_message(
        self,
        db: Session,
        session: AiTutorSession,
        tenant_id: str,
        role: str,
        content: str,
        message_type: str = "chat",
    ) -> None:
        db.add(
            AiTutorMessage(
                id=uuid.uuid4(),
                session_id=session.id,
                tenant_id=tenant_id,
                role=role,
                content=content[:8000],
                message_type=message_type,
            )
        )

    def _load_history(self, db: Session, session_id: uuid.UUID) -> list[dict[str, str]]:
        rows = (
            db.query(AiTutorMessage)
            .filter(AiTutorMessage.session_id == session_id)
            .order_by(AiTutorMessage.created_at.asc())
            .all()
        )
        return [{"role": row.role, "content": row.content} for row in rows]

    def _session_mastery(self, db: Session, ctx: RequestContext, session: AiTutorSession) -> int:
        if session.mastery_pct > 0:
            return session.mastery_pct
        items = self._mastery.list_mastery(
            tenant_id=ctx.tenant_id,
            student_id=session.student_id,
            db=db,
        )
        for item in items:
            if item.topic_key.endswith(session.topic.lower()) or session.topic.lower() in item.topic_key:
                return item.mastery_pct
        return session.mastery_pct or 0
