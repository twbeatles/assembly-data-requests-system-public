# -*- coding: utf-8 -*-
"""후속 단계 Job 상태 (메모리 + TTL). 파이프라인 실행 자체는 하지 않는다.

`pipeline plan`과 장래 `pipeline run`의 유효 작업 구분·단계별 상태·재개 가능성을
표시하기 위한 최소 관찰대다. 기존 `PipelineGuard` OS 락·소유권 규칙을 재사용하며,
MCP가 독자적으로 파이프라인을 기동하지는 않는다.
"""

import threading
import time
import uuid

_JOBS = {}
_LOCK = threading.Lock()
TTL_SECONDS = 24 * 3600


def create_job(operation, params=None):
    job_id = "job-%s" % uuid.uuid4().hex[:12]
    now = time.time()
    with _LOCK:
        _prune_locked(now)
        _JOBS[job_id] = {"job_id": job_id, "operation": operation,
                         "params": dict(params or {}), "status": "planned",
                         "created_at": now, "updated_at": now, "steps": []}
        return dict(_JOBS[job_id])


def update_job(job_id, status, step=None, error=None):
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        job["status"] = status
        job["updated_at"] = time.time()
        if step:
            job["steps"].append(step)
        if error:
            job["error"] = error
        return dict(job)


def get_job(job_id):
    with _LOCK:
        _prune_locked(time.time())
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _prune_locked(now):
    stale = [key for key, job in _JOBS.items()
             if now - job.get("created_at", now) > TTL_SECONDS]
    for key in stale:
        del _JOBS[key]
