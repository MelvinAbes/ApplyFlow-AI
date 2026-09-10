import re
import unicodedata

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "first_name": ("first name", "firstname", "given name", "forename", "vorname"),
    "last_name": ("last name", "lastname", "surname", "family name", "nachname"),
    "preferred_name": ("preferred name", "chosen name"),
    "full_name": ("full name", "your name", "legal name", "name"),
    "email": ("email", "email address", "e mail"),
    "phone": ("phone", "telephone", "mobile", "phone number", "telefon"),
    "phone_country_code": ("country calling code", "country code", "landercode"),
    "city": ("city", "town", "ort", "stadt"),
    "country": ("country", "land"),
    "postal_code": ("postal code", "postcode", "zip", "zip code", "plz", "postleitzahl"),
    "street_name": (
        "street name",
        "street",
        "road name",
        "strasse",
    ),
    "house_number": (
        "house number",
        "building number",
        "street number",
        "hausnummer",
    ),
    "address": (
        "address",
        "street address",
        "address line 1",
        "address line",
        "anschrift",
        "strasse und hausnummer",
    ),
    "nationality": ("nationality", "nationalitat", "staatsangehorigkeit"),
    "date_of_birth": ("date of birth", "birth date", "geburtsdatum"),
    "gender": ("gender", "geschlecht"),
    "honorific_title": ("honorific title", "salutation title"),
    "name_suffix": ("name suffix", "namenszusatz"),
    "linkedin": ("linkedin", "linkedin profile", "linkedin url"),
    "github": ("github", "github profile", "github url"),
    "portfolio": ("portfolio", "portfolio url"),
    "personal_website": ("personal website", "website", "homepage"),
    "university": ("university", "school", "institution", "hochschule", "universitat"),
    "degree": ("degree", "qualification", "abschluss"),
    "field_of_study": ("field of study", "major", "study program"),
    "graduation_date": ("graduation date", "expected graduation", "when do you graduate"),
    "current_student": ("currently enrolled", "current student", "enrolled as a student"),
    "german_level": ("german level", "german proficiency", "deutschkenntnisse"),
    "english_level": ("english level", "english proficiency", "englischkenntnisse"),
    "work_authorization": (
        "work authorization",
        "work authorisation",
        "authorized to work",
        "permission to work",
        "legally work",
        "work permit",
    ),
    "availability": ("availability", "when can you start"),
    "preferred_start_date": ("available start date", "preferred start date", "start date"),
    "weekly_hours": ("hours per week", "weekly hours", "working hours"),
    "salary_expectation": ("salary expectation", "expected salary", "salary requirements"),
    "relocation_willingness": ("willing to relocate", "relocation"),
    "travel_willingness": ("willingness to travel", "travel willingness", "reisebereitschaft"),
    "preferred_location_primary": ("preferred location first choice", "location priority 1"),
    "preferred_location_secondary": ("preferred location second choice", "location priority 2"),
    "consider_other_positions": (
        "consider other open positions",
        "consider me for other positions",
    ),
    "employer_save_answers": ("save my answers for future",),
    "resume": ("resume", "cv", "curriculum vitae", "lebenslauf"),
}

AUTOCOMPLETE_KEYS = {
    "name": "full_name",
    "given-name": "first_name",
    "family-name": "last_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
    "tel-country-code": "phone_country_code",
    "street-address": "address",
    "address-line1": "address",
    "address-level2": "city",
    "postal-code": "postal_code",
    "country": "country",
    "country-name": "country",
}


def normalize_label(value: str) -> str:
    value = value.replace("ß", "ss").replace("ẞ", "SS")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def map_field(
    question: str,
    *,
    html_name: str | None = None,
    html_id: str | None = None,
    autocomplete: str | None = None,
    placeholder: str | None = None,
    section: str | None = None,
) -> tuple[str, float] | None:
    normalized = normalize_label(question)
    if not normalized:
        return None
    matches = _alias_matches(normalized, confidence_scale=1.0)
    autocomplete_key = AUTOCOMPLETE_KEYS.get((autocomplete or "").strip().casefold())
    if autocomplete_key:
        matches.append((autocomplete_key, 0.99))
    for metadata in (html_name, html_id, placeholder):
        normalized_metadata = normalize_label(metadata or "")
        if normalized_metadata:
            matches.extend(_alias_matches(normalized_metadata, confidence_scale=0.94))

    # Section text is supporting evidence only. It can make a qualified label safer, but it
    # must never map a generic control by itself.
    normalized_section = normalize_label(section or "")
    if matches and normalized_section:
        matches = [
            (key, min(1.0, confidence + _section_bonus(key, normalized_section)))
            for key, confidence in matches
        ]
    if not matches:
        return None
    return max(matches, key=lambda item: (item[1], len(item[0])))


def _alias_matches(normalized: str, *, confidence_scale: float) -> list[tuple[str, float]]:
    matches: list[tuple[str, float]] = []
    for key, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_label(alias)
            if normalized == normalized_alias:
                matches.append((key, confidence_scale))
            elif _substring_mapping_is_safe(key, normalized_alias, normalized):
                coverage = len(normalized_alias) / len(normalized)
                matches.append(
                    (key, min(0.98, 0.82 + coverage * 0.16) * confidence_scale)
                )
    return matches


def _substring_mapping_is_safe(key: str, alias: str, question: str) -> bool:
    if not re.search(rf"(?:^| ){re.escape(alias)}(?: |$)", question):
        return False
    # Single generic nouns are useful exact labels but unsafe substring classifiers. This is
    # what previously turned "Street Name" into a person's full name.
    if alias in {"name", "street", "address", "country", "land", "city", "town"}:
        return False
    if key == "full_name" and any(
        qualifier in question
        for qualifier in (
            "preferred",
            "middle",
            "street",
            "road",
            "house",
            "address",
            "company",
            "employer",
            "manager",
            "reference",
            "school",
            "university",
        )
    ):
        return False
    if key == "work_authorization" and _unsafe_work_authorization_context(question):
        return False
    return True


def _section_bonus(key: str, section: str) -> float:
    address_section = any(
        marker in section
        for marker in ("address", "location", "contact information", "anschrift", "adresse")
    )
    if address_section and key in {"street_name", "house_number", "address", "postal_code", "city", "country"}:
        return 0.02
    if address_section and key in {"full_name", "phone"}:
        return -0.08
    return 0.0


def _unsafe_work_authorization_context(question: str) -> bool:
    negative_phrases = (
        "not authorized",
        "not authorised",
        "do not have permission",
        "without authorization",
        "without authorisation",
    )
    if any(phrase in question for phrase in negative_phrases) or re.search(
        r"\bnot\b.{0,30}\b(authori[sz]ed|permission)\b", question
    ):
        return True
    non_german_jurisdictions = (
        "united states",
        " usa ",
        " us ",
        " u s ",
        "canada",
        "united kingdom",
        " uk ",
        "switzerland",
        "austria",
        "france",
        "netherlands",
        "ireland",
        "australia",
        " european union ",
        " eu ",
    )
    padded = f" {question} "
    return any(jurisdiction in padded for jurisdiction in non_german_jurisdictions)
