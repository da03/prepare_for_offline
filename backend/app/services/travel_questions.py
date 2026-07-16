"""Likely travel questions used for coverage planning and starter cards."""

from __future__ import annotations


CATEGORY_TEMPLATES = {
    "arrival and transit": [
        "How do I get from the airport to {destination}?",
        "Which transit card should I buy?",
        "Can I pay for transit with a credit card?",
        "What time does public transit stop running?",
        "How do taxis work?",
        "What should I do if I miss the last train?",
        "Which station is closest to my destination?",
        "How much time should I allow for the airport?",
    ],
    "safety": [
        "What are the emergency phone numbers?",
        "Where is the nearest embassy or consulate?",
        "What should I do if I lose my passport?",
        "How do I contact local police?",
        "How do I get medical help?",
        "Are there common local safety risks?",
        "What information should I keep offline for emergencies?",
    ],
    "language": [
        "How do I say hello?",
        "How do I say thank you?",
        "How do I ask where the restroom is?",
        "How do I ask how much something costs?",
        "How do I ask for help?",
        "How do I explain a food allergy?",
        "What formal or polite endings might I hear?",
        "How do I pronounce the destination name?",
    ],
    "food and etiquette": [
        "Is tipping expected?",
        "What dining etiquette should I know?",
        "What common dishes should I recognize?",
        "Which foods are usually spicy?",
        "How do I find vegetarian food?",
        "Are side dishes included?",
        "Should I remove my shoes indoors?",
        "How should I greet an older person?",
    ],
    "money": [
        "What currency is used?",
        "Are international credit cards widely accepted?",
        "Where can I withdraw cash?",
        "How much cash should I carry?",
        "Are service charges included?",
        "What is the stored exchange-rate snapshot?",
    ],
    "lodging": [
        "What time is hotel check-in?",
        "What time is hotel checkout?",
        "How do I reach my lodging?",
        "What is my lodging address in the local language?",
        "Does my reservation include breakfast?",
        "Who do I contact if I arrive late?",
    ],
    "event schedule and venue": [
        "When and where is {event}?",
        "How do I reach the {event} venue?",
        "When does registration open?",
        "Where is the keynote?",
        "Which sessions are on my schedule?",
        "Where are the workshops?",
        "Is there an official event app or help desk?",
        "What changed in the latest event schedule?",
    ],
    "offline limitations": [
        "Which information in this pack is time-sensitive?",
        "When was this trip pack last refreshed?",
        "Which answers require checking online?",
        "What sources are included in this pack?",
        "What important coverage is still missing?",
    ],
}


def generate(trip: dict, source_titles: list[str], needs: list[str]) -> list[str]:
    brief = trip.get("trip_brief") or {}
    destination = brief.get("destination") or trip.get("name") or "my destination"
    event = brief.get("event") or "the event"
    categories = list(brief.get("coverage") or CATEGORY_TEMPLATES)
    # Always retain safety and offline-limits coverage.
    for required in ("safety", "offline limitations"):
        if required not in categories:
            categories.append(required)
    questions: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = " ".join(value.format(destination=destination, event=event).split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            questions.append(text)

    for category in categories:
        for template in CATEGORY_TEMPLATES.get(category, []):
            add(template)
    # Fill out the evaluation set with useful cross-category combinations.
    base = list(questions)
    for question in base:
        add(f"{question.rstrip('?')} for my first day?")
        if len(questions) >= 100:
            break
    for need in needs:
        add(need if str(need).endswith("?") else f"What should I know about {need}?")
    for title in source_titles[:20]:
        add(f"What are the key details in {title}?")
    return questions[:150]
