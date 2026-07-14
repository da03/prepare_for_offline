"""Curated seed knowledge library for the Korea pack.

Entries are topic-tagged so the PackPlanner can select a subset under a storage
budget. Each entry carries mishearing aliases so phonetic input reaches the
canonical entry. Tiers:
  - Tier 1: precomputed answer cards (most reliable; see ANSWER_CARDS).
  - Tier 2: structured facts / dictionary entries (deterministic rendering).
  - Tier 3: longer raw passages (fallback RAG / synthesis by the answerer).

Some facts are time-sensitive (stable=False) and carry an as_of date; answers
mark these as possibly stale.
"""

from __future__ import annotations

# Topics the planner can select among.
TOPICS = ["language", "food", "transport", "etiquette", "money", "emergency", "itinerary"]

# Capability -> (topics it needs, experts it needs)
CAPABILITIES: dict[str, dict] = {
    "heard_expression": {
        "label": "Understand heard/misspelled Korean words",
        "topics": ["language"],
        "experts": ["heard_expression_resolver"],
    },
    "menu_help": {
        "label": "Explain menu items and dishes",
        "topics": ["food"],
        "experts": [],
    },
    "getting_around": {
        "label": "Transport, subway, taxis",
        "topics": ["transport"],
        "experts": [],
    },
    "etiquette": {
        "label": "Local customs and etiquette",
        "topics": ["etiquette"],
        "experts": [],
    },
    "money": {
        "label": "Money, tipping, currency basics",
        "topics": ["money"],
        "experts": [],
    },
    "safety": {
        "label": "Emergencies and safety",
        "topics": ["emergency"],
        "experts": [],
    },
}


def _d(source_id, title, text, aliases, tier, stable, topic, as_of=None):
    return {
        "source_id": source_id,
        "title": title,
        "text": text,
        "aliases": aliases,
        "tier": tier,
        "stable": stable,
        "topic": topic,
        "as_of": as_of,
    }


DOCUMENTS: list[dict] = [
    # --- language ---
    _d("korean-ending-seumnida", "-습니다 / -ㅂ니다 (formal polite sentence ending)",
       "In Korean, -습니다 (-seumnida) and -ㅂ니다 (-mnida) are formal, polite "
       "sentence endings used in statements. They are not standalone words; "
       "they attach to a verb or adjective stem. Examples: 감사합니다 "
       "(gamsahamnida, 'thank you'), 괜찮습니다 (gwaenchanseumnida, 'it is "
       "okay'), 갑니다 (gamnida, 'I am going'). What sounds like 'simida' to an "
       "English speaker is almost always this -seumnida / -mnida ending.",
       ["simida", "seumnida", "seubnida", "smnida", "imnida", "mnida", "seumida"],
       2, True, "language"),
    _d("gamsahamnida", "감사합니다 (gamsahamnida) - thank you",
       "감사합니다 (gamsahamnida) means 'thank you' and is the standard formal "
       "way to express gratitude in Korean. It ends with the formal polite "
       "ending -습니다.",
       ["kamsahamnida", "gamsahamnida", "kamsahabnida", "thank you"], 2, True, "language"),
    _d("annyeonghaseyo", "안녕하세요 (annyeonghaseyo) - hello",
       "안녕하세요 (annyeonghaseyo) is the common polite greeting, 'hello'. "
       "안녕히 가세요 is 'goodbye' to someone leaving; 안녕히 계세요 is 'goodbye' "
       "said by the person leaving.",
       ["anyonghaseyo", "annyeonghaseyo", "anyoung", "hello"], 2, True, "language"),
    _d("juseyo", "주세요 (juseyo) - please give me",
       "주세요 (juseyo) means 'please give me' and is added after a noun to ask "
       "for something. Example: 이거 주세요 (igeo juseyo) = 'this one, please'.",
       ["juseyo", "chuseyo", "please give"], 2, True, "language"),
    _d("eolmayeyo", "얼마예요? (eolmayeyo) - how much is it?",
       "얼마예요? (eolmayeyo) means 'how much is it?' Use it when shopping or at "
       "a market to ask the price.",
       ["eolmayeyo", "olmayeyo", "how much"], 2, True, "language"),
    _d("hwajangsil", "화장실 (hwajangsil) - restroom",
       "화장실 (hwajangsil) means 'restroom/toilet'. 화장실이 어디예요? "
       "(hwajangsil-i eodiyeyo?) = 'where is the restroom?'",
       ["hwajangsil", "hwajangshil", "toilet", "restroom", "bathroom"], 2, True, "language"),
    # --- food ---
    _d("bibimbap", "비빔밥 (bibimbap)",
       "비빔밥 (bibimbap) is a rice bowl topped with assorted vegetables, often "
       "egg and meat, mixed with gochujang (red chili paste). Usually not "
       "spicy-hot unless you add more gochujang.",
       ["bibimbap", "bibimbab", "bibim"], 2, True, "food"),
    _d("bossam", "보쌈 (bossam)",
       "보쌈 (bossam) is boiled pork belly sliced and wrapped in leafy "
       "vegetables (like napa cabbage or lettuce) with ssamjang and "
       "seasonings. Mild in heat.",
       ["bosam", "possam", "bossam"], 2, True, "food"),
    _d("sungnyung", "숭늉 (sungnyung) - the white liquid after a meal",
       "The cloudy white liquid sometimes served at the end of a Korean meal "
       "is often 숭늉 (sungnyung), a warm drink made from the scorched rice at "
       "the bottom of the pot with added water. It is safe to drink and mildly "
       "nutty. (Milky white alcohol is makgeolli, a rice wine.)",
       ["sungnyung", "white liquid", "rice water", "makgeolli", "makkoli"], 2, True, "food"),
    _d("kimchi", "김치 (kimchi)",
       "김치 (kimchi) is fermented vegetables, most commonly napa cabbage, "
       "seasoned with chili, garlic, ginger and salted seafood. It is served "
       "as a side dish (banchan) at almost every meal. Can be spicy.",
       ["kimchi", "gimchi"], 2, True, "food"),
    _d("banchan", "반찬 (banchan) - side dishes",
       "반찬 (banchan) are the small shared side dishes served with a Korean "
       "meal. They are complimentary and often refillable for free.",
       ["banchan", "banchang", "side dishes"], 2, True, "food"),
    _d("samgyeopsal", "삼겹살 (samgyeopsal)",
       "삼겹살 (samgyeopsal) is grilled pork belly, cooked at the table and "
       "wrapped in lettuce with garlic, ssamjang, and kimchi. Not spicy by "
       "default.",
       ["samgyeopsal", "samgyupsal", "pork belly"], 2, True, "food"),
    _d("tteokbokki", "떡볶이 (tteokbokki)",
       "떡볶이 (tteokbokki) is chewy rice cakes in a sweet-and-spicy gochujang "
       "sauce. It is a popular street food and is usually spicy.",
       ["tteokbokki", "ddeokbokki", "topokki", "rice cake"], 2, True, "food"),
    # --- transport ---
    _d("tmoney-transport", "T-money card and Seoul subway",
       "A T-money card is a rechargeable transit card usable on the Seoul "
       "subway, buses, and many taxis and convenience stores. Tap in and out "
       "on the subway. Cards are sold at station machines and convenience "
       "stores such as GS25, CU, and 7-Eleven.",
       ["tmoney", "t money", "subway", "metro", "transport", "transit card"], 2, True, "transport"),
    _d("subway-hours", "Seoul subway operating hours",
       "The Seoul Metro generally runs from about 5:30 AM until around "
       "midnight, with the last trains varying by line. Check the last-train "
       "times if you are out late.",
       ["subway hours", "last train", "metro hours"], 3, True, "transport"),
    _d("taxi", "Taxis in Korea",
       "Regular taxis are orange/silver; black taxis are premium (deluxe) and "
       "cost more. You can hail taxis or use apps like Kakao T. Pay by card, "
       "T-money, or cash. Tipping is not expected.",
       ["taxi", "kakao t", "cab"], 2, True, "transport"),
    _d("ktx", "KTX high-speed train",
       "KTX is Korea's high-speed rail, connecting Seoul with cities like "
       "Busan, Daejeon, and Gwangju. Book via Korail or at the station. Seats "
       "are reserved; arrive a few minutes early.",
       ["ktx", "high speed train", "korail", "bullet train"], 2, True, "transport"),
    # --- etiquette ---
    _d("shoes-off", "Taking shoes off indoors",
       "Remove your shoes when entering homes, many guesthouses, temples, and "
       "some traditional restaurants with floor seating. Look for a raised "
       "floor or a shoe rack at the entrance.",
       ["shoes off", "take off shoes", "shoes indoors"], 2, True, "etiquette"),
    _d("two-hands", "Giving and receiving with two hands",
       "When giving or receiving items, money, or drinks - especially with "
       "elders - use two hands (or support your right forearm with your left "
       "hand). Pour drinks for others, not yourself.",
       ["two hands", "pouring drinks", "respect elders"], 2, True, "etiquette"),
    _d("elevators-separated", "Why hotel elevators may be separated",
       "Some Korean hotels and tall buildings split elevators into banks that "
       "serve different floor ranges (e.g., low-rise vs high-rise) to reduce "
       "wait times. Signage above each elevator shows which floors it serves; "
       "take the bank that lists your floor.",
       ["elevators separated", "three elevators", "elevator banks", "why elevators"], 2, True, "etiquette"),
    _d("tipping", "Tipping culture",
       "Tipping is generally not expected or practiced in Korea, including at "
       "restaurants and in taxis. Prices are as listed; some upscale venues "
       "add a service charge.",
       ["tipping", "tip", "gratuity"], 2, True, "etiquette"),
    # --- money ---
    _d("won", "Korean won (KRW)",
       "The currency is the South Korean won (₩, KRW). Bills come in 1,000, "
       "5,000, 10,000, and 50,000 won. Cards are widely accepted; carry some "
       "cash for small vendors and markets.",
       ["won", "krw", "currency", "money"], 2, True, "money"),
    _d("atm", "ATMs and cash",
       "Look for ATMs marked 'Global' or with foreign-card logos (often in "
       "convenience stores, banks, and subway stations) to withdraw cash with "
       "an international card.",
       ["atm", "cash machine", "withdraw cash"], 2, True, "money"),
    # --- emergency ---
    _d("emergency-numbers", "Emergency phone numbers",
       "In South Korea, dial 112 for police and 119 for fire and medical "
       "emergencies (ambulance). 1330 is the Korea Travel Hotline with "
       "multilingual help.",
       ["emergency", "police", "ambulance", "112", "119", "1330"], 2, True, "emergency"),
    _d("pharmacy", "Pharmacy (약국, yakguk)",
       "약국 (yakguk) is a pharmacy, marked with a green cross. Pharmacists can "
       "advise on minor ailments. Larger hospitals have international clinics.",
       ["pharmacy", "yakguk", "drugstore", "medicine"], 2, True, "emergency"),
]

# (question, answer, sources, aliases, topic, stable, as_of)
ANSWER_CARDS: list[dict] = [
    {
        "question": "What does simida mean?",
        "answer": "What you heard as 'simida' is almost certainly the Korean "
                  "formal polite sentence ending -습니다 (-seumnida) / -ㅂ니다 "
                  "(-mnida). It is not a standalone word; it attaches to a verb "
                  "or adjective stem to make a polite statement. For example: "
                  "감사합니다 (gamsahamnida) = 'thank you', 갑니다 (gamnida) = "
                  "'I am going'.",
        "sources": ["korean-ending-seumnida", "gamsahamnida"],
        "aliases": ["simida", "seumnida", "what is simida", "smida"],
        "topic": "language", "stable": True,
    },
    {
        "question": "Can I drink the white liquid served after the meal?",
        "answer": "Yes. The warm cloudy white liquid served at the end of a "
                  "meal is usually 숭늉 (sungnyung), made from scorched rice and "
                  "water. It is safe and mildly nutty. (Milky white alcohol "
                  "would be makgeolli, a rice wine.)",
        "sources": ["sungnyung"],
        "aliases": ["white liquid", "drink white liquid", "can i drink white"],
        "topic": "food", "stable": True,
    },
    {
        "question": "Why are the three hotel elevators separated?",
        "answer": "Tall Korean buildings often split elevators into banks that "
                  "serve different floor ranges (e.g., low floors vs high "
                  "floors) to cut waiting time. Check the sign above each "
                  "elevator and take the one that lists your floor.",
        "sources": ["elevators-separated"],
        "aliases": ["elevators separated", "three elevators", "why elevators separated"],
        "topic": "etiquette", "stable": True,
    },
    {
        "question": "What number do I call in an emergency?",
        "answer": "Dial 112 for police and 119 for fire or medical emergencies "
                  "(ambulance). For travel help in English, call the 1330 Korea "
                  "Travel Hotline.",
        "sources": ["emergency-numbers"],
        "aliases": ["emergency number", "call police", "ambulance number"],
        "topic": "emergency", "stable": True,
    },
    {
        "question": "Do I need to tip in Korea?",
        "answer": "No. Tipping is generally not expected in Korea, including "
                  "restaurants and taxis. Pay the listed price.",
        "sources": ["tipping"],
        "aliases": ["tip in korea", "should i tip", "tipping"],
        "topic": "money", "stable": True,
    },
]
