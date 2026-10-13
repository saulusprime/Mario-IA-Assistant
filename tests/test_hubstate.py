"""Stato dell'hub senza bleak: valori combinati (dati reali del 12/10/2026), capabilities, eventi."""
import json
import unittest
from pathlib import Path

from mario import lwp3
from mario.hubstate import HubState

CAPS = Path(__file__).resolve().parent.parent / "docs" / "capabilities-fw-6.5.01.json"


def msg(hex_text):
    return lwp3.parse(bytes.fromhex(hex_text))


class HubStateTests(unittest.TestCase):
    def setUp(self):
        self.st = HubState.from_capabilities(json.loads(CAPS.read_text(encoding="utf-8")))

    def test_from_capabilities(self):
        self.assertEqual(sorted(self.st.ports), [0, 1, 2, 3, 4, 6, 7])
        p0 = self.st.ports[0]
        self.assertEqual((p0.io_type, p0.combinations, p0.modes[1].name), (0x47, [[0, 1]], "GEST"))
        self.assertEqual(str(p0.value_format(1)), "2 x int16, 3 cifre, 0 decimali")
        self.assertTrue(p0.info.capabilities & 0x04)
        self.assertEqual(lwp3.decode_hub_property(lwp3.HubProperty.FW_VERSION, self.st.properties[lwp3.HubProperty.FW_VERSION]),
                         "6.5.01.0000")

    def test_combined_values_big_endian_mask_and_partial_update(self):
        # definizione inviata dal PC (come tools/live_checks.py), poi valori reali della sessione delle 19:17
        self.st.note_sent(lwp3.port_combination_set(0, 0, [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]))
        m = msg("0d 00 46 00 00 1f 01 23 fa 00 00 00 00")
        self.st.update(m)
        self.assertEqual(self.st.last_combined[0], {0: bytes.fromhex("0123fa"), 1: bytes(4)})
        text = self.st.describe(m)
        self.assertIn("accelerazione x=1 y=35 z=-6", text)
        self.assertIn("gesto: nessuno", text)
        m = msg("08 00 46 00 00 06 23 f9")  # multi-update: solo y e z
        self.st.update(m)
        self.assertEqual(self.st.last_combined[0], {0: bytes.fromhex("0123f9")})
        self.assertNotIn("GEST", self.st.describe(m))
        m = msg("0d 00 46 00 00 1f 02 68 0a 00 21 00 21")
        self.st.update(m)
        self.assertIn("rotazione (turning)", self.st.describe(m))
        ev = self.st.event(m, "x")
        self.assertEqual(ev["type"], "PORT_VALUE_COMBINED")
        self.assertEqual(ev["modes"]["1"]["data"], "00 21 00 21")
        self.assertEqual(ev["modes"]["0"]["values"], [2, 104, 10])

    def test_default_combination_from_format_message(self):
        # senza aver visto il comando: il messaggio 0x48 dice indice 0 -> tutti i dataset dei modi [0, 1]
        self.st.update(msg("07 00 48 01 80 1f 00"))
        self.assertEqual(self.st.ports[1].combination, [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2)])
        self.assertTrue(self.st.ports[1].multi_update)
        m = msg("0d 00 46 01 00 1f b8 00 25 00 10 20 30")
        self.st.update(m)
        text = self.st.describe(m)
        self.assertIn("Start Mario", text)
        self.assertIn("RGB r=16 g=32 b=48", text)
        self.st.note_sent(lwp3.port_input_format_setup_combined(1, lwp3.CombinedModeSubcommand.RESET_SENSOR))
        self.assertEqual(self.st.ports[1].combination, [])

    def test_single_values_and_properties(self):
        self.st.update(msg("0a 00 47 02 00 01 00 00 00 01"))
        self.assertEqual(self.st.ports[2].current_mode, 0)
        m = msg("05 00 45 02 21")
        self.st.update(m)
        self.assertEqual(self.st.ports[2].last_value, b"\x21")
        ev = self.st.event(m, self.st.describe(m))
        self.assertEqual((ev["port"], ev["mode"], ev["io_type"], ev["values"]), (2, 0, 0x4A, [33]))
        self.assertIn("Mario normale", ev["decoded"])
        m = msg("06 00 01 06 06 4b")
        self.st.update(m)
        self.assertEqual(self.st.event(m, "")["value"], 75)
        self.assertEqual(self.st.property_text(lwp3.HubProperty.BATTERY_VOLTAGE), "75 %")

    def test_reattach_keeps_discovered_modes(self):
        before = self.st.ports[0].modes
        self.st.update(msg("0f 00 04 00 01 47 00 00 00 00 10 00 00 00 10"))
        self.assertIs(self.st.ports[0].modes, before)
        self.st.update(msg("05 00 04 00 00"))
        self.assertNotIn(0, self.st.ports)


if __name__ == "__main__":
    unittest.main()
