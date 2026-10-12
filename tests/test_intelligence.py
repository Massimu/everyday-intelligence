import unittest
from intelligence import infer_goals, plan, haversine

def place(name, cat, lat, lon, subtype, brand=None, access=None):
    return {"name":name,"category":cat,"subtype":subtype,
            "brand":brand,"access":access,"lat":lat,"lon":lon,
            "distance_m":haversine(52.2689,10.5268,lat,lon),
            "id":"node/"+name.lower().replace(" ","_"),
            "url":"https://www.openstreetmap.org/node/1"}

DATA=[
    place("Rose Nails","shopping",52.269,10.527,"beauty"),
    place("Lidl","shopping",52.271,10.528,"supermarket",brand="Lidl"),
    place("Rossmann","shopping",52.270,10.525,"chemist",brand="Rossmann"),
    place("Altstadt-Apotheke","pharmacy",52.271,10.527,"pharmacy"),
    place("Bibliothek","pharmacy",52.270,10.526,"library"),
    place("Park View","outdoor",52.272,10.529,"park"),
    place("City Museum","discovery",52.272,10.529,"museum"),
    place("Residents Parking","parking",52.269,10.527,"parking",access="private"),
    place("Parking garage","parking",52.270,10.528,"parking",access="yes"),
]

class IntelligenceTests(unittest.TestCase):
    def test_brand_exact(self):
        res=plan("lidl",DATA,52.2689,10.5268,3000)
        self.assertEqual(len(res["stops"]),1)
        self.assertEqual(res["stops"][0]["name"],"Lidl")
        self.assertNotIn("Rose Nails",res["summary"])
    def test_missing_brand_no_fake_substitution(self):
        res=plan("find Aldi",DATA,52.2689,10.5268,3000)
        self.assertFalse(res["stops"])
        self.assertIn("Aldi",res["missing"])
    def test_mixed(self):
        res=plan("Find a Lidl, a pharmacy and a park",DATA,52.2689,10.5268,3000)
        self.assertEqual(len(res["stops"]),3)
        self.assertSetEqual({s["name"] for s in res["stops"]},
                            {"Lidl","Altstadt-Apotheke","Park View"})
    def test_supermarket_does_not_pick_nails(self):
        res=plan("nearest supermarket",DATA,52.2689,10.5268,3000)
        self.assertEqual(res["stops"][0]["name"],"Lidl")
    def test_parking_private_excluded(self):
        res=plan("parking",DATA,52.2689,10.5268,3000)
        self.assertEqual(res["stops"][0]["name"],"Parking garage")
    def test_parking_and_park_distinct(self):
        goals=infer_goals("parking and park")
        self.assertSetEqual({g.category for g in goals},{"parking","outdoor"})
    def test_parking_first_mixed_route(self):
        res=plan("parking, pharmacy, museum",DATA,52.2689,10.5268,3000)
        self.assertEqual([p["category"] for p in res["stops"]][0],"parking")
    def test_neighborhood_report(self):
        res=plan("Describe this neighborhood",DATA,52.2689,10.5268,3000)
        self.assertIn("Neighborhood overview",res["summary"])
        self.assertTrue(any("Shopping" in s for s in res["insights"]))
    def test_active_filter(self):
        goals=infer_goals("nearest",active="pharmacy")
        self.assertEqual(goals[0].category,"pharmacy")
    def test_multiple_brands(self):
        res=plan("Lidl and Rossmann",DATA,52.2689,10.5268,3000)
        self.assertEqual({s["name"] for s in res["stops"]},{"Lidl","Rossmann"})
    def test_unavailable(self):
        res=plan("Lidl",[],52.2689,10.5268,3000,source_status="unavailable")
        self.assertFalse(res["stops"])
        self.assertIn("unavailable",res["summary"])
if __name__=="__main__": unittest.main()
