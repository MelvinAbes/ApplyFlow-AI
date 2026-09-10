import pytest

from app.automation.field_mapper import map_field


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("First Name", "first_name"),
        ("Given name", "first_name"),
        ("Mobile", "phone"),
        ("LinkedIn Profile", "linkedin"),
        ("Vorname", "first_name"),
        ("Straße und Hausnummer", "address"),
        ("Street Name", "street_name"),
        ("Straße", "street_name"),
        ("House Number", "house_number"),
        ("Hausnummer", "house_number"),
        ("Resume / CV", "resume"),
    ],
)
def test_common_candidate_fields_are_mapped(label: str, expected: str) -> None:
    result = map_field(label)
    assert result is not None
    assert result[0] == expected
    assert result[1] >= 0.82


def test_unknown_field_is_not_guessed() -> None:
    assert map_field("What makes a remarkable team?") is None


@pytest.mark.parametrize(
    "label",
    [
        "Middle name",
        "Company name",
        "Street naming convention",
        "Are you NOT authorized to work in Germany?",
        "Are you not legally authorized to work in Germany?",
        "Are you authorized to work in the United States?",
        "Are you authorized to work in the US?",
    ],
)
def test_contextually_different_fields_are_not_mapped_to_candidate_facts(label: str) -> None:
    assert map_field(label) is None


def test_preferred_name_maps_to_its_own_fact() -> None:
    assert map_field("Preferred name") == ("preferred_name", 1.0)


def test_address_metadata_is_semantic_evidence_without_using_field_values() -> None:
    assert map_field("Address", autocomplete="address-line1") == ("address", 1.0)
    mapped = map_field(
        "Street",
        html_name="candidateStreetName",
        section="Profile information — address",
    )
    assert mapped is not None
    assert mapped[0] == "street_name"
    assert map_field("Street Name", autocomplete="address-line1") == ("street_name", 1.0)


def test_street_name_can_never_fall_back_to_person_name() -> None:
    result = map_field("Street Name")
    assert result is not None
    assert result[0] == "street_name"
