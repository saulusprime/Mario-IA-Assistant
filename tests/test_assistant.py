"""Test dell'assistente (Mario come I/O di un LLM): senza rete, senza Bluetooth, senza voce."""
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

from mario.assistant import (
    AssistantBusy, AssistantSession, DemoBackend, EmotionStream, IntentMapper, LLMConfig, SentenceBuffer, Speaker,
    make_backend, strip_tags,
)
from mario.assistant.behaviours import BehaviourEngine, NullActions
from mario.assistant.llm import LLMError
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


class RecordingActions(NullActions):
    def __init__(self):
        self.calls = []

    def connected(self):
        return True

    def get_volume(self):
        return 50

    def set_volume(self, level):
        self.calls.append(("volume", level))
        return level

    def hub_action(self, action):
        self.calls.append(("action", action))

    def port_output(self, port, subcommand, payload):
        self.calls.append(("port", port, payload.hex()))


class FailingBackend:
    model = ""

    def describe(self):
        return "guasto"

    def stream(self, system, messages):
        raise LLMError("chiave mancante")
        yield  # pragma: no cover


class EmotionTests(unittest.TestCase):
    def test_tags_split_across_chunks(self):
        es = EmotionStream()
        items = es.feed("[gio") + es.feed("ia] Yahoo! [sorp") + es.feed("resa] Oh! [boh] x [Felice] fine")
        self.assertEqual(items, [("emotion", "gioia"), ("text", "Yahoo! "), ("emotion", "sorpresa"),
                                 ("text", "Oh! [boh] x "), ("emotion", "gioia"), ("text", "fine")])
        self.assertEqual(es.seen, ["gioia", "sorpresa", "gioia"])
        self.assertEqual(es.current, "gioia")

    def test_long_bracket_is_text_and_flush(self):
        es = EmotionStream()
        self.assertEqual(es.feed("[questa è una parentesi molto lunga che non è un tag"),
                         [("text", "[questa è una parentesi molto lunga che non è un tag")])
        es = EmotionStream()
        self.assertEqual(es.feed("ciao ["), [("text", "ciao ")])
        self.assertEqual(es.flush(), [("text", "[")])

    def test_strip_tags(self):
        self.assertEqual(strip_tags("[gioia] Yahoo! [calma] Ok. [x] resta"), "Yahoo! Ok. [x] resta")

    def test_sentence_buffer(self):
        sb = SentenceBuffer()
        self.assertEqual(sb.feed("Ciao a tutti! Come va? Io bene... davvero. Ultima"),
                         ["Ciao a tutti!", "Come va?", "Io bene... davvero."])
        self.assertEqual(sb.flush(), "Ultima")
        sb = SentenceBuffer()
        self.assertEqual(sb.feed("Prima riga\nSeconda. Terza"), ["Prima riga", "Seconda."])
        self.assertEqual(sb.feed(" parte."), [])  # senza spazio dopo: aspetta
        self.assertEqual(sb.feed(" Fine"), ["Terza parte."])
        self.assertEqual(sb.flush(), "Fine")


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.m = IntentMapper()

    def test_gesture_with_cooldown(self):
        t0 = 1000.0
        first = self.m.feed(port_event(0x47, 1, b"\x00\x80", t0))
        self.assertEqual([(i.kind, i.name) for i in first], [("prompt", "salto")])
        self.assertEqual(self.m.feed(port_event(0x47, 1, b"\x00\x80", t0 + 1)), [])
        self.assertEqual([i.name for i in self.m.feed(port_event(0x47, 1, b"\x00\x80", t0 + 3))], ["salto"])
        self.assertEqual(self.m.context["gesture"], "salto")

    def test_shake_is_control_and_bump_disabled(self):
        self.assertEqual([(i.kind, i.text) for i in self.m.feed(port_event(0x47, 1, b"\x11\x00"))],
                         [("control", "repeat")])
        self.m.set_rule("urto", enabled=True)
        self.assertEqual([i.name for i in self.m.feed(port_event(0x47, 1, b"\x01\x00", time.time() + 10))], ["urto"])

    def test_pants_first_value_is_context_only(self):
        self.assertEqual(self.m.feed(port_event(0x4A, 0, b"\x21")), [])
        self.assertEqual(self.m.context["pants_name"], "Mario normale")
        change = self.m.feed(port_event(0x4A, 0, b"\x12"))
        self.assertEqual(len(change), 1)
        self.assertIn("Fuoco", change[0].text)
        self.assertIn("Sei Mario Fuoco", self.m.context_text())

    def test_scanner_tile_and_color(self):
        tile = self.m.feed(port_event(0x49, 0, bytes([0xB8, 0x00, 0x25, 0x00])))
        self.assertEqual([i.name for i in tile], ["basetta"])
        self.assertIn("Start Mario", tile[0].text)
        self.assertIn("Si parte", tile[0].text)
        self.assertEqual(self.m.context["color_name"], "verde")
        lava = self.m.feed(port_event(0x49, 0, bytes([0xFF, 0xFF, 0x15, 0x00])))
        self.assertEqual([i.name for i in lava], ["terreno"])
        self.assertIn("lava", lava[0].text)
        # stesso colore di nuovo: niente; basetta sconosciuta dopo la pausa: prompt generico
        self.assertEqual(self.m.feed(port_event(0x49, 0, bytes([0xFF, 0xFF, 0x15, 0x00]))), [])
        later = self.m.feed(port_event(0x49, 0, bytes([0xCD, 0xAB, 0xFF, 0xFF]), time.time() + 10))
        self.assertIn("sconosciuta 0xABCD", later[0].text)

    def test_worker_style_raw_event(self):
        ev = {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "raw": "08 00 45 01 02 00 25 00"}
        self.assertEqual([i.name for i in self.m.feed(ev)], ["basetta"])
        self.assertEqual(self.m.context["tile_name"], "Goomba")

    def test_button_and_battery(self):
        self.assertEqual([i.text for i in self.m.feed({"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True})],
                         ["toggle_listen"])
        self.assertEqual(self.m.feed({"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": False}), [])
        self.assertEqual(self.m.feed({"type": "HUB_PROPERTIES", "prop": "BATTERY_VOLTAGE", "value": 80}), [])
        low = self.m.feed({"type": "HUB_PROPERTIES", "prop": "BATTERY_VOLTAGE", "value": 10})
        self.assertEqual([i.name for i in low], ["batteria"])
        self.assertEqual(self.m.feed({"type": "HUB_PROPERTIES", "prop": "BATTERY_VOLTAGE", "value": 9}), [])
        self.assertEqual(self.m.context["battery"], 9)

    def test_coin_rule_when_enabled(self):
        self.m.set_rule("moneta", enabled=True)
        self.assertEqual(self.m.feed(port_event(0x46, 2, bytes([0x61, 0x38, 0x01, 0x00]))), [])
        coin = self.m.feed(port_event(0x46, 2, bytes([0x61, 0x38, 0x02, 0x00])))
        self.assertEqual([i.text for i in coin], ["Ho raccolto una moneta, sono a 2!"])


class LLMTests(unittest.TestCase):
    def test_auto_backend_without_keys_is_demo(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "OPENAI_BASE_URL")}
        try:
            self.assertEqual(LLMConfig().resolved_backend(), "demo")
            self.assertIsInstance(make_backend(LLMConfig()), DemoBackend)
            with self.assertRaises(LLMError):
                make_backend(LLMConfig(backend="anthropic"))
            with self.assertRaises(LLMError):
                make_backend(LLMConfig(backend="openai", base_url="http://localhost:11434/v1"))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_demo_streams_tagged_answer(self):
        text = "".join(DemoBackend(LLMConfig(demo_delay=0)).stream("sys", [{"role": "user", "content": "Ho fatto un salto"}]))
        self.assertTrue(text.startswith("[gioia]"))
        self.assertIn("salto", text.lower())
        self.assertEqual(LLMConfig(api_key="sk-ant-1234567890abcd").to_dict()["api_key"], "sk-ant…abcd")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.speaker = Speaker(enabled=False, engine=None)
        self.actions = RecordingActions()
        self.engine = BehaviourEngine(self.actions, emit=self.events.append)
        self.messages = []
        self.session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), self.speaker, self.engine,
                                        emit=self.events.append, on_message=self.messages.append)

    def kinds(self):
        return [e["kind"] for e in self.events]

    def test_typed_turn(self):
        self.session.ask("Ciao Mario!")
        self.assertTrue(wait_idle(self.session))
        kinds = self.kinds()
        for k in ("user", "start", "emotion", "delta", "sentence", "done"):
            self.assertIn(k, kinds)
        self.assertEqual(self.session.last_answer, "Ciao! Sono Mario, piacere di conoscerti! Vuoi giocare con me?")
        self.assertEqual([s for s, _ in self.speaker.spoken],
                         ["Ciao!", "Sono Mario, piacere di conoscerti!", "Vuoi giocare con me?"])
        self.assertEqual([e for _, e in self.speaker.spoken], ["gioia", "gioia", "calma"])
        self.assertEqual(len(self.session.history), 2)
        self.assertEqual([m["role"] for m in self.messages], ["user", "assistant"])
        self.assertEqual(self.session.emotion, "calma")

    def test_behaviour_script_follows_emotion(self):
        self.engine.express("gioia")
        time.sleep(0.9)
        self.assertEqual(self.actions.calls[:2], [("volume", 72), ("volume", 50)])  # base 50: su a 72, poi base
        self.engine.express("sonno")
        time.sleep(0.2)
        self.engine.express("calma")  # interrompe lo script precedente
        time.sleep(0.4)
        self.assertEqual(self.actions.calls[2:], [("volume", 5), ("volume", 50)])
        from mario.assistant.behaviours import volume_level
        self.assertEqual((volume_level("up", 100), volume_level("up", 30), volume_level(0.1, 100), volume_level("base", 42)),
                         (100, 45, 10, 42))

    def test_shutdown_restores_and_disables(self):
        self.engine.express("sonno")
        time.sleep(0.2)
        self.engine.shutdown(timeout=2.0)
        self.assertEqual(self.actions.calls[-1], ("volume", 50))
        self.assertFalse(self.engine.enabled)
        self.engine.express("gioia")
        time.sleep(0.3)
        self.assertEqual(self.actions.calls[-1], ("volume", 50))  # disattivato: nessuna azione

    def test_behaviours_follow_speech_when_voice_is_on(self):
        class SilentSpeaker(Speaker):
            def _run(self):  # il thread della voce esce subito: le frasi restano in coda, nessun processo
                pass

        speaker = SilentSpeaker(enabled=True, engine="spd-say")
        session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), speaker, self.engine,
                                   emit=self.events.append)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        time.sleep(0.2)
        self.assertEqual(self.actions.calls, [])  # niente volume: aspetta che la frase venga pronunciata
        self.assertEqual(speaker._queue.qsize(), 3)
        speaker.on_start("Ciao!", "gioia")  # ciò che farebbe il thread della voce all'inizio della frase
        time.sleep(0.3)
        self.assertEqual(self.actions.calls[0], ("volume", 72))
        self.assertIn("speaking", self.kinds())

    def test_sensor_intents_wait_for_speech_and_merge(self):
        class SilentSpeaker(Speaker):
            def _run(self):
                pass

        speaker = SilentSpeaker(enabled=True, engine="spd-say")
        session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), speaker, self.engine,
                                   emit=self.events.append)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        self.assertFalse(speaker.idle)  # le frasi restano in coda: Mario "sta parlando"
        session.handle_event(port_event(0x47, 1, b"\x00\x80"))        # salto
        session.handle_event(port_event(0x49, 0, bytes([0xB8, 0, 0x25, 0])))  # basetta Start
        outcomes = [e["outcome"] for e in self.events if e["kind"] == "intent"]
        self.assertEqual(outcomes[-2:], ["in attesa: Mario sta ancora parlando"] * 2)
        self.assertEqual([i.name for i in session.pending], ["salto", "basetta"])
        self.assertEqual(session.turn, 1)
        # la voce finisce: la coda si svuota e l'ultima frase termina -> un solo prompt unito
        while not speaker._queue.empty():
            speaker._queue.get_nowait()
        speaker.on_end("ultima frase", "calma")
        self.assertTrue(wait_idle(session))
        self.assertEqual(session.turn, 2)
        sent = session.history[-2]["content"]
        self.assertIn("Nel frattempo sono successe più cose", sent)
        self.assertIn("salto", sent)
        self.assertIn("Start Mario", sent)
        self.assertIn("(Rispondi con una frase breve, al massimo due.)", sent)
        self.assertEqual(session.pending, [])
        # eventi vecchi vengono scartati
        session.pending = [__import__("mario.assistant.intents", fromlist=["Intent"]).Intent("prompt", "salto", "x", "gesto: salto", 1, time.time() - 60)]
        speaker._queue.queue.clear()
        speaker.on_end("fine", "calma")
        time.sleep(0.1)
        self.assertEqual(session.turn, 2)
        self.assertIn("intent_dropped", self.kinds())

    def test_typed_message_cuts_speech(self):
        class SilentSpeaker(Speaker):
            def _run(self):
                pass

        speaker = SilentSpeaker(enabled=True, engine="spd-say")
        session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), speaker, self.engine,
                                   emit=self.events.append)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        self.assertGreater(speaker._queue.qsize(), 0)
        session.ask("raccontami una storia")  # da tastiera: la voce in corso viene tagliata
        self.assertTrue(wait_idle(session))
        self.assertEqual([s for s, _ in speaker.spoken][-3:][0][:3], "Mmm")  # la storia parte da "Mmm, vediamo."

    def test_sensor_turn_and_context_prefix(self):
        self.session.handle_event(port_event(0x4A, 0, b"\x21"))
        self.session.handle_event(port_event(0x47, 1, b"\x00\x80"))
        self.assertTrue(wait_idle(self.session))
        self.assertIn("intent", self.kinds())
        user = [e for e in self.events if e["kind"] == "user"][0]
        self.assertTrue(user["sensor"])
        self.assertEqual(user["source"], "gesto: salto")
        sent = self.session.history[0]["content"]
        self.assertTrue(sent.startswith("(Stato del corpo: pantaloni: Mario normale"))
        self.assertIn("[sensore: gesto: salto] Ho appena fatto un salto!", sent)
        self.assertIn("Yahoo", self.session.last_answer)

    def test_busy_and_listening(self):
        slow = DemoBackend(LLMConfig(demo_delay=0.05))
        session = AssistantSession(lambda: slow, self.speaker, self.engine, emit=self.events.append)
        session.ask("raccontami una storia")
        time.sleep(0.05)
        with self.assertRaises(AssistantBusy):
            session.ask("altro")
        session.handle_event(port_event(0x47, 1, b"\x00\x80"))
        outcome = [e for e in self.events if e["kind"] == "intent"][-1]["outcome"]
        self.assertEqual(outcome, "in attesa: Mario sta ancora parlando")
        self.assertEqual([i.name for i in session.pending], ["salto"])
        session.stop()  # svuota anche gli intenti in attesa
        self.assertTrue(wait_idle(session))
        self.assertIn("stopped", self.kinds())
        self.assertEqual(session.pending, [])
        session.set_listening(False)
        session.handle_event(port_event(0x47, 1, b"\x00\x80", time.time() + 10))
        self.assertEqual([e for e in self.events if e["kind"] == "intent"][-1]["outcome"], "ignorato: ascolto sospeso")
        # il pulsante riattiva l'ascolto
        session.handle_event({"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True})
        self.assertTrue(session.listening)

    def test_error_from_backend(self):
        session = AssistantSession(lambda: FailingBackend(), self.speaker, self.engine, emit=self.events.append)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        self.assertIn("error", self.kinds())
        self.assertEqual(session.last_error, "chiave mancante")
        self.assertEqual(session.emotion, "tristezza")
        self.assertFalse(session.busy)

    def test_repeat_say_reset(self):
        self.assertFalse(self.session.repeat())
        self.session.say("Prova della voce. Seconda frase", "sorpresa")
        self.assertEqual([s for s, _ in self.speaker.spoken], ["Prova della voce.", "Seconda frase"])
        self.assertEqual(self.session.emotion, "sorpresa")
        self.session.ask("ciao")
        self.assertTrue(wait_idle(self.session))
        self.assertTrue(self.session.repeat())
        self.session.reset()
        self.assertEqual((self.session.history, self.session.last_answer), ([], ""))

    def test_behaviour_safety(self):
        engine = BehaviourEngine(self.actions, port_writes=True,
                                 port_payloads={"gioia": "4:01 00", "allarme": "1:ff", "sonno": "7:" + "00 " * 17})
        engine.express("gioia")
        time.sleep(0.2)
        engine.express("allarme")
        time.sleep(0.2)
        engine.express("sonno")
        time.sleep(0.2)
        ports = [c for c in self.actions.calls if c[0] == "port"]
        self.assertEqual(ports, [("port", 4, "0100")])
        self.assertFalse(any(c[0] == "action" for c in self.actions.calls))  # busy spento di default


class DotenvTests(unittest.TestCase):
    def test_parse_and_load_without_override(self):
        import os
        from mario.env import load_dotenv, parse_dotenv
        text = "# commento\nexport A=1\nB = 'due parole'\nC=\"tre\" \nD=quattro # nota\nriga senza uguale\n"
        self.assertEqual(parse_dotenv(text), {"A": "1", "B": "due parole", "C": "tre", "D": "quattro"})
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / ".env"
            f.write_text("MARIO_TEST_VAR=dal_file\nMARIO_TEST_VAR2=x\n")
            os.environ["MARIO_TEST_VAR"] = "ambiente"
            os.environ.pop("MARIO_TEST_VAR2", None)
            try:
                self.assertEqual(load_dotenv(f), ["MARIO_TEST_VAR2"])
                self.assertEqual(os.environ["MARIO_TEST_VAR"], "ambiente")
                self.assertEqual(os.environ["MARIO_TEST_VAR2"], "x")
                self.assertEqual(load_dotenv(Path(tmp) / "manca"), [])
            finally:
                os.environ.pop("MARIO_TEST_VAR", None)
                os.environ.pop("MARIO_TEST_VAR2", None)


class AssistantDbTests(unittest.TestCase):
    def test_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "t.sqlite3")
            db.add_assistant_message("user", "ciao", source="utente", turn=1)
            db.add_assistant_message("assistant", "Ciao!", emotion="gioia", source="demo", turn=1)
            rows = db.list_assistant_messages(limit=10)
            self.assertEqual([r["role"] for r in rows], ["user", "assistant"])
            self.assertEqual(db.assistant_stats()["total"], 2)
            self.assertEqual(db.clear_assistant_messages(), 2)


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        from mario.web import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tmp.name) / "t.sqlite3", start_worker=False, quiet_assistant=True)
        self.client = self.app.test_client()
        self.assistant = self.app.extensions["assistant"]
        self.client.post("/api/assistant/settings", json={"backend": "demo"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_and_status(self):
        self.assertEqual(self.client.get("/assistant").status_code, 200)
        st = self.client.get("/api/assistant/status").get_json()["status"]
        self.assertEqual(st["llm"]["resolved_backend"], "demo")
        self.assertTrue(st["listening"])
        self.assertFalse(st["speaker"]["enabled"])

    def test_ask_simulate_history(self):
        r = self.client.post("/api/assistant/ask", json={"text": "ciao"}).get_json()
        self.assertTrue(r["ok"])
        self.assertTrue(wait_idle(self.assistant.session))
        hist = self.client.get("/api/assistant/history").get_json()["messages"]
        self.assertEqual([m["role"] for m in hist], ["user", "assistant"])
        r = self.client.post("/api/assistant/simulate", json={"what": "pantaloni_fuoco"}).get_json()
        self.assertEqual([i["name"] for i in r["intents"]], ["pantaloni"])
        self.assertTrue(wait_idle(self.assistant.session))
        self.assertIn("Pantaloni", self.assistant.session.last_answer)
        r = self.client.post("/api/assistant/simulate", json={"what": "pulsante"}).get_json()
        self.assertEqual(r["intents"][0]["text"], "toggle_listen")
        self.assertFalse(self.assistant.session.listening)
        self.assertEqual(self.client.post("/api/assistant/ask", json={"text": ""}).status_code, 400)

    def test_settings_roundtrip_keeps_secret(self):
        r = self.client.post("/api/assistant/settings", json={"api_key": "sk-test-123", "tts": 0, "rate": 20,
                                                                 "rules": {"urto": {"enabled": True, "cooldown": 9}}}).get_json()
        self.assertTrue(r["settings"]["api_key_set"])
        self.assertEqual(r["settings"]["api_key"], "")
        r = self.client.post("/api/assistant/settings", json={"api_key": ""}).get_json()
        self.assertTrue(r["settings"]["api_key_set"])
        rule = [x for x in self.assistant.session.mapper.rules_dict() if x["name"] == "urto"][0]
        self.assertEqual((rule["enabled"], rule["cooldown"]), (True, 9.0))
        r = self.client.post("/api/assistant/settings", json={"api_key": "", "clear_api_key": 1}).get_json()
        self.assertFalse(r["settings"]["api_key_set"])


if __name__ == "__main__":
    unittest.main()
