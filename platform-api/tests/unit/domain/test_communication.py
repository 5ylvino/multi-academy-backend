from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.communication.announcements import (
    AUDIENCE_ROLES,
    Announcement,
    AnnouncementState,
    Audience,
    Priority,
    UrgentAnnouncementPublished,
)
from app.domain.communication.conversations import MAX_PARTICIPANTS, Conversation
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import SchoolRole
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed

NOW = datetime(2026, 11, 27, 9, tzinfo=UTC)


def _post(**kw: object) -> Announcement:
    args: dict[str, object] = {
        "tenant_id": uuid4(),
        "title": "Open day",
        "body": "Saturday at 10.",
        "audience": Audience.of(["parent"], []),
        "priority": Priority.NORMAL,
        "pinned": False,
        "publish_at": None,
        "expires_at": None,
        "by": uuid4(),
        "now": NOW,
    }
    args.update(kw)
    return Announcement.publish(**args)  # type: ignore[arg-type]


def test_audience_roles_match_the_school_roles() -> None:
    assert {r.value for r in SchoolRole} == AUDIENCE_ROLES


def test_audience_rules() -> None:
    with pytest.raises(ValidationFailed):
        Audience.of([], [])
    with pytest.raises(ValidationFailed):
        Audience.of(["janitor"], [])
    klass = uuid4()
    a = Audience.of([" Parent "], [klass])
    assert a.includes(frozenset({"parent"}), frozenset({klass}))
    assert not a.includes(frozenset({"parent"}), frozenset({uuid4()}))
    assert not a.includes(frozenset({"student"}), frozenset({klass}))
    assert Audience.of(["parent"], []).includes(frozenset({"parent"}), frozenset())


def test_lifecycle_states() -> None:
    later = _post(publish_at=NOW + timedelta(days=1), expires_at=NOW + timedelta(days=5))
    assert later.state(NOW) is AnnouncementState.SCHEDULED
    assert later.state(NOW + timedelta(days=2)) is AnnouncementState.LIVE
    assert later.state(NOW + timedelta(days=6)) is AnnouncementState.EXPIRED
    later.withdraw(by=uuid4(), reason="Cancelled", now=NOW)
    assert later.state(NOW + timedelta(days=2)) is AnnouncementState.WITHDRAWN
    with pytest.raises(InvalidStateTransition):
        later.withdraw(by=uuid4(), reason="Again", now=NOW)


@pytest.mark.parametrize(
    "kw",
    [
        {"title": "Hi"},
        {"body": "  "},
        {"publish_at": NOW + timedelta(days=200)},
        {"expires_at": NOW - timedelta(minutes=1)},
        {"expires_at": NOW + timedelta(days=500)},
        {"priority": Priority.URGENT, "publish_at": NOW + timedelta(hours=1)},
    ],
)
def test_publish_validation(kw: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        _post(**kw)


def test_urgent_raises_an_event_and_freezes_its_audience() -> None:
    a = _post(priority=Priority.URGENT)
    (event,) = a.pull_events()
    assert isinstance(event, UrgentAnnouncementPublished)
    with pytest.raises(ValidationFailed):
        a.edit(
            title=a.title,
            body=a.body,
            audience=Audience.of(["student"], []),
            pinned=False,
            publish_at=None,
            expires_at=None,
            now=NOW,
        )
    a.edit(
        title="Road closed",
        body=a.body,
        audience=a.audience,
        pinned=True,
        publish_at=None,
        expires_at=None,
        now=NOW + timedelta(minutes=5),
    )
    assert a.edited_at == NOW + timedelta(minutes=5)
    assert _post().pull_events() == []


def test_conversation_rules() -> None:
    me, you = uuid4(), uuid4()
    c = Conversation.start(
        tenant_id=uuid4(),
        subject="  Fees   question ",
        student_id=None,
        by=me,
        others={you, me},
        first_message=" Hello ",
        now=NOW,
    )
    assert c.subject == "Fees question"
    assert c.participants == {me, you}
    assert c.new_messages[0].body == "Hello"
    c.send(by=you, body="Hi", now=NOW + timedelta(minutes=1))
    assert c.message_count == 2
    assert c.last_message_at == NOW + timedelta(minutes=1)
    with pytest.raises(ValidationFailed):
        c.send(by=uuid4(), body="Let me in", now=NOW)
    with pytest.raises(ValidationFailed):
        c.send(by=me, body="x" * 5001, now=NOW)
    with pytest.raises(ValidationFailed):
        Conversation.start(
            tenant_id=uuid4(),
            subject="Alone",
            student_id=None,
            by=me,
            others={me},
            first_message="Hi",
            now=NOW,
        )
    with pytest.raises(ValidationFailed):
        Conversation.start(
            tenant_id=uuid4(),
            subject="Crowd",
            student_id=None,
            by=me,
            others={uuid4() for _ in range(MAX_PARTICIPANTS)},
            first_message="Hi",
            now=NOW,
        )


def test_notifications_are_trimmed() -> None:
    n = Notification.new(
        tenant_id=uuid4(),
        user_id=uuid4(),
        kind=NotificationKind.GENERAL,
        title="t " * 200,
        body="b\n" * 300,
        link_type=None,
        link_id=None,
        now=NOW,
    )
    assert len(n.title) <= 160
    assert len(n.body) <= 280
