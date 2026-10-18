"""Archivio delle partite (porta 7), canale della porta 4 e scritture note dell'app: record veri della cattura del 18/10."""
import io
import unittest

from mario import codes, decode, history

# record presi dalla cattura del traffico dell'app ufficiale (18/10/2026), dati della porta 7 senza intestazione LWP3
SESSION = bytes.fromhex("01 0b 92 01 00 00 a0 08 8e 08 01 13 04 00 21 01")
TILES = bytes.fromhex("02 02 00 0a 14 00 01 2e 00 03 b8 00 01 f5 00 01")
TILES_SHORT = bytes.fromhex("02 5b 01 01 60 01 01 00 00 00 00 00 00 00 00 00")
COUNTERS = bytes.fromhex("05 00 11 00 01 07 00 02 02 00 03 01 00 04 04 00")
COUNTERS_A = bytes.fromhex("03 03 01 00 06 02 00 00 00 00 00 00 00 00 00 00")
END = bytes.fromhex("ff ff ff ff ff ff ff ff ff ff ff ff 3d 00 08 00")


def session(number: int, records: int) -> bytes:
    return bytes([1, records]) + number.to_bytes(4, "little") + bytes.fromhex("a0 08 8e 08 01 13 04 00 21 01")


class HistoryTest(unittest.TestCase):
    def test_parse_records(self):
        s = history.parse(SESSION)
        self.assertEqual((s["kind"], s["records"], s["number"], s["raw_a"], s["raw_b"], s["pants"]),
                         ("session", 11, 402, 2208, 2190, 0x21))
        self.assertEqual(history.parse(TILES)["tiles"], [(0x02, 10), (0x14, 1), (0x2E, 3), (0xB8, 1), (0xF5, 1)])
        self.assertEqual(history.parse(TILES_SHORT)["tiles"], [(0x15B, 1), (0x160, 1)])  # zeri finali = riempimento
        self.assertEqual(history.parse(COUNTERS)["counters"], [(0, 17), (1, 7), (2, 2), (3, 1), (4, 4)])  # #0 è valido
        self.assertEqual(history.parse(COUNTERS_A)["counters"], [(3, 1), (6, 2)])
        self.assertEqual(history.parse(END), {"kind": "end", "records": 61, "sessions": 8})
        self.assertIsNone(history.parse(b"\x01"))
        self.assertEqual(history.parse(bytes([0x09]) + bytes(15))["kind"], "unknown")

    def test_describe(self):
        self.assertIn("partita n. 402 (11 record)", history.describe(SESSION))
        self.assertIn("pantaloni Mario normale", history.describe(SESSION))
        self.assertEqual(history.describe(TILES_SHORT), "archivio: basette Carrello minerario ×1, Aereo ×1")
        self.assertEqual(history.describe(END), "archivio: fine, 61 record in 8 partite")
        # il monitor e decode passano dalla stessa funzione
        self.assertEqual(codes.decode_value(codes.IO_MARIO_TELEMETRY, 0, END, []), history.describe(END))
        self.assertEqual(codes.decode_value(codes.IO_MARIO_TELEMETRY, 0, b"\x01\x02", []), "telemetria (grezza) 01 02")

    def test_collector_groups_and_checks(self):
        c = history.Collector()
        for rec in (session(402, 3), TILES, COUNTERS, session(403, 1)):
            c.feed(rec)
        self.assertFalse(c.complete)
        self.assertEqual(c.problems(), ["manca il record finale: scarico interrotto?"])
        c.feed(bytes([0xFF] * 12) + (4).to_bytes(2, "little") + (2).to_bytes(2, "little"))
        self.assertTrue(c.complete)
        self.assertEqual(c.problems(), [])
        self.assertEqual(c.sessions[0]["tiles"][0x02], 10)
        self.assertEqual(c.sessions[0]["counters"][history.COUNTERS_B][0], 17)
        text = "\n".join(c.summary())
        self.assertIn("archivio delle partite (porta 7): 2 partite, 4 record", text)
        self.assertIn("basette: Goomba ×10, Rotazione ×1, Nuvola ×3, Start Mario ×1", text)
        bad = history.Collector()
        for rec in (session(1, 3), TILES, END):
            bad.feed(rec)
        self.assertEqual(bad.problems(), ["partite: 1 ricevute, 8 annunciate", "record: 3 dichiarati dalle partite, 61 nel record finale",
                                          "partita n. 1: 2 record ricevuti su 3"])

    def test_port4_and_known_writes(self):
        self.assertEqual(codes.decode_value(codes.IO_MARIO_ALT_EVENTS, 0, bytes.fromhex("0b 00 ec ca"), []),
                         "porta 4: risposta 0x0B valore 0xCAEC (risposta alle richieste 0x09 e 0x0A)")
        self.assertIn("richiesta 0x05 con 0x00FE", codes.describe_write(4, 0x51, bytes.fromhex("00 05 00 fe 00")))
        self.assertIn("istantanea dei contatori", codes.describe_write(3, 0x51, bytes.fromhex("02 01 f0")))
        self.assertIn("richiesta dell'archivio", codes.describe_write(7, 0x51, bytes.fromhex("00 01")))
        self.assertIn("probabile cancellazione", codes.describe_write(7, 0x51, bytes.fromhex("00 02")))
        self.assertIsNone(codes.describe_write(7, 0x51, bytes.fromhex("00 03")))
        self.assertIsNone(codes.describe_write(4, 0x50, bytes.fromhex("00 05 00 fe 00")))
        self.assertIn("istantanea", codes.GAME_EVENTS[0x1902])

    def test_decode_summary_of_the_app_handshake(self):
        port7 = lambda d: "14 00 45 07 " + d.hex(" ")  # noqa: E731
        lines = ["-> 09 00 81 03 11 51 02 01 f0", "08 00 45 03 02 19 a8 00",
                 "-> 0b 00 81 04 11 51 00 05 00 fe 00", "08 00 45 04 01 00 fe 00",
                 "-> 08 00 81 07 01 51 00 01", port7(session(402, 2)), port7(TILES),
                 port7(bytes([0xFF] * 12) + bytes.fromhex("02 00 01 00")), "-> 08 00 81 07 01 51 00 02"]
        buf = io.StringIO()
        decode.run([], hex_args=lines, out=buf, summary=True)
        text = buf.getvalue()
        self.assertIn("Comando output porta 7: WriteDirectModeData, avvio in coda, feedback sì, modo 0, dati 01 → richiesta "
                      "dell'archivio delle partite", text)
        self.assertIn("archivio: partita n. 402 (2 record)", text)
        self.assertIn("0x1902:    1  [168]  istantanea", text)
        self.assertIn("scritture note dell'app ufficiale e risposte della porta 4:", text)
        self.assertIn("<- porta 4: risposta 0x01 valore 0x00FE", text)
        self.assertIn("archivio delle partite (porta 7): 1 partita, 2 record", text)
        self.assertNotIn("attenzione", text)


if __name__ == "__main__":
    unittest.main()
