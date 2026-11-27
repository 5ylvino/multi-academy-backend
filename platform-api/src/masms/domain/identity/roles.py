"""Role names for the two identity realms."""

from __future__ import annotations

from enum import StrEnum


class Realm(StrEnum):
    """Who a principal is. Tokens from one realm are rejected by the other realm's routes."""

    SCHOOL = "school"
    PLATFORM = "platform"


class SchoolRole(StrEnum):
    DIRECTOR = "director"
    SCHOOL_ADMIN = "school_admin"
    IT_ADMIN = "it_admin"
    HEAD_TEACHER = "head_teacher"
    PRINCIPAL = "principal"
    VICE_PRINCIPAL = "vice_principal"
    ASSISTANT_HEAD_TEACHER = "assistant_head_teacher"
    BURSAR = "bursar"
    CLASS_TEACHER = "class_teacher"
    SUBJECT_TEACHER = "subject_teacher"
    ADMINISTRATIVE_STAFF = "administrative_staff"
    STUDENT = "student"
    PARENT = "parent"
    DRIVER = "driver"
    GUARD = "guard"
    NURSE = "nurse"


class PlatformRole(StrEnum):
    OWNER = "owner"
    OPS_ADMIN = "ops_admin"
    BILLING_ADMIN = "billing_admin"
    SUPPORT_AGENT = "support_agent"
    SECURITY_ANALYST = "security_analyst"
    AUDITOR = "auditor"
    ENGINEER_STAGING = "engineer_staging"
