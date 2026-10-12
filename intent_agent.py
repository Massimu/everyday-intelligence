"""Parse the *whole* user request before retrieving mapped locations.

No generative text is used to invent a place. Extracts:
- an explicitly named city (only if it resolves unambiguously),
- an optional radius with units,
- an ambiguous barbecue intent that requires a choice.
A plain query is never interpreted from the currently selected category tab.
"""
from __future__ import annotations
import re
from intelligence import normalize

_RADIUS = re.compile(r"\b(?:within|radius|range|inside|up to|less than|unter|bis zu|umkreis|im umkreis von|in einem umkreis von)\s*(?:of|von|about|ca\.?|approximately)?\s*(\d+(?:[.,]\d+)?)\s*(km|kilometers?|kilometres?|kilometer|m|meters?|metres?|meter)\b", re.I)
_SIMPLE_RADIUS = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(km|kilometers?|kilometres?|kilometer|m|meters?|metres?|meter)\s*(?:radius|umkreis)\b", re.I)
_CITY_PHRASE = re.compile(r"\b(?:in|around|near|bei|rund um|im raum|innerhalb von)\s+([A-Za-zÀ-žßÄÖÜäöü][A-Za-zÀ-žßÄÖÜäöü-]{1,39}(?:\s+[A-Za-zÀ-žßÄÖÜäöü-]{2,39}){0,2})",re.I)

def radius_from_text(q: str) -> int | None:
    m = _RADIUS.search(q) or _SIMPLE_RADIUS.search(q)
    if not m:
        return None
    raw = float(m.group(1).replace(",","."))
    meters = round(raw*(1000 if m.group(2).lower().startswith(("km","kilo")) else 1))
    return max(500, min(meters, 6000))

def explicit_city(q: str, names: list[str]) -> str | None:
    text = normalize(q)
    for name in sorted(names,key=len,reverse=True):
        pattern = r"(?<!\w)" + re.escape(normalize(name)) + r"(?!\w)"
        if re.search(pattern,text):
            return name
    return None

def city_candidate(q: str) -> str | None:
    """Return one likely city phrase; the caller must geocode and verify it.

    Do not mistake 'in a park', 'near a pharmacy', or adjectives for cities.
    """
    q = re.sub(_RADIUS," ",q)
    m = _CITY_PHRASE.search(q)
    if not m:
        return None
    phrase = m.group(1).strip(" ,.?!")
    words = phrase.split()
    while words and normalize(words[0]) in {"a","an","the","my","any","ein","eine","einem","der","die","das","dem"}:
        words.pop(0)
    while words and normalize(words[-1]) in {"with","and","for","within","radius","near","nearby","mit","und","oder","bis"}:
        words.pop()
    if not words: return None
    candidate=" ".join(words[:2])
    if normalize(candidate) in {"park","parking","shop","shops","pharmacy","supermarket","drugs","museum","restaurant","grill place","grill","cafe","bakery","the area"}:
        return None
    return candidate

def strip_city(q: str, city: str | None) -> str:
    if not city:return q
    return re.sub(r"(?<!\w)"+re.escape(city)+r"(?!\w)"," ",q,flags=re.I)

def clarification(q: str, city: str | None) -> dict | None:
    text = normalize(q)
    bbq = r"\b(grill(?:en|platz|stelle|spots?|platzchen)?|grilling|barbe?que|bbq)\b"
    if not re.search(bbq,text):
        return None
    # Clear uses: a public location, a restaurant or a retail shop.
    public = bool(re.search(r"\b(grillplatz|grillstelle|public|outdoor|free|outside|picknick|picnic|offentlich|grillen|barbecue area|bbq area)\b",text))
    restaurant = bool(re.search(r"\b(restaurant|eat|eating|food|essen|grillrestaurant|dining|kebab|imbiss)\b",text))
    retail = bool(re.search(r"\b(buy|purchase|kaufen|shop|store|equipment|grillgerat)\b",text))
    if sum([public,restaurant,retail])==1:
        return None
    suffix = " in "+city if city else ""
    return {
        "question":"What kind of grill place are you looking for?",
        "options":[
            {"label":"Public BBQ / grilling area","query":"Find a public barbecue area"+suffix},
            {"label":"Grill restaurant","query":"Find a grill restaurant"+suffix},
            {"label":"Shop selling grills","query":"Find a shop selling grill equipment"+suffix},
        ],
        "reason":"A 'grill place' can mean an outdoor public grilling spot, a restaurant or a retailer. I'll search only the type you choose."
    }
