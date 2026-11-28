"""Communication: announcements, conversations and the notification inbox."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.communication.announcements import AnnouncementDraft
from app.use_cases.communication.conversations import StartCommand

router = APIRouter(tags=["communication"])

Cursor = Annotated[str | None, Query(max_length=120)]
Limit = Annotated[int, Query(ge=1, le=100)]


# ---- announcements -------------------------------------------------------------------------


class AnnouncementRequest(ApiModel):
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=1, max_length=10_000)
    roles: list[str] = Field(
        min_length=1,
        max_length=16,
        description="Who sees it, e.g. ['parent', 'student'] or every staff role.",
    )
    class_ids: list[UUID] = Field(
        default_factory=list,
        max_length=60,
        description="Only people in these classes. Empty means the whole school.",
    )
    pinned: bool = False
    publish_at: datetime | None = Field(
        default=None, description="Schedule it; leave out to publish now."
    )
    expires_at: datetime | None = None


class PublishRequest(AnnouncementRequest):
    priority: str = Field(
        default="normal",
        pattern="^(normal|urgent)$",
        description="urgent: emailed to everyone at once and asks for acknowledgement.",
    )


class WithdrawRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=300)


class AnnouncementResponse(ApiModel):
    id: UUID
    title: str
    body: str
    roles: list[str]
    class_ids: list[UUID]
    priority: str
    pinned: bool
    publish_at: datetime
    expires_at: datetime | None
    state: str = Field(description="scheduled, live, expired or withdrawn")
    author_id: UUID
    author_name: str
    created_at: datetime
    edited_at: datetime | None
    read_at: datetime | None = Field(description="When you read it (the feed).")
    reads: int | None = Field(description="How many have read it (for publishers).")
    withdraw_reason: str | None


class FeedResponse(ApiModel):
    pinned: list[AnnouncementResponse] = Field(description="First page only.")
    items: list[AnnouncementResponse]
    unread: int
    page: PageMeta


class AnnouncementPage(ApiModel):
    items: list[AnnouncementResponse]
    page: PageMeta


def _draft(body: AnnouncementRequest, priority: str = "normal") -> AnnouncementDraft:
    return AnnouncementDraft(
        title=body.title,
        body=body.body,
        roles=body.roles,
        class_ids=body.class_ids,
        pinned=body.pinned,
        publish_at=body.publish_at,
        expires_at=body.expires_at,
        priority=priority,
    )


@router.get("/announcements", response_model=FeedResponse, summary="My announcements")
async def feed(
    principal: SchoolPrincipal, uc: UseCasesDep, cursor: Cursor = None, limit: Limit = 20
) -> FeedResponse:
    """What the signed-in person should see now: pinned first, then newest first."""
    result = await uc.announcements.feed(principal, cursor=cursor, limit=limit)
    return FeedResponse(
        pinned=[AnnouncementResponse.model_validate(a) for a in result.pinned],
        items=[AnnouncementResponse.model_validate(a) for a in result.items],
        unread=result.unread,
        page=PageMeta(next_cursor=result.next_cursor),
    )


@router.post(
    "/announcements", response_model=AnnouncementResponse, status_code=status.HTTP_201_CREATED
)
async def publish(
    body: PublishRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> AnnouncementResponse:
    """Leaders can announce to anyone; teachers to the classes they teach. Urgent needs
    ``emergency:send``."""
    view = await uc.announcements.publish(principal, _draft(body, body.priority))
    return AnnouncementResponse.model_validate(view)


@router.get("/announcements/manage", response_model=AnnouncementPage)
async def managed(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    state: Annotated[str | None, Query(pattern="^(scheduled|live|expired|withdrawn)$")] = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> AnnouncementPage:
    """Everything published, with how many have read each one."""
    page = await uc.announcements.managed(principal, state=state, cursor=cursor, limit=limit)
    return AnnouncementPage(
        items=[AnnouncementResponse.model_validate(a) for a in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/announcements/{announcement_id}", response_model=AnnouncementResponse)
async def open_announcement(
    announcement_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> AnnouncementResponse:
    """Opening it records that you read it (for urgent ones, your acknowledgement)."""
    view = await uc.announcements.open(principal, announcement_id)
    return AnnouncementResponse.model_validate(view)


@router.put("/announcements/{announcement_id}", response_model=AnnouncementResponse)
async def edit_announcement(
    announcement_id: UUID, body: AnnouncementRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> AnnouncementResponse:
    """The author, or ``announcements:update``. A live one is marked edited."""
    view = await uc.announcements.edit(principal, announcement_id, _draft(body))
    return AnnouncementResponse.model_validate(view)


@router.post("/announcements/{announcement_id}/withdraw", response_model=AnnouncementResponse)
async def withdraw_announcement(
    announcement_id: UUID, body: WithdrawRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> AnnouncementResponse:
    view = await uc.announcements.withdraw(principal, announcement_id, reason=body.reason)
    return AnnouncementResponse.model_validate(view)


# ---- conversations -------------------------------------------------------------------------


class StartRequest(ApiModel):
    subject: str = Field(min_length=3, max_length=160)
    participant_ids: list[UUID] = Field(min_length=1, max_length=19)
    body: str = Field(min_length=1, max_length=5_000)
    student_id: UUID | None = Field(default=None, description="The child it is about, if any.")


class ReplyRequest(ApiModel):
    body: str = Field(min_length=1, max_length=5_000)


class PersonResponse(ApiModel):
    id: UUID
    name: str


class MessageResponse(ApiModel):
    id: UUID
    sender_id: UUID
    sender_name: str
    body: str
    sent_at: datetime
    mine: bool


class ConversationResponse(ApiModel):
    id: UUID
    subject: str
    student_id: UUID | None
    participants: list[PersonResponse]
    created_by: UUID
    created_at: datetime
    last_message_at: datetime
    message_count: int
    unread: int
    last_message: MessageResponse | None


class ConversationPage(ApiModel):
    items: list[ConversationResponse]
    page: PageMeta


class ThreadResponse(ApiModel):
    conversation: ConversationResponse
    messages: list[MessageResponse] = Field(description="Newest first.")
    older_cursor: str | None = Field(description="Pass as `before` to load older messages.")


@router.get("/conversations", response_model=ConversationPage)
async def my_conversations(
    principal: SchoolPrincipal, uc: UseCasesDep, cursor: Cursor = None, limit: Limit = 20
) -> ConversationPage:
    """Most recent activity first, with unread counts."""
    page = await uc.conversations.mine(principal, cursor=cursor, limit=limit)
    return ConversationPage(
        items=[ConversationResponse.model_validate(c) for c in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.post("/conversations", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
async def start_conversation(
    body: StartRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ThreadResponse:
    view = await uc.conversations.start(
        principal,
        StartCommand(
            subject=body.subject,
            participant_ids=body.participant_ids,
            body=body.body,
            student_id=body.student_id,
        ),
    )
    return ThreadResponse.model_validate(view)


@router.get("/conversations/{conversation_id}", response_model=ThreadResponse)
async def open_conversation(
    conversation_id: UUID,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    before: Cursor = None,
    limit: Limit = 50,
) -> ThreadResponse:
    view = await uc.conversations.open(principal, conversation_id, before=before, limit=limit)
    return ThreadResponse.model_validate(view)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reply(
    conversation_id: UUID, body: ReplyRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> MessageResponse:
    """Messages can't be edited or deleted once sent."""
    view = await uc.conversations.reply(principal, conversation_id, body=body.body)
    return MessageResponse.model_validate(view)


# ---- notifications -------------------------------------------------------------------------


class NotificationResponse(ApiModel):
    id: UUID
    kind: str
    title: str
    body: str
    link_type: str | None = Field(description="What to open, e.g. 'conversation'.")
    link_id: UUID | None
    created_at: datetime
    read_at: datetime | None


class NotificationPage(ApiModel):
    items: list[NotificationResponse]
    page: PageMeta


class UnreadResponse(ApiModel):
    notifications: int
    announcements: int


class MarkReadRequest(ApiModel):
    ids: list[UUID] | None = Field(
        default=None, max_length=200, description="Leave out to mark everything read."
    )


class MarkedResponse(ApiModel):
    marked: int


@router.get("/notifications", response_model=NotificationPage)
async def notifications(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    unread_only: bool = False,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> NotificationPage:
    page = await uc.conversations.notifications(
        principal, unread_only=unread_only, cursor=cursor, limit=limit
    )
    return NotificationPage(
        items=[NotificationResponse.model_validate(n) for n in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/notifications/unread", response_model=UnreadResponse)
async def unread(principal: SchoolPrincipal, uc: UseCasesDep) -> UnreadResponse:
    """Counts for the badges in the app bar."""
    notes, news = await uc.conversations.unread_counts(principal)
    return UnreadResponse(notifications=notes, announcements=news)


@router.post("/notifications/read", response_model=MarkedResponse)
async def mark_read(
    body: MarkReadRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> MarkedResponse:
    return MarkedResponse(marked=await uc.conversations.mark_read(principal, body.ids))
