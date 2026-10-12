"""Small noncommercial six-module city intelligence prototype."""
import asyncio, math, os, time, json
from functools import lru_cache
from intelligence import plan, normalize
from intent_agent import radius_from_text, explicit_city, city_candidate, strip_city, clarification
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app=FastAPI(title="Everyday Intelligence",version="0.6")
CITIES={"Braunschweig":(52.2689,10.5268),"Hannover":(52.3759,9.732),"Berlin":(52.52,13.405),"Hamburg":(53.5511,9.9937),"Leipzig":(51.3397,12.3731),"München":(48.1374,11.5755)}
KINDS={"parking":"Parking","shopping":"Supermarkets and drugstores","neighborhood":"Neighborhood","outdoor":"Parks and gardens","pharmacy":"Pharmacies and services","discovery":"Culture and discovery"}
cache={}
def distance(a,b,c,d):
    p1,p2=math.radians(a),math.radians(c);dp=p2-p1;dl=math.radians(d-b)
    return round(12742000*math.asin(min(1,math.sqrt(math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2))))
def category(t):
    amenity=t.get("amenity")
    leisure=t.get("leisure")
    if amenity=="parking":return "parking"
    if t.get("shop") in ("supermarket","convenience","grocery","chemist","beauty","hardware","garden_centre"):
        return "shopping"
    if amenity in ("pharmacy","clinic","library","doctors","post_office","townhall","bank","atm","toilets"):
        return "pharmacy"
    if amenity=="bbq" or leisure=="firepit" or t.get("barbecue")=="yes" or t.get("bbq")=="yes":
        return "outdoor"
    if leisure in ("park","garden","nature_reserve","playground") or t.get("tourism")=="picnic_site":
        return "outdoor"
    if amenity in ("restaurant","cafe","fast_food","biergarten") or t.get("shop")=="bakery":
        return "food"
    if t.get("tourism") in ("museum","gallery","attraction","viewpoint") or t.get("historic") in ("castle","monument") or amenity in ("theatre","arts_centre"):
        return "discovery"

# Two community-operated instances. Keep request volume low and reuse cached responses.
OVERPASS_URLS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
logger = __import__("logging").getLogger("everyday_intelligence")
data_lock = asyncio.Lock()
# The website must remain usable when public Overpass endpoints are offline.
# This file is produced by a GitHub Actions workflow from daily BBBike extracts,
# committed only when verified and containing real, attributed OSM objects.
snapshot_file = Path(__file__).resolve().parent / "data" / "places.json"

@lru_cache(maxsize=1)
def _snapshot():
    if not snapshot_file.is_file():
        return {"cities": {}}
    return json.loads(snapshot_file.read_text(encoding="utf-8"))

def _saved_places(lat, lon, radius):
    try:
        dataset = _snapshot()
        best = None
        best_distance = float("inf")
        for name, record in dataset.get("cities", {}).items():
            d = distance(lat, lon, record["latitude"], record["longitude"])
            if d < best_distance:
                best = (name, record)
                best_distance = d
        # City extracts retain features around approximately 10 km from the
        # original center. Disallow remote centers outside this region.
        if best is None or best_distance > 8000:
            return [], None, None, None
        name, record = best
        output = []
        for original in record.get("places", []):
            d = distance(lat, lon, original["lat"], original["lon"])
            if d <= radius:
                p = dict(original)
                p["distance_m"] = d
                output.append(p)
        output.sort(key=lambda p: p["distance_m"])
        coverage = ("partial" if best_distance + radius > 7500 else "local snapshot")
        return output, name, record.get("updated_at"), coverage
    except Exception as exc:
        logger.error("Failed to load saved OpenStreetMap data: %s", exc)
        return [], None, None, None


async def _load_osm(client, lat, lon, radius):
    # Request all six categories in one query. A short output limit would
    # arbitrarily discard categories based on element ID, so do not use one.
    selectors = [
        '"amenity"="parking"',
        '"shop"~"^(supermarket|convenience|grocery|chemist|beauty)$"',
        '"amenity"~"^(pharmacy|clinic|library|doctors|post_office|townhall|theatre|arts_centre)$"',
        '"leisure"~"^(park|garden|nature_reserve)$"',
        '"tourism"~"^(museum|gallery|attraction|viewpoint)$"',
        '"historic"~"^(castle|monument)$"',
        '"amenity"~"^(restaurant|cafe|fast_food|biergarten|bbq)$"',
        '"leisure"~"^(firepit|playground)$"',
        '"tourism"="picnic_site"',
        '"shop"="bakery"',
        '"barbecue"="yes"',
    ]
    clauses = "".join(f"nwr[{selector}](around:{radius},{lat},{lon});" for selector in selectors)
    query = f"[out:json][timeout:22];({clauses});out center;"
    errors = []
    for url in OVERPASS_URLS:
        source = url.split("/")[2]
        try:
            resp = await client.post(url, data={"data": query}, timeout=29)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}; {resp.text[:90]}")
            raw = resp.json()
            if not isinstance(raw.get("elements"), list):
                raise ValueError("No elements collection in response")
            data = raw["elements"]
            logger.info("OpenStreetMap: %s returned %d features", source, len(data))
            if data:
                return data, source, errors
            errors.append(f"{source}: empty response")
        except Exception as exc:
            logger.warning("OpenStreetMap request failed on %s: %s", source, str(exc)[:220])
            errors.append(f"{source}: {type(exc).__name__} ({getattr(getattr(exc, 'response', None), 'status_code', 'no HTTP response')})")
    return [], None, errors

def _parse_places(elements, lat, lon, radius):
    places = []
    seen = set()
    for e in elements:
        t = e.get("tags") or {}
        kind = category(t)
        center = e.get("center") or {}
        la = e.get("lat", center.get("lat"))
        lo = e.get("lon", center.get("lon"))
        if not kind or la is None or lo is None:
            continue
        identifier = f"{e.get('type')}/{e.get('id')}"
        if identifier in seen:
            continue
        d = distance(lat, lon, la, lo)
        if d > radius:
            continue
        seen.add(identifier)
        places.append({
            "id": identifier,
            "name": t.get("name") or t.get("name:de") or "Unnamed mapped location",
            "category": kind,
            "lat": la,
            "lon": lo,
            "distance_m": d,
            "opening_hours": t.get("opening_hours"),
            "brand": t.get("brand"),
            "subtype": ("bbq" if t.get("amenity")=="bbq" or t.get("leisure")=="firepit" or t.get("barbecue")=="yes" or t.get("bbq")=="yes" else
                        t.get("shop") or t.get("amenity") or t.get("leisure") or t.get("tourism") or t.get("historic")),
            "cuisine":t.get("cuisine"),"description":t.get("description"),
            "access": t.get("access"), "fee": t.get("fee"), "wheelchair": t.get("wheelchair"),
            "url": f"https://www.openstreetmap.org/{identifier}",
        })
    places.sort(key=lambda p: p["distance_m"])
    return places

async def get_data(lat, lon, radius):
    key = (round(lat, 3), round(lon, 3), radius)
    now = time.monotonic()
    # Retry failed external reads soon; cache successes for one hour.
    cached = cache.get(key)
    if cached and now - cached[0] < (3600 if cached[1]["status"]["places"] in ("live OSM snapshot","saved OSM snapshot") else 60):
        return cached[1]
    async with data_lock:
        now = time.monotonic()
        cached = cache.get(key)
        if cached and now - cached[0] < (3600 if cached[1]["status"]["places"] in ("live OSM snapshot", "saved OSM snapshot") else 60):
            return cached[1]
        status = {"places": "unavailable", "weather": "unavailable"}
        errors = []
        weather = None
        headers = {"User-Agent": os.getenv(
            "EVERYDAY_USER_AGENT",
            "EverydayIntelligence-Hobby/0.3 (+https://github.com/Massimu/everyday-intelligence)"
        )}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            async def weather_call():
                try:
                    r = await client.get("https://api.open-meteo.com/v1/forecast", params={
                        "latitude": lat, "longitude": lon,
                        "current": "temperature_2m,precipitation,wind_speed_10m",
                        "timezone": "Europe/Berlin"
                    }, timeout=15)
                    r.raise_for_status()
                    return r.json().get("current"), "live forecast"
                except Exception as exc:
                    logger.warning("Weather request failed: %s", str(exc)[:200])
                    return None, "unavailable"
            saved, _, _, _ = _saved_places(lat, lon, radius)
            if saved:
                # Avoid slow, rate-limited public Overpass calls for supported cities.
                # Verified offline data is refreshed weekly by GitHub Actions.
                elements, source, failures = [], None, []
                weather, status["weather"] = await weather_call()
            else:
                # Public Overpass queries are often blocked from shared cloud
                # IPs. Fail fast outside downloaded coverage unless explicitly
                # enabled, rather than freezing the interface for 60 seconds.
                if os.getenv("ENABLE_LIVE_OVERPASS", "false").lower() == "true":
                    (elements, source, failures), (weather, status["weather"]) = await asyncio.gather(
                        _load_osm(client, lat, lon, radius), weather_call()
                    )
                else:
                    elements, source, failures = [], None, ["No downloaded snapshot covers this search center"]
                    weather, status["weather"] = await weather_call()
        places = _parse_places(elements, lat, lon, radius)
        if source:
            status["places"] = "live OSM snapshot"
            status["source"] = source
        else:
            errors = failures
            # Prefer previously verified, attributed OSM data over empty responses.
            stored, stored_city, saved_at, coverage = _saved_places(lat, lon, radius)
            if stored:
                places = stored
                status["places"] = "saved OSM snapshot"
                status["source"] = f"BBBike OSM extract, {stored_city}"
                status["data_date"] = saved_at
                status["coverage"] = coverage
                logger.info("Using %s saved OSM places for %s", len(places), stored_city)
            elif cached and cached[1]["places"]:
                places = cached[1]["places"]
                status["places"] = "stale cached OSM"
                status["source"] = cached[1]["status"].get("source", "cached")
        if not weather and cached:
            weather = cached[1].get("weather")
            if weather:
                status["weather"] = "stale cached weather"
        result = {
            "places": places, "weather": weather, "status": status,
            "source_errors": errors,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "counts": dict(Counter(p["category"] for p in places))
        }
        cache[key] = (time.monotonic(), result)
        return result

@app.get("/api/health")
async def health():
    return {"status":"ok", "version":"0.6", "supported_cities":list(_snapshot().get("cities",{}))}
@app.get("/api/cities")
async def cities():
    return [{"name":name,"lat":data["latitude"],"lon":data["longitude"],"has_places":True}
            for name,data in _snapshot().get("cities",{}).items()]

geocode_cache = {}
@app.get("/api/locations")
async def locations(q: str = Query(min_length=2,max_length=80)):
    """Look up German cities via the openly accessible Open-Meteo geocoder.

    Geocoding a location does NOT imply mapped POI coverage at that location.
    """
    key=q.strip().casefold()
    if key in geocode_cache and time.monotonic()-geocode_cache[key][0]<86400:
        return geocode_cache[key][1]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response=await client.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name":q.strip(),"count":8,"language":"de","countryCode":"DE","format":"json"})
            response.raise_for_status()
            items=response.json().get("results",[])
        mapped=[]
        for item in items:
            la,lo=item.get("latitude"),item.get("longitude")
            if isinstance(la,(int,float)) and isinstance(lo,(int,float)):
                _,city,_,_= _saved_places(la,lo,500)
                mapped.append({"name":item.get("name"),"region":item.get("admin1"),"lat":la,"lon":lo,
                              "has_places":bool(city)})
        geocode_cache[key]=(time.monotonic(),mapped)
        if len(geocode_cache)>200: geocode_cache.clear()
        return mapped
    except Exception as exc:
        logger.warning("Geocoding request failed: %s",str(exc)[:180])
        return []

@app.get("/api/overview")
async def overview(lat:float=Query(52.2689,ge=47,le=55.1),
                   lon:float=Query(10.5268,ge=5,le=16),
                   radius:int=Query(1800,ge=500,le=6000)):
    return await get_data(lat,lon,radius)

class Ask(BaseModel):
    question:str=Field(min_length=2,max_length=400)
    lat:float=Field(ge=47,le=55.1)
    lon:float=Field(ge=5,le=16)
    radius:int=Field(default=1800,ge=500,le=6000)
    active_category: str|None = None

async def _resolve_city_in_question(question: str, lat: float, lon: float):
    """Prefer explicitly named cached cities; verify other towns with geocoding.

    If a city cannot be resolved, leave the center unchanged and tell the
    visitor: geocoding failures must never silently pretend the city changed.
    """
    known = {**_snapshot().get("cities",{})}
    explicit=explicit_city(question,list(known))
    if explicit:
        record=known[explicit]
        return record["latitude"],record["longitude"],explicit,None
    candidate=city_candidate(question)
    if not candidate:
        return lat,lon,None,None
    options=await locations(candidate)
    exact=[p for p in options if normalize(p["name"])==normalize(candidate)]
    if len(exact)==1:
        place=exact[0]
        return place["lat"],place["lon"],place["name"],None
    # Multiple German towns share a name: do not pick a random one.
    if len(exact)>1:
        return lat,lon,None,f"Several locations are called {candidate}. Select a city using the location search above."
    return lat,lon,None,f"I could not verify the location '{candidate}'. Choose a city using the location search above."

@app.post("/api/ask")
async def ask(body:Ask):
    radius=radius_from_text(body.question) or body.radius
    lat,lon,city,city_error=await _resolve_city_in_question(body.question,body.lat,body.lon)
    context={"lat":lat,"lon":lon,"radius":radius,"city":city,
             "changed": (abs(lat-body.lat)>0.00001 or abs(lon-body.lon)>0.00001 or radius != body.radius)}
    clean_question=strip_city(body.question,city)
    data=await get_data(lat,lon,radius)
    clarify=clarification(clean_question,city)
    if clarify:
        return {"summary":clarify["reason"],"clarification":clarify,
                "goals":[],"stops":[],"recommended":[],"alternatives":[],"missing":[],
                "insights":[],"limits":["Select which kind of grilling destination you want."],
                "search_context":context,"status":data["status"]}
    if city_error:
        return {"summary":city_error,"goals":[],"stops":[],"recommended":[],
                "alternatives":[],"missing":[],"insights":[],
                "limits":["No location was changed because the city could not be verified."],
                "search_context":context,"status":data["status"]}
    # Explicit queries must not be reinterpreted as whatever tab is selected.
    generic = normalize(clean_question.strip()) in {"nearest","near me","nearby","find nearby","closest","show nearby","here"}
    active=body.active_category if generic else None
    results=plan(clean_question,data["places"],lat,lon,radius,
                 active=active,weather=data["weather"],
                 source_status=data["status"]["places"])
    return {**results,"search_context":context,"status":data["status"]}

@app.get("/",response_class=HTMLResponse)
async def root():
    with open(os.path.join(os.path.dirname(__file__),"index.html"),encoding="utf-8") as f:
        return f.read()
