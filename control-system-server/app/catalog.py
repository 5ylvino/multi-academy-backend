"""Feature flag catalog + plan entitlements.

Source of truth alignment: MULTI-ACADEMY-SMS-BUILD.md §3.3 (flag catalog) and
§8 (plan ↔ feature entitlements). Keys are stable — never rename without a
migration.
"""

# (key, name, default_enabled, min_plan, requires_providers)
FLAG_CATALOG: list[tuple[str, str, bool, str | None, list[str]]] = [
    # Foundation
    ("auth.login", "Email/password login", True, None, []),
    ("auth.register", "Self-service school registration", True, None, []),
    ("auth.password_reset", "Password reset", True, None, []),
    ("auth.biometric_webauthn", "Staff WebAuthn / fingerprint", True, None, []),
    ("auth.google_oauth", "Google login", False, None, []),
    ("auth.remember_me", "Remember-me sessions", True, None, []),
    ("users.management", "User CRUD", True, None, []),
    ("users.passport_photo", "Passport photo upload", True, None, []),
    ("org.profile", "Organization profile", True, None, []),
    ("org.branding", "Logo / custom login branding", True, None, []),
    ("org.subscription_view", "School-facing subscription view", True, None, []),
    ("audit.logs", "Audit log viewing", True, None, []),
    ("rbac.permissions", "Permission-gated APIs/UI", True, None, []),
    # Academics
    ("academic.sessions_terms", "Sessions & terms", True, None, []),
    ("academic.classes", "Classes", True, None, []),
    ("academic.subjects", "Subjects", True, None, []),
    ("academic.enrollments", "Student class enrollment", True, None, []),
    ("academic.assignments", "Assignments / homework", True, None, []),
    ("academic.results", "CA + exam results", True, None, []),
    ("academic.results_approve", "Result approval workflow", True, None, []),
    ("academic.scheme_of_work", "Scheme of work planning and progress", True, None, []),
    ("academic.report_cards", "Printable report cards", False, "premium", []),
    ("academic.term_ranking", "Class position / ranks", False, "premium", []),
    ("academic.grading_matrix", "Configurable CA/exam weights", False, "premium", []),
    ("academic.cbt", "Computer-based tests", False, "elite", []),
    ("academic.lesson_notes", "Digital lesson registries", False, "elite", []),
    ("academic.timetable", "Timetable & clash solver", False, "elite", []),
    ("academic.nerdc_curriculum", "NERDC curriculum mapping", False, "elite", []),
    ("academic.waec_neco_analytics", "WAEC/NECO analytics", False, "elite", []),
    ("academic.jamb_cbt", "JAMB-style CBT prep", False, "elite", []),
    # Attendance
    ("attendance.students", "Student attendance", True, None, []),
    ("attendance.staff", "Staff attendance", True, None, []),
    ("attendance.manual_codes", "Admin OTP verification codes", True, None, []),
    ("attendance.geo_verification", "Location verification window", True, None, []),
    ("attendance.absence_alerts", "Low-attendance notifications", False, "premium", []),
    ("attendance.auto_absent_eod", "Auto-mark absent end of day", True, None, []),
    # Finance
    ("fees.structures", "Fee structures", True, None, []),
    ("fees.invoices", "Invoices", True, None, []),
    ("fees.payments_manual", "Manual payment recording", True, None, []),
    ("fees.scholarships", "Scholarships", True, None, []),
    ("fees.refunds", "Refunds", True, None, []),
    ("fees.gateway", "Online payment checkout", False, "premium", ["payment"]),
    ("fees.installments", "Installment / deferred plans", False, "premium", []),
    ("fees.advance_payment", "Ahead-of-time / prepay school fees", False, "premium", ["payment"]),
    ("fees.balance_freeze", "Freeze portal on arrears", False, "premium", []),
    ("fees.split_settlement", "School + SaaS fee split", False, "elite", ["payment"]),
    ("fees.bank_reconciliation", "Bank feed / virtual account match", False, "elite", ["payment"]),
    ("finance.payroll_paye", "Payroll + PAYE", False, "elite", []),
    ("finance.inventory", "Inventory / uniforms", False, "elite", []),
    ("finance.budgets", "Campus P&L / budgets", False, "elite", []),
    # Communication
    ("comms.announcements", "Announcements", True, None, []),
    ("comms.in_app_notifications", "In-app notifications", True, None, []),
    ("comms.realtime_ws", "WebSocket push", False, "premium", []),
    ("comms.messaging", "Staff ↔ parent messaging", False, "premium", []),
    ("comms.email", "Email outbound", False, "premium", ["email"]),
    ("comms.sms", "SMS outbound", False, "premium", ["sms"]),
    ("comms.push", "Mobile push", False, "elite", ["push"]),
    ("comms.consent_slips", "Digital permission / medical waivers", False, "elite", []),
    ("comms.calendar", "School calendar / events", False, "premium", []),
    ("comms.emergency_broadcast", "Emergency audience blast", False, "elite", ["sms"]),
    ("comms.meetings", "Virtual meetings", False, "elite", ["meeting"]),
    ("comms.meetings_calendar_sync", "Meetings calendar sync", False, "elite", ["meeting"]),
    ("comms.pta", "PTA meetings, RSVP, minutes", True, None, []),
    ("comms.inbox", "Unified inbox", True, None, []),
    # Portals & roles
    ("portal.parent", "Dedicated parent portal", True, None, []),
    ("portal.student", "Dedicated student portal", False, "premium", []),
    ("portal.bursar", "Bursar-focused finance UX", False, "elite", []),
    ("roles.ops_staff", "Driver / guard / nurse roles", False, "elite", []),
    # Operations modules
    ("ops.library", "Library", False, "elite", []),
    ("ops.transport", "Transport / bus tracking", False, "elite", ["maps"]),
    ("ops.hostel", "Hostel", False, "elite", []),
    ("ops.clinic", "Nurse / clinic logs", False, "elite", []),
    ("ops.gate_security", "Gate / pickup tokens", False, "elite", []),
    ("ops.safeguarding", "Safeguarding / digital exeats", True, None, []),
    ("users.parent_activation", "Parent activation codes", True, None, []),
    # Stage-specific & advanced
    ("nursery.developmental", "Developmental scorecards", False, "elite", []),
    ("nursery.wellness", "Daily wellness logs", False, "elite", []),
    ("nursery.media_moments", "Classroom media to parents", False, "elite", ["storage"]),
    ("primary.badges", "Gamified badges", False, "elite", []),
    ("primary.literacy_log", "Reading logs", False, "elite", []),
    ("secondary.career", "Career / alumni / e-portfolio", False, "elite", []),
    ("ai.assistant", "In-app AI help for school ops", False, "elite", ["ai"]),
    ("ai.support_chatbot", "AI customer-support chatbot", False, "elite", ["ai"]),
    ("ai.performance_detection", "AI student performance pattern detection", False, "elite", ["ai"]),
    ("ai.performance_recommendations", "AI performance recommendations", False, "elite", ["ai"]),
    ("ai.risk_analytics", "Predictive risk analytics", False, "elite", ["ai"]),
    ("ai.essay_grading", "Essay grading assist", False, "elite", ["ai"]),
    ("ai.tutor", "AI self-tutor for students", False, "elite", ["ai"]),
    ("ai.teacher_copilot", "Teacher intervention copilot", False, "elite", ["ai"]),
    ("ai.report_comments", "AI report card comment drafts", False, "elite", ["ai"]),
    ("ai.timetable_solver", "Smart timetable solver", False, "elite", ["ai"]),
    ("tutoring.marketplace", "Tutoring marketplace (find & book tutors)", False, "elite", []),
    ("tutoring.payments", "Tutoring session payments", False, "elite", ["payment"]),
    ("tutoring.ai_hybrid", "AI prep before human tutoring sessions", False, "elite", ["ai"]),
    ("offline.pwa", "Offline-first PWA sync", False, "elite", []),
    ("media.compression", "Aggressive upload compression", False, "elite", ["storage"]),
    ("reports.view", "Core reports generate/download", True, None, []),
    ("reports.advanced", "Advanced analytics dashboards", False, "elite", []),
    ("integrations.workspace", "Google Workspace extras", False, "elite", []),
]

PLAN_RANKS = {"basic": 1, "premium": 2, "elite": 3}

# Premium/Elite add these on top of foundation defaults (cumulative).
# Elite list = every gated catalog flag so plan entitlements stay complete.
PREMIUM_EXTRAS = [
    "fees.gateway",
    "fees.installments",
    "fees.advance_payment",
    "fees.balance_freeze",
    "comms.sms",
    "comms.email",
    "comms.realtime_ws",
    "comms.messaging",
    "comms.calendar",
    "portal.student",
    "academic.report_cards",
    "academic.term_ranking",
    "academic.grading_matrix",
    "attendance.absence_alerts",
]

ELITE_EXTRAS = PREMIUM_EXTRAS + [
    key
    for key, _, _, min_plan, _ in FLAG_CATALOG
    if min_plan == "elite" and key not in PREMIUM_EXTRAS
]

DEFAULT_PROVIDERS = {
    "payment": "paystack",
    "sms": "africas_talking",
    "email": "resend",
    "ai": "openai",
    "meeting": "google_meet",
}

KNOWN_PROVIDERS: dict[str, list[str]] = {
    "payment": ["paystack", "flutterwave", "opay", "palmpay", "momo", "monnify", "stripe"],
    "sms": ["africas_talking", "termii", "twilio", "hubtel"],
    "email": ["resend", "cpanel", "ses", "sendgrid", "mailgun"],
    "ai": ["openai", "gemini", "kilo", "nvidia", "anthropic", "azure_openai"],
    "meeting": ["google_meet", "zoom", "microsoft_teams", "whereby"],
    "storage": ["s3", "r2", "gcs", "local"],
    "push": ["fcm", "onesignal"],
    "maps": ["google", "mapbox"],
    # Google login (auth.google_oauth). Vault secrets: client_id, client_secret.
    "oauth": ["google"],
}
