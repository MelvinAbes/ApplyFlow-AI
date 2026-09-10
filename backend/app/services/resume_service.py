from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from pypdf import PdfReader
from sqlmodel import Session, select

from app.config import Settings
from app.models import Resume

MAX_RESUME_BYTES = 10 * 1024 * 1024


class ResumeValidationError(ValueError):
    pass


class ResumeService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def list(self) -> list[Resume]:
        return list(self.session.exec(select(Resume).order_by(Resume.created_at.desc())))

    async def upload(
        self,
        file: UploadFile,
        *,
        label: str,
        target_roles: list[str],
        skills: list[str],
        is_default: bool,
    ) -> Resume:
        original_name = Path(file.filename or "resume.pdf").name
        if Path(original_name).suffix.casefold() != ".pdf":
            raise ResumeValidationError("Only PDF resumes are accepted")
        content = await file.read(MAX_RESUME_BYTES + 1)
        if not content or len(content) > MAX_RESUME_BYTES:
            raise ResumeValidationError("Resume must be a non-empty PDF no larger than 10 MB")
        if not content.startswith(b"%PDF"):
            raise ResumeValidationError("Uploaded file is not a valid PDF")

        self.settings.resume_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid4()}.pdf"
        destination = self.settings.resume_dir / stored_name
        destination.write_bytes(content)
        try:
            reader = PdfReader(destination)
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as error:
            destination.unlink(missing_ok=True)
            raise ResumeValidationError("PDF text extraction failed") from error

        if is_default or not self.list():
            self._clear_default()
            is_default = True
        resume = Resume(
            filename=original_name,
            path=str(destination),
            label=label.strip() or original_name,
            extracted_text=text,
            target_roles=target_roles,
            skills=skills,
            is_default=is_default,
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
        self.session.add(resume)
        self.session.commit()
        self.session.refresh(resume)
        return resume

    def set_default(self, resume: Resume) -> Resume:
        self._clear_default()
        resume.is_default = True
        self.session.add(resume)
        self.session.commit()
        self.session.refresh(resume)
        return resume

    def delete(self, resume: Resume) -> None:
        path = Path(resume.path)
        was_default = resume.is_default
        self.session.delete(resume)
        self.session.commit()
        path.unlink(missing_ok=True)
        if was_default:
            replacement = self.session.exec(select(Resume).limit(1)).first()
            if replacement:
                self.set_default(replacement)

    def _clear_default(self) -> None:
        for existing in self.session.exec(select(Resume).where(Resume.is_default)).all():
            existing.is_default = False
            self.session.add(existing)
