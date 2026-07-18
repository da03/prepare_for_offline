"""Candidate specifications for bounded, answer-aware follow-up rewriting."""

from __future__ import annotations


FOLLOWUP_ANSWER_AWARE_PROSE_SPEC = """
Rewrite a follow-up request as one self-contained question.

You receive the immediately preceding user question, its answer, and the
follow-up. Resolve references such as "it", "that", "there", "which one", and
"what about" from that single question-answer pair. Preserve the user's exact
intent and constraints. Include only the context needed to understand the
follow-up. Do not answer the question, add facts, or mention the conversation.
Return only the rewritten question.
""".strip()


FOLLOWUP_ANSWER_AWARE_EXAMPLES_SPEC = """
Rewrite a follow-up request as one self-contained question.

You receive the immediately preceding user question, its answer, and the
follow-up. Resolve references such as "it", "that", "there", "which one", and
"what about" from that single question-answer pair. Preserve the user's exact
intent and constraints. Include only the context needed to understand the
follow-up. Do not answer the question, add facts, or mention the conversation.
Return only the rewritten question.

Input:
PREVIOUS_QUESTION: What causes the northern lights?
PREVIOUS_ANSWER: Charged particles from the Sun interact with gases in Earth's upper atmosphere, producing visible light.
FOLLOW_UP: Can they happen farther south?
Output: Can the northern lights occur at lower latitudes farther south?

Input:
PREVIOUS_QUESTION: What are chickpeas commonly used for?
PREVIOUS_ANSWER: They are used in dishes including hummus, falafel, curries, and salads.
FOLLOW_UP: Which one is fried?
Output: Which chickpea dish, hummus or falafel, is fried?

Input:
PREVIOUS_QUESTION: What weather should I pack for in Lima?
PREVIOUS_ANSWER: Lima is often mild and cloudy, with cooler evenings and little rain.
FOLLOW_UP: What about in August?
Output: What weather should I pack for in Lima in August?
""".strip()


def specifications() -> dict[str, str]:
    return {
        "answer_aware_prose": FOLLOWUP_ANSWER_AWARE_PROSE_SPEC,
        "answer_aware_examples": FOLLOWUP_ANSWER_AWARE_EXAMPLES_SPEC,
    }
