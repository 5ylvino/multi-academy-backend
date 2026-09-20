import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security.context import RequestContext
from app.security.dependencies import get_request_context
from app.services.job_store import JobStore
from app.services.school_client import SchoolClient

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])
_jobs = JobStore()
_school = SchoolClient()


class NotificationJob(BaseModel):
    tenantId: str
    userId: str
    title: str
    message: str
    type: str | None = None
    href: str | None = None


class ReportDownloadJob(BaseModel):
    tenantId: str
    actorId: str
    reportId: str
    format: str = Field(default="pdf")


async def _run_notification(job_id: str, tenant_id: str, actor_id: str, payload: dict[str, Any]) -> None:
    try:
        result = await _school.create_notification(tenant_id, actor_id, payload)
        _jobs.complete(job_id, result)
    except Exception as exc:  # noqa: BLE001
        _jobs.fail(job_id, str(exc))


async def _run_report(job_id: str, tenant_id: str, actor_id: str, report_id: str, fmt: str) -> None:
    try:
        result = await _school.download_report(tenant_id, actor_id, report_id, fmt)
        _jobs.complete(job_id, result)
    except Exception as exc:  # noqa: BLE001
        _jobs.fail(job_id, str(exc))


@router.post("/notifications", status_code=202)
async def enqueue_notification(
    body: NotificationJob,
    background: BackgroundTasks,
    ctx: RequestContext = Depends(get_request_context),
):
    if body.tenantId != ctx.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant mismatch")
    job_id = _jobs.create(ctx.tenant_id)
    payload = {
        "userId": body.userId,
        "title": body.title,
        "message": body.message,
        "type": body.type,
        "href": body.href,
    }
    background.add_task(_run_notification, job_id, ctx.tenant_id, ctx.actor_id, payload)
    return {"jobId": job_id, "status": "queued"}


@router.post("/reports/download", status_code=202)
async def enqueue_report(
    body: ReportDownloadJob,
    background: BackgroundTasks,
    ctx: RequestContext = Depends(get_request_context),
):
    if body.tenantId != ctx.tenant_id:
        raise HTTPException(status_code=400, detail="Tenant mismatch")
    job_id = _jobs.create(ctx.tenant_id)
    background.add_task(
        _run_report,
        job_id,
        ctx.tenant_id,
        body.actorId or ctx.actor_id,
        body.reportId,
        body.format,
    )
    return {"jobId": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job(job_id: str, ctx: RequestContext = Depends(get_request_context)):
    job = _jobs.get(job_id, ctx.tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "jobId": job.job_id,
        "status": job.status,
        "result": job.result,
        "error": job.error,
    }
