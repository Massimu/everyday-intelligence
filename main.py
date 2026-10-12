"""Small noncommercial six-module city intelligence prototype."""
import asyncio, math, os, time
from collections import Counter
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

app=FastAPI(title="Everyday Intelligence",version="0.2")
CITIES={"Braunschweig":(52.2689,10.5268),"Hannover":(52.3759,9.732),"Berlin":(52.52,13.405),"Hamburg":(53.5511,9.9937),"Leipzig":(51.3397,12.3731),"München":(48.1374,11.5755)}
KINDS={"parking":"Parking","shopping":"Supermarkets and drugstores","neighborhood":"Neighborhood","outdoor":"Parks and gardens","pharmacy":"Pharmacies and services","discovery":"Culture and discovery"}
cache={}
def distance(a,b,c,d):
    p1,p2=math.radians(a),math.radians(c);dp=p2-p1;dl=math.radians(d-b)
    return round(12742000*math.asin(min(1,math.sqrt(math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2))))
def category(t):
    if t.get("amenity")=="parking":return "parking"
    if t.get("shop") in ("supermarket","convenience","grocery","chemist","beauty"):return "shopping"
    if t.get("amenity") in ("pharmacy","clinic","library","doctors","post_office","townhall"):return "pharmacy"
    if t.get("leisure") in ("park","garden","nature_reserve"):return "outdoor"
    if t.get("tourism") in ("museum","gallery","attraction","viewpoint") or t.get("historic") in ("castle","monument") or t.get("amenity") in ("theatre","arts_centre"):return "discovery"
async def get_data(lat,lon,radius):
    key=(round(lat,3),round(lon,3),radius);now=time.monotonic()
    if key in cache and now-cache[key][0]<3600:return cache[key][1]
    selections=['amenity="parking"','shop~"^(supermarket|convenience|grocery|chemist|beauty)$"','amenity~"^(pharmacy|clinic|library|doctors|post_office|townhall|theatre|arts_centre)$"','leisure~"^(park|garden|nature_reserve)$"','tourism~"^(museum|gallery|attraction|viewpoint)$"','historic~"^(castle|monument)$"']
    query="[out:json][timeout:25];("+ "".join(f'nwr[{s}](around:{radius},{lat},{lon});' for s in selections)+");out center 900;"
    places=[];status={"places":"unavailable","weather":"unavailable"}
    headers={"User-Agent":os.getenv("EVERYDAY_USER_AGENT","EverydayIntelligence-hobby-demo/0.2")}
    weather=None
    async with httpx.AsyncClient(timeout=32,headers=headers) as client:
        try:
            res=await client.post("https://overpass-api.de/api/interpreter",data={"data":query});res.raise_for_status()
            for e in res.json().get("elements",[]):
                t=e.get("tags",{});kind=category(t);center=e.get("center") or {};la=e.get("lat",center.get("lat"));lo=e.get("lon",center.get("lon"))
                if not kind or la is None or lo is None:continue
                d=distance(lat,lon,la,lo)
                if d>radius:continue
                places.append({"id":f"{e.get('type')}/{e.get('id')}","name":t.get("name") or t.get("name:de") or "Unnamed mapped location","category":kind,"lat":la,"lon":lo,"distance_m":d,"opening_hours":t.get("opening_hours"),"brand":t.get("brand"),"url":f"https://www.openstreetmap.org/{e.get('type')}/{e.get('id')}"})
            places.sort(key=lambda p:p["distance_m"]);status["places"]="live OSM snapshot"
        except Exception:
            if key in cache:places=cache[key][1]["places"];status["places"]="stale cached"
        try:
            r=await client.get("https://api.open-meteo.com/v1/forecast",params={"latitude":lat,"longitude":lon,"current":"temperature_2m,precipitation,wind_speed_10m","timezone":"Europe/Berlin"},timeout=12);r.raise_for_status();weather=r.json().get("current");status["weather"]="live forecast"
        except Exception:
            if key in cache:weather=cache[key][1]["weather"];status["weather"]="stale cached"
    data={"places":places,"weather":weather,"status":status,"updated_at":datetime.now(timezone.utc).isoformat(),"counts":dict(Counter(p["category"] for p in places))}
    cache[key]=(now,data);return data
@app.get("/api/health")
async def health():return {"status":"ok","version":"0.2"}
@app.get("/api/cities")
async def cities():return [{"name":k,"lat":v[0],"lon":v[1]} for k,v in CITIES.items()]
@app.get("/api/overview")
async def overview(lat:float=Query(52.2689,ge=47,le=55.1),lon:float=Query(10.5268,ge=5,le=16),radius:int=Query(1800,ge=500,le=3000)):
    return await get_data(lat,lon,radius)
class Ask(BaseModel):
    question:str=Field(min_length=2,max_length=400)
    lat:float=Field(ge=47,le=55.1)
    lon:float=Field(ge=5,le=16)
    radius:int=Field(default=1800,ge=500,le=3000)
@app.post("/api/ask")
async def ask(body:Ask):
    data=await get_data(body.lat,body.lon,body.radius);q=body.question.lower()
    words={"parking":("parking","parkhaus","parkplatz","parken"),"shopping":("shop","supermarket","drugstore","dm","rossmann","einkauf","drogerie"),"outdoor":("park","garden","outdoor","walk","spazier","grün"),"pharmacy":("pharmacy","apotheke","clinic","pharmacies"),"discovery":("museum","discover","culture","kultur","attraction","weekend","wochenende")}
    requested=[k for k,items in words.items() if any(w in q for w in items)]
    if not requested:requested=["shopping","outdoor","pharmacy"]
    picked=[];missing=[]
    for kind in requested:
        options=[p for p in data["places"] if p["category"]==kind]
        if options:picked.append(options[0])
        else:missing.append(KINDS[kind])
    text=" · ".join(f'{KINDS[p["category"]]}: {p["name"]} ({p["distance_m"]} m straight-line)' for p in picked)
    return {"summary":text or "No matching mapped places are available for this area.","recommended":picked,"missing":missing,"caveat":"OpenStreetMap coverage may be incomplete. Distances are straight-line estimates. Opening status, actual walking routes, prices and parking availability are not verified.","status":data["status"]}
@app.get("/",response_class=HTMLResponse)
async def root():
    with open(os.path.join(os.path.dirname(__file__),"index.html"),encoding="utf-8") as f:return f.read()
