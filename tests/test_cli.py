"""CLI senza Bluetooth: parser, salvataggio delle funzioni, log di sessione, comandi dell'assistente, decode."""
import importlib.util
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

HAVE_BLEAK = importlib.util.find_spec("bleak") is not None


def wait_idle(session, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not session.busy:
            return True
        time.sleep(0.01)
    return False


@unittest.skipUnless(HAVE_BLEAK, "mario.cli importa bleak")
class CliTests(unittest.TestCase):
    def setUp(self):
        from mario import cli
        self.cli = cli

    def test_parser_and_subscriptions(self):
        parser = self.cli.build_parser()
        args = parser.parse_args(["monitor", "--combined", "--json", "--duration", "30", "--no-battery", "--no-reconnect"])
        self.assertEqual((args.command, args.combined, args.json, args.duration, args.no_battery), ("monitor", True, True, 30.0, True))
        self.assertEqual(self.cli.parse_subscription("1:0:5"), (1, 0, 5))
        self.assertEqual(self.cli.parse_subscription("7"), (7, 0, 1))
        args = parser.parse_args(["decode", "x.txt", "--type", "45", "--summary"])
        self.assertEqual((args.inputs, args.type, args.summary), (["x.txt"], "45", True))
        argv = ["-t", "60", "live-checks", "--steps", "gesti", "-t", "5"]
        args, _unknown = parser.parse_known_args(argv)
        self.assertEqual((args.command, args.timeout), ("live-checks", 60.0))
        self.assertEqual(self.cli.tool_argv(argv, "live-checks"), ["--steps", "gesti", "-t", "5"])
        for name in self.cli.TOOLS:
            self.assertTrue((Path(self.cli.__file__).resolve().parent.parent / "tools" / self.cli.TOOLS[name][0]).is_file())

    def test_save_capabilities_masks_name(self):
        data = {"address": "AA:BB", "bonded": True,
                "properties": {"FW_VERSION": {"id": "0x03", "raw": "", "value": "6.5.01.0000"},
                               "ADVERTISING_NAME": {"id": "0x01", "raw": "4d", "value": "Nome Personale"}},
                "ports": {}}
        with tempfile.TemporaryDirectory() as tmp:
            path = self.cli.save_capabilities(data, Path(tmp))
            self.assertEqual(path.name, "capabilities-fw-6.5.01.json")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["properties"]["ADVERTISING_NAME"]["value"], "(nome omesso)")
            self.assertEqual(saved["properties"]["FW_VERSION"]["value"], "6.5.01.0000")
            self.assertEqual(data["properties"]["ADVERTISING_NAME"]["value"], "Nome Personale")  # originale intatto

    def test_session_log_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = self.cli.SessionLog("prova", Path(tmp) / "a.txt")
            log.write("ciao", b"\x05\x00\x45\x02\x21")
            self.assertEqual(log.switch(Path(tmp) / "b.txt"), Path(tmp) / "b.txt")
            log.write("seconda")
            self.assertIsNone(log.switch(None))
            log.write("persa")
            self.assertIn("ciao   [05 00 45 02 21]", (Path(tmp) / "a.txt").read_text(encoding="utf-8"))
            self.assertIn("seconda", (Path(tmp) / "b.txt").read_text(encoding="utf-8"))
            log.close()

    def test_shell_help_topics(self):
        self.assertIn("sub <porta> <modo>", self.cli.shell_help("sub"))
        self.assertIn("modo:dataset", self.cli.shell_help("combo"))
        self.assertIn("Nessun comando", self.cli.shell_help("boh"))
        self.assertIn("log", self.cli.SHELL_COMMANDS)
        self.assertIn("help", self.cli.SHELL_COMMANDS)

    def test_assistant_printer_and_commands(self):
        from mario.assistant import AssistantSession, DemoBackend, LLMConfig, Speaker
        from mario.assistant.behaviours import BehaviourEngine, NullActions
        out = io.StringIO()
        printer = self.cli.AssistantPrinter(out)
        printer.line("prima riga")
        printer.start("Mario: ")
        printer.delta("Ciao ")
        printer.line("intento durante lo streaming")
        printer.delta("a te!")
        printer.end("(fine)")
        text = out.getvalue()
        self.assertLess(text.index("Ciao a te!"), text.index("intento durante lo streaming"))
        self.assertTrue(text.endswith(self.cli.PROMPT))
        speaker = Speaker(enabled=False, engine=None)
        session = AssistantSession(lambda: DemoBackend(LLMConfig(demo_delay=0)), speaker, BehaviourEngine(NullActions()))
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/ripeti")
        self.assertEqual((leave, msg), (False, "niente da ripetere"))
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/emozione gioia")
        self.assertIn("gioia", msg)
        self.assertEqual(speaker.spoken[-1][1], "gioia")
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/emozione boh")
        self.assertTrue(msg.startswith("emozioni:"))
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/ascolto off")
        self.assertFalse(session.listening)
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/stato")
        self.assertFalse(json.loads(msg)["listening"])
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/regole")
        self.assertIn("salto", msg)
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/aiuto")
        self.assertIn("/stop", msg)
        self.assertEqual(self.cli.handle_assistant_command(session, speaker, "/esci"), (True, ""))
        session.ask("ciao")
        self.assertTrue(wait_idle(session))
        leave, msg = self.cli.handle_assistant_command(session, speaker, "/ripeti")
        self.assertEqual(msg, "")

    def test_adventure_simulation_events(self):
        ev = self.cli.adventure_sim_event("basetta 0x02")
        self.assertEqual((ev["io_type"], ev["data"]), (0x49, bytes.fromhex("0200ffff")))
        self.assertEqual(self.cli.adventure_sim_event("colore 0x25")["data"], bytes.fromhex("ffff2500"))
        self.assertEqual(self.cli.adventure_sim_event("salto")["data"], bytes.fromhex("0080"))
        self.assertEqual(self.cli.adventure_sim_event("evento 0x386B 7")["data"], bytes.fromhex("6b380700"))
        self.assertEqual(self.cli.adventure_sim_event("pulsante")["prop"], "BUTTON")
        self.assertIsNone(self.cli.adventure_sim_event("boh"))
        args = self.cli.build_parser().parse_args(["adventure", "--play", "quiz", "--no-mario", "--no-tts"])
        self.assertEqual((args.play, args.no_mario, args.webhooks), ("quiz", True, False))
        with tempfile.TemporaryDirectory() as tmp:
            from mario.web.db import Database
            db = Database(Path(tmp) / "a.sqlite3")
            aid, data = self.cli._pick_adventure("colori", db)   # senza database popolato: esempi
            self.assertEqual(data["name"], "Caccia al tesoro dei colori")
            path = Path(tmp) / "x.json"
            path.write_text(json.dumps({"name": "Da file", "steps": [{"trigger": {"kind": "button"}}]}), encoding="utf-8")
            self.assertEqual(self.cli._pick_adventure(str(path), db)[1]["name"], "Da file")
            with self.assertRaises(ValueError):
                self.cli._pick_adventure("inesistente", db)

    def test_decode_via_main(self):
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self.cli.main(["decode", "05 00 45 02 21", "--summary"])
        self.assertEqual(code, 0)
        self.assertIn("Mario normale", buf.getvalue())
        self.assertIn("=== Riepilogo ===", buf.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.cli.main(["decode", "non-esiste.bin"]), 2)


if __name__ == "__main__":
    unittest.main()
