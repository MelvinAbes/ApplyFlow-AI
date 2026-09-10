from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlmodel import Session, select

from app.ai.job_analyzer import JobAnalyzer
from app.api.dependencies import get_browser_manager
from app.ats import ATSRegistry
from app.automation.browser import BrowserManager
from app.config import Settings, get_settings
from app.database import get_session
from app.models import CandidateProfile, Job, Resume
from app.schemas.job import (
    CsvImportResult,
    JobAnalysisRead,
    JobCreate,
    JobListItem,
    JobRead,
    JobUrlInput,
)
from app.services.job_service import DuplicateJobError, JobService

router = APIRouter(tags=["jobs"])


@router.get("/jobs", response_model=list[JobListItem])
def list_jobs(
    search: str | None = None,
    location: str | None = None,
    min_score: int | None = Query(default=None, ge=0, le=100),
    session: Session = Depends(get_session),
):
    jobs = session.exec(select(Job).order_by(Job.created_at.desc())).all()
    result: list[JobListItem] = []
    service = JobService(session)
    for job in jobs:
        if search and search.casefold() not in f"{job.company} {job.title}".casefold():
            continue
        if location and location.casefold() not in (job.location or "").casefold():
            continue
        analysis = service.latest_analysis(job.id)
        score = analysis.result.get("overall_score") if analysis else None
        if min_score is not None and (score is None or score < min_score):
            continue
        result.append(
            JobListItem(
                **JobRead.model_validate(job).model_dump(),
                match_score=score,
                recommendation=analysis.result.get("recommendation") if analysis else None,
            )
        )
    return result


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, session: Session = Depends(get_session)):
    try:
        return JobService(session).create(payload)
    except DuplicateJobError as error:
        raise HTTPException(
            status_code=409,
            detail={"message": "Probable duplicate job", "existing_job_id": error.existing_id},
        ) from error


@router.post("/jobs/from-url", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job_from_url(
    payload: JobUrlInput,
    session: Session = Depends(get_session),
    browser: BrowserManager = Depends(get_browser_manager),
):
    key = "ingest-job"
    try:
        page = await browser.open(key, payload.url)
        if await browser.has_captcha(page):
            raise HTTPException(
                status_code=409, detail="Automated access blocked; manual action required"
            )
        extracted = await ATSRegistry().for_url(page.url).extract_job(page)
        return JobService(session).create(JobCreate(**asdict(extracted), source="manual_url"))
    except DuplicateJobError as error:
        raise HTTPException(
            status_code=409, detail={"existing_job_id": error.existing_id}
        ) from error
    finally:
        await browser.forget(key)


@router.post("/jobs/import-csv", response_model=CsvImportResult)
async def import_jobs_csv(file: UploadFile = File(...), session: Session = Depends(get_session)):
    content = (await file.read(2_000_000)).decode("utf-8-sig")
    return JobService(session).import_csv(content)


@router.get("/jobs/{job_id}", response_model=JobListItem)
def get_job(job_id: str, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    analysis = JobService(session).latest_analysis(job.id)
    return JobListItem(
        **JobRead.model_validate(job).model_dump(),
        match_score=analysis.result.get("overall_score") if analysis else None,
        recommendation=analysis.result.get("recommendation") if analysis else None,
    )


@router.post("/jobs/{job_id}/analyze", response_model=JobAnalysisRead)
async def analyze_job(
    job_id: str,
    resume_id: str | None = None,
    force: bool = False,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    job = session.get(Job, job_id)
    profile = session.exec(select(CandidateProfile).limit(1)).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not profile:
        raise HTTPException(status_code=409, detail="Create a candidate profile first")
    analyzer = JobAnalyzer(session, settings)
    if resume_id:
        resume = session.get(Resume, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found")
    else:
        resume = analyzer.select_resume(job, list(session.exec(select(Resume)).all()))
    record, result = await analyzer.analyze(job, profile, resume, force)
    return JobAnalysisRead(
        **result.model_dump(),
        analysis_id=record.id,
        ai_used=record.ai_used,
        created_at=record.created_at,
    )
