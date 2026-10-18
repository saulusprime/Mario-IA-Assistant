"""Servizio e API delle avventure: catalogo nel database, partita simulata, partite salvate, pagine."""
import importlib.util
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path

from mario.adventures import Adventure, validate
from mario.adventures.service import AdventureService
from mario.web.db import Database


class FakeWorker:
    def __init__(self):
        self.device = None
        self.pushed = []
        self.streams = []

    def open_stream(self):
        q = queue.Queue()
        self.streams.append(q)
        return q

    def push(self, ev):
        self.pushed.append(ev)

    def set_volume(self, level):
        return level

    def feed(self, ev):
        for q in self.streams:
            q.put(ev)


def wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "a.sqlite3")
        self.worker = FakeWorker()
        self.service = AdventureService(self.worker, self.db, speaker=None, quiet=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_catalog_and_crud(self):
        items = self.service.list()
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["name"], "Caccia al tesoro dei colori")
        self.assertEqual(items[1]["steps"], 4)
        new_id = self.service.save({"name": "Mia", "steps": [{"id": "x", "trigger": {"kind": "button"}}]})
        self.assertEqual(self.service.get(new_id)["data"]["steps"][0]["trigger"], {"kind": "button"})
        self.service.save({"name": "Mia 2", "steps": [{"id": "x", "trigger": {"kind": "button"}}]}, new_id)
        self.assertEqual(self.service.get(new_id)["name"], "Mia 2")
        with self.assertRaises(ValueError):
            self.service.save({"name": "rotta", "steps": [{"trigger": {"kind": "boh"}}]})
        self.assertEqual(self.service.delete(new_id), 1)
        lib = self.service.library()
        self.assertIn("tiles", lib)
        self.assertIn("levels", lib)
        self.assertFalse(any(t["owned"] for t in lib["tiles"]))

    def test_record_mode_builds_a_route(self):
        def tile(code, ts):
            return {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "ts": ts,
                    "data": code.to_bytes(2, "little") + (0x25).to_bytes(2, "little")}
        self.service.simulate(tile(0x00B8, 100.0))                  # prima della registrazione: ignorata
        self.assertEqual(self.service.record_start()["tiles"], [])
        for code, ts in ((0x00B8, 100.0), (0x00B8, 101.0), (0x0002, 104.0), (0xFFFF, 105.0), (0x00B7, 150.0)):
            self.service.simulate(tile(code, ts))
        self.assertEqual([t["name"] for t in self.service.record_status()["tiles"]], ["Start Mario", "Goomba", "Traguardo"])
        out = self.service.record_stop("Il mio percorso")
        draft = out["draft"]
        self.assertEqual(draft["name"], "Il mio percorso")
        self.assertEqual([s["trigger"] for s in draft["steps"]],
                         [{"kind": "barcode", "code": "0x00B8"}, {"kind": "barcode", "code": "0x0002"},
                          {"kind": "barcode", "code": "0x00B7"}])
        self.assertEqual([s["timeout"] for s in draft["steps"]], [0, 30, 138])   # 3 × il tempo, almeno 30 s
        self.assertEqual(validate(Adventure.from_dict(draft)), [])
        self.assertFalse(self.service.record_status()["recording"])

    def test_new_examples_reach_existing_databases(self):
        from mario.adventures.service import EXAMPLES_KEY, seed_examples
        db = Database(Path(self.tmp.name) / "vecchio.sqlite3")
        for name in ("Caccia al tesoro dei colori", "Percorso", "Quiz"):  # database di prima del conto
            db.save_adventure({"name": name, "steps": [{"id": "x", "trigger": {"kind": "button"}}]})
        self.assertEqual(seed_examples(db), 1)                               # solo l'esempio nuovo
        self.assertEqual(db.list_adventures()[-1]["name"], "Sfida a tempo nel livello")
        db.delete_adventure(db.list_adventures()[-1]["id"])
        self.assertEqual(seed_examples(db), 0)                               # cancellato: non torna
        self.assertEqual(len(json.loads(db.get_setting(EXAMPLES_KEY))), 4)
        self.assertEqual(seed_examples(self.db), 0)                          # database nuovo: già tutti

    def test_play_through_worker_stream_and_store_run(self):
        aid = self.service.save({"name": "Due passi", "steps": [
            {"id": "a", "title": "Salta", "trigger": {"kind": "gesture", "name": "salto"}, "points": 10},
            {"id": "b", "title": "Tasto", "trigger": {"kind": "button"}, "points": 5}]})
        st = self.service.start(aid)
        self.assertEqual((st["runner"]["state"], st["adventure_id"]), ("running", aid))
        self.worker.feed({"type": "STATE", "text": "x"})
        self.worker.feed({"type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1, "raw": "08 00 45 00 00 80 00 80"})
        self.assertTrue(wait_for(lambda: self.service.runner.index == 1))
        self.assertTrue(self.service.simulate({"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True}))
        self.assertEqual(self.service.runner.state, "finished")
        runs = self.service.runs()
        self.assertEqual((len(runs), runs[0]["score"], runs[0]["outcome"]), (1, 15, "completata"))
        run = self.db.get_run(runs[0]["id"])
        self.assertTrue(any(e["kind"] == "finish" for e in run["log"]))
        kinds = [e["kind"] for e in self.worker.pushed if e.get("type") == "ADVENTURE"]
        self.assertEqual(kinds[:2], ["start", "step"])
        # una seconda partita interrotta viene salvata come abbandonata
        self.service.start(aid)
        self.service.stop()
        self.assertEqual(self.service.runs()[0]["outcome"], "abbandonata")
        with self.assertRaises(ValueError):
            self.service.start(9999)


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
class AdventureApiTests(unittest.TestCase):
    def setUp(self):
        from mario.web import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tmp.name) / "t.sqlite3", start_worker=False, quiet_assistant=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_and_api(self):
        self.assertEqual(self.client.get("/adventures").status_code, 200)
        self.assertEqual(self.client.get("/adventures/play?id=1").status_code, 200)
        self.assertIn(b"/adventures", self.client.get("/").data)
        items = self.client.get("/api/adventures").get_json()["adventures"]
        self.assertEqual(len(items), 4)
        lib = self.client.get("/api/adventures/library").get_json()
        self.assertIn("barcode", lib["trigger_kinds"])
        r = self.client.post("/api/adventures/validate", json={"data": {"name": "x", "steps": [{"trigger": {"kind": "timer"}}]}}).get_json()
        self.assertTrue(any("timer" in p for p in r["problems"]))
        r = self.client.post("/api/adventures", json={"data": {"name": "Nuova", "steps": [{"id": "s", "trigger": {"kind": "button"}, "points": 3}]}}).get_json()
        aid = r["id"]
        self.assertEqual(self.client.get(f"/api/adventures/{aid}").get_json()["adventure"]["name"], "Nuova")
        exp = self.client.get(f"/api/adventures/{aid}/export")
        self.assertIn("attachment; filename=avventura-nuova.json", exp.headers["Content-Disposition"])
        r = self.client.post("/api/adventures/import", json={"json": exp.data.decode("utf-8")}).get_json()
        self.assertTrue(r["ok"])
        self.assertEqual(self.client.post("/api/adventures", json={"data": {"name": "rotta", "steps": [{"trigger": {"kind": "boh"}}]}}).status_code, 400)
        st = self.client.post("/api/adventure/start", json={"id": aid}).get_json()["status"]
        self.assertEqual(st["runner"]["state"], "running")
        r = self.client.post("/api/adventure/simulate", json={"what": "salto"}).get_json()
        self.assertFalse(r["progressed"])
        r = self.client.post("/api/adventure/simulate", json={"what": "pulsante"}).get_json()
        self.assertTrue(r["progressed"])
        self.assertEqual(r["status"]["runner"]["state"], "finished")
        runs = self.client.get("/api/adventure/runs").get_json()["runs"]
        self.assertEqual(runs[0]["score"], 3)
        run = self.client.get(f"/api/adventure/runs/{runs[0]['id']}").get_json()["run"]
        self.assertEqual(run["outcome"], "completata")
        page = self.client.get(f"/adventures/runs/{runs[0]['id']}")
        self.assertEqual(page.status_code, 200)
        html = page.data.decode("utf-8")
        for needle in ("🚩 inizio", "⭐ riuscito", "🏁 fine", 'id="rp-data"', "Gioca ancora"):
            self.assertIn(needle, html)
        self.assertEqual(self.client.get("/adventures/runs/999").status_code, 404)
        r = self.client.post("/api/adventure/simulate", json={"what": "livello_fine"}).get_json()
        self.assertFalse(r["progressed"])  # nessuna partita in corso
        self.assertTrue(self.client.post("/api/adventure/record", json={"action": "start"}).get_json()["recording"])
        self.client.post("/api/adventure/simulate", json={"what": "basetta_goomba"})
        r = self.client.post("/api/adventure/record", json={"action": "stop"}).get_json()
        self.assertEqual(r["draft"]["steps"][0]["title"], "Goomba")
        self.assertIn("Registra un percorso", self.client.get("/adventures").data.decode("utf-8"))
        self.assertEqual(self.client.delete(f"/api/adventures/{aid}").get_json()["deleted"], 1)
        self.assertEqual(self.client.get("/api/adventures/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
