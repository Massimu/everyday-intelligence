"""Collect a small reusable POI cache from explicitly licensed OSM extracts.

Runs in GitHub Actions, not on each visitor request. Nothing is fabricated:
all features originate from the BBBike OSM extract and retain OSM object links.
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import osmium

CITIES = {
    "Braunschweig": (52.2689, 10.5268, "Braunschweig"),
    "Hannover": (52.3759, 9.732, "Hannover"),
}
DATA_FILE = Path("data/places.json")

def category(tags):
    if tags.get("amenity") == "parking":
        return "parking"
    if tags.get("shop") in ("supermarket", "convenience", "grocery", "chemist", "beauty"):
        return "shopping"
    if tags.get("amenity") in ("pharmacy", "clinic", "library", "doctors", "post_office", "townhall"):
        return "pharmacy"
    if tags.get("leisure") in ("park", "garden", "nature_reserve"):
        return "outdoor"
    if tags.get("tourism") in ("museum", "gallery", "attraction", "viewpoint") or tags.get("historic") in ("castle", "monument") or tags.get("amenity") in ("theatre", "arts_centre"):
        return "discovery"

class Collector(osmium.SimpleHandler):
    def __init__(self, latitude, longitude):
        super().__init__()
        self.latitude=latitude
        self.longitude=longitude
        self.places=[]
        self.seen=set()

    def collect(self, obj, kind, lat, lon):
        if not 47 < lat < 56 or not 5 < lon < 16:
            return
        # City extract covers a wider area; retain only 10 km from city center.
        if abs(lat-self.latitude)>0.093 or abs(lon-self.longitude)>0.16:
            return
        tags=obj.tags
        cat=category(tags)
        if cat is None:
            return
        ident=f"{kind}/{obj.id}"
        if ident in self.seen:
            return
        self.seen.add(ident)
        name=tags.get("name") or tags.get("name:de") or "Unnamed mapped location"
        self.places.append({
            "id":ident, "name":name, "category":cat,
            "lat":round(lat,6), "lon":round(lon,6),
            "brand":tags.get("brand"),"opening_hours":tags.get("opening_hours"),
            "subtype": tags.get("shop") or tags.get("amenity") or tags.get("leisure") or tags.get("tourism") or tags.get("historic"),
            "access": tags.get("access"),"fee":tags.get("fee"),"parking_type":tags.get("parking"),
            "wheelchair": tags.get("wheelchair"),
            "url":f"https://www.openstreetmap.org/{ident}",
        })

    def node(self, n):
        if n.location.valid():
            self.collect(n,"node",n.location.lat,n.location.lon)

    def way(self,w):
        if not any(tag.k in ("amenity","shop","leisure","tourism","historic") for tag in w.tags):
            return
        pts=[(n.location.lat,n.location.lon) for n in w.nodes if n.location.valid()]
        if pts:
            self.collect(w,"way",sum(x[0] for x in pts)/len(pts),sum(x[1] for x in pts)/len(pts))

def download(url, destination):
    print("Downloading",url,flush=True)
    req=Request(url,headers={"User-Agent":"EverydayIntelligence/0.3 public OSM archive cache"})
    with urlopen(req,timeout=140) as source,open(destination,"wb") as dest:
        while True:
            chunk=source.read(1024*1024)
            if not chunk:break
            dest.write(chunk)
    print("Bytes:",os.path.getsize(destination),flush=True)
    if os.path.getsize(destination)<10000:
        raise ValueError("Unexpectedly small OSM extract")

def main():
    try:
        previous=json.loads(DATA_FILE.read_text())
        results=previous.get("cities",{})
    except (OSError,ValueError):
        results={}
    successes=0
    for name,(lat,lon,directory) in CITIES.items():
        path=f"/tmp/{name}.osm.pbf"
        url=f"https://download.bbbike.org/osm/bbbike/{directory}/{directory}.osm.pbf"
        try:
            download(url,path)
            collector=Collector(lat,lon)
            collector.apply_file(path,locations=True)
            if len(collector.places)<20:
                raise ValueError(f"Too few mapped places for {name}: {len(collector.places)}")
            results[name]={
                "latitude":lat, "longitude":lon, "source_url":url,
                "updated_at":datetime.now(timezone.utc).isoformat(),
                "places":collector.places,
            }
            successes+=1
            print(name, "places:",len(collector.places),flush=True)
        except Exception as exc:
            print("SKIPPED",name,type(exc).__name__,str(exc)[:250],flush=True)
        finally:
            if os.path.exists(path):os.unlink(path)
    if not results:
        raise RuntimeError("No trustworthy OpenStreetMap snapshots available; refusing to publish empty data.")
    DATA_FILE.parent.mkdir(parents=True,exist_ok=True)
    data={"license":"ODbL-1.0","attribution":"© OpenStreetMap contributors, derived from BBBike extracts",
          "cities":results}
    DATA_FILE.write_text(json.dumps(data,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    print("Published",DATA_FILE,"successful city updates:",successes,flush=True)

if __name__=="__main__":
    main()
