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
        items = es.feed("[gioia] Ciao Leo! [ricor") + es.feed("da: il bambino si chiama Leo] [calma] Giochiamo.")
        self.assertEqual(items, [("emotion", "gioia"), ("text", "Ciao Leo! "), ("memory", "il bambino si chiama Leo"),
                                 ("emotion", "calma"), ("text", "Giochiamo.")])
        self.assertEqual(es.memories, ["il bambino si chiama Leo"])
        raw = "[gioia] Ciao Leo! [ricorda: il bambino si chiama Leo] [calma] Giochiamo."
        self.assertEqual(strip_tags(raw), "Ciao Leo! Giochiamo.")
        self.assertEqual(memory_tags(raw), ["il bambino si chiama Leo"])
        es = EmotionStream()
        self.assertEqual(es.feed("[ricorda: " + "x" * 300 + "] fine"), [("text", "[ricorda: " + "x" * 300 + "] fine")])

    def test_memory_stores(self):
        mem = ListMemory(["A"])
        self.assertEqual(mem.add("a"), 1)  # doppione, senza distinzione di maiuscole
        self.assertEqual(mem.add("Il bambino si chiama Leo."), 2)
        self.assertEqual([m["text"] for m in mem.list()], ["A", "Il bambino si chiama Leo"])
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
        backend = ScriptedBackend("[gioia] Ciao Leo, che bello conoscerti! [ricorda: il bambino si chiama Leo]")
        session = AssistantSession(lambda: backend, Speaker(enabled=False, engine=None), self.engine, emit=self.events.append)
        session.ask("Ciao, sono Leo")
        self.assertTrue(wait_idle(session))
        self.assertEqual(session.last_answer, "Ciao Leo, che bello conoscerti!")
        self.assertEqual([m["text"] for m in session.memory.list()], ["il bambino si chiama Leo"])
        self.assertIn("memory", self.kinds())
        self.assertIn("il bambino si chiama Leo", session.build_system_prompt())
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


class PiperTests(unittest.TestCase):
    def test_piper_command_and_detection(self):
        sp = Speaker(enabled=True, engine="piper", piper_model="/tmp/it_IT-x.onnx", rate=0)
        cmd = sp._command("Ciao", "gioia")  # gioia: +15 -> length_scale 1 - 15/200
        self.assertEqual(cmd[:3], ["piper", "--model", "/tmp/it_IT-x.onnx"])
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
        r = self.client.post("/api/assistant/memory", json={"text": "Il bambino si chiama Leo"}).get_json()
        self.assertTrue(r["ok"])
        mid = r["id"]
        self.assertEqual([m["text"] for m in self.client.get("/api/assistant/memory").get_json()["memory"]], ["Il bambino si chiama Leo"])
        self.assertIn("Leo", self.assistant.session.build_system_prompt())
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
