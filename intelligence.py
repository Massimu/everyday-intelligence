"""Deterministic, evidence-based place planner.

Every result comes from the supplied OpenStreetMap snapshot. The planner does
not claim current opening hours, stock, live parking or real pedestrian routes.
"""
from __future__ import annotations

import itertools
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

LABELS = {
    "parking": "Parking", "shopping": "Shopping", "outdoor": "Parks & outdoors",
    "pharmacy": "Pharmacy & services", "discovery": "Culture & discovery",
}
BRANDS = ("lidl", "aldi", "rewe", "edeka", "netto", "penny", "kaufland",
          "rossmann", "mueller", "dm", "budni", "tegut", "norma", "obi", "bauhaus")
RULES = [
    ("parking", "parking", r"\b(parking|parkhaus|parkplatz|parken|car park|garage)\b"),
    ("shopping", "supermarket", r"\b(supermarkets?|grocer(?:y|ies)|lebensmittel|food shopping|einkaufen|discounter)\b"),
    ("shopping", "chemist", r"\b(drugstores?|drogerie|drogerien|chemists?|cosmetic stores?)\b"),
    ("shopping", None, r"\b(shop(?:s|ping)?|store(?:s)?|geschaft(?:e)?|laden|einkauf)\b"),
    ("outdoor", "park", r"\b(park|parks|garden|gardens|green space|outdoor|spaziergang|walk|walking|grunflachen|grunanlage)\b"),
    ("pharmacy", "pharmacy", r"\b(pharmacy|pharmacies|apotheke|apotheken|medication|medikamente)\b"),
    ("pharmacy", "library", r"\b(library|libraries|bibliothek|bucherei)\b"),
    ("pharmacy", "clinic", r"\b(clinic|doctor|doctors|arzt|arztpraxis)\b"),
    ("pharmacy", None, r"\b(service|services|post office|postamt|behorde)\b"),
    ("discovery", "museum", r"\b(museum|museums|museen|gallery|galerie)\b"),
    ("discovery", None, r"\b(discover|attraction|sightseeing|culture|kultur|theatre|theater|interesting places|ausflug|wochenende|weekend|sehenswurdigkeit)\b"),
]
STOPWORDS = {
    "find","show","me","a","an","the","near","nearby","here","from","my","to","and","or","for",
    "want","need","looking","get","please","around","where","is","are","can","i","you",
    "best","nearest","closest","good","in","on","at","within","meters","km","with","also",
    "finde","suche","mir","und","oder","bei","in","der","die","das","ein","eine","ich",
    "wo","um","mich","von","zum","zur","fur","bitte","nah","nahe","am","im","den",
    "parking","park","shopping","shop","store","supermarket","pharmacy","apotheke",
    "museum","outdoor","walk","service","services","drugstore","drogerie",
}
MAX_GOALS = 5


def normalize(s: str) -> str:
    s = str(s or "").casefold().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def haversine(a: float, b: float, c: float, d: float) -> int:
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = p2-p1, math.radians(d-b)
    return round(12742000*math.asin(min(1, math.sqrt(math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2))))


@dataclass(frozen=True)
class Goal:
    category: str | None
    subtype: str | None = None
    brand: str | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        if self.brand: return self.brand.upper() if self.brand == "dm" else self.brand.title()
        if self.name: return self.name
        return {"supermarket": "Supermarket", "chemist": "Drugstore", "pharmacy": "Pharmacy",
                "park": "Park", "library": "Library", "museum": "Museum", "clinic": "Medical service"}.get(
                    self.subtype, LABELS.get(self.category, "Place"))


def infer_goals(question: str, active: str | None = None) -> list[Goal]:
    text = normalize(question)
    goals: list[Goal] = []
    for brand in BRANDS:
        if re.search(r"(?<!\w)" + re.escape(brand) + r"(?!\w)", text):
            goals.append(Goal("shopping", brand=brand))
    for cat, subtype, pattern in RULES:
        if re.search(pattern, text):
            if cat == "shopping" and any(g.brand for g in goals) and not subtype:
                continue
            if cat == "shopping" and any(g.brand for g in goals) and subtype in ("chemist", "supermarket"):
                # Explicitly requesting Lidl + a drugstore means two stops,
                # but "Lidl supermarket" just describes the Lidl stop.
                if subtype == "supermarket" and any(g.brand in ("lidl","aldi","rewe","edeka","netto","penny","kaufland","tegut","norma") for g in goals):
                    continue
                if subtype == "chemist" and any(g.brand in ("dm","rossmann","mueller","budni") for g in goals):
                    continue
            g = Goal(cat, subtype)
            if g not in goals: goals.append(g)
    if goals:
        # Explicitly requested "park and parking" remain different intents.
        return goals[:MAX_GOALS]
    if active and active in LABELS:
        return [Goal(active)]
    # Name search for "Rose Nails" or "Lidl" (Lidl handled as known brand).
    words = [w for w in re.findall(r"[\w-]+", text) if len(w) >= 3 and w not in STOPWORDS]
    if words:
        return [Goal(None, name=" ".join(words[:4]))]
    return []


def _goal_matches(goal: Goal, places: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched = []
    for p in places:
        if goal.category and p.get("category") != goal.category:
            continue
        if goal.category == "parking" and p.get("access") in ("no", "private"):
            continue
        name = normalize(p.get("name", ""))
        brand = normalize(p.get("brand") or "")
        if goal.brand:
            # Don't confuse 'dm' with substrings of other names.
            if not (re.search(r"(?<!\w)" + re.escape(goal.brand) + r"(?!\w)", name)
                    or re.search(r"(?<!\w)" + re.escape(goal.brand) + r"(?!\w)", brand)):
                continue
        if goal.name and not all(w in name for w in normalize(goal.name).split()):
            continue
        sub = normalize(p.get("subtype") or "")
        if goal.subtype and not sub:
            # Legacy OSM snapshots did not retain the subtype tags.
            # Never treat a nearby nail salon as a supermarket or a
            # library as a pharmacy. In this case use recognizable names
            # only, and flag the missing metadata to users.
            name_or_brand = name + " " + brand
            hints = {
                "supermarket": r"\b(lidl|aldi|rewe|edeka|netto|penny|kaufland|tegut|norma|supermarkt|lebensmittel|nahkauf|sparen|combi|famila)\b",
                "chemist": r"\b(dm|rossmann|mueller|drogerie|budni|budnikowsky)\b",
                "pharmacy": r"\b(apotheke|pharmacy)\b",
                "library": r"\b(bibliothek|bucherei|library)\b",
                "clinic": r"\b(klinik|arzt|praxis|doctor|clinic)\b",
                "museum": r"\b(museum|museen|galerie|kunsthalle|gallery)\b",
            }
            if goal.subtype in hints and not re.search(hints[goal.subtype], name_or_brand):
                continue
        if goal.subtype:
            accepted = {
                "chemist": {"chemist", "beauty"}, "supermarket": {"supermarket","grocery","convenience"},
                "park": {"park", "garden", "nature_reserve"},
                "museum": {"museum", "gallery"}, "pharmacy": {"pharmacy"},
                "library": {"library"}, "clinic": {"doctors", "clinic"}
            }.get(goal.subtype, {goal.subtype})
            if sub and sub not in accepted:
                continue
            # Existing snapshots do not include subtype. Use name only for
            # branded searches; otherwise old snapshots lack fine distinctions.
        matched.append(p)
    # Prioritize named, understandable places over anonymous map polygons.
    # Parking is an exception: most useful parking facilities have no name tag.
    if goal.category != "parking" and any(not p.get("name","").startswith("Unnamed") for p in matched):
        matched = [p for p in matched if not p.get("name","").startswith("Unnamed")]
    matched.sort(key=lambda p: p["distance_m"])
    return matched


def _pick_combination(goals: list[Goal], options: list[list[dict]], origin: tuple[float,float]):
    """Beam search over stop order *and* locations.

    Hops are aerial distances. A modest search width and candidate cap make
    requests fast on Render free tier while avoiding nearest-per-category bias.
    Parking begins the itinerary when explicitly requested.
    """
    if not options or any(not group for group in options): return [], 0
    groups = [g[:10] for g in options]
    initial = [(0.0, origin[0], origin[1], (), frozenset())]
    beam = initial
    width = 42
    for step in range(len(groups)):
        nxt = []
        for cost, lat, lon, route, used in beam:
            for i, group in enumerate(groups):
                if i in used: continue
                if step == 0 and any(g.category == "parking" for g in goals) and goals[i].category != "parking":
                    continue
                for p in group:
                    # Never use the same mapped location to satisfy different stops
                    if p["id"] in (item[1]["id"] for item in route): continue
                    d = haversine(lat, lon, p["lat"], p["lon"])
                    nxt.append((cost + d, p["lat"], p["lon"], route+((i,p),), used|{i}))
        if not nxt: break
        nxt.sort(key=lambda x: (x[0] + (0.15*haversine(x[1],x[2],origin[0],origin[1])),x[0]))
        beam = nxt[:width]
    if not beam or len(beam[0][3]) != len(groups): return [],0
    best = min(beam, key=lambda x:x[0])
    return [{"goal": goals[i].label, "place":p} for i,p in best[3]], round(best[0])


def plan(question: str, places: list[dict], lat: float, lon: float,
         radius: int, active: str | None = None, weather: dict | None = None,
         source_status: str = "saved OSM snapshot") -> dict[str,Any]:
    report_mode = bool(re.search(r"\b(neighborhood|neighbourhood|viertel|wohngegend|living|leben|area|umgebung)\b",normalize(question))) or active == "neighborhood"
    goals = [] if report_mode else infer_goals(question, active)
    count_mode = bool(re.search(r"\b(how many|number of|count|wie viele|anzahl)\b",normalize(question)))
    km = f"{radius/1000:g} km"
    if source_status == "unavailable":
        return {"summary":"Location data is currently unavailable for this area. Move to Braunschweig or Hannover, or try again later.",
                "goals":[], "stops":[], "recommended":[], "alternatives":[], "missing":[],
                "insights":[], "estimate_m":None, "limits":["No verified mapped locations were loaded."]}
    if not goals and not report_mode:
        goals = [Goal(cat) for cat in ("shopping","outdoor","pharmacy") if any(p["category"] == cat for p in places)]
    groups = [_goal_matches(g,places) for g in goals]
    missing=[g.label for g,choices in zip(goals,groups) if not choices]
    valid_goals=[g for g,choices in zip(goals,groups) if choices]
    valid_options=[choices for choices in groups if choices]
    steps,est=_pick_combination(valid_goals,valid_options,(lat,lon))
    if count_mode:
        steps = []
        est = None
    if not steps and valid_goals and not count_mode:
        for g,choices in zip(valid_goals,valid_options):
            steps.append({"goal":g.label,"place":choices[0]})
        est=None
    stops=[dict(s["place"],goal=s["goal"],order=i+1) for i,s in enumerate(steps)]
    brand_requested=any(g.brand for g in goals)
    if count_mode and goals:
        summary=f"Within {km}: " + "; ".join(f"{g.label}: {len(group)} mapped matches" for g,group in zip(goals,groups)) + "."
    elif missing:
        summary=("No mapped " + ", ".join(missing) + f" within {km}." if not stops else
                 f"Found {len(stops)} of {len(goals)} requested stops within {km}. "
                 + "Not found: " + ", ".join(missing) + ".")
    elif report_mode:
        summary=f"Neighborhood overview within {km} of the selected point, using available mapped places. This is not a quality or livability score."
    elif stops:
        summary=(f"Found {stops[0]['name']} {round(stops[0]['distance_m'])} m from the selected point."
                 if len(stops)==1 else f"Found a {len(stops)}-stop combination within {km}.")
        if est is not None and len(stops)>1:
            summary+=f" Approximate straight-line journey: {est/1000:.1f} km from the selected point."
    else:
        summary=f"No matching mapped places within {km}."
    alt=[]
    if len(goals)==1 and groups and groups[0]:
        alt=[p for p in groups[0] if p["id"] not in {s["id"] for s in stops}][:3]
    elif missing and brand_requested:
        # Never present an unrelated retailer as a substitute for a named brand.
        alt=[p for p in places if p.get("category")=="shopping" and
             re.search(r"\b(lidl|aldi|rewe|edeka|netto|penny|kaufland|dm|rossmann|mueller|tegut|norma|supermarkt|drogerie)\b",
                       normalize(p.get("name","")+" "+str(p.get("brand") or "")))][:3]
    insights=[]
    if count_mode:
        insights.append("Counts reflect mapped OpenStreetMap features, not a complete or live business census.")
    elif len(stops)==1:
        p=stops[0]
        insights.append(f"Nearest mapped match: {p['name']} ({p['distance_m']} m aerial distance).")
    elif len(stops)>1:
        insights.append("The combination considers distances between stops, not just distance from your start.")
    if weather and ("outdoor" in [g.category for g in goals]):
        temp=weather.get("temperature_2m")
        rain=weather.get("precipitation")
        if isinstance(rain,(int,float)) and rain>0:
            insights.append(f"Weather context: currently reporting {rain:g} mm precipitation; outdoor conditions may be wet.")
        elif isinstance(temp,(int,float)):
            insights.append(f"Weather context: {temp:g}°C currently; this is not a forecast for your visit time.")
    if active=="neighborhood" or re.search(r"\b(neighborhood|neighbourhood|viertel|wohngegend|living|leben|area|umgebung)\b",normalize(question)):
        for cat in ("shopping","pharmacy","outdoor","discovery"):
            subset=[p for p in places if p["category"]==cat]
            if subset: insights.append(f"{LABELS[cat]}: {len(subset)} mapped places; closest is {subset[0]['distance_m']} m away.")
    limits=["Distances and journey length are straight-line estimates, not walking routes.",
            "Opening hours, current availability, prices and accessibility have not been verified."]
    if source_status=="saved OSM snapshot":
        limits.append("Places come from a periodically updated OpenStreetMap snapshot, not live business data.")
        if any(p.get("subtype") is None for p in places):
            limits.append("Some older records lack store-type tags, so category-specific searches use conservative name matching and may miss places.")
    if any(s["category"]=="parking" for s in stops):
        limits.append("A mapped parking location is not confirmation of public access or free spaces.")
    return {"summary":summary,"goals":[g.label for g in goals],"stops":stops,
            "recommended":stops,"alternatives":alt,"missing":missing,"insights":insights,
            "estimate_m":est if len(stops)>1 else None,"limits":limits,
            "caveat":" ".join(limits)}
