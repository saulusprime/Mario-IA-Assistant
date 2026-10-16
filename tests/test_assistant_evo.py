"""Evoluzioni dell'assistente (16/10): modello per i sensori, storia interattiva con [aspetta: ...], sicurezza
(temi, limite di messaggi, riepilogo per i genitori), ricordi (riassunto, scadenza, profili), ingresso vocale.
Senza rete né audio: backend a copione e voce finta."""
import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

from mario.assistant import AssistantSession, LLMConfig, Speaker
from mario.assistant.behaviours import BehaviourEngine, NullActions

HAVE_WEB = importlib.util.find_spec("flask") is not None and importlib.util.find_spec("bleak") is not None


def wait_idle(session, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not session.busy:
            return True
        time.sleep(0.01)
    return False


class ScriptedBackend:
    def __init__(self, text, name="copione"):
        self.text = text
        self.model = name
        self.calls = []

    def describe(self):
        return self.model

    def stream(self, system, messages):
        self.calls.append(messages[-1]["content"])
        self.system = system
        for word in self.text.split(" "):
            yield word + " "


def quiet_speaker():
    return Speaker(enabled=False, engine=None, sounds=False)


class SensorModelTests(unittest.TestCase):
    def test_resolved_sensor_model(self):
        env = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            claude = LLMConfig(backend="anthropic", model="claude-sonnet-5-5")
            self.assertEqual(claude.resolved_sensor_model(), "claude-haiku-5-5")
            self.assertEqual(claude.for_sensors().model, "claude-haiku-5-5")
            self.assertEqual(claude.for_sensors().max_tokens, claude.max_tokens)
            self.assertEqual(LLMConfig(api_key="sk-ant-xxxxxxxxxxxx").resolved_sensor_model(), "claude-haiku-5-5")
            self.assertEqual(LLMConfig(backend="demo").resolved_sensor_model(), "")  # demo: stesso backend
            self.assertEqual(LLMConfig(backend="openai", model="llama3.2").for_sensors().model, "llama3.2")
            same = LLMConfig(backend="anthropic", model="claude-sonnet-5-5", sensor_model="")
            self.assertEqual(same.for_sensors().model, "claude-sonnet-5-5")
            haiku = LLMConfig(backend="anthropic", model="claude-haiku-5-5")
            self.assertEqual(haiku.resolved_sensor_model(), "")  # già haiku: nessuna differenza
            self.assertEqual(LLMConfig(backend="openai", model="big", sensor_model="small").for_sensors().model, "small")
            self.assertIn("resolved_sensor_model", claude.to_dict())
        finally:
            if env is not None:
                os.environ["ANTHROPIC_API_KEY"] = env

    def test_session_uses_sensor_backend_only_for_sensors(self):
        chat, sensors, events = ScriptedBackend("[gioia] Ciao!", "grande"), ScriptedBackend("[gioia] Hop!", "rapido"), []
        session = AssistantSession(lambda: chat, quiet_speaker(), BehaviourEngine(NullActions()), emit=events.append,
                                   sensor_backend_factory=lambda: sensors)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        session.ask("Ho appena fatto un salto!", source="gesto: salto", sensor=True)
        self.assertTrue(wait_idle(session))
        self.assertEqual((len(chat.calls), len(sensors.calls)), (1, 1))
        self.assertTrue(sensors.calls[0].startswith("[sensore: gesto: salto]"))
        self.assertIn("Ciao!", str(session.history))  # la cronologia è la stessa per i due modelli
        self.assertEqual([e["backend"] for e in events if e["kind"] == "start"], ["grande", "rapido"])
        alone = AssistantSession(lambda: chat, quiet_speaker(), BehaviourEngine(NullActions()))
        alone.ask("salto", sensor=True)  # senza factory dei sensori: il modello unico
        self.assertTrue(wait_idle(alone))
        self.assertEqual(len(chat.calls), 2)

    @unittest.skipUnless(HAVE_WEB, "servono flask e bleak (venv dell'host)")
    def test_service_setting(self):
        from mario.web import create_app
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(db_path=Path(tmp) / "t.sqlite3", start_worker=False, quiet_assistant=True)
            client = app.test_client()
            s = client.get("/api/assistant/settings").get_json()["settings"]
            self.assertEqual(s["sensor_model"], "auto")
            client.post("/api/assistant/settings", json={"backend": "anthropic", "model": "claude-sonnet-5-5",
                                                         "sensor_model": "claude-haiku-5-5"})
            service = app.extensions["assistant"]
            self.assertEqual(service._make_sensor_backend().model, "claude-haiku-5-5")
            self.assertEqual(service._make_backend().model, "claude-sonnet-5-5")
            st = client.get("/api/assistant/status").get_json()["status"]
            self.assertEqual(st["llm"]["resolved_sensor_model"], "claude-haiku-5-5")



class SequenceBackend:
    """Risponde con i testi in ordine (l'ultimo si ripete)."""
    model = "copione"

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def describe(self):
        return "copione"

    def stream(self, system, messages):
        self.calls.append(messages[-1]["content"])
        self.system = system
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        for word in text.split(" "):
            yield word + " "


def port_event(io_type, mode, data, **extra):
    return {"ts": time.time(), "type": "PORT_VALUE_SINGLE", "io_type": io_type, "mode": mode, "data": bytes(data),
            **extra}


JUMP = (0x47, 1, b"\x00\x80")
ROTATION = (0x47, 1, b"\x00\x01")
FALL = (0x47, 1, b"\x00\x08")


def tile(code):
    return (0x49, 0, code.to_bytes(2, "little") + b"\xff\xff")


class ChallengeTests(unittest.TestCase):
    def make(self, *texts, **kw):
        self.events = []
        self.backend = SequenceBackend(*texts)
        session = AssistantSession(lambda: self.backend, quiet_speaker(), BehaviourEngine(NullActions()),
                                   emit=self.events.append, **kw)
        return session

    def kinds(self, prefix="challenge"):
        return [e["kind"] for e in self.events if e["kind"].startswith(prefix)]

    def turn(self, session, text="ciao", **kw):
        session.ask(text, **kw)
        self.assertTrue(wait_idle(session))

    def test_parse_and_prompt(self):
        from mario.assistant.emotions import strip_tags, wait_tags
        from mario.assistant.session import parse_challenge
        self.assertEqual(parse_challenge("Salta!").rule, "salto")
        ch = parse_challenge("basetta Goomba", 3)
        self.assertEqual((ch.rule, ch.value, ch.turn), ("basetta", "goomba", 3))
        self.assertEqual(parse_challenge("giro su te stesso").value, "")  # solo pantaloni, basetta e terreno
        self.assertIsNone(parse_challenge("vola"))
        self.assertIsNone(parse_challenge("salto", enabled={"basetta"}))
        self.assertEqual(wait_tags("[gioia] Salta! [aspetta: salto]"), ["salto"])
        self.assertEqual(strip_tags("[gioia] Salta! [aspetta: salto] "), "Salta!")
        session = self.make("[gioia] Ciao!")
        prompt = session.build_system_prompt()
        self.assertIn("[aspetta: azione]", prompt)
        self.assertIn("salto, giro, pantaloni, basetta, terreno, scossa, pulsante", prompt)
        session.mapper.set_rule("rotazione", enabled=False)
        self.assertNotIn("giro", session.wait_targets())  # regola spenta: azione non verificabile
        session.challenges_enabled = False
        self.assertNotIn("[aspetta:", session.build_system_prompt())

    def test_jump_completes_and_story_continues(self):
        session = self.make("[gioia] Fai un salto con me! [aspetta: salto]", "[gioia] Bravissimo, che salto!")
        self.turn(session)
        self.assertEqual(session.challenge.rule, "salto")
        self.assertIsNotNone(session.challenge.started)  # niente voce: l'attesa parte subito
        self.assertEqual(session.status()["challenge"]["text"], "salto")
        self.assertNotIn("aspetta", session.last_answer)  # il tag non si legge
        session.handle_event(port_event(*ROTATION))  # sensore estraneo: ignorato
        outcomes = [e["outcome"] for e in self.events if e["kind"] == "intent"]
        self.assertEqual(outcomes, ["ignorato: Mario aspetta salto"])
        session.handle_event(port_event(*JUMP))
        self.assertTrue(wait_idle(session))
        self.assertIsNone(session.challenge)
        self.assertEqual(self.kinds(), ["challenge", "challenge_done"])
        self.assertIn("[sensore: sfida riuscita: gesto: salto] Ce l'ha fatta", self.backend.calls[-1])
        self.assertIn("Bravissimo", session.last_answer)

    def test_story_turns_use_conversation_model(self):
        quick = SequenceBackend("[gioia] Hop!")
        session = self.make("[gioia] Salta! [aspetta: salto]", "[gioia] Il cancello si apre!",
                            sensor_backend_factory=lambda: quick)
        self.turn(session)
        session.handle_event(port_event(*JUMP))  # sfida: la storia la continua il modello grande
        self.assertTrue(wait_idle(session))
        self.assertIn("(Continua la storia in due o tre frasi.)", self.backend.calls[-1])
        self.assertEqual(quick.calls, [])
        session.handle_event(port_event(*ROTATION))  # nessuna attesa: reazione rapida del modello dei sensori
        self.assertTrue(wait_idle(session))
        self.assertIn("(Rispondi con una frase breve, al massimo due.)", quick.calls[-1])

    def test_fall_passes_and_wrong_tile_then_family(self):
        session = self.make("[gioia] Trova una basetta nemico! [aspetta: basetta nemico]", "[sorpresa] Ops!",
                            "[gioia] Riprova! [aspetta: basetta nemico]", "[gioia] Evviva!")
        session.mapper.set_rule("basetta", cooldown=0)  # due basette di fila nel giro di pochi secondi
        self.turn(session)
        session.handle_event(port_event(*FALL))  # caduta: priorità alta, passa e non chiude l'attesa
        self.assertTrue(wait_idle(session))
        self.assertIsNotNone(session.challenge)
        session.handle_event(port_event(*tile(0x00B8)))  # Start Mario: non è un nemico
        self.assertTrue(wait_idle(session))
        self.assertEqual(self.kinds(), ["challenge", "challenge_missed", "challenge"])
        self.assertIn("Avevi chiesto basetta nemico, invece è arrivato: basetta:", self.backend.calls[-1])
        session.handle_event(port_event(*tile(0x0002)))  # Goomba: famiglia nemico
        self.assertTrue(wait_idle(session))
        self.assertEqual(self.kinds()[-1], "challenge_done")
        self.assertIn("Evviva", session.last_answer)

    def test_timeouts_and_user_text(self):
        session = self.make("[gioia] Salta! [aspetta: salto]", "[gioia] Dai, salta! [aspetta: salto]",
                            "[gioia] Ciao! [aspetta: salto]", "[calma] Va bene, parliamo.", wait_seconds=0.05)
        self.turn(session)
        time.sleep(0.1)
        session.tick()  # scaduta: il modello incoraggia e chiede di nuovo
        self.assertTrue(wait_idle(session))
        self.assertIn("nessuno ha fatto quello che avevi chiesto (salto)", self.backend.calls[-1])
        self.assertIsNotNone(session.challenge)
        time.sleep(0.1)
        session.tick()  # seconda di fila: Mario smette di aspettare, nessun nuovo turno
        self.assertTrue(wait_idle(session))
        timeouts = [e for e in self.events if e["kind"] == "challenge_timeout"]
        self.assertEqual([e["final"] for e in timeouts], [False, True])
        self.assertEqual(len(self.backend.calls), 2)
        self.assertIsNone(session.challenge)
        self.turn(session, "ciao di nuovo")  # chi scrive azzera il conteggio
        self.assertIsNotNone(session.challenge)
        session.ask("parliamo d'altro")  # e cambia discorso: niente più attesa
        self.assertTrue(wait_idle(session))
        self.assertIsNone(session.challenge)

    def test_invalid_and_disabled(self):
        session = self.make("[gioia] Vola! [aspetta: vola in cielo]")
        self.turn(session)
        self.assertIsNone(session.challenge)
        self.assertEqual(self.kinds(), ["challenge_invalid"])
        off = self.make("[gioia] Salta! [aspetta: salto]", challenges=False)
        self.turn(off)
        self.assertIsNone(off.challenge)
        self.assertEqual(off.last_answer, "Salta!")

    def test_waits_for_speech_before_timer(self):
        class SilentSpeaker(Speaker):
            def _run(self):  # le frasi restano in coda: Mario "sta parlando"
                pass
        speaker = SilentSpeaker(enabled=True, engine="spd-say")
        backend = SequenceBackend("[gioia] Salta! [aspetta: salto]")
        session = AssistantSession(lambda: backend, speaker, BehaviourEngine(NullActions()), wait_seconds=0.01)
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        self.assertIsNone(session.challenge.started)  # la richiesta non è ancora stata detta
        time.sleep(0.05)
        session.tick()
        self.assertIsNotNone(session.challenge)  # niente scadenza mentre parla
        speaker.stop()
        session._on_spoken("Salta!", "gioia")
        self.assertIsNotNone(session.challenge.started)

if __name__ == "__main__":
    unittest.main()
