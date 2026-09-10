import re

SENSITIVE_SCREENING_MARKERS = (
    "authorized",
    "authorised",
    "right to work",
    "eligible to work",
    "work permit",
    "visa",
    "sponsorship",
    "language level",
    "degree required",
)

POSITIVE_ANSWERS = {"yes", "true", "ja", "authorized", "authorised"}
NEGATIVE_ANSWERS = {"no", "false", "nein", "not authorized", "not authorised"}


def requires_screening_verification(question: str) -> bool:
    lowered = question.casefold()
    return any(marker in lowered for marker in SENSITIVE_SCREENING_MARKERS)


def is_potentially_disqualifying(question: str, answer: str | None) -> bool:
    truth_value = _answer_truth_value(answer)
    if truth_value is None:
        return False

    lowered = re.sub(r"\s+", " ", question.casefold()).strip()
    needs_immigration_support = any(term in lowered for term in ("need", "require")) and any(
        term in lowered for term in ("visa", "sponsorship", "work permit")
    )
    if needs_immigration_support:
        return truth_value

    if "sponsorship" in lowered:
        if any(term in lowered for term in ("have", "hold", "secured")):
            return not truth_value
        return False

    authorization_markers = (
        "authorized to work",
        "authorised to work",
        "right to work",
        "eligible to work",
        "work authorization",
        "work authorisation",
    )
    if any(marker in lowered for marker in authorization_markers):
        negated = any(
            marker in lowered
            for marker in (
                "not authorized",
                "not authorised",
                "not eligible to work",
                "no right to work",
                "without authorization",
                "without authorisation",
            )
        ) or bool(
            re.search(
                r"\bnot\b(?:\s+\w+){0,3}\s+(?:authori[sz]ed|eligible)\b",
                lowered,
            )
        )
        return truth_value if negated else not truth_value

    if "visa" in lowered and any(term in lowered for term in ("have", "hold", "valid")):
        return not truth_value
    if "degree" in lowered and any(term in lowered for term in ("have", "hold", "meet")):
        return not truth_value
    if "language level" in lowered and any(term in lowered for term in ("have", "meet", "satisfy")):
        return not truth_value
    return False


def _answer_truth_value(answer: str | None) -> bool | None:
    if not answer:
        return None
    normalized = re.sub(r"\s+", " ", answer.casefold()).strip(" .!?;:")
    if normalized in POSITIVE_ANSWERS or re.match(r"^(yes|true|ja)\b", normalized):
        return True
    if normalized in NEGATIVE_ANSWERS or re.match(r"^(no|false|nein)\b", normalized):
        return False
    return None
