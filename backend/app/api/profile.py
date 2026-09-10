from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from app.database import get_session
from app.models import AnswerBankEntry
from app.schemas.profile import (
    AnswerBankInput,
    AnswerBankRead,
    CandidateProfileInput,
    CandidateProfileRead,
)
from app.services.answer_bank import AnswerBankService
from app.services.profile_service import ProfileService

router = APIRouter(tags=["profile"])


@router.get("/profile", response_model=CandidateProfileRead | None)
def get_profile(session: Session = Depends(get_session)):
    return ProfileService(session).get()


@router.put("/profile", response_model=CandidateProfileRead)
def put_profile(payload: CandidateProfileInput, session: Session = Depends(get_session)):
    return ProfileService(session).upsert(payload)


@router.get("/answer-bank", response_model=list[AnswerBankRead])
def list_answers(session: Session = Depends(get_session)):
    return AnswerBankService(session).list()


@router.post("/answer-bank", response_model=AnswerBankRead, status_code=status.HTTP_201_CREATED)
def create_answer(payload: AnswerBankInput, session: Session = Depends(get_session)):
    return AnswerBankService(session).create(payload)


@router.put("/answer-bank/{entry_id}", response_model=AnswerBankRead)
def update_answer(entry_id: str, payload: AnswerBankInput, session: Session = Depends(get_session)):
    entry = session.get(AnswerBankEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Answer not found")
    return AnswerBankService(session).update(entry, payload)


@router.delete("/answer-bank/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_answer(entry_id: str, session: Session = Depends(get_session)):
    entry = session.get(AnswerBankEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Answer not found")
    session.delete(entry)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
