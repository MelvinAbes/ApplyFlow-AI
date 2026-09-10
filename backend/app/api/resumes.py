from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlmodel import Session

from app.config import Settings, get_settings
from app.database import get_session
from app.models import Resume
from app.schemas.profile import ResumeRead, ResumeUpdate
from app.services.resume_service import ResumeService, ResumeValidationError

router = APIRouter(tags=["resumes"])


@router.get("/resumes", response_model=list[ResumeRead])
def list_resumes(
    session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
):
    return ResumeService(session, settings).list()


@router.post("/resumes", response_model=ResumeRead, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile = File(...),
    label: str = Form(...),
    target_roles: str = Form(""),
    skills: str = Form(""),
    is_default: bool = Form(False),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    try:
        return await ResumeService(session, settings).upload(
            file,
            label=label,
            target_roles=[item.strip() for item in target_roles.split(",") if item.strip()],
            skills=[item.strip() for item in skills.split(",") if item.strip()],
            is_default=is_default,
        )
    except ResumeValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.patch("/resumes/{resume_id}", response_model=ResumeRead)
def update_resume(
    resume_id: str,
    payload: ResumeUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    resume = session.get(Resume, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    service = ResumeService(session, settings)
    values = payload.model_dump(exclude_unset=True)
    if values.pop("is_default", False):
        service.set_default(resume)
    for key, value in values.items():
        setattr(resume, key, value)
    session.add(resume)
    session.commit()
    session.refresh(resume)
    return resume


@router.delete("/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(
    resume_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    resume = session.get(Resume, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    ResumeService(session, settings).delete(resume)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
