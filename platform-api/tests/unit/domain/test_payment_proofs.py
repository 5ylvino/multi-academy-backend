"""Proof of payment, payment options and guardian links."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.finance.catalog import BankAccount, FinanceSettings
from app.domain.finance.payments import PaymentMethod
from app.domain.finance.proofs import (
    MAX_FILE_BYTES,
    PaymentProof,
    ProofFile,
    ProofStatus,
    ReceiptType,
    detect_receipt_type,
    normalise_reference,
)
from app.domain.identity.guardians import GuardianLink, Relationship, parse_relationship
from app.domain.identity.roles import Realm
from app.domain.identity.user import UserAccount
from app.domain.shared.errors import (
    InvalidStateTransition,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.money import Money
from app.domain.shared.value_objects import EmailAddress

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
TODAY = NOW.date()
TENANT = uuid4()
PARENT = uuid4()
STUDENT = uuid4()
PDF = b"%PDF-1.7\n" + b"0" * 200


def _proof(**overrides: object) -> PaymentProof:
    args: dict[str, object] = {
        "tenant_id": TENANT,
        "student_id": STUDENT,
        "by": PARENT,
        "amount": Money(5_000_000, "NGN"),
        "method": PaymentMethod.BANK_TRANSFER,
        "paid_on": TODAY - timedelta(days=1),
        "reference": "FT-2611 0042 xyz",
        "payer_name": "  Ngozi   Obi ",
        "note": None,
        "file": ProofFile(key="k", content_type=ReceiptType.PDF, size=209, sha256="a" * 64),
        "invoice_id": None,
        "today": TODAY,
        "now": NOW,
    }
    args.update(overrides)
    return PaymentProof.submit(**args)  # type: ignore[arg-type]


def test_receipt_type_comes_from_the_bytes() -> None:
    assert detect_receipt_type(PDF) is ReceiptType.PDF
    assert detect_receipt_type(b"\x89PNG\r\n\x1a\n" + b"0" * 200) is ReceiptType.PNG
    assert detect_receipt_type(b"\xff\xd8\xff\xe0" + b"0" * 200) is ReceiptType.JPEG
    assert detect_receipt_type(b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 200) is ReceiptType.WEBP
    with pytest.raises(ValidationFailed) as err:
        detect_receipt_type(b"<html><script>alert(1)</script>" + b" " * 200)
    assert err.value.code == "unsupported_file"
    with pytest.raises(ValidationFailed):
        detect_receipt_type(b"%PDF-")  # too small
    with pytest.raises(ValidationFailed):
        detect_receipt_type(b"%PDF-" + b"0" * MAX_FILE_BYTES)
    assert ReceiptType.JPEG.extension == "jpg"


def test_references_are_compared_loosely() -> None:
    assert normalise_reference(" ft-2611 0042/xyz ") == "FT26110042XYZ"


def test_submit_rules() -> None:
    proof = _proof()
    assert proof.reference_key == "FT26110042XYZ"
    assert proof.payer_name == "Ngozi Obi"
    assert proof.pull_events()[0].name == "finance.payment_proof_submitted"
    with pytest.raises(ValidationFailed):
        _proof(method=PaymentMethod.CASH)
    with pytest.raises(ValidationFailed):
        _proof(paid_on=TODAY + timedelta(days=1))
    with pytest.raises(ValidationFailed):
        _proof(paid_on=TODAY - timedelta(days=400))
    with pytest.raises(ValidationFailed):
        _proof(reference="--")


def test_review_needs_another_person_and_a_note_for_a_different_amount() -> None:
    proof = _proof()
    with pytest.raises(PermissionDenied) as denied:
        proof.approve(by=PARENT, now=NOW, payment_id=uuid4(), amount=None, note=None)
    assert denied.value.code == "dual_control_required"
    bursar = uuid4()
    with pytest.raises(ValidationFailed):
        proof.approve(
            by=bursar, now=NOW, payment_id=uuid4(), amount=Money(4_000_000, "NGN"), note=None
        )
    assert proof.status is ProofStatus.SUBMITTED
    received = proof.approve(
        by=bursar,
        now=NOW,
        payment_id=uuid4(),
        amount=Money(4_000_000, "NGN"),
        note="Bank shows N40,000",
    )
    assert received == Money(4_000_000, "NGN")
    assert proof.status is ProofStatus.APPROVED
    with pytest.raises(InvalidStateTransition):
        proof.reject(by=bursar, now=NOW, reason="Changed my mind")

    other = _proof()
    with pytest.raises(ValidationFailed):
        other.reject(by=bursar, now=NOW, reason="")
    other.reject(by=bursar, now=NOW, reason="No such transfer on the statement")
    assert other.review_note == "No such transfer on the statement"

    mine = _proof()
    with pytest.raises(PermissionDenied):
        mine.withdraw(by=bursar)
    mine.withdraw(by=PARENT)
    assert mine.status is ProofStatus.WITHDRAWN
    with pytest.raises(InvalidStateTransition):
        mine.withdraw(by=PARENT)


def test_payment_options() -> None:
    settings = FinanceSettings.defaults(TENANT, "NGN")
    assert settings.online_payments_enabled is False
    assert settings.payment_proofs_enabled is True
    with pytest.raises(ValidationFailed) as err:
        settings.set_payment_options(
            online_payments_enabled=True,
            payment_proofs_enabled=True,
            payment_instructions=None,
            bank_accounts=[],
            gateway_ready=False,
        )
    assert err.value.code == "gateway_not_configured"
    account = BankAccount("GTBank", "Fees Academy", "0123 456 789")
    assert account.account_number == "0123456789"
    settings.set_payment_options(
        online_payments_enabled=False,
        payment_proofs_enabled=False,
        payment_instructions="  Use the student's admission number as the narration.  ",
        bank_accounts=[account],
        gateway_ready=False,
    )
    assert settings.payment_instructions == "Use the student's admission number as the narration."
    with pytest.raises(ValidationFailed):
        BankAccount("GTBank", "Fees", "12")
    with pytest.raises(ValidationFailed):
        settings.set_payment_options(
            online_payments_enabled=False,
            payment_proofs_enabled=True,
            payment_instructions=None,
            bank_accounts=[account] * 6,
            gateway_ready=False,
        )


def _user(role: str, tenant: object = TENANT) -> UserAccount:
    return UserAccount.create(
        realm=Realm.SCHOOL,
        tenant_id=tenant,  # type: ignore[arg-type]
        email=EmailAddress(f"{uuid4().hex[:8]}@x.ng"),
        full_name="Someone",
        password_hash="x",
        roles=frozenset({role}),
        now=NOW,
    )


def test_guardian_links_need_a_parent_and_a_student_in_the_same_school() -> None:
    parent, student = _user("parent"), _user("student")
    link = GuardianLink.create(
        parent=parent,
        student=student,
        relationship=Relationship.MOTHER,
        existing_count=0,
        by=uuid4(),
        now=NOW,
    )
    assert link.parent_id == parent.id
    with pytest.raises(ValidationFailed):
        GuardianLink.create(
            parent=_user("class_teacher"),
            student=student,
            relationship=Relationship.GUARDIAN,
            existing_count=0,
            by=uuid4(),
            now=NOW,
        )
    with pytest.raises(ValidationFailed):
        GuardianLink.create(
            parent=parent,
            student=_user("student", uuid4()),
            relationship=Relationship.GUARDIAN,
            existing_count=0,
            by=uuid4(),
            now=NOW,
        )
    with pytest.raises(ValidationFailed) as err:
        GuardianLink.create(
            parent=parent,
            student=student,
            relationship=Relationship.GUARDIAN,
            existing_count=6,
            by=uuid4(),
            now=NOW,
        )
    assert err.value.code == "too_many_guardians"
    with pytest.raises(ValidationFailed):
        parse_relationship("uncle-ish")
