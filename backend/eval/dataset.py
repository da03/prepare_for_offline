"""Evaluation set for the Korea pack.

Labeled with gold source + key facts and whether the question is answerable
from the pack, so we can measure grounded accuracy, citation correctness, and
abstention behavior. Phonetic items test misspelled/heard input.
"""

from __future__ import annotations

# id, question, answerable, gold_source, gold_keywords, phonetic
EVAL: list[dict] = [
    {"id": "simida", "q": "What does simida mean?", "answerable": True,
     "gold_source": "korean-ending-seumnida", "keywords": ["seumnida"], "phonetic": True},
    {"id": "seumnida_bare", "q": "seumnida", "answerable": True,
     "gold_source": "korean-ending-seumnida", "keywords": ["ending"], "phonetic": True},
    {"id": "kamsa", "q": "what does kamsahamnida mean", "answerable": True,
     "gold_source": "gamsahamnida", "keywords": ["thank"], "phonetic": True},
    {"id": "anyong", "q": "anyonghaseyo", "answerable": True,
     "gold_source": "annyeonghaseyo", "keywords": ["hello"], "phonetic": True},
    {"id": "possam", "q": "what is possam", "answerable": True,
     "gold_source": "bossam", "keywords": ["pork"], "phonetic": True},
    {"id": "bossam", "q": "what is bossam", "answerable": True,
     "gold_source": "bossam", "keywords": ["pork belly"], "phonetic": False},
    {"id": "white_liquid", "q": "can I drink the white liquid served after the meal",
     "answerable": True, "gold_source": "sungnyung", "keywords": ["rice"], "phonetic": False},
    {"id": "tmoney", "q": "how do I use the T-money card", "answerable": True,
     "gold_source": "tmoney-transport", "keywords": ["subway"], "phonetic": False},
    {"id": "tteokbokki", "q": "is tteokbokki spicy", "answerable": True,
     "gold_source": "tteokbokki", "keywords": ["spicy"], "phonetic": False},
    {"id": "emergency", "q": "what number do I call in an emergency", "answerable": True,
     "gold_source": "emergency-numbers", "keywords": ["112"], "phonetic": False},
    {"id": "tipping", "q": "do I need to tip in Korea", "answerable": True,
     "gold_source": "tipping", "keywords": ["not"], "phonetic": False},
    {"id": "shoes", "q": "do I take my shoes off indoors", "answerable": True,
     "gold_source": "shoes-off", "keywords": ["shoes"], "phonetic": False},
    {"id": "elevators", "q": "why are the three hotel elevators separated", "answerable": True,
     "gold_source": "elevators-separated", "keywords": ["floor"], "phonetic": False},
    {"id": "restroom", "q": "how do I ask where the restroom is", "answerable": True,
     "gold_source": "hwajangsil", "keywords": ["hwajangsil"], "phonetic": False},
    # Unanswerable from this pack -> should abstain.
    {"id": "france", "q": "what is the capital of France", "answerable": False,
     "gold_source": None, "keywords": [], "phonetic": False},
    {"id": "worldcup", "q": "who won the 2018 world cup", "answerable": False,
     "gold_source": None, "keywords": [], "phonetic": False},
    {"id": "wifi", "q": "what is the wifi password at my hotel", "answerable": False,
     "gold_source": None, "keywords": [], "phonetic": False},
    {"id": "namsan_height", "q": "exactly how many meters tall is Namsan Tower",
     "answerable": False, "gold_source": None, "keywords": [], "phonetic": False},
]
