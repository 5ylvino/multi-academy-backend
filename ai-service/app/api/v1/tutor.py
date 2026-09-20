from fastapi import APIRouter, Depends, HTTPException, Query

from app.agents.tutor_graph import TutorGraph
from app.schemas.tutor import TutorChatRequest, TutorQuizAnswerRequest, TopicMasteryResponse
from app.security.context import RequestContext
from app.security.features import require_feature
from app.services.audit import audit_timer, write_audit_event
from app.services.mastery import MasteryService

router = APIRouter(prefix="/v1/tutor", tags=["tutor"])


@router.post("/chat")
async def tutor_chat(
    payload: TutorChatRequest,
    ctx: RequestContext = Depends(require_feature("ai.tutor")),
) -> dict:
    graph = TutorGraph()
    with audit_timer() as timing:
        try:
            response = await graph.run_turn(ctx, payload)
            write_audit_event(
                ctx=ctx,
                feature="ai.tutor",
                provider_id=response.provider_id,
                model=response.model,
                usage=None,
                sources=[],
                latency_ms=timing["latency_ms"],
            )
            return response.model_dump(by_alias=True)
        except Exception as exc:
            write_audit_event(
                ctx=ctx,
                feature="ai.tutor",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise


@router.post("/quiz/answer")
async def tutor_quiz_answer(
    payload: TutorQuizAnswerRequest,
    ctx: RequestContext = Depends(require_feature("ai.tutor")),
) -> dict:
    graph = TutorGraph()
    with audit_timer() as timing:
        try:
            response = await graph.grade_quiz(
                ctx,
                session_id=payload.session_id,
                answer=payload.answer,
            )
            write_audit_event(
                ctx=ctx,
                feature="ai.tutor",
                provider_id=response.provider_id,
                model=response.model,
                usage=None,
                sources=[],
                latency_ms=timing["latency_ms"],
            )
            return response.model_dump(by_alias=True)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            write_audit_event(
                ctx=ctx,
                feature="ai.tutor",
                provider_id=None,
                model=None,
                usage=None,
                sources=[],
                status="error",
                error_message=str(exc),
                latency_ms=timing["latency_ms"],
            )
            raise


@router.get("/mastery")
async def tutor_mastery(
    student_id: str = Query(alias="studentId"),
    ctx: RequestContext = Depends(require_feature("ai.tutor")),
) -> dict:
    if ctx.actor_id != student_id and "teacher" not in ctx.roles and "parent" not in ctx.roles:
        raise HTTPException(status_code=403, detail="Not allowed to view this student's mastery")
    items = MasteryService().list_mastery(tenant_id=ctx.tenant_id, student_id=student_id)
    return TopicMasteryResponse(studentId=student_id, items=items).model_dump(by_alias=True)
