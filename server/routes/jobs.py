import logging
from fastapi import APIRouter, HTTPException

import settings
from database.db import get_engine
from database.repository import get_all_jobs, get_job

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])
logger = logging.getLogger(__name__)


@router.get("")
async def list_jobs():
    engine = get_engine()
    jobs = get_all_jobs(engine)
    return [
        {
            "id": j.id,
            "type": j.type,
            "status": j.status,
            "input_filename": j.input_filename,
            "input_row_count": j.input_row_count,
            "has_input_file": bool(j.input_s3_key),
            "has_output_file": bool(j.output_s3_key),
            "output_filename": j.output_filename,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        }
        for j in jobs
    ]


@router.get("/{job_id}/input-url")
async def get_input_url(job_id: int):
    if not settings.AWS_S3_BUCKET_NAME or not settings.AWS_ACCESS_KEY_ID:
        raise HTTPException(400, "S3 is not configured on this server")
    engine = get_engine()
    job = get_job(engine, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.input_s3_key:
        raise HTTPException(404, "Input file was not saved to S3 for this job")
    from services.aws_service import get_presigned_url
    url = get_presigned_url(job.input_s3_key)
    return {"url": url}


@router.get("/{job_id}/output-url")
async def get_output_url(job_id: int):
    if not settings.AWS_S3_BUCKET_NAME or not settings.AWS_ACCESS_KEY_ID:
        raise HTTPException(400, "S3 is not configured on this server")
    engine = get_engine()
    job = get_job(engine, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.output_s3_key:
        raise HTTPException(404, "Output file not available yet for this job")
    from services.aws_service import get_presigned_url
    url = get_presigned_url(job.output_s3_key)
    return {"url": url}
