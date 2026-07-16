from __future__ import annotations

from datetime import date

import pytest

from app.services.source_queries import (
    MAX_QUERY_CHARS,
    PrivateQueryDataError,
    PublicTripFields,
    contains_private_token,
    generate_public_trip_queries,
    public_trip_fields_from_mapping,
)


def test_private_mapping_fields_are_rejected_without_echoing_values():
    private_email = "traveler-secret@example.com"

    with pytest.raises(PrivateQueryDataError) as caught:
        public_trip_fields_from_mapping(
            {
                "event": "Public Conference",
                "destination": "Paris",
                "name": "Private Traveler",
                "email": private_email,
                "attachment_text": "Entire private itinerary",
            }
        )

    assert caught.value.field_names == ("attachment_text", "email", "name")
    assert private_email not in str(caught.value)


def test_filter_mode_ignores_private_fields_and_redacts_personal_tokens():
    source = {
        "event": "Open Data Summit",
        "destination": (
            "Paris alice@example.com reservation code ZX9K21"
        ),
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "public_needs": [
            "wheelchair access for Alice Smith",
            "transit phone +1 (212) 555-0199",
        ],
        "attachment_text": "Never send this attachment body",
    }

    plan = generate_public_trip_queries(
        source,
        private_tokens=["Alice Smith"],
        reject_private_fields=False,
    )
    joined = " ".join(plan.queries).casefold()

    assert "alice@example.com" not in joined
    assert "zx9k21" not in joined
    assert "alice smith" not in joined
    assert "555-0199" not in joined
    assert "never send" not in joined
    assert plan.ignored_private_fields == ("attachment_text",)
    assert {
        "caller_private_token",
        "email",
        "phone_number",
        "reservation_code",
    }.issubset(plan.redaction_categories)


def test_queries_use_only_public_allowlist_fields():
    plan = generate_public_trip_queries(
        {
            "event": "PyCon",
            "destination": "Pittsburgh",
            "starts_at": "2026-05-15T08:00:00",
            "ends_at": "2026-05-18T18:00:00",
            "public_needs": ["airport transit"],
            "goal": "Secret free-form notes must never be searched",
            "languages": ["en"],
        }
    )

    joined = " ".join(plan.queries)
    assert "PyCon" in joined
    assert "Pittsburgh" in joined
    assert "Secret" not in joined
    assert "languages" not in joined


def test_public_event_name_is_allowed_but_caller_names_require_redaction():
    fields = PublicTripFields(
        event="Taylor Swift public concert",
        destination="London",
        public_needs=("entry policy for Private Guest",),
    )

    plan = generate_public_trip_queries(
        fields, private_tokens=("Private Guest",)
    )
    joined = " ".join(plan.queries)

    assert "Taylor Swift" in joined
    assert "Private Guest" not in joined


def test_direct_booking_reference_and_string_need_are_filtered():
    fields = PublicTripFields(
        event="Public Expo",
        destination="Rome booking AB12CD",
        public_needs="metro access",
    )

    plan = generate_public_trip_queries(fields)
    joined = " ".join(plan.queries)

    assert "AB12CD" not in joined
    assert "metro access" in joined
    assert "reservation_reference" in plan.redaction_categories


def test_dates_are_normalized_and_queries_are_bounded():
    fields = PublicTripFields(
        event="A" * 500,
        destination="Tokyo",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 8),
        public_needs=("rail information",) * 20,
    )

    plan = generate_public_trip_queries(fields)

    assert plan.queries
    assert all(len(query) <= MAX_QUERY_CHARS for query in plan.queries)
    assert len(plan.queries) <= 8
    assert any("2026-07-01" in query for query in plan.queries)


@pytest.mark.parametrize(
    "private_text",
    [
        "contact me at me@example.com",
        "PNR: ABC123",
        "passport number X1234567",
        "api_key=super-secret-value",
        "eyJabcdefgh.abcdefgh.abcdefgh",
    ],
)
def test_private_token_detector(private_text):
    assert contains_private_token(private_text)
