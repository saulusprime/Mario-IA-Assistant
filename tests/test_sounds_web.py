"""Pagina Suoni della dashboard: catalogo, file per l'anteprima, assegnazioni, volume, suoni propri (archivio finto,
nessun lettore audio: durante i test non suona niente)."""
import importlib.util
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path

HAVE_WEB = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("bleak") is not None


def wav_bytes(seconds=0.2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x10\x00" * int(22050 * seconds))
    return buf.getvalue()


@unittest.skipUnless(HAVE_WEB, "servono flask e bleak (ambiente host)")
class SoundsWebTests(unittest.TestCase):
    def setUp(self):
        from mario.assistant.soundpack import SoundPack
        from mario.web import create_app
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "sounds"
        tmk = root / "tmk"
        items = []
        for game, desc, rel in (("smb", "Coin", "smb/smb_coin.wav"), ("sm64", "\"Yahoo!\"", "sm64/sm64_yahoo.wav"),
                                ("sm64", "Bowser's laugh", "sm64/sm64_bowser_laugh.wav")):
            (tmk / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmk / rel).write_bytes(wav_bytes())
            items.append({"section": "wav", "game": game, "category": "SFX", "description": desc, "file": rel})
        (tmk / "mid" / "smb").mkdir(parents=True)
        (tmk / "mid" / "smb" / "smb_over.mid").write_bytes(b"MThd\x00\x00\x00\x06")
        items.append({"section": "mid", "game": "smb", "category": "Super Mario Bros.", "description": "Overworld",
                      "file": "mid/smb/smb_over.mid"})
        (tmk / "catalogo.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        self.app = create_app(db_path=Path(self.tmp.name) / "t.sqlite3", start_worker=False, quiet_assistant=True)
        self.client = self.app.test_client()
        self.speaker = self.app.extensions["assistant"].speaker
        self.speaker.pack = SoundPack(root)
        self.speaker.player = None  # niente audio durante i test

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_catalog_and_files(self):
        r = self.client.get("/sounds")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Archivio", r.get_data(as_text=True))
        cat = self.client.get("/api/sounds/catalog?section=wav").get_json()
        self.assertEqual((cat["total"], cat["matches"], cat["sections"]), (4, 3, {"wav": 3, "mid": 1}))
        self.assertTrue(all(i["browser"] for i in cat["items"]))
        self.assertEqual([i["rel"] for i in self.client.get("/api/sounds/catalog?q=yahoo").get_json()["items"]],
                         ["tmk/sm64/sm64_yahoo.wav"])
        r = self.client.get("/api/sounds/file/tmk/smb/smb_coin.wav")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"RIFF"))
        r.close()
        for bad in ("tmk/../../t.sqlite3", "tmk/manca.wav", "..%2Ft.sqlite3"):
            self.assertEqual(self.client.get("/api/sounds/file/" + bad).status_code, 404, bad)
        if not self.speaker.pack.midi_synth():  # senza fluidsynth il MIDI non si converte
            self.assertEqual(self.client.get("/api/sounds/file/tmk/mid/smb/smb_over.mid").status_code, 415)

    def test_assignments_volume_style_and_play(self):
        rows = self.client.get("/api/sounds/assignments").get_json()["rows"]
        coin = next(r for r in rows if r["name"] == "coin")
        self.assertEqual([f["rel"] for f in coin["giochi"]["default"]], ["tmk/smb/smb_coin.wav"])
        r = self.client.post("/api/sounds/assign", json={"style": "giochi", "name": "coin",
                                                         "files": ["tmk/sm64/sm64_bowser_laugh.wav"]}).get_json()
        self.assertEqual(r["files"], ["tmk/sm64/sm64_bowser_laugh.wav"])
        self.assertEqual(self.speaker.sound_path("coin").name, "sm64_bowser_laugh.wav")  # l'assistente usa la scelta
        r = self.client.post("/api/sounds/assign", json={"style": "giochi", "name": "coin", "append": 1,
                                                         "files": "tmk/smb/smb_coin.wav"}).get_json()
        self.assertEqual(len(r["files"]), 2)
        self.assertEqual(self.client.post("/api/sounds/assign", json={"style": "giochi", "name": "Coin!",
                                                                      "files": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/sounds/assign", json={"style": "giochi", "name": "coin",
                                                                      "files": ["/etc/passwd"]}).status_code, 400)
        self.client.post("/api/sounds/assign", json={"style": "giochi", "name": "coin", "files": []})
        self.assertEqual(self.speaker.sound_path("coin").name, "smb_coin.wav")
        self.assertEqual(self.client.post("/api/sounds/volume", json={"volume": 80}).get_json()["volume"], 80)
        self.assertEqual(self.client.get("/api/sounds/assignments").get_json()["volume"], 80)
        self.client.post("/api/assistant/settings", json={"sound_style": "voce"})
        self.assertEqual(self.speaker.sound_style, "voce")
        self.assertEqual(self.speaker.sound_path("coin").name, "sm64_yahoo.wav")
        r = self.client.post("/api/sounds/play", json={"name": "coin"})
        self.assertEqual(r.status_code, 409)  # nessun lettore audio
        self.assertIn("lettore", r.get_json()["error"])

    def test_upload_assign_and_delete(self):
        data = {"file": (io.BytesIO(wav_bytes()), "risata.wav"), "description": "risata di Luca", "name": "risata",
                "style": "voce"}
        r = self.client.post("/api/sounds/upload", data=data, content_type="multipart/form-data").get_json()
        self.assertTrue(r["ok"], r)
        rel = r["item"]["rel"]
        self.assertTrue(rel.startswith("custom/risata-"))
        rows = self.client.get("/api/sounds/assignments").get_json()["rows"]
        risata = next(x for x in rows if x["name"] == "risata")
        self.assertEqual([f["rel"] for f in risata["voce"]["override"]], [rel])
        self.assertEqual(self.client.get("/api/sounds/catalog?section=custom").get_json()["matches"], 1)
        self.assertEqual(self.client.delete("/api/sounds/file/tmk/smb/smb_coin.wav").status_code, 400)  # solo i propri
        self.assertTrue(self.client.delete("/api/sounds/file/" + rel).get_json()["deleted"])
        self.assertEqual(self.client.get("/api/sounds/catalog?section=custom").get_json()["matches"], 0)
        self.assertEqual(self.client.post("/api/sounds/upload").status_code, 400)


if __name__ == "__main__":
    unittest.main()
