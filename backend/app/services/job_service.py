import csv
import hashlib
import io
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlmodel import Session, select

from app.models import Job, JobAnalysisRecord
from app.schemas.job import CsvImportResult, JobCreate


class DuplicateJobError(ValueError):
    def __init__(self, existing_id: str):
        self.existing_id = existing_id
        super().__init__(f"Probable duplicate of job {existing_id}")


def description_hash(description: str) -> str:
    normalized = re.sub(r"\s+", " ", description.strip())
    return hashlib.sha256(normalized.encode()).hexdigest()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parts.query)
        if not key.casefold().startswith(("utm_", "ref", "tracking"))
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(filtered_query),
            "",
        )
    )


class JobService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, payload: JobCreate, reject_exact: bool = True) -> Job:
        duplicate = self.find_duplicate(payload)
        exact_url = normalize_url(payload.application_url or payload.source_url)
        if duplicate and reject_exact and exact_url:
            duplicate_url = normalize_url(duplicate.application_url or duplicate.source_url)
            if duplicate_url == exact_url:
                raise DuplicateJobError(duplicate.id)
        job = Job(
            **payload.model_dump(exclude={"source_url", "application_url"}),
            source_url=normalize_url(payload.source_url),
            application_url=normalize_url(payload.application_url),
            description_sha256=description_hash(payload.description),
            probable_duplicate_of=duplicate.id if duplicate else None,
        )
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def find_duplicate(self, payload: JobCreate) -> Job | None:
        all_jobs = self.session.exec(select(Job)).all()
        incoming_url = normalize_url(payload.application_url or payload.source_url)
        for job in all_jobs:
            existing_url = normalize_url(job.application_url or job.source_url)
            if incoming_url and existing_url == incoming_url:
                return job
        candidates = [
            job
            for job in all_jobs
            if job.company.strip().casefold() == payload.company.strip().casefold()
            and job.title.strip().casefold() == payload.title.strip().casefold()
        ]
        return candidates[0] if candidates else None

    def import_csv(self, content: str) -> CsvImportResult:
        reader = csv.DictReader(io.StringIO(content))
        imported = duplicates = 0
        errors: list[str] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                payload = JobCreate(
                    company=(row.get("company") or "").strip(),
                    title=(row.get("title") or "").strip(),
                    location=(row.get("location") or "").strip() or None,
                    source_url=(row.get("url") or "").strip() or None,
                    application_url=(row.get("url") or "").strip() or None,
                    description=(row.get("description") or "").strip(),
                    source="csv",
                )
                self.create(payload)
                imported += 1
            except DuplicateJobError:
                duplicates += 1
            except Exception as error:
                errors.append(f"line {line_number}: {type(error).__name__}")
        return CsvImportResult(imported=imported, duplicates=duplicates, errors=errors)

    def latest_analysis(self, job_id: str) -> JobAnalysisRecord | None:
        return self.session.exec(
            select(JobAnalysisRecord)
            .where(JobAnalysisRecord.job_id == job_id)
            .order_by(JobAnalysisRecord.created_at.desc())
        ).first()
