from app.schemas.profile import AnswerBankInput
from app.services.answer_bank import AnswerBankService, infer_canonical_key, normalize_question


def test_equivalent_work_authorization_questions_share_key(session) -> None:
    service = AnswerBankService(session)
    entry = service.create(
        AnswerBankInput(
            canonical_key="work_authorization",
            question="Are you authorized to work in Germany?",
            answer="Yes",
            aliases=["Do you have a German work permit?"],
        )
    )
    match = service.match("Do you currently have permission to work in Germany?")
    assert match is not None
    assert match[0].id == entry.id
    assert match[1] >= 0.9


def test_normalization_is_case_and_punctuation_insensitive() -> None:
    assert normalize_question("When can YOU start?") == normalize_question("when can you start")
    assert infer_canonical_key("How many hours per week are possible?") == "weekly_hours"


def test_german_authorization_answer_is_not_reused_for_another_country(session) -> None:
    service = AnswerBankService(session)
    service.create(
        AnswerBankInput(
            canonical_key="work_authorization",
            question="Are you authorized to work in Germany?",
            answer="Yes",
        )
    )
    assert service.match("Are you authorized to work in the United States?") is None
    assert service.match("Are you NOT authorized to work in Germany?") is None


def test_manual_answer_can_be_remembered_with_original_language_alias(session) -> None:
    service = AnswerBankService(session)
    entry = service.remember(
        question="Expected annual salary",
        answer="€72,000 gross per year",
        canonical_key="salary_expectation",
        aliases=["Erwartetes Jahresgehalt"],
    )

    match = service.match("Erwartetes Jahresgehalt")

    assert match is not None
    assert match[0].id == entry.id
    assert match[0].answer == "€72,000 gross per year"


def test_house_number_never_reuses_a_phone_answer(session) -> None:
    service = AnswerBankService(session)
    service.create(
        AnswerBankInput(
            canonical_key="phone",
            question="Phone number",
            answer="301234567",
            aliases=["Telephone"],
        )
    )

    assert service.match("House Number", canonical_key="house_number") is None
    assert service.match("House Number") is None


def test_answer_bank_reuse_stays_within_the_confirmed_semantic_category(session) -> None:
    service = AnswerBankService(session)
    house = service.create(
        AnswerBankInput(
            canonical_key="house_number",
            question="House Number",
            answer="42",
            aliases=["Hausnummer"],
        )
    )
    service.create(
        AnswerBankInput(
            canonical_key="phone",
            question="Phone number",
            answer="301234567",
        )
    )

    match = service.match("Building number", canonical_key="house_number")

    assert match is not None
    assert match[0].id == house.id
    assert match[0].answer == "42"
