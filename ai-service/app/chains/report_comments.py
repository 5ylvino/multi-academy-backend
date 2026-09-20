from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.chains.base import load_prompt, run_llm_chat
from app.db.models import AiReportCommentDraft
from app.db.session import SessionLocal
from app.providers.llm.base import LlmMessage
from app.schemas.reports import ReportCommentRequest, ReportCommentResponse
from app.security.context import RequestContext


class ReportCommentChain:
    async def run(
        self,
        ctx: RequestContext,
        payload: ReportCommentRequest,
        *,
        db: Session | None = None,
    ) -> ReportCommentResponse:
        locale = (payload.locale or "en").lower()
        locale_hint = ""
        if locale in {"hausa", "yoruba", "igbo"}:
            locale_hint = f"Write the comment in {locale.title()}."
        system = f"{load_prompt('report_comments')}\n{locale_hint}"
        user_content = (
            f"Student: {payload.student_name or payload.student_id}\n"
            f"Performance: {payload.performance_summary or 'No detailed summary provided.'}\n"
            "Draft a report card comment."
        )
        result = await run_llm_chat(
            ctx=ctx,
            system_prompt=system,
            messages=[LlmMessage(role="user", content=user_content)],
            provider=payload.provider,
        )
        comment = result.content.strip()
        session = db or SessionLocal()
        owns = db is None
        draft_id = uuid.uuid4()
        try:
            session.add(
                AiReportCommentDraft(
                    id=draft_id,
                    tenant_id=ctx.tenant_id,
                    student_id=payload.student_id,
                    term_id=payload.term_id,
                    subject_id=payload.subject_id,
                    status="draft",
                    comment_text=comment[:4000],
                    comment_json={"locale": locale, "providerId": result.provider_id},
                    created_by=ctx.actor_id,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            if owns:
                session.close()
            raise
        finally:
            if owns:
                session.close()
        return ReportCommentResponse(
            draftId=str(draft_id),
            studentId=payload.student_id,
            status="draft",
            commentText=comment,
            locale=locale,
            disclaimer="Draft only — teacher must review and approve before publishing on report cards.",
        )
