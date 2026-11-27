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
    COMMUNICATION = "communication"
    SAFEGUARDING = "safeguarding"
    WELFARE = "welfare"


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
_C = AuditCategory.COMMUNICATION
_SG = AuditCategory.SAFEGUARDING
_W = AuditCategory.WELFARE

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
        "finance.expense_category_added": ActionInfo("Added an expense category", _F),
        "finance.expense_category_updated": ActionInfo("Changed an expense category", _F),
        "finance.budget_set": ActionInfo("Set a term budget", _F),
        "finance.expense_recorded": ActionInfo("Recorded an expense", _F),
        "finance.expense_receipt_attached": ActionInfo("Attached an expense receipt", _F),
        "finance.expense_receipt_viewed": ActionInfo("Opened an expense receipt", _F),
        "finance.expense_approved": ActionInfo("Approved an expense", _F),
        "finance.expense_rejected": ActionInfo("Rejected an expense", _F),
        "finance.expense_voided": ActionInfo("Cancelled an expense", _F),
        # Inventory
        "inventory.item_added": ActionInfo("Added a stock item", _F),
        "inventory.item_updated": ActionInfo("Changed a stock item", _F),
        "inventory.stock_receive": ActionInfo("Received stock", _F),
        "inventory.stock_issue": ActionInfo("Issued stock", _F),
        "inventory.stock_return": ActionInfo("Took back stock", _F),
        "inventory.stock_adjust": ActionInfo("Corrected stock after a count", _F),
        "inventory.stock_write_off": ActionInfo("Wrote off stock", _F),
        # Payroll
        "payroll.settings_updated": ActionInfo("Changed the payroll tax and pension settings", _F),
        "payroll.profile_created": ActionInfo("Set up a staff member's pay", _F),
        "payroll.profile_updated": ActionInfo("Changed a staff member's pay", _F),
        "payroll.run_prepared": ActionInfo("Prepared a pay run", _F),
        "payroll.run_refreshed": ActionInfo("Recalculated a pay run", _F),
        "payroll.run_approved": ActionInfo("Approved a pay run", _F),
        "payroll.run_paid": ActionInfo("Marked a pay run as paid", _F),
        "payroll.run_voided": ActionInfo("Cancelled a pay run", _F),
        "payroll.bank_schedule_exported": ActionInfo("Downloaded a bank payment schedule", _F),
        # Communication
        "communication.announcement_published": ActionInfo("Published an announcement", _C),
        "communication.announcement_edited": ActionInfo("Edited an announcement", _C),
        "communication.announcement_withdrawn": ActionInfo("Withdrew an announcement", _C),
        "communication.conversation_started": ActionInfo("Started a conversation", _C),
        "communication.conversation_reviewed": ActionInfo(
            "Reviewed a conversation they are not in", _C
        ),
        # Safeguarding (the activity log never holds what was written)
        "safeguarding.concern_reported": ActionInfo("Recorded a safeguarding concern", _SG),
        "safeguarding.case_opened": ActionInfo("Opened a safeguarding case", _SG),
        "safeguarding.note_added": ActionInfo("Added a note to a safeguarding case", _SG),
        "safeguarding.parent_contact_recorded": ActionInfo(
            "Recorded contact with parents on a safeguarding case", _SG
        ),
        "safeguarding.referred": ActionInfo("Referred a safeguarding case to an agency", _SG),
        "safeguarding.lead_assigned": ActionInfo("Assigned a safeguarding case lead", _SG),
        "safeguarding.status_changed": ActionInfo("Changed a safeguarding case's status", _SG),
        # Welfare: clinic, gate, transport and boarding (the log never holds medical detail)
        "clinic.record_viewed": ActionInfo("Viewed a student's health record", _W),
        "clinic.record_updated": ActionInfo("Updated a student's health record", _W),
        "clinic.visit_opened": ActionInfo("Booked a student into the clinic", _W),
        "clinic.visit_updated": ActionInfo("Updated a clinic visit", _W),
        "clinic.visit_closed": ActionInfo("Closed a clinic visit", _W),
        "gate.collector_added": ActionInfo("Added an adult who may collect a student", _W),
        "gate.collector_removed": ActionInfo("Removed an adult who may collect a student", _W),
        "gate.student_released": ActionInfo("Released a student at the gate", _W),
        "gate.student_refused": ActionInfo("Refused to release a student at the gate", _W),
        "gate.visitor_signed_in": ActionInfo("Signed a visitor in", _W),
        "gate.visitor_signed_out": ActionInfo("Signed a visitor out", _W),
        # Transport
        "transport.route_created": ActionInfo("Created a bus route", _W),
        "transport.route_updated": ActionInfo("Changed a bus route", _W),
        "transport.stop_added": ActionInfo("Added a stop to a bus route", _W),
        "transport.stop_updated": ActionInfo("Changed a bus stop", _W),
        "transport.stop_removed": ActionInfo("Removed a bus stop", _W),
        "transport.student_assigned": ActionInfo("Put a student on a bus route", _W),
        "transport.student_unassigned": ActionInfo("Took a student off a bus route", _W),
        "transport.boarding_recorded": ActionInfo("Recorded a student on the bus", _W),
        # Boarding
        "hostel.created": ActionInfo("Opened a hostel", _W),
        "hostel.updated": ActionInfo("Changed a hostel", _W),
        "hostel.room_added": ActionInfo("Added a hostel room", _W),
        "hostel.room_updated": ActionInfo("Changed a hostel room", _W),
        "hostel.boarder_allocated": ActionInfo("Gave a boarder a room", _W),
        "hostel.boarder_vacated": ActionInfo("Moved a boarder out of the hostel", _W),
        "hostel.exeat_requested": ActionInfo("Asked for an exeat", _W),
        "hostel.exeat_approved": ActionInfo("Approved an exeat", _W),
        "hostel.exeat_rejected": ActionInfo("Turned down an exeat", _W),
        "hostel.exeat_cancelled": ActionInfo("Cancelled an exeat", _W),
        "hostel.exeat_signed_out": ActionInfo("Signed a boarder out on exeat", _W),
        "hostel.exeat_returned": ActionInfo("Signed a boarder back in from exeat", _W),
        # School account
        "tenant.registered": ActionInfo("Signed up the school", _SC),
        "tenant.email_verified": ActionInfo("Confirmed the school's email", _SC),
        "tenant.suspend": ActionInfo("Suspended the school", _SC),
        "tenant.reinstate": ActionInfo("Reinstated the school", _SC),
        "tenant.activate": ActionInfo("Activated the school", _SC),
        "tenant.suspended_for_non_payment": ActionInfo(
            "Suspended the school for an unpaid platform invoice", _SC
        ),
        "tenant.reinstated_after_payment": ActionInfo("Reinstated the school after payment", _SC),
        # The school's subscription to the platform
        "subscription.started": ActionInfo("Started the school's subscription", _SC),
        "subscription.period_renewed": ActionInfo("Renewed the free plan for another period", _SC),
        "subscription.invoice_issued": ActionInfo("Issued a platform invoice", _SC),
        "subscription.invoice_reminder_sent": ActionInfo(
            "Sent a reminder for an unpaid platform invoice", _SC
        ),
        "subscription.checkout_started": ActionInfo(
            "Started paying a platform invoice online", _SC
        ),
        "subscription.invoice_paid": ActionInfo("Paid a platform invoice", _SC),
        "subscription.payment_mismatch": ActionInfo("Platform invoice payment needs checking", _SC),
        "subscription.invoice_voided": ActionInfo("Cancelled a platform invoice", _PL),
        "subscription.plan_changed": ActionInfo("Changed the school's plan", _PL),
        "subscription.cycle_run": ActionInfo("Ran platform billing", _PL),
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
