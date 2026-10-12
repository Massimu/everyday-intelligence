import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from intent_agent import radius_from_text, explicit_city, city_candidate, clarification
from intelligence import infer_goals, plan
from main import app

class IntentTests(unittest.TestCase):
    def test_radius_units(self):
        self.assertEqual(radius_from_text("Pharmacy within 3 km"),3000)
        self.assertEqual(radius_from_text("Lidl within 750 meters"),750)
        self.assertEqual(radius_from_text("in einem Umkreis von 2 km"),2000)
        self.assertEqual(radius_from_text("supermarket"),None)
    def test_explicit_city(self):
        self.assertEqual(explicit_city("looking for a grill place in Braunschweig",["Braunschweig","Hannover"]),"Braunschweig")
        self.assertEqual(explicit_city("Lidl in Hannover",["Braunschweig","Hannover"]),"Hannover")
    def test_candidate_not_generic(self):
        self.assertIsNone(city_candidate("find a pharmacy near a park"))
        self.assertEqual(city_candidate("restaurant in Wolfsburg"),"Wolfsburg")
    def test_grill_ambiguous(self):
        a=clarification("looking for a grill place in Braunschweig","Braunschweig")
        self.assertIsNotNone(a)
        self.assertEqual(len(a["options"]),3)
        self.assertIn("Braunschweig",a["options"][0]["query"])
    def test_grill_explicit_type(self):
        self.assertIsNone(clarification("public grillplatz in Braunschweig","Braunschweig"))
        self.assertIsNone(clarification("grill restaurant in Braunschweig","Braunschweig"))
        self.assertIsNone(clarification("buy a grill in Braunschweig","Braunschweig"))
    def test_grill_never_generic_shopping(self):
        x=infer_goals("public barbecue area in Braunschweig",active="shopping")
        self.assertEqual(x[0].subtype,"bbq")
        self.assertEqual(x[0].category,"outdoor")
    def test_food_intent_overrides_active_tab(self):
        x=infer_goals("Find a restaurant",active="shopping")
        self.assertEqual(x[0].category,"food")
    def test_public_barbecue_area_not_neighborhood_report(self):
        data=[{"id":"node/7","name":"Öffentlicher Grillplatz","category":"outdoor",
               "subtype":"bbq","distance_m":400,"lat":52.27,"lon":10.53,
               "url":"https://www.openstreetmap.org/node/7"}]
        r=plan("Find a public barbecue area",data,52.2689,10.5268,3000)
        self.assertEqual(r["goals"],["Public barbecue area"])
        self.assertEqual(r["stops"][0]["name"],"Öffentlicher Grillplatz")
    def test_unnamed_barbecue_is_not_claimed_publicly_accessible(self):
        data=[
            {"id":"node/1","name":"Unnamed mapped location","category":"outdoor","subtype":"bbq",
             "access":"private","distance_m":50,"lat":52.269,"lon":10.527,"url":"https://www.openstreetmap.org/node/1"},
            {"id":"node/2","name":"Unnamed mapped location","category":"outdoor","subtype":"bbq",
             "access":None,"distance_m":500,"lat":52.27,"lon":10.53,"url":"https://www.openstreetmap.org/node/2"}]
        r=plan("Find a public barbecue area",data,52.2689,10.5268,3000)
        self.assertEqual(len(r["stops"]),1)
        self.assertEqual(r["stops"][0]["id"],"node/2")
        self.assertIn("unnamed",r["stops"][0]["name"].lower())
        self.assertIn("fire restrictions",r["caveat"])
    def test_no_false_grill_spot(self):
        places=[{"id":"node/1","name":"Verlockung","brand":None,"category":"shopping","subtype":"beauty",
                 "distance_m":225,"lat":52.269,"lon":10.527,"url":"https://www.openstreetmap.org/node/1"}]
        r=plan("public grillplatz",places,52.2689,10.5268,3000,active="shopping")
        self.assertEqual(r["stops"],[])
        self.assertEqual(r["missing"],["Public barbecue area"])
    def test_api_city_is_used(self):
        async def fake_data(lat,lon,radius):
            return {"places":[{"id":"node/42","name":"Lidl","category":"shopping","subtype":"supermarket",
                                "brand":"Lidl","lat":52.27,"lon":10.53,"distance_m":400,
                                "url":"https://www.openstreetmap.org/node/42"}],
                    "weather":None,"status":{"places":"saved OSM snapshot"}}
        with patch("main.get_data",new=AsyncMock(side_effect=fake_data)) as loaded:
            c=TestClient(app)
            r=c.post("/api/ask",json={"question":"Find Lidl in Braunschweig within 3 km","lat":52.3759,"lon":9.732,"radius":1000,"active_category":"outdoor"})
            self.assertEqual(r.status_code,200,r.text)
            data=r.json()
            self.assertEqual(data["search_context"]["city"],"Braunschweig")
            self.assertTrue(data["search_context"]["changed"])
            self.assertEqual(data["search_context"]["radius"],3000)
            self.assertEqual(data["stops"][0]["name"],"Lidl")
            args=loaded.call_args.args
            self.assertAlmostEqual(args[0],52.2689,places=3)
            self.assertEqual(args[2],3000)
    def test_api_ambiguous_question_changes_city_without_hallucinating(self):
        async def fake_data(lat,lon,radius):
            return {"places":[],"weather":None,"status":{"places":"saved OSM snapshot"}}
        with patch("main.get_data",new=AsyncMock(side_effect=fake_data)):
            c=TestClient(app)
            r=c.post("/api/ask",json={"question":"looking for a grill place in Braunschweig","lat":52.3759,"lon":9.732,"radius":2000,"active_category":"shopping"})
            self.assertEqual(r.status_code,200,r.text)
            data=r.json()
            self.assertTrue(data["clarification"])
            self.assertEqual(data["search_context"]["city"],"Braunschweig")
            self.assertEqual(data["stops"],[])
            self.assertNotIn("Verlockung",data["summary"])
if __name__=="__main__":
    unittest.main()
