from eventkit import Attendee, normalize_attendee, normalize_institution, normalize_name


def test_name_and_institution_normalization_collapses_whitespace_and_uses_nfc():
    name = "  Cafe\u0301   Noir  "
    institution = "  E\u0301cole   Polytechnique  "

    assert normalize_name(name) == "Café Noir"
    assert normalize_institution(institution) == "École Polytechnique"


def test_normalize_attendee_keeps_source_values_and_populates_comparison_forms():
    attendee = Attendee(
        row_number=7,
        name="  Cafe\u0301   Noir  ",
        email="  PERSON@Example.COM ",
        phone=" +1 (202) 555-0107 ",
        institution="  E\u0301cole   Polytechnique  ",
    )

    normalized = normalize_attendee(attendee)

    assert normalized.name == attendee.name
    assert normalized.email == attendee.email
    assert normalized.phone == attendee.phone
    assert normalized.institution == attendee.institution
    assert normalized.normalized_name == "Café Noir"
    assert normalized.normalized_email == "person@example.com"
    assert normalized.normalized_phone == "+12025550107"
    assert normalized.normalized_institution == "École Polytechnique"
