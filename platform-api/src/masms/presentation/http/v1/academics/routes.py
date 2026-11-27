"""Academic structure for a school: calendar, classes, subjects and enrolment."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from masms.presentation.http.dependencies.auth import SchoolPrincipal
from masms.presentation.http.dependencies.context import UseCasesDep
from masms.presentation.http.v1.academics.schemas import (
    ClassResponse,
    CreateClassRequest,
    CreateSessionRequest,
    CreateSubjectRequest,
    CreateTermRequest,
    EnrolmentResponse,
    EnrolRequest,
    LinkSubjectRequest,
    SessionResponse,
    SubjectResponse,
    TermResponse,
    UpdateClassRequest,
)
from masms.use_cases.academics.structure import CreateClassCommand

router = APIRouter(prefix="/academics", tags=["academics"])

LevelQuery = Annotated[str | None, Query(pattern="^(nursery|primary|secondary)$")]


# ---- calendar ----------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(principal: SchoolPrincipal, uc: UseCasesDep) -> list[SessionResponse]:
    views = await uc.academic_calendar.list_sessions(principal)
    return [SessionResponse.model_validate(v) for v in views]


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SessionResponse:
    view = await uc.academic_calendar.create_session(
        principal, name=body.name, starts_on=body.starts_on, ends_on=body.ends_on
    )
    return SessionResponse.model_validate(view)


@router.post(
    "/sessions/{session_id}/terms",
    response_model=TermResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_term(
    session_id: UUID, body: CreateTermRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> TermResponse:
    view = await uc.academic_calendar.add_term(
        principal,
        session_id=session_id,
        code=body.code,
        name=body.name,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
    )
    return TermResponse.model_validate(view)


@router.post("/terms/{term_id}/current", response_model=TermResponse)
async def set_current_term(
    term_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> TermResponse:
    return TermResponse.model_validate(
        await uc.academic_calendar.set_current_term(principal, term_id)
    )


# ---- classes and subjects ----------------------------------------------------------------


@router.get("/classes", response_model=list[ClassResponse])
async def list_classes(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    level: LevelQuery = None,
    include_inactive: bool = False,
) -> list[ClassResponse]:
    views = await uc.classes_and_subjects.list_classes(
        principal, level=level, include_inactive=include_inactive
    )
    return [ClassResponse.model_validate(v) for v in views]


@router.post("/classes", response_model=ClassResponse, status_code=status.HTTP_201_CREATED)
async def create_class(
    body: CreateClassRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ClassResponse:
    view = await uc.classes_and_subjects.create_class(
        principal,
        CreateClassCommand(
            name=body.name,
            code=body.code,
            school_level=body.school_level,
            capacity=body.capacity,
            class_teacher_id=body.class_teacher_id,
        ),
    )
    return ClassResponse.model_validate(view)


@router.patch("/classes/{class_id}", response_model=ClassResponse)
async def update_class(
    class_id: UUID, body: UpdateClassRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ClassResponse:
    clear_teacher = "class_teacher_id" in body.model_fields_set and body.class_teacher_id is None
    view = await uc.classes_and_subjects.update_class(
        principal,
        class_id,
        name=body.name,
        capacity=body.capacity,
        class_teacher_id=body.class_teacher_id,
        active=body.active,
        clear_teacher=clear_teacher,
    )
    return ClassResponse.model_validate(view)


@router.put("/classes/{class_id}/subjects/{subject_id}", response_model=ClassResponse)
async def link_subject(
    class_id: UUID,
    subject_id: UUID,
    body: LinkSubjectRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
) -> ClassResponse:
    view = await uc.classes_and_subjects.link_subject(
        principal,
        class_id=class_id,
        subject_id=subject_id,
        teacher_id=body.teacher_id,
        linked=True,
    )
    return ClassResponse.model_validate(view)


@router.delete("/classes/{class_id}/subjects/{subject_id}", response_model=ClassResponse)
async def unlink_subject(
    class_id: UUID, subject_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> ClassResponse:
    view = await uc.classes_and_subjects.link_subject(
        principal, class_id=class_id, subject_id=subject_id, teacher_id=None, linked=False
    )
    return ClassResponse.model_validate(view)


@router.get("/subjects", response_model=list[SubjectResponse])
async def list_subjects(
    principal: SchoolPrincipal, uc: UseCasesDep, level: LevelQuery = None
) -> list[SubjectResponse]:
    views = await uc.classes_and_subjects.list_subjects(principal, level=level)
    return [SubjectResponse.model_validate(v) for v in views]


@router.post("/subjects", response_model=SubjectResponse, status_code=status.HTTP_201_CREATED)
async def create_subject(
    body: CreateSubjectRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SubjectResponse:
    view = await uc.classes_and_subjects.create_subject(
        principal,
        name=body.name,
        code=body.code,
        school_level=body.school_level,
        description=body.description,
    )
    return SubjectResponse.model_validate(view)


# ---- enrolment ---------------------------------------------------------------------------


@router.post("/enrolments", response_model=EnrolmentResponse, status_code=status.HTTP_201_CREATED)
async def enrol(
    body: EnrolRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> EnrolmentResponse:
    view = await uc.enrolments.enrol(principal, student_id=body.student_id, class_id=body.class_id)
    return EnrolmentResponse.model_validate(view)


@router.get("/classes/{class_id}/roster", response_model=list[EnrolmentResponse])
async def roster(
    class_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[EnrolmentResponse]:
    views = await uc.enrolments.roster(principal, class_id)
    return [EnrolmentResponse.model_validate(v) for v in views]
