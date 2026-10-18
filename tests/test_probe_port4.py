"""tools/probe_port4.py: piano della scansione dei sottocomandi, esiti, mappa compressa, prove con un Mario finto."""
import asyncio
import importlib.util
import tempfile
import types
import unittest
from pathlib import Path

from mario import lwp3
from mario.lwp3 import MessageType

PROJECT = Path(__file__).resolve().parent.parent
HAVE_BLEAK = importlib.util.find_spec("bleak") is not None


def load_tool():
    spec = importlib.util.spec_from_file_location("probe_port4", PROJECT / "tools" / "probe_port4.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def msg(msg_type, payload):
    return lwp3.parse(lwp3.build(msg_type, bytes(payload)))


class FakeMario:
    """Risponde ai Port Output Command come Mario il 12/10: porta 4 accetta solo 0x51 (feedback "completato"),
    0x50 -> INVALID_USE, il resto -> COMMAND_NOT_RECOGNIZED; porta 7 non risponde mai."""

    def __init__(self):
        self.sent: list[bytes] = []
        self.rec = None

    async def send(self, frame: bytes) -> None:
        self.sent.append(frame)
        m = lwp3.parse(frame)
        port, sub = m.payload[0], m.payload[2]
        if port != 4:
            return
        if sub == 0x51:
            reply = msg(MessageType.PORT_OUTPUT_COMMAND_FEEDBACK, [4, 0x0A])
        else:
            reply = msg(MessageType.GENERIC_ERROR, [0x81, 0x06 if sub == 0x50 else 0x05])
        asyncio.get_running_loop().call_later(0.01, self.rec.on_message, reply, "risposta")

    async def request_value(self, port, timeout=3.0):
        return bytes(4)


@unittest.skipUnless(HAVE_BLEAK, "bleak non installato")
class ProbeScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_scan_plan_three_payloads_per_subcommand(self):
        plan = self.tool.scan_plan([4, 7], [0x01, 0x02, 0x05, 0x06, 0x07, 0x08])
        self.assertEqual(len(plan), 36)
        self.assertEqual(plan[0], (4, 0x01, b"", "senza payload"))
        self.assertEqual(plan[1][2], b"\x00")
        self.assertEqual(plan[2][2], bytes([50]))
        self.assertEqual({p for p, *_ in plan[18:]}, {7})
        # sottocomando senza forma nota: solo vuoto e un byte zero, niente doppioni
        self.assertEqual([p[2] for p in self.tool.scan_plan([4], [0x30])], [b"", b"\x00"])

    def test_outcome(self):
        o = self.tool.outcome
        self.assertEqual(o([], 4), "silenzio")
        self.assertEqual(o([msg(MessageType.GENERIC_ERROR, [0x81, 0x05])], 4), "COMMAND_NOT_RECOGNIZED")
        # un errore per un altro comando non conta
        self.assertEqual(o([msg(MessageType.GENERIC_ERROR, [0x01, 0x06])], 4), "silenzio")
        self.assertEqual(o([msg(MessageType.PORT_OUTPUT_COMMAND_FEEDBACK, [7, 0x01, 4, 0x0A])], 4), "feedback 0x0A")
        self.assertEqual(o([msg(MessageType.PORT_OUTPUT_COMMAND_FEEDBACK, [7, 0x01])], 4), "silenzio")
        echo = [msg(MessageType.PORT_VALUE_SINGLE, [3, 0x61, 0x38, 1, 0]), msg(MessageType.PORT_VALUE_SINGLE, [1, 0, 0])]
        self.assertEqual(o(echo, 7), "silenzio + valore porta 3")

    def test_compress_and_hex_list(self):
        items = [(0, "A"), (1, "A"), (2, "B"), (3, "A"), (5, "A")]
        self.assertEqual(self.tool.compress(items), [(0, 1, "A"), (2, 2, "B"), (3, 3, "A"), (5, 5, "A")])
        self.assertEqual(self.tool.parse_hex_list("01, 0x05,ff"), [1, 5, 255])

    def test_is_rejected_reads_messages(self):
        # il 12/10 il controllo cercava "GENERIC_ERROR" nel testo, che invece dice "ERRORE per il comando ..."
        self.assertTrue(self.tool.is_rejected([msg(MessageType.GENERIC_ERROR, [0x81, 0x06])]))
        self.assertFalse(self.tool.is_rejected([msg(MessageType.PORT_OUTPUT_COMMAND_FEEDBACK, [4, 0x0A])]))

    def run_scan(self, **kw):
        tool = self.tool
        saved = tool.WINDOW, tool.LEAD_IN, tool.SWEEP_ANSWER, tool.SWEEP_SETTLE
        tool.WINDOW, tool.LEAD_IN, tool.SWEEP_ANSWER, tool.SWEEP_SETTLE = 0.05, 0, 0.05, 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                async def go():
                    dev = FakeMario()
                    rec = tool.Recorder(dev, Path(tmp) / "log.txt")
                    dev.rec = rec
                    args = types.SimpleNamespace(readback=False, only=None, scan=False, sweep=False)
                    for key, value in kw.pop("args", {}).items():
                        setattr(args, key, value)
                    summary = []
                    extra = await tool.run_scan(dev, rec, voice=False, summary=summary, args=args, **kw)
                    rec.fh.close()
                    return dev, summary, extra
                return asyncio.run(go())
        finally:
            tool.WINDOW, tool.LEAD_IN, tool.SWEEP_ANSWER, tool.SWEEP_SETTLE = saved

    def test_scan_with_fake_mario(self):
        dev, summary, extra = self.run_scan(ports=[4, 7], subs=[0x01, 0x51], args={"scan": True})
        self.assertEqual(len(dev.sent), 10)  # 0x01: 3 payload, 0x51: 2 (lo standard non ha forma) x 2 porte
        self.assertEqual(dev.sent[0], lwp3.port_output_command(4, 0x01, b""))
        self.assertEqual(len(summary), 10)
        self.assertIn("porta 4  0x01  senza payload: COMMAND_NOT_RECOGNIZED", extra)
        self.assertIn("porta 4  0x51  un byte zero: feedback 0x0A", extra)
        self.assertIn("porta 7  0x01  senza payload: silenzio", extra)

    def test_sweep_map_is_compressed(self):
        dev, summary, extra = self.run_scan(ports=[4], subs=[], args={"sweep": True, "only": "1-83"})
        self.assertEqual(len(dev.sent), 83)  # 0x00-0x52
        self.assertEqual(summary, [])
        self.assertEqual(extra[2:], ["porta 4  0x00-0x4F   COMMAND_NOT_RECOGNIZED", "porta 4  0x50        INVALID_USE",
                                     "porta 4  0x51        feedback 0x0A", "porta 4  0x52        COMMAND_NOT_RECOGNIZED"])


if __name__ == "__main__":
    unittest.main()
