"""Evoluzioni dell'assistente: ricordi, rotazione solo a terra, scossa = stop, limite di tempo, parole bloccate,
latenza della prima frase, voce piper, API e comandi CLI."""
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

from mario.assistant import AssistantBusy, AssistantSession, DemoBackend, EmotionStream, IntentMapper, LLMConfig, Speaker
from mario.assistant.behaviours import BehaviourEngine, NullActions
from mario.assistant.emotions import memory_tags, strip_tags
from mario.assistant.memory import DatabaseMemory, ListMemory, memory_prompt
from mario.assistant.tts import detect_engine
from mario.web.db import Database


def port_event(io_type, mode, data, ts=None):
    return {"ts": ts or time.time(), "type": "PORT_VALUE_SINGLE", "io_type": io_type, "mode": mode, "data": bytes(data)}


def wait_idle(session, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not session.busy:
            return True
        time.sleep(0.01)
    return False


class ScriptedBackend:
    model = "copione"

    def __init__(self, text):
        self.text = text

    def describe(self):
        return "copione"

    def stream(self, system, messages):
        self.system = system
        for word in self.text.split(" "):
            yield word + " "


class SilentSpeaker(Speaker):
    def _run(self):  # nessun processo: le frasi restano in coda (Mario "sta parlando")
        pass


class MemoryTagTests(unittest.TestCase):
    def test_memory_tag_split_across_chunks(self):
        es = EmotionStream()
        items = es.feed("[gioia] Ciao Luca! [ricor") + es.feed("da: il bambino si chiama Luca] [calma] Giochiamo.")
        self.assertEqual(items, [("emotion", "gioia"), ("text", "Ciao Luca! "), ("memory", "il bambino si chiama Luca"),
                                 ("emotion", "calma"), ("text", "Giochiamo.")])
        self.assertEqual(es.memories, ["il bambino si chiama Luca"])
        raw = "[gioia] Ciao Luca! [ricorda: il bambino si chiama Luca] [calma] Giochiamo."
        self.assertEqual(strip_tags(raw), "Ciao Luca! Giochiamo.")
        self.assertEqual(memory_tags(raw), ["il bambino si chiama Luca"])
        es = EmotionStream()
        self.assertEqual(es.feed("[ricorda: " + "x" * 300 + "] fine"), [("text", "[ricorda: " + "x" * 300 + "] fine")])

    def test_memory_stores(self):
        mem = ListMemory(["A"])
        self.assertEqual(mem.add("a"), 1)  # doppione, senza distinzione di maiuscole
        self.assertEqual(mem.add("Il bambino si chiama Luca."), 2)
        self.assertEqual([m["text"] for m in mem.list()], ["A", "Il bambino si chiama Luca"])
        self.assertIn("Cose che ricordi", memory_prompt(mem.list()))
        self.assertEqual(mem.delete(1), 1)
        self.assertEqual(mem.clear(), 1)
        self.assertEqual(memory_prompt([]), "")
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "m.sqlite3")
            dm = DatabaseMemory(db)
            mid = dm.add("Ama Yoshi")
            self.assertEqual(dm.add("ama yoshi"), mid)
            self.assertEqual([m["text"] for m in dm.list()], ["Ama Yoshi"])
            self.assertEqual(dm.delete(mid), 1)
            self.assertEqual(dm.clear(), 0)


class IntentExtTests(unittest.TestCase):
    def test_rotation_only_on_ground(self):
        m = IntentMapper()
        m.feed(port_event(0x49, 0, bytes([0xFF, 0xFF, 0xFF, 0xFF])))  # scanner: niente sotto i piedi
        self.assertTrue(m.context["lifted"])
        self.assertEqual(m.feed(port_event(0x47, 1, b"\x00\x01")), [])  # rotazione in mano: ignorata
        self.assertIn("sollevato", m.context_text())
        m.feed(port_event(0x49, 0, bytes([0xFF, 0xFF, 0x25, 0x00]), time.time() + 1))  # appoggiato sull'erba
        self.assertFalse(m.context["lifted"])
        self.assertEqual([i.name for i in m.feed(port_event(0x47, 1, b"\x00\x01", time.time() + 2))], ["rotazione"])
        m2 = IntentMapper()
        m2.rotation_only_on_ground = False
        m2.feed(port_event(0x49, 0, bytes([0xFF] * 4)))
        self.assertEqual([i.name for i in m2.feed(port_event(0x47, 1, b"\x00\x01"))], ["rotazione"])


class SessionExtTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.engine = BehaviourEngine(NullActions(), emit=self.events.append)

    def kinds(self):
        return [e["kind"] for e in self.events]

    def test_shake_stops_while_speaking_and_repeats_at_rest(self):
        speaker = SilentSpeaker(enabled=True, engine="spd-say")
        session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), speaker, self.engine, emit=self.events.append)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        self.assertFalse(speaker.idle)
        session.handle_event(port_event(0x47, 1, b"\x10\x00"))  # scossa mentre parla
        outcome = [e for e in self.events if e["kind"] == "intent"][-1]["outcome"]
        self.assertEqual(outcome, "fermato (scossa durante la risposta)")
        self.assertTrue(speaker.idle)
        session.handle_event(port_event(0x47, 1, b"\x10\x00", time.time() + 10))  # scossa a riposo
        self.assertEqual([e for e in self.events if e["kind"] == "intent"][-1]["outcome"], "ripeto l'ultima risposta")

    def test_memory_round_trip_in_prompt(self):
        backend = ScriptedBackend("[gioia] Ciao Luca, che bello conoscerti! [ricorda: il bambino si chiama Luca]")
        session = AssistantSession(lambda: backend, Speaker(enabled=False, engine=None), self.engine, emit=self.events.append)
        session.ask("Ciao, sono Luca")
        self.assertTrue(wait_idle(session))
        self.assertEqual(session.last_answer, "Ciao Luca, che bello conoscerti!")
        self.assertEqual([m["text"] for m in session.memory.list()], ["il bambino si chiama Luca"])
        self.assertIn("memory", self.kinds())
        self.assertIn("il bambino si chiama Luca", session.build_system_prompt())
        self.assertIn("[ricorda:", session.build_system_prompt())
        self.assertNotIn("ricorda", [s for s, _ in session.speaker.spoken][0].lower())
        session.forget()
        self.assertEqual(session.memory.list(), [])
        self.assertIn("memory_forgotten", self.kinds())
        session.configure_safety(memory_enabled=False)
        self.assertNotIn("[ricorda:", session.build_system_prompt())

    def test_blocked_words(self):
        backend = ScriptedBackend("[gioia] Parliamo di cavoli e di stelle!")
        session = AssistantSession(lambda: backend, Speaker(enabled=False, engine=None), self.engine,
                                   emit=self.events.append, blocked_words=["cavoli", "broccoli"])
        self.assertEqual(session.ask("Mi piacciono i Broccoli"), 0)  # non parte nessun turno
        self.assertIn("blocked", self.kinds())
        self.assertEqual(session.turn, 0)
        self.assertIn("preferisco non parlare", session.speaker.spoken[-1][0])
        session.ask("Di cosa parliamo?")
        self.assertTrue(wait_idle(session))
        self.assertEqual(session.last_answer, "Parliamo di … e di stelle!")
        self.assertEqual(session.speaker.spoken[-1][0], "Parliamo di … e di stelle!")
        self.assertIn("cavoli", session.history[-1]["content"])  # la cronologia resta fedele al modello

    def test_time_limit_and_first_sentence(self):
        session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), Speaker(enabled=False, engine=None),
                                   self.engine, emit=self.events.append, session_limit=0.2)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        done = [e for e in self.events if e["kind"] == "done"][-1]
        self.assertIsNotNone(done["first_sentence"])
        self.assertLessEqual(done["first_sentence"], done["elapsed"])
        time.sleep(0.25)
        with self.assertRaises(AssistantBusy):
            session.ask("ancora")
        self.assertTrue(session.time_up)
        self.assertFalse(session.listening)
        self.assertIn("time_up", self.kinds())
        session.handle_event(port_event(0x47, 1, b"\x00\x80"))
        self.assertEqual([e for e in self.events if e["kind"] == "intent"][-1]["outcome"], "ignorato: ascolto sospeso")
        self.assertEqual(session.status()["session_limit"], 0.2)
        session.reset()  # ricomincia il conto
        self.assertFalse(session.time_up)
        self.assertTrue(session.listening)
        session.ask("ciao di nuovo")
        self.assertTrue(wait_idle(session))
        self.assertEqual(session.turn, 2)
        session.configure_safety(session_limit=0)
        time.sleep(0.25)
        session.ask("senza limite")
        self.assertTrue(wait_idle(session))
        self.assertEqual(session.turn, 3)


class ExclamationTests(unittest.TestCase):
    """{letsago} nelle risposte: riconosciuto anche spezzato, suonato al suo posto, mai letto né salvato nel diario."""

    def setUp(self):
        import json
        import wave
        from mario.assistant.soundpack import SoundPack
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        items = []
        for game, desc, rel in (("sm64", "\"Let's-a go!\"", "sm64/letsago.wav"), ("sm64", "\"Mamma-mia\"", "sm64/mammamia.wav"),
                                ("sm64", "\"Wa-ha!\"", "sm64/waha.wav")):
            path = root / "tmk" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * 800)
            items.append({"section": "wav", "game": game, "description": desc, "file": rel})
        (root / "tmk" / "catalogo.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        calls = self.calls = []

        class RecordingSpeaker(Speaker):  # registra frasi ed esclamazioni invece di suonarle
            def speak(self, text, emotion=None):
                calls.append(("frase", text))

            def speak_sound(self, name, style="voce"):
                calls.append(("suono", name))

        self.speaker = RecordingSpeaker(enabled=True, engine="spd-say", sounds=False, soundpack=SoundPack(root))
        self.events = []
        self.engine = BehaviourEngine(NullActions(), emit=lambda ev: None)

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, text, **kw):
        backend = ScriptedBackend(text)
        session = AssistantSession(lambda: backend, self.speaker, self.engine, emit=self.events.append, **kw)
        return session, backend

    def test_stream_parser_and_strip(self):
        es = EmotionStream()
        items = es.feed("[gioia] {lets") + es.feed("ago} Ciao! {Non è un nome} e {mamma") + es.feed("mia} fine")
        self.assertEqual(items, [("emotion", "gioia"), ("sound", "letsago"), ("text", "Ciao! {Non è un nome} e "),
                                 ("sound", "mammamia"), ("text", "fine")])
        self.assertEqual(es.sounds, ["letsago", "mammamia"])
        es = EmotionStream()
        self.assertEqual(es.feed("{" + "x" * 40 + " testo"), [("text", "{" + "x" * 40 + " testo")])
        self.assertEqual(strip_tags("[gioia] {letsago} Ciao! [calma] {wahoo} Fine."), "Ciao! Fine.")
        from mario.assistant.emotions import sound_tags
        self.assertEqual(sound_tags("{letsago} Ciao {wahoo}"), ["letsago", "wahoo"])

    def test_session_plays_exclamations_in_place(self):
        session, backend = self.session("[gioia] {letsago} Ciao! Sono Mario. {boh} {mammamia} {wahoo} [calma] Fine.")
        prompt = session.build_system_prompt()
        self.assertIn("{letsago} = \"Let's-a go!\"", prompt)
        self.assertIn("{wahoo}", prompt)
        self.assertNotIn("{yippee}", prompt)  # nessun file: non viene offerta
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        # al massimo due per risposta, nomi sconosciuti ignorati, frase in corso detta prima dell'esclamazione
        self.assertEqual(self.calls, [("suono", "letsago"), ("frase", "Ciao!"), ("frase", "Sono Mario."),
                                      ("suono", "mammamia"), ("frase", "Fine.")])
        done = [e for e in self.events if e["kind"] == "done"][-1]
        self.assertEqual(done["text"], "Ciao! Sono Mario. Fine.")
        self.assertIn("🔊 Let's-a go!", done["display"])
        self.assertEqual([e["name"] for e in self.events if e["kind"] == "sound"], ["letsago", "mammamia"])
        self.assertEqual(session.last_answer, "Ciao! Sono Mario. Fine.")
        self.assertIn("{letsago}", backend.system)  # la regola era nel prompt di sistema

    def test_disabled_exclamations_are_silent(self):
        session, backend = self.session("{letsago} Ciao!", exclamations=False)
        self.assertNotIn("graffe", session.build_system_prompt())
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        self.assertEqual(self.calls, [("frase", "Ciao!")])  # niente suono, e la parola tra graffe non viene letta


class PiperTests(unittest.TestCase):
    def test_piper_command_and_detection(self):
        sp = Speaker(enabled=True, engine="piper", piper_model="/tmp/it_IT-x.onnx", rate=0)
        cmd = sp._command("Ciao", "gioia")  # gioia: +15 -> length_scale 1 - 15/200
        self.assertEqual(Path(cmd[0]).name, "piper")  # nel PATH o in .venv-openvoice/bin
        self.assertEqual(cmd[1:3], ["--model", "/tmp/it_IT-x.onnx"])
        self.assertEqual(cmd[cmd.index("--length_scale") + 1], "0.93")
        sp.configure(rate=-100)
        self.assertEqual(sp._command("x", "sonno")[4], "1.5")  # -100 bloccato: 1 + 100/200
        self.assertEqual(sp.status()["piper_model"], "/tmp/it_IT-x.onnx")
        self.assertNotEqual(detect_engine("/sicuramente/non/esiste.onnx"), "piper")
        sp2 = Speaker(enabled=False, engine=None)
        sp2.engine = "spd-say"
        sp2.configure(piper_model="/non/esiste.onnx")
        self.assertNotEqual(sp2.engine, "piper")


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
class AssistantExtApiTests(unittest.TestCase):
    def setUp(self):
        from mario.web import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tmp.name) / "t.sqlite3", start_worker=False, quiet_assistant=True)
        self.client = self.app.test_client()
        self.assistant = self.app.extensions["assistant"]
        self.client.post("/api/assistant/settings", json={"backend": "demo"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_memory_endpoints_and_settings(self):
        r = self.client.post("/api/assistant/memory", json={"text": "Il bambino si chiama Luca"}).get_json()
        self.assertTrue(r["ok"])
        mid = r["id"]
        self.assertEqual([m["text"] for m in self.client.get("/api/assistant/memory").get_json()["memory"]], ["Il bambino si chiama Luca"])
        self.assertIn("Luca", self.assistant.session.build_system_prompt())
        self.assertEqual(self.client.delete(f"/api/assistant/memory/{mid}").get_json()["deleted"], 1)
        self.client.post("/api/assistant/memory", json={"text": "uno"})
        self.client.post("/api/assistant/memory", json={"text": "due"})
        self.assertEqual(self.client.delete("/api/assistant/memory").get_json()["deleted"], 2)
        self.assertEqual(self.client.post("/api/assistant/memory", json={"text": ""}).status_code, 400)
        s = self.client.post("/api/assistant/settings", json={"session_minutes": "15", "blocked_words": "cavoli, broccoli",
                                                                 "memory": 0, "piper_model": ""}).get_json()["settings"]
        self.assertEqual((s["session_minutes"], s["blocked_words"], s["memory"]), ("15", "cavoli, broccoli", False))
        self.assertEqual(self.assistant.session.session_limit, 900.0)
        self.assertEqual(self.assistant.session.blocked_words, ["cavoli", "broccoli"])
        self.assertFalse(self.assistant.session.memory_enabled)
        st = self.client.get("/api/assistant/status").get_json()["status"]
        self.assertEqual(st["session_limit"], 900.0)

    def test_voice_reference_upload_and_settings(self):
        import io as _io
        import wave as _wave

        def wav_bytes(seconds):
            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"\x10\x00" * int(16000 * seconds))
            return buf.getvalue()

        r = self.client.post("/api/assistant/voice-reference", data={"file": (_io.BytesIO(wav_bytes(4)), "voce.wav")},
                             content_type="multipart/form-data").get_json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["seconds"], 4.0)
        saved = Path(r["path"])
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.parent, Path(self.tmp.name) / "voices")
        r = self.client.post("/api/assistant/voice-reference", data={"file": (_io.BytesIO(wav_bytes(1)), "corta.wav")},
                             content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("troppo corta", r.get_json()["error"])
        self.assertEqual(self.client.post("/api/assistant/voice-reference").status_code, 400)
        # impostazioni: tau e marchio salvati; il riferimento c'è ma OpenVoice no (o sì, sull'host): lo stato lo dice
        s = self.client.post("/api/assistant/settings", json={"openvoice_reference": str(saved), "openvoice_tau": "0.5",
                                                                 "openvoice_watermark": 1}).get_json()["settings"]
        self.assertEqual((s["openvoice_reference"], s["openvoice_tau"], s["openvoice_watermark"]), (str(saved), "0.5", True))
        sp = self.assistant.speaker.status()
        self.assertEqual((sp["openvoice_reference"], sp["openvoice_tau"]), (str(saved), 0.5))
        self.assertIn("openvoice", sp)
        self.client.post("/api/assistant/settings", json={"openvoice_reference": ""})
        self.assertEqual(self.assistant.speaker.status()["openvoice"]["state"], "spento")

    def test_simulate_lifted_rotation_and_diary_csv(self):
        self.client.post("/api/assistant/simulate", json={"what": "sollevato"})
        r = self.client.post("/api/assistant/simulate", json={"what": "rotazione"}).get_json()
        self.assertEqual(r["intents"], [])
        self.client.post("/api/assistant/simulate", json={"what": "terreno_lava"})
        r = self.client.post("/api/assistant/simulate", json={"what": "rotazione"}).get_json()
        self.assertEqual([i["name"] for i in r["intents"]], ["rotazione"])
        self.assertTrue(wait_idle(self.assistant.session))
        csv_text = self.client.get("/api/assistant/history.csv").data.decode("utf-8-sig")
        self.assertTrue(csv_text.startswith("id;data_ora;turno;chi"))
        self.assertIn("sensore", csv_text)
        self.assertIn("Mario", csv_text)
        page = self.client.get("/assistant")
        self.assertIn(b"Diario per i genitori", page.data)
        self.assertIn(b'id="memory-list"', page.data)
        acc = self.client.get("/accessories")
        self.assertIn(b"Verificato", acc.data)
        self.assertIn(b"12/10/2026", acc.data)


@unittest.skipUnless(importlib.util.find_spec("bleak"), "mario.cli importa bleak")
class CliMemoryTests(unittest.TestCase):
    def test_memory_commands(self):
        from mario import cli
        speaker = Speaker(enabled=False, engine=None)
        session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), speaker, BehaviourEngine(NullActions()))
        self.assertEqual(cli.handle_assistant_command(session, speaker, "/ricordi")[1], "nessun ricordo")
        self.assertIn("salvato", cli.handle_assistant_command(session, speaker, "/ricorda Ama le stelle")[1])
        self.assertIn("Ama le stelle", cli.handle_assistant_command(session, speaker, "/ricordi")[1])
        self.assertEqual(cli.handle_assistant_command(session, speaker, "/dimentica 1")[1], "1 ricordo cancellato")
        self.assertEqual(cli.handle_assistant_command(session, speaker, "/dimentica x")[1], "uso: /dimentica [numero|tutto]")
        self.assertEqual(cli.handle_assistant_command(session, speaker, "/dimentica")[1], "0 ricordi cancellati")
        args = cli.build_parser().parse_args(["assistant", "--no-mario", "--minutes", "20", "--block", "a,b", "--no-memory"])
        self.assertEqual((args.minutes, args.block, args.no_memory, args.no_db), (20.0, "a,b", True, False))


if __name__ == "__main__":
    unittest.main()
