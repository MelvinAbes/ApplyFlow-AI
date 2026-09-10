from pathlib import Path

import pytest

from app.ai.question_answerer import AIAnswerAttempt
from app.automation.browser import BrowserManager
from app.automation.submission import SubmissionBlockedError
from app.models import Job, Resume
from app.models.enums import AnswerSource, ApplicationStatus
from app.schemas.application import AIAnswer
from app.schemas.profile import CandidateProfileInput, EducationInput
from app.services.application_service import ApplicationService, ApplicationStateError
from app.services.job_service import description_hash
from app.services.profile_service import ProfileService


@pytest.mark.asyncio
async def test_complete_demo_flow_requires_approval(
    session, test_settings, tmp_path, monkeypatch
) -> None:
    ProfileService(session).upsert(
        CandidateProfileInput(
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.test",
            phone="+49 30 123456",
            city="Berlin",
            country="Germany",
            linkedin="https://linkedin.example/ada",
            work_authorization="Yes",
            preferred_start_date="2026-10-01",
            skills=["Python", "FastAPI", "LLM"],
            educations=[
                EducationInput(
                    institution="Berlin Technical University",
                    degree="MSc Computer Science",
                    current_student=True,
                    graduation_date="2027-09-30",
                )
            ],
        )
    )
    fake_pdf = tmp_path / "resume.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n% local automation fixture")
    resume = Resume(
        filename="AI_resume.pdf",
        path=str(fake_pdf),
        label="AI / ML resume",
        extracted_text="Python FastAPI LLM projects",
        target_roles=["AI"],
        skills=["Python"],
        is_default=True,
        content_sha256="fixture",
    )
    fixture_url = (Path(__file__).parent / "fixtures" / "application_form.html").as_uri()
    job = Job(
        company="Acme Labs",
        title="Working Student AI",
        description="Python required. Work on LLM services.",
        application_url=fixture_url,
        source_url=fixture_url,
        description_sha256=description_hash("Python required. Work on LLM services."),
    )
    session.add(resume)
    session.add(job)
    session.commit()

    async def grounded_answer(self, question, job, resume, character_limit=None):
        return AIAnswerAttempt(
            answer=AIAnswer(
                status="ANSWERED",
                answer="I am interested in applying my Python, FastAPI, and LLM project experience.",
                grounded_facts=["Python"],
                confidence=0.91,
            )
        )

    monkeypatch.setattr("app.ai.question_answerer.QuestionAnswerer.attempt", grounded_answer)
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        application = await service.start(job, resume)
        assert application.status == ApplicationStatus.READY_FOR_REVIEW
        read = service.read(application)
        assert len(read.fields) == 10
        assert not [field for field in read.fields if not field.answer]
        motivation = next(field for field in read.fields if "interested" in field.question)
        assert motivation.source == AnswerSource.LLM_GENERATED
        page = browser.page(application.id)
        assert page is not None
        assert await page.locator("#first").input_value() == "Ada"
        assert await page.locator("#university").input_value() == "Berlin Technical University"
        assert await page.locator("#authorization").input_value() == "Yes"
        assert await page.locator("#resume").evaluate("element => element.files[0].name") == (
            "AI_resume.pdf"
        )
        assert await page.locator("#success").is_hidden()
        authorization = next(
            field for field in read.fields if field.field_key == "work_authorization"
        )
        assert authorization.requires_verification is True
        assert authorization.disqualifying is False

        with pytest.raises(SubmissionBlockedError):
            await service.submit(application, "not-approved")
        assert await page.locator("#success").is_hidden()

        wrong_resume = tmp_path / "wrong-resume.pdf"
        wrong_resume.write_bytes(b"%PDF-1.4\n% wrong local fixture")
        await page.locator("#resume").set_input_files(wrong_resume)
        with pytest.raises(ApplicationStateError, match="has not confirmed the selected résumé"):
            await service.approve(application)
        await page.locator("#resume").set_input_files(
            {
                "name": resume.filename,
                "mimeType": "application/pdf",
                "buffer": fake_pdf.read_bytes(),
            }
        )
        await page.locator("#resume").evaluate(
            """(input, filename) => {
              const confirmation = document.createElement('span')
              confirmation.id = 'employer-upload-confirmation'
              confirmation.textContent = filename
              input.insertAdjacentElement('afterend', confirmation)
              input.form.noValidate = true
              input.value = ''
            }""",
            resume.filename,
        )
        assert await page.locator("#resume").evaluate("element => element.files.length") == 0
        assert await page.locator("#employer-upload-confirmation").inner_text() == resume.filename

        token, _ = await service.approve(application)
        await page.locator("#first").fill("Mallory")
        with pytest.raises(SubmissionBlockedError, match="differs"):
            await service.submit(application, token)
        session.refresh(application)
        assert application.status == ApplicationStatus.READY_FOR_REVIEW
        assert await page.locator("#success").is_hidden()

        await page.locator("#first").fill("Ada")
        token, _ = await service.approve(application)
        submitted = await service.submit(application, token)
        assert submitted.status == ApplicationStatus.SUBMITTED
        assert await page.locator("#success").is_visible()

        with pytest.raises(SubmissionBlockedError):
            await service.submit(submitted, token)
    finally:
        await browser.close()
