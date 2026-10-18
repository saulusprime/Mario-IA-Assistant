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

    def test_summary_rebuilds_debug_trace_text(self):
        # porta 3 in modo DEBUG (re-checks del 14/10/2026): il testo attraversa i confini dei pacchetti
        from mario import codes
        text = b"LEAF 0x65010000 May 27 2025 08:44:31\x00"
        chunks = [text[i:i + 16].ljust(16, b"\x00") for i in range(0, len(text), 16)]
        lines = ["0a 00 47 03 03 01 00 00 00 01"] + [(b"\x14\x00\x45\x03" + c).hex(" ") for c in chunks]
        lines.insert(2, lines[1])  # risposta a una lettura ripetuta come prima notifica: va saltata
        buf = io.StringIO()
        decode.run([], hex_args=lines, out=buf, summary=True)
        out = buf.getvalue()
        self.assertIn("testo: LEAF 0x65010000", out)  # descrizione del singolo pezzo
        self.assertIn("traccia DEBUG della porta 3: 3 pezzi, 48 byte (1 pezzi uguali al precedente saltati)", out)
        self.assertIn("testo: 'LEAF 0x65010000 May 27 2025 08:44:31'", out)
        self.assertIn("CHAL", codes.decode_value(0x46, 0, bytes.fromhex("c0 00 00 00"), []))


if __name__ == "__main__":
    unittest.main()


def btsnooz_blob(records, version=2, last_ts=1_700_000_000_000_000):
    """records: [(tipo btsnooz, delta µs, dati senza il byte H4)] -> sezione btsnooz (prima del base64)."""
    import zlib
    body = b""
    for kind, delta, data in records:
        if version == 1:
            body += struct.pack("<HIb", len(data) + 1, delta, kind) + data
        else:
            body += struct.pack("<HHIb", len(data) + 1, len(data) + 1, delta, kind) + data
    return struct.pack("<bQ", version, last_ts) + zlib.compress(body)


def bugreport_text(blob):
    import base64
    b64 = base64.b64encode(blob).decode()
    lines = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    return ("== dumpstate: 2026-10-13\n--- BEGIN:BTSNOOP_LOG_SUMMARY (%d bytes in) ---\n" % len(blob)
            + "\n".join(lines) + "\n--- END:BTSNOOP_LOG_SUMMARY ---\n")


class AndroidBugreportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_btsnooz_v1_and_v2_sections(self):
        value = bytes.fromhex("05 00 45 02 21")
        recs = [(0x21, 0, acl(0x40, att_write(lwp3.hub_action(0x02)))),      # ACL in uscita
                (0x11, 1500, acl(0x40, att_notify(value))),                    # ACL in ingresso
                (0x10, 20, b"\x0e\x04\x01\x00\x00\x00")]                      # evento HCI: ignorato
        for version in (1, 2):
            path = self.dir / f"bugreport-v{version}.txt"
            path.write_text(bugreport_text(btsnooz_blob(recs, version)), encoding="utf-8")
            notes = []
            frames = list(decode.read_any(path, notes))
            self.assertEqual([(f.direction, f.msg.type_name) for f in frames],
                             [("->", "HUB_ACTIONS"), ("<-", "PORT_VALUE_SINGLE")], f"versione {version}")
            self.assertEqual(frames[1].when, "0.002s")  # delta µs ricostruito dall'ultimo timestamp
            self.assertEqual(notes, [])

    def test_zip_with_full_snoop_and_truncated_summary(self):
        import zipfile
        value = bytes.fromhex("05 00 45 02 21")
        full = btsnoop([(1, b"\x02" + acl(0x40, att_notify(value)))], datalink=1002)
        # btsnooz "riassunto": l'intestazione ACL dichiara 9 byte ma ne seguono 4 (dati tagliati)
        cut = acl(0x40, att_notify(value))[:8]
        summary = bugreport_text(btsnooz_blob([(0x11, 0, cut)]))
        zpath = self.dir / "bugreport-pixel.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("FS/data/misc/bluetooth/logs/btsnoop_hci.log", full)
            zf.writestr("bugreport-pixel-2026-10-13.txt", summary)
        notes = []
        frames = list(decode.read_any(zpath, notes))
        self.assertEqual([(f.direction, f.msg.type_name, f.source) for f in frames],
                         [("<-", "PORT_VALUE_SINGLE", "bugreport-pixel.zip:btsnoop_hci.log")])
        self.assertEqual(notes, [])  # con la cattura completa il riassunto non viene letto
        # solo il riassunto: niente messaggi e una nota che spiega il troncamento
        tpath = self.dir / "bugreport-solo-riassunto.txt"
        tpath.write_text(summary, encoding="utf-8")
        notes = []
        self.assertEqual(list(decode.read_any(tpath, notes)), [])
        self.assertEqual(len(notes), 1)
        self.assertIn("1 pacchetti ACL troncati", notes[0])
        # zip senza nulla di Bluetooth
        empty = self.dir / "vuoto.zip"
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("version.txt", "x")
        notes = []
        self.assertEqual(list(decode.read_any(empty, notes)), [])
        self.assertIn("nessuna cattura Bluetooth", notes[0])

    def test_gzip_btsnoop(self):
        import gzip
        value = bytes.fromhex("05 00 45 02 12")
        path = self.dir / "btsnoop_hci.log.gz"
        path.write_bytes(gzip.compress(btsnoop([(5, acl(0x40, att_notify(value)))])))
        frames = list(decode.read_any(path))
        self.assertEqual([(f.direction, f.msg.type_name) for f in frames], [("<-", "PORT_VALUE_SINGLE")])


def pklg(records, order=">"):
    """records: [(tipo, secondi, µs, dati)] -> file PacketLogger."""
    out = b""
    for kind, secs, usecs, data in records:
        payload = bytes([kind]) + data
        out += struct.pack(order + "III", 8 + len(payload), secs, usecs) + payload
    return out


class PacketLoggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_byte_orders(self):
        value = bytes.fromhex("05 00 45 02 21")
        recs = [(0xFC, 1_700_000_000, 10, b"nota di PacketLogger"),
                (0x02, 1_700_000_000, 500, acl(0x40, att_write(lwp3.hub_action(0x02)))),
                (0x03, 1_700_000_001, 500, acl(0x40, att_notify(value))),
                (0x01, 1_700_000_001, 600, b"\x13\x05\x01\x40\x00\x01\x00")]
        for order in (">", "<"):
            path = self.dir / f"cattura-{'be' if order == '>' else 'le'}.pklg"
            path.write_bytes(pklg(recs, order))
            self.assertEqual(decode.pklg_byte_order(path.read_bytes()), order)
            frames = list(decode.read_any(path))
            self.assertEqual([(f.direction, f.msg.type_name, f.when) for f in frames],
                             [("->", "HUB_ACTIONS", "0.000s"), ("<-", "PORT_VALUE_SINGLE", "1.000s")], order)

    def test_text_logs_are_not_mistaken_for_pklg(self):
        path = self.dir / "monitor.txt"
        path.write_text("[17:08:32] <- Valore porta 3   [08 00 45 03 61 38 05 00]\n", encoding="utf-8")
        self.assertIsNone(decode.pklg_byte_order(path.read_bytes()))
        frames = list(decode.read_any(path))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].msg.type_name, "PORT_VALUE_SINGLE")
