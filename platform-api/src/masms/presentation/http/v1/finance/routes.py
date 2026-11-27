"""School fees: catalog, scholarships, billing, invoices, payments, refunds and statements."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Query, Response, UploadFile, status

from masms.domain.finance.proofs import MAX_FILE_BYTES
from masms.domain.shared.errors import ValidationFailed
from masms.presentation.http.dependencies.auth import SchoolPrincipal
from masms.presentation.http.dependencies.context import UseCasesDep
from masms.presentation.http.v1.finance.schemas import (
    ApproveProofRequest,
    BillingRunRequest,
    BillingRunResponse,
    DecisionRequest,
    FeeItemRequest,
    FeeItemResponse,
    InvoicePage,
    InvoiceRequest,
    InvoiceResponse,
    PaymentPage,
    PaymentRequest,
    PaymentResponse,
    ProofPage,
    ProofResponse,
    ReasonRequest,
    RefundRequest,
    RefundResponse,
    ScholarshipRequest,
    ScholarshipResponse,
    SettingsRequest,
    SettingsResponse,
    StatementResponse,
    TrialBalanceResponse,
)
from masms.presentation.http.v1.schemas import PageMeta
from masms.use_cases.finance.catalog import (
    BankAccountView,
    FeeItemCommand,
    ScholarshipCommand,
    UpdateSettingsCommand,
)
from masms.use_cases.finance.payments import RecordPaymentCommand
from masms.use_cases.finance.proofs import SubmitProofCommand

router = APIRouter(prefix="/finance", tags=["finance"])

CursorQuery = Annotated[str | None, Query(max_length=64)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


def _fee_command(body: FeeItemRequest) -> FeeItemCommand:
    return FeeItemCommand(
        name=body.name,
        amount_minor=body.amount_minor,
        currency=body.currency,
        school_level=body.school_level,
        class_id=body.class_id,
        terms=list(body.terms),
        mandatory=body.mandatory,
    )


# ---- settings ----------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(principal: SchoolPrincipal, uc: UseCasesDep) -> SettingsResponse:
    return SettingsResponse.model_validate(await uc.finance_catalog.get_settings(principal))


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SettingsResponse:
    view = await uc.finance_catalog.update_settings(
        principal,
        UpdateSettingsCommand(
            currency=body.currency,
            default_due_days=body.default_due_days,
            online_payments_enabled=body.online_payments_enabled,
            payment_proofs_enabled=body.payment_proofs_enabled,
            payment_instructions=body.payment_instructions,
            bank_accounts=[
                BankAccountView(a.bank_name, a.account_name, a.account_number)
                for a in body.bank_accounts
            ],
        ),
    )
    return SettingsResponse.model_validate(view)


# ---- fee items ---------------------------------------------------------------------------


@router.get("/fee-items", response_model=list[FeeItemResponse])
async def list_fee_items(
    principal: SchoolPrincipal, uc: UseCasesDep, include_inactive: bool = False
) -> list[FeeItemResponse]:
    items = await uc.finance_catalog.list_fee_items(principal, include_inactive=include_inactive)
    return [FeeItemResponse.model_validate(i) for i in items]


@router.post("/fee-items", response_model=FeeItemResponse, status_code=status.HTTP_201_CREATED)
async def create_fee_item(
    body: FeeItemRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> FeeItemResponse:
    item = await uc.finance_catalog.create_fee_item(principal, _fee_command(body))
    return FeeItemResponse.model_validate(item)


@router.put("/fee-items/{item_id}", response_model=FeeItemResponse)
async def update_fee_item(
    item_id: UUID, body: FeeItemRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> FeeItemResponse:
    item = await uc.finance_catalog.update_fee_item(principal, item_id, _fee_command(body))
    return FeeItemResponse.model_validate(item)


@router.post("/fee-items/{item_id}/retire", response_model=FeeItemResponse)
async def retire_fee_item(
    item_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> FeeItemResponse:
    item = await uc.finance_catalog.set_fee_item_active(principal, item_id, active=False)
    return FeeItemResponse.model_validate(item)


@router.post("/fee-items/{item_id}/restore", response_model=FeeItemResponse)
async def restore_fee_item(
    item_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> FeeItemResponse:
    item = await uc.finance_catalog.set_fee_item_active(principal, item_id, active=True)
    return FeeItemResponse.model_validate(item)


# ---- scholarships ------------------------------------------------------------------------


@router.get("/scholarships", response_model=list[ScholarshipResponse])
async def list_scholarships(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    student_id: UUID | None = None,
    active_only: bool = True,
) -> list[ScholarshipResponse]:
    found = await uc.finance_catalog.list_scholarships(
        principal, student_id=student_id, active_only=active_only
    )
    return [ScholarshipResponse.model_validate(s) for s in found]


@router.post(
    "/scholarships", response_model=ScholarshipResponse, status_code=status.HTTP_201_CREATED
)
async def grant_scholarship(
    body: ScholarshipRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ScholarshipResponse:
    view = await uc.finance_catalog.grant_scholarship(
        principal,
        ScholarshipCommand(
            student_id=body.student_id,
            session_id=body.session_id,
            name=body.name,
            kind=body.kind,
            value=body.value,
        ),
    )
    return ScholarshipResponse.model_validate(view)


@router.post("/scholarships/{scholarship_id}/revoke", response_model=ScholarshipResponse)
async def revoke_scholarship(
    scholarship_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> ScholarshipResponse:
    view = await uc.finance_catalog.revoke_scholarship(principal, scholarship_id)
    return ScholarshipResponse.model_validate(view)


# ---- billing and invoices ----------------------------------------------------------------


@router.post("/billing-runs", response_model=BillingRunResponse)
async def billing_run(
    body: BillingRunRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> BillingRunResponse:
    result = await uc.billing.billing_run(
        principal, term_id=body.term_id, class_ids=body.class_ids, due_on=body.due_on
    )
    return BillingRunResponse.model_validate(result)


@router.post("/invoices", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    body: InvoiceRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> InvoiceResponse:
    view = await uc.billing.create_invoice(
        principal, student_id=body.student_id, fee_item_ids=body.fee_item_ids, due_on=body.due_on
    )
    return InvoiceResponse.model_validate(view)


@router.get("/invoices", response_model=InvoicePage)
async def list_invoices(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    student_id: UUID | None = None,
    term_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 50,
) -> InvoicePage:
    page = await uc.billing.list_invoices(
        principal,
        student_id=student_id,
        term_id=term_id,
        status=status_filter,
        cursor=cursor,
        limit=limit,
    )
    return InvoicePage(
        items=[InvoiceResponse.model_validate(i) for i in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> InvoiceResponse:
    return InvoiceResponse.model_validate(await uc.billing.get_invoice(principal, invoice_id))


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceResponse)
async def void_invoice(
    invoice_id: UUID, body: ReasonRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> InvoiceResponse:
    view = await uc.billing.void_invoice(principal, invoice_id, reason=body.reason)
    return InvoiceResponse.model_validate(view)


# ---- payments ----------------------------------------------------------------------------


@router.post("/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def record_payment(
    body: PaymentRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", max_length=128)] = "",
) -> PaymentResponse:
    view = await uc.payments.record(
        principal,
        RecordPaymentCommand(
            student_id=body.student_id,
            amount_minor=body.amount_minor,
            currency=body.currency,
            method=body.method,
            reference=body.reference,
            received_on=body.received_on,
            idempotency_key=idempotency_key,
        ),
    )
    if view.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return PaymentResponse.model_validate(view)


@router.get("/payments", response_model=PaymentPage)
async def list_payments(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    student_id: UUID | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 50,
) -> PaymentPage:
    page = await uc.payments.list_payments(
        principal, student_id=student_id, cursor=cursor, limit=limit
    )
    return PaymentPage(
        items=[PaymentResponse.model_validate(p) for p in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/payments/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> PaymentResponse:
    return PaymentResponse.model_validate(await uc.payments.get(principal, payment_id))


@router.post("/payments/{payment_id}/reverse", response_model=PaymentResponse)
async def reverse_payment(
    payment_id: UUID, body: ReasonRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> PaymentResponse:
    view = await uc.payments.reverse(principal, payment_id, reason=body.reason)
    return PaymentResponse.model_validate(view)


# ---- refunds -----------------------------------------------------------------------------


@router.post(
    "/payments/{payment_id}/refunds",
    response_model=RefundResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_refund(
    payment_id: UUID, body: RefundRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RefundResponse:
    view = await uc.payments.request_refund(
        principal,
        payment_id,
        amount_minor=body.amount_minor,
        method=body.method,
        reason=body.reason,
    )
    return RefundResponse.model_validate(view)


@router.get("/refunds", response_model=list[RefundResponse])
async def list_refunds(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
) -> list[RefundResponse]:
    refunds = await uc.payments.list_refunds(principal, status=status_filter)
    return [RefundResponse.model_validate(r) for r in refunds]


@router.post("/refunds/{refund_id}/approve", response_model=RefundResponse)
async def approve_refund(
    refund_id: UUID, body: DecisionRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RefundResponse:
    view = await uc.payments.decide_refund(principal, refund_id, approve=True, note=body.note)
    return RefundResponse.model_validate(view)


@router.post("/refunds/{refund_id}/reject", response_model=RefundResponse)
async def reject_refund(
    refund_id: UUID, body: DecisionRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> RefundResponse:
    view = await uc.payments.decide_refund(principal, refund_id, approve=False, note=body.note)
    return RefundResponse.model_validate(view)


# ---- statements and books ----------------------------------------------------------------


@router.get("/students/{student_id}/statement", response_model=StatementResponse)
async def student_statement(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> StatementResponse:
    return StatementResponse.model_validate(await uc.statements.statement(principal, student_id))


@router.get("/ledger/trial-balance", response_model=TrialBalanceResponse)
async def trial_balance(principal: SchoolPrincipal, uc: UseCasesDep) -> TrialBalanceResponse:
    return TrialBalanceResponse.model_validate(await uc.statements.trial_balance(principal))


# ---- proof of payment (money paid to the school outside the platform) ---------------------


async def _read_capped(upload: UploadFile) -> bytes:
    """Read at most the size limit plus one byte, so a huge upload is refused cheaply."""
    data = await upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValidationFailed("Upload a file of up to 5 MB.", details={"field": "file"})
    return data


@router.post("/payment-proofs", response_model=ProofResponse, status_code=status.HTTP_201_CREATED)
async def submit_payment_proof(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    file: Annotated[UploadFile, File(description="PDF, PNG, JPEG or WebP, up to 5 MB")],
    student_id: Annotated[UUID, Form()],
    amount_minor: Annotated[int, Form(gt=0, le=10**12)],
    method: Annotated[str, Form(max_length=16)],
    paid_on: Annotated[date, Form()],
    reference: Annotated[str, Form(min_length=1, max_length=120)],
    payer_name: Annotated[str | None, Form(max_length=120)] = None,
    note: Annotated[str | None, Form(max_length=500)] = None,
    invoice_id: Annotated[UUID | None, Form()] = None,
    currency: Annotated[str | None, Form(min_length=3, max_length=3)] = None,
) -> ProofResponse:
    view = await uc.payment_proofs.submit(
        principal,
        SubmitProofCommand(
            student_id=student_id,
            amount_minor=amount_minor,
            currency=currency,
            method=method,
            paid_on=paid_on,
            reference=reference,
            payer_name=payer_name,
            note=note,
            invoice_id=invoice_id,
            file_bytes=await _read_capped(file),
        ),
    )
    return ProofResponse.model_validate(view)


@router.get("/payment-proofs", response_model=ProofPage)
async def list_payment_proofs(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    student_id: UUID | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 50,
) -> ProofPage:
    page = await uc.payment_proofs.list(
        principal, status=status_filter, student_id=student_id, cursor=cursor, limit=limit
    )
    return ProofPage(
        items=[ProofResponse.model_validate(p) for p in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/payment-proofs/{proof_id}", response_model=ProofResponse)
async def get_payment_proof(
    proof_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> ProofResponse:
    return ProofResponse.model_validate(await uc.payment_proofs.get(principal, proof_id))


@router.get("/payment-proofs/{proof_id}/file", response_class=Response)
async def download_payment_proof(
    proof_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> Response:
    """Always served as a download, never rendered inline, so a crafted file can't run."""
    view = await uc.payment_proofs.file(principal, proof_id)
    return Response(
        content=view.content,
        media_type=view.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{view.filename}"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/payment-proofs/{proof_id}/approve", response_model=ProofResponse)
async def approve_payment_proof(
    proof_id: UUID, body: ApproveProofRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ProofResponse:
    view = await uc.payment_proofs.approve(
        principal, proof_id, amount_minor=body.amount_minor, note=body.note
    )
    return ProofResponse.model_validate(view)


@router.post("/payment-proofs/{proof_id}/reject", response_model=ProofResponse)
async def reject_payment_proof(
    proof_id: UUID, body: ReasonRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ProofResponse:
    view = await uc.payment_proofs.reject(principal, proof_id, reason=body.reason)
    return ProofResponse.model_validate(view)


@router.post("/payment-proofs/{proof_id}/withdraw", response_model=ProofResponse)
async def withdraw_payment_proof(
    proof_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> ProofResponse:
    return ProofResponse.model_validate(await uc.payment_proofs.withdraw(principal, proof_id))
