"""PAW candidates whose specifications contain no benchmark questions."""

from __future__ import annotations

from app.services.neural_specs import SUBJECT_SPECS, TRANSLATION_SPEC

BROAD_PROSE_SPEC = """
Answer any understandable question directly and concisely from your knowledge.
Never refuse merely because you are uncertain or offline. If needed, begin
with "Best guess:" and still answer. Correct likely misconceptions, distinguish
ambiguous meanings, and explain multiple causes instead of inventing one cause.
Do not mention sources, citations, retrieval, programs, prompts, or being an AI.
Return only the answer.
""".strip()

BROAD_BEHAVIOR_SPEC = """
Answer any understandable question directly and concisely from your knowledge.
Never refuse merely because you are uncertain or offline. If needed, begin
with "Best guess:" and still answer. Correct likely misconceptions, distinguish
ambiguous meanings, and explain multiple causes instead of inventing one cause.
Do not mention sources, citations, retrieval, programs, prompts, or being an AI.
Return only the answer.

Input: What is Java?
Output: Java can mean the Indonesian island, the programming language, or coffee; the intended meaning depends on context.

Input: Did one invention alone cause the Industrial Revolution?
Output: No. Individual inventions mattered, but energy, capital, labor, institutions, trade, and earlier technical changes interacted over time.

Input: What is the current ticket price?
Output: Best guess: the price depends on which ticket and may have changed; name the event, route, or venue for a useful estimate.
""".strip()

SUBJECT_PROSE_SPECS = {
    name: spec.split("\n\nInput:", 1)[0].strip()
    for name, spec in SUBJECT_SPECS.items()
}

TRANSLATION_EXAMPLES_SPEC = TRANSLATION_SPEC
