"""Plain-English names for every audited action, so the trail reads well without a lookup table
in each frontend. A unit test fails when code writes an action that isn't listed here."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class AuditCategory(StrEnum):
    SECURITY = "security"
    PEOPLE = "people"
    ACADEMICS = "academics"
    ATTENDANCE = "attendance"
    FINANCE = "finance"
    SCHOOL = "school"
    PLATFORM = "platform"
    AUDIT = "audit"


@dataclass(frozen=True, slots=True)
class ActionInfo:
    label: str
    category: AuditCategory


_S, _P, _A, _T, _F, _SC, _PL, _AU = (
    AuditCategory.SECURITY,
    AuditCategory.PEOPLE,
    AuditCategory.ACADEMICS,
    AuditCategory.ATTENDANCE,
    AuditCategory.FINANCE,
    AuditCategory.SCHOOL,
    AuditCategory.PLATFORM,
    AuditCategory.AUDIT,
)

ACTIONS: MappingProxyType[str, ActionInfo] = MappingProxyType(
    {
        # Security: sign-in, sessions, two-step verification
        "identity.signed_in": ActionInfo("Signed in", _S),
        "identity.sign_in_failed": ActionInfo("Failed sign-in attempt", _S),
        "identity.signed_out": ActionInfo("Signed out", _S),
        "identity.session_reuse_detected": ActionInfo(
            "Stolen session suspected: all sessions ended", _S
        ),
        "identity.sessions_ended_inactive_account": ActionInfo(
            "Sessions ended because the account is no longer active", _S
        ),
        "identity.mfa_enrolled": ActionInfo("Set up two-step verification", _S),
        "identity.mfa_recovery_code_used": ActionInfo("Signed in with a recovery code", _S),
        "identity.mfa_recovery_codes_regenerated": ActionInfo("Created new recovery codes", _S),
        "identity.mfa_reset": ActionInfo("Reset two-step verification", _S),
        # People
        "school_user.invited": ActionInfo("Invited a user", _P),
        "identity.invite_accepted": ActionInfo("Accepted an invitation", _P),
        "identity.guardian_linked": ActionInfo("Linked a parent to a student", _P),
        "identity.guardian_unlinked": ActionInfo("Removed a parent from a student", _P),
        "platform_user.created": ActionInfo("Added an owner-console user", _PL),
        # Academics
        "academics.session_created": ActionInfo("Created an academic session", _A),
        "academics.term_added": ActionInfo("Added a term", _A),
        "academics.current_term_set": ActionInfo("Changed the current term", _A),
        "academics.class_created": ActionInfo("Created a class", _A),
        "academics.class_updated": ActionInfo("Changed a class", _A),
        "academics.subject_created": ActionInfo("Created a subject", _A),
        "academics.subject_linked": ActionInfo("Added a subject to a class", _A),
        "academics.subject_unlinked": ActionInfo("Removed a subject from a class", _A),
        "academics.student_enrolled": ActionInfo("Enrolled a student", _A),
        "academics.student_withdrawn": ActionInfo("Withdrew a student from a class", _A),
        # Attendance
        "attendance.register_submitted": ActionInfo("Took the class register", _T),
        "attendance.mark_corrected": ActionInfo("Corrected an attendance mark", _T),
        "attendance.settings_updated": ActionInfo("Changed staff check-in settings", _T),
        "attendance.kiosk_code_created": ActionInfo("Set up the staff check-in screen", _T),
        "attendance.staff_checked_in": ActionInfo("Checked in for work", _T),
        # Finance
        "finance.settings_updated": ActionInfo("Changed payment settings", _F),
        "finance.fee_item_created": ActionInfo("Added a fee", _F),
        "finance.fee_item_updated": ActionInfo("Changed a fee", _F),
        "finance.fee_item_activated": ActionInfo("Brought back a retired fee", _F),
        "finance.fee_item_retired": ActionInfo("Retired a fee", _F),
        "finance.scholarship_granted": ActionInfo("Granted a scholarship", _F),
        "finance.scholarship_revoked": ActionInfo("Ended a scholarship", _F),
        "finance.billing_run": ActionInfo("Billed a term", _F),
        "finance.invoice_created": ActionInfo("Created an invoice", _F),
        "finance.invoice_voided": ActionInfo("Cancelled an invoice", _F),
        "finance.payment_recorded": ActionInfo("Recorded a payment", _F),
        "finance.payment_reversed": ActionInfo("Reversed a payment", _F),
        "finance.refund_requested": ActionInfo("Asked for a refund", _F),
        "finance.refund_approved": ActionInfo("Approved a refund", _F),
        "finance.refund_rejected": ActionInfo("Turned down a refund", _F),
        "finance.payment_proof_submitted": ActionInfo("Uploaded proof of payment", _F),
        "finance.payment_proof_approved": ActionInfo("Approved proof of payment", _F),
        "finance.payment_proof_rejected": ActionInfo("Turned down proof of payment", _F),
        "finance.payment_proof_withdrawn": ActionInfo("Withdrew proof of payment", _F),
        "finance.payment_proof_file_viewed": ActionInfo("Opened a payment receipt", _F),
        "finance.gateway_connected": ActionInfo("Connected the online payment account", _F),
        "finance.gateway_disconnected": ActionInfo("Disconnected the online payment account", _F),
        "finance.checkout_started": ActionInfo("Started an online payment", _F),
        "finance.online_payment_received": ActionInfo("Received an online payment", _F),
        "finance.checkout_failed": ActionInfo("Online payment failed", _F),
        "finance.checkout_abandoned": ActionInfo("Online payment not completed", _F),
        "finance.checkout_needs_review": ActionInfo("Online payment needs checking", _F),
        # School account
        "tenant.registered": ActionInfo("Signed up the school", _SC),
        "tenant.email_verified": ActionInfo("Confirmed the school's email", _SC),
        "tenant.suspend": ActionInfo("Suspended the school", _SC),
        "tenant.reinstate": ActionInfo("Reinstated the school", _SC),
        "tenant.activate": ActionInfo("Activated the school", _SC),
        # Owner console
        "flag.upserted": ActionInfo("Changed a feature switch", _PL),
        "flag.override_set": ActionInfo("Changed a feature switch for one school", _PL),
        "plan.upserted": ActionInfo("Changed a plan", _PL),
        # The trail itself
        "audit.exported": ActionInfo("Downloaded the activity log", _AU),
    }
)


def describe(action: str) -> ActionInfo:
    known = ACTIONS.get(action)
    if known is not None:
        return known
    # Unknown actions (rows written by an older or newer release) still read sensibly.
    return ActionInfo(action.replace(".", ": ").replace("_", " ").capitalize(), _PL)
