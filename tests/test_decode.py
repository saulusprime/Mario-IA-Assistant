"""Strumento `decode`: catture btsnoop sintetiche (monitor e H4, con frammentazione L2CAP), log di testo, hex."""
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from mario import decode, lwp3
from mario.lwp3 import MessageType


def btsnoop(records, datalink=2001):
    """records: [(flags, payload bytes)] -> file btsnoop."""
    out = b"btsnoop\x00" + struct.pack(">II", 1, datalink)
    for i, (flags, data) in enumerate(records):
        out += struct.pack(">IIIIq", len(data), len(data), flags, 0, 1_700_000_000_000_000 + i * 1000) + data
    return out


def acl(handle, l2cap_payload, cid=4, first=True, total=None):
    """Pacchetto ACL HCI: handle+PB, lunghezza, [L2CAP len+cid se primo] + dati."""
    pb = 0b10 if first else 0b01
    body = (struct.pack("<HH", total if total is not None else len(l2cap_payload), cid) if first else b"") + l2cap_payload
    return struct.pack("<HH", handle | (pb << 12), len(body)) + body


def att_notify(value, handle=0x0011):
    return bytes([0x1B]) + struct.pack("<H", handle) + value


def att_write(value, handle=0x0011):
    return bytes([0x52]) + struct.pack("<H", handle) + value


class BtsnoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_monitor_format_with_fragmentation(self):
        sub = lwp3.port_input_format_setup(2, 0, 1, True)
        reply = bytes.fromhex("0a 00 47 02 00 01 00 00 00 01")
        value = bytes.fromhex("05 00 45 02 21")
        big = lwp3.build(MessageType.PORT_VALUE_SINGLE, bytes([7]) + bytes(range(40)))  # frammentato in due ACL
        notify_big = att_notify(big)
        recs = [
            (4, acl(0x40, att_write(sub))),                                   # TX
            (5, acl(0x40, att_notify(reply))),                                # RX
            (5, acl(0x40, att_notify(value))),
            (5, acl(0x40, notify_big[:20], first=True, total=len(notify_big))),
            (5, acl(0x40, notify_big[20:], first=False)),
            (3, b"\x0e\x04\x01\x00\x00\x00"),                                 # evento HCI: ignorato
        ]
        path = self.dir / "cap.btsnoop"
        path.write_bytes(btsnoop(recs))
        frames = list(decode.read_btsnoop(path))
        self.assertEqual([(f.direction, f.msg.type_name) for f in frames],
                         [("->", "PORT_INPUT_FORMAT_SETUP_SINGLE"), ("<-", "PORT_INPUT_FORMAT_SINGLE"),
                          ("<-", "PORT_VALUE_SINGLE"), ("<-", "PORT_VALUE_SINGLE")])
        self.assertEqual(len(frames[3].msg.payload), 41)
        buf = io.StringIO()
        decode.run([str(path)], as_json=True, out=buf, summary=False)
        rows = [json.loads(line) for line in buf.getvalue().splitlines()]
        self.assertEqual(rows[0]["text"], "Imposta porta 2 modo 0: delta 1, notifiche ON")
        self.assertIn("Mario normale", rows[2]["decoded"])
        self.assertEqual(rows[2]["port"], 2)

    def test_h4_format_direction_flags(self):
        value = bytes.fromhex("05 00 45 02 12")
        recs = [(1, b"\x02" + acl(0x40, att_notify(value))),      # ricevuto
                (0, b"\x02" + acl(0x40, att_write(lwp3.hub_action(0x02))))]  # inviato
        path = self.dir / "android.log"
        path.write_bytes(btsnoop(recs, datalink=1002))
        frames = list(decode.read_any(path))
        self.assertEqual([(f.direction, f.msg.type_name) for f in frames],
                         [("<-", "PORT_VALUE_SINGLE"), ("->", "HUB_ACTIONS")])
        self.assertEqual(lwp3.describe_downstream(frames[1].msg), "Azione hub: DISCONNECT")

    def test_text_log_and_hex_lines(self):
        log = self.dir / "monitor-x.txt"
        log.write_text("[18:07:08] Notifiche attive: porta 0 modo 1 (GEST) delta 1\n"
                       "[18:07:08] Valore porta 0 modo 1: 00 21 00 21 = [8448, 8448]   [08 00 45 00 00 21 00 21]\n"
                       "[18:07:09] -> sub 2 0\n"
                       "      TX 0a 00 41 02 00 01 00 00 00 01\n"
                       "        Handle Value Notification (0x1b) len 7\n"
                       "        Data: 0500450221\n"
                       "[18:07:10] <- Valore porta 2: 21   [05 00 45 02 21]\n", encoding="utf-8")
        frames = list(decode.read_text_log(log))
        self.assertEqual([(f.direction, f.msg.type_name, f.when) for f in frames],
                         [("<-", "PORT_VALUE_SINGLE", "18:07:08"), ("->", "PORT_INPUT_FORMAT_SETUP_SINGLE", ""),
                          ("<-", "PORT_VALUE_SINGLE", ""), ("<-", "PORT_VALUE_SINGLE", "18:07:10")])
        buf = io.StringIO()
        decode.run([str(log)], out=buf, summary=True)
        text = buf.getvalue()
        self.assertIn("rotazione (turning)", text)       # porta 0 nel modo GEST di default
        self.assertIn("pantaloni: Mario normale", text)
        self.assertIn("=== Riepilogo ===", text)
        frames = list(decode.read_hex_lines(["05 00 45 02 21", "-> 04 00 02 01", "# commento", ""]))
        self.assertEqual([f.direction for f in frames], ["<-", "->"])

    def test_filters_and_summary_of_game_events(self):
        buf = io.StringIO()
        decode.run([], hex_args=["08 00 45 03 6b 38 07 00", "08 00 45 03 42 20 03 00", "05 00 45 02 21"],
                   out=buf, summary=True, only_port=3)
        text = buf.getvalue()
        self.assertIn("totale monete", text)
        self.assertNotIn("pantaloni", text.split("=== Riepilogo ===")[0])
        self.assertIn("0x386B:    1  [7]", text)
        self.assertEqual(decode.parse_type_filter("45,hub_properties,0x46"), {0x45, 0x01, 0x46})
        with self.assertRaises(ValueError):
            decode.parse_type_filter("boh")
        buf = io.StringIO()
        decode.run([], hex_args=["-> 04 00 02 01", "05 00 45 02 21"], out=buf, downstream=False)
        self.assertEqual(len(buf.getvalue().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
