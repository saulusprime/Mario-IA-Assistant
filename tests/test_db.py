"""Test del database locale della dashboard (solo libreria standard)."""
import tempfile
import time
import unittest
from pathlib import Path

from mario import codes
from mario.web.db import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_devices_upsert_and_update(self):
        did = self.db.upsert_device("F4:60:77:5A:20:11", name="Mario 5wzZ", fw_version="6.5.01.0000", bonded=1)
        same = self.db.upsert_device("F4:60:77:5A:20:11", name="MarioCasa", last_battery=84)
        self.assertEqual(did, same)
        row = self.db.get_device(did)
        self.assertEqual((row["name"], row["fw_version"], row["last_battery"], row["bonded"]), ("MarioCasa", "6.5.01.0000", 84, 1))
        self.db.update_device(did, notes="il mio Mario")
        self.assertEqual(self.db.get_device(did)["notes"], "il mio Mario")
        self.assertEqual(len(self.db.list_devices()), 1)
        self.db.delete_device(did)
        self.assertEqual(self.db.list_devices(), [])

    def test_seed_accessories_is_idempotent(self):
        first = self.db.seed_accessories(codes.PANTS, codes.BARCODES, codes.COLORS)
        second = self.db.seed_accessories(codes.PANTS, codes.BARCODES, codes.COLORS)
        self.assertGreater(first, 60)
        self.assertEqual(second, 0)
        stats = self.db.accessory_stats()
        self.assertEqual(stats["pants"]["total"], len(codes.PANTS) - 1)  # senza "nessun pantalone"
        self.assertEqual(stats["tile"]["total"], len(codes.BARCODES))

    def test_mark_seen_known_and_unknown(self):
        self.db.seed_accessories(codes.PANTS, codes.BARCODES, codes.COLORS)
        self.db.mark_seen("pants", 0x21)
        self.db.mark_seen("pants", 0x21)
        mario = [a for a in self.db.list_accessories("pants") if a["code"] == 0x21][0]
        self.assertEqual(mario["seen_count"], 2)
        self.db.mark_seen("tile", 0x0ABC)
        unknown = [a for a in self.db.list_accessories("tile") if a["code"] == 0x0ABC][0]
        self.assertEqual((unknown["source"], unknown["seen_count"]), ("rilevato", 1))

    def test_add_update_filter_accessories(self):
        aid = self.db.add_accessory("other", "Tubo verde", owned=True, set_number="71360", notes="start")
        self.db.update_accessory(aid, quantity=2, notes="regalo")
        item = self.db.get_accessory(aid)
        self.assertEqual((item["owned"], item["quantity"], item["notes"]), (1, 2, "regalo"))
        self.assertEqual(len(self.db.list_accessories(owned=True)), 1)
        self.assertEqual(len(self.db.list_accessories(query="71360")), 1)
        with self.assertRaises(ValueError):
            self.db.add_accessory("sconosciuto", "x")

    def test_events_and_prune(self):
        did = self.db.upsert_device("AA:BB")
        for i in range(30):
            self.db.add_event(did, {"ts": time.time() + i, "type": "PORT_VALUE_SINGLE", "port": i % 3, "mode": 0,
                                    "raw": "05 00 45 02 21", "text": f"valore {i}", "decoded": "pantaloni: Mario"})
        self.assertEqual(len(self.db.list_events(limit=10)), 10)
        self.assertEqual(len(self.db.list_events(port=1, limit=100)), 10)
        self.assertEqual(len(self.db.list_events(query="Mario", limit=100)), 30)
        self.db.prune_events(max_rows=5)
        self.assertEqual(self.db.event_stats()["total"], 5)

    def test_settings(self):
        self.assertIsNone(self.db.get_setting("x"))
        self.db.set_setting("x", "1")
        self.db.set_setting("x", "2")
        self.assertEqual(self.db.get_setting("x"), "2")


if __name__ == "__main__":
    unittest.main()
