from app.services.job_store import JobStore


def test_job_store_scopes_reads_by_tenant() -> None:
    store = JobStore()
    job_id = store.create("tenant-a")

    assert store.get(job_id, "tenant-a") is not None
    assert store.get(job_id, "tenant-b") is None
