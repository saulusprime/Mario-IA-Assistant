"""AssistantService con un worker finto: flusso eventi -> intenti, salvataggio messaggi, impostazioni."""
import queue
import tempfile
import time
import unittest
from pathlib import Path

from mario.assistant.service import AssistantService, DEFAULTS, as_bool
from mario.web.db import Database


class FakeWorker:
    def __init__(self):
        self.device = None
        self.device_row_id = 7
        self.pushed: list[dict] = []
        self.streams: list[queue.Queue] = []
        self.volume_calls: list[int] = []

    def open_stream(self):
        q = queue.Queue()
        self.streams.append(q)
        return q

    def push(self, ev):
        self.pushed.append(ev)

    def call(self, factory, timeout=10):
        return factory()

    def set_volume(self, level):
        self.volume_calls.append(level)
        return level

    def hub_action(self, action):
        pass

    def port_output(self, port, subcommand, payload):
        pass

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
        self.db = Database(Path(self.tmp.name) / "s.sqlite3")
        self.db.set_setting("assistant.backend", "demo")
        self.worker = FakeWorker()
        self.service = AssistantService(self.worker, self.db, quiet=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_public_settings(self):
        s = self.service.settings()
        self.assertEqual(set(s), {k.split(".", 1)[1] for k in DEFAULTS})
        pub = self.service.public_settings()
        self.assertEqual(pub["backend"], "demo")
        self.assertFalse(pub["api_key_set"])
        self.assertEqual(pub["api_key"], "")
        self.assertIs(pub["tts"], True)
        self.assertEqual(pub["resolved_backend"], "demo")
        self.assertTrue(as_bool("sì") and as_bool("on") and not as_bool("0"))
        self.assertFalse(self.service.speaker.enabled)      # quiet: niente voce
        self.assertFalse(self.service.behaviours.enabled)

    def test_stream_events_become_turns_and_messages(self):
        self.worker.feed({"ts": time.time(), "type": "STATE", "text": "connected"})          # ignorato
        self.worker.feed({"ts": time.time(), "type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1,
                          "raw": "08 00 45 00 00 80 00 80"})                                 # salto
        self.assertTrue(wait_for(lambda: self.db.assistant_stats()["total"] >= 2))
        rows = self.db.list_assistant_messages()
        self.assertEqual([r["role"] for r in rows], ["user", "assistant"])
        self.assertEqual(rows[0]["source"], "gesto: salto")
        self.assertEqual(rows[0]["device_id"], 7)
        self.assertIn("salto", rows[1]["content"].lower())
        kinds = [e["kind"] for e in self.worker.pushed if e.get("type") == "ASSISTANT"]
        for k in ("intent", "user", "start", "done"):
            self.assertIn(k, kinds)
        # pantaloni: il primo valore è solo contesto, il cambio vale dopo il filtro di stabilità (tick nel thread)
        self.worker.feed({"ts": time.time(), "type": "PORT_VALUE_SINGLE", "io_type": 0x4A, "mode": 0, "raw": "05 00 45 02 21"})
        self.worker.feed({"ts": time.time(), "type": "PORT_VALUE_SINGLE", "io_type": 0x4A, "mode": 0, "raw": "05 00 45 02 12"})
        self.assertTrue(wait_for(lambda: self.service.session.mapper.context["pants"] == 0x12, timeout=4.0))
        self.assertTrue(wait_for(lambda: self.db.assistant_stats()["total"] >= 4))
        self.assertIn("Fuoco", self.db.list_assistant_messages()[-2]["content"])

    def test_update_settings_rules_payloads_and_secret(self):
        pub = self.service.update_settings({"api_key": "sk-test-1", "rules": {"salto": {"enabled": False, "cooldown": 9, "text": "Hop! {name}"}},
                                            "port_payloads": {"gioia": "4:01 02"}, "port_writes": "1", "persona": "Parli con Leo",
                                            "listening": "0", "rate": "30"})
        self.assertTrue(pub["api_key_set"])
        self.assertEqual(self.db.get_setting("assistant.api_key"), "sk-test-1")
        rule = self.service.session.mapper.rules["salto"]
        self.assertEqual((rule.enabled, rule.cooldown, rule.text), (False, 9.0, "Hop! {name}"))
        self.assertEqual(self.service.behaviours.port_payloads, {"gioia": "4:01 02"})
        self.assertTrue(self.service.behaviours.port_writes)
        self.assertEqual(self.service.session.persona, "Parli con Leo")
        self.assertFalse(self.service.session.listening)
        self.assertEqual(self.service.speaker.rate, 30)
        # chiave lasciata vuota: resta; con clear_api_key: via
        self.assertTrue(self.service.update_settings({"api_key": ""})["api_key_set"])
        self.assertFalse(self.service.update_settings({"api_key": "", "clear_api_key": 1})["api_key_set"])
        self.assertEqual(self.service.update_settings({"rules": "non json"})["persona"], "Parli con Leo")
        st = self.service.status()
        self.assertEqual(st["llm"]["resolved_backend"], "demo")
        self.assertEqual(st["settings"]["port_payloads"], '{"gioia": "4:01 02"}')


if __name__ == "__main__":
    unittest.main()
