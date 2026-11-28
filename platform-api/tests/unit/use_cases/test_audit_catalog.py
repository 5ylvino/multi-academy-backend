"""Every audited action has a plain-English label, and the trail helpers behave."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.shared.errors import ValidationFailed
from app.use_cases.audit.catalog import ACTIONS, AuditCategory, describe
from app.use_cases.audit.trail import decode_cursor, encode_cursor
from app.use_cases.shared.audit import changes

USE_CASES = Path(__file__).resolve().parents[3] / "app" / "use_cases"
_ACTION = re.compile(
    r'"((?:identity|tenant|academics|attendance|finance|flag|plan|platform_user|school_user|audit)'
    r'\.[a-z_]+)"'
)


def _actions_in_code() -> set[str]:
    found: set[str] = set()
    for path in USE_CASES.rglob("*.py"):
        if path.name == "catalog.py" and path.parent.name == "audit":
            continue
        found |= set(_ACTION.findall(path.read_text()))
    # Built from a command value: tenant.{suspend,reinstate,activate}
    found |= {"tenant.suspend", "tenant.reinstate", "tenant.activate"}
    return found


def test_every_audited_action_has_a_label() -> None:
    missing = sorted(_actions_in_code() - set(ACTIONS))
    assert not missing, f"add these to app/use_cases/audit/catalog.py: {missing}"


def test_labels_are_sentences_without_codes() -> None:
    for code, info in ACTIONS.items():
        assert info.label[0].isupper(), code
        assert "_" not in info.label, code
        assert isinstance(info.category, AuditCategory)


def test_unknown_actions_still_read_sensibly() -> None:
    info = describe("library.book_lent")
    assert info.label == "Library: book lent"


def test_changes_lists_only_changed_fields() -> None:
    diff = changes({"name": "JSS 1A", "capacity": 30}, {"name": "JSS 1B", "capacity": 30})
    assert diff == {"name": {"from": "JSS 1A", "to": "JSS 1B"}}


def test_cursor_round_trip() -> None:
    at = datetime(2026, 11, 27, 9, 30, tzinfo=UTC)
    event_id = uuid4()
    assert decode_cursor(encode_cursor(at, event_id)) == (at, event_id)


@pytest.mark.parametrize("bad", ["not-a-cursor", "", "MjAyNi0xMS0yN3w"])
def test_bad_cursor_is_a_validation_error(bad: str) -> None:
    with pytest.raises(ValidationFailed):
        decode_cursor(bad or "x")
