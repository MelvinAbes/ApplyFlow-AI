import pytest

from app.models import Application, ApplicationField
from app.models.enums import ApplicationStatus
from app.services.application_service import ApplicationService
from app.services.screening import (
    is_potentially_disqualifying,
    requires_screening_verification,
)


@pytest.mark.parametrize(
    ("question", "answer", "expected"),
    [
        ("Are you legally authorized to work in Germany?", "Yes", False),
        ("Are you legally authorized to work in Germany?", "No", True),
        ("Are you NOT authorized to work in Germany?", "Yes", True),
        ("Are you NOT authorized to work in Germany?", "No", False),
        ("Are you not legally authorized to work in Germany?", "Yes", True),
        ("Are you not legally authorized to work in Germany?", "No", False),
        ("Will you require visa sponsorship?", "Yes", True),
        ("Will you require visa sponsorship?", "No", False),
        ("Do you hold a valid visa?", "Yes", False),
        ("Do you hold a valid visa?", "No", True),
        ("What is your visa status?", "EU Blue Card", False),
        ("What is your German language level?", "B2", False),
    ],
)
def test_disqualification_risk_uses_question_polarity_and_answer(
    question: str, answer: str, expected: bool
) -> None:
    assert is_potentially_disqualifying(question, answer) is expected


def test_sensitive_screening_questions_still_require_verification() -> None:
    assert requires_screening_verification("Are you legally authorized to work in Germany?")
    assert requires_screening_verification("What is your German language level?")
    assert not requires_screening_verification("What is your first name?")


@pytest.mark.asyncio
async def test_editing_screening_answer_recalculates_risk(session, test_settings) -> None:
    application = Application(status=ApplicationStatus.NEEDS_USER_INPUT, job_id="job-id")
    session.add(application)
    session.commit()
    field = ApplicationField(
        application_id=application.id,
        selector="#authorization",
        question="Are you legally authorized to work in Germany?",
        field_type="select",
        answer="No",
        requires_verification=True,
        disqualifying=True,
    )
    session.add(field)
    session.commit()

    class NoPageBrowser:
        def page(self, application_id: str):
            return None

    await ApplicationService(
        session,
        test_settings,
        NoPageBrowser(),  # type: ignore[arg-type]
    ).update_field(field, "Yes")

    session.refresh(field)
    assert field.requires_verification is True
    assert field.disqualifying is False
