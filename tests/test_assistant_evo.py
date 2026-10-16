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


if __name__ == "__main__":
    unittest.main()
