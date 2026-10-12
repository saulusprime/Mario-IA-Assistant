"""Test offline del codec LWP3 e dei decodificatori Mario (non serve il Bluetooth)."""
import unittest

from mario import codes, lwp3
from mario.lwp3 import HubProperty, MessageType, ModeInfoType, ValueFormat


class BuildTests(unittest.TestCase):
    def test_header_short(self):
        self.assertEqual(lwp3.hub_property_request(HubProperty.VOLUME), bytes.fromhex("05 00 01 12 05"))
        self.assertEqual(lwp3.hub_property_set(HubProperty.VOLUME, bytes([50])), bytes.fromhex("06 00 01 12 01 32"))
        self.assertEqual(lwp3.hub_action(lwp3.HubAction.SWITCH_OFF), bytes.fromhex("04 00 02 01"))

    def test_port_input_format_setup_matches_known_capture(self):
        # sequenza nota per attivare i gesti (porta 0, modo 1) su LEGO Mario
        self.assertEqual(lwp3.port_input_format_setup(0, 1, 1, True), bytes.fromhex("0a 00 41 00 01 01 00 00 00 01"))

    def test_header_long(self):
        msg = lwp3.build(0x81, bytes(130))
        self.assertEqual(msg[0] & 0x80, 0x80)
        length = (msg[0] & 0x7F) | (msg[1] << 7)
        self.assertEqual(length, len(msg))
        parsed = lwp3.parse(msg)
        self.assertEqual(parsed.msg_type, 0x81)
        self.assertEqual(len(parsed.payload), 130)

    def test_parse_all_splits_multiple_messages(self):
        data = bytes.fromhex("05 00 45 02 21") + bytes.fromhex("04 00 02 30")
        msgs = list(lwp3.parse_all(data))
        self.assertEqual([m.msg_type for m in msgs], [MessageType.PORT_VALUE_SINGLE, MessageType.HUB_ACTIONS])


class DecodeTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(lwp3.decode_version(bytes.fromhex("00 00 00 50")), "5.0.00.0000")
        self.assertEqual(lwp3.decode_version(bytes.fromhex("23 01 05 12")), "1.2.05.0123")

    def test_hub_properties(self):
        self.assertEqual(lwp3.format_hub_property(HubProperty.VOLUME, bytes([100])), "100 %")
        self.assertEqual(lwp3.format_hub_property(HubProperty.SYSTEM_TYPE_ID, bytes([0x43])), "0x43 (LEGO Mario)")
        self.assertEqual(lwp3.decode_hub_property(HubProperty.ADVERTISING_NAME, b"Mario 5wzZ"), "Mario 5wzZ")
        self.assertEqual(lwp3.decode_hub_property(HubProperty.RSSI, bytes([0xD7])), -41)
        self.assertEqual(lwp3.decode_hub_property(HubProperty.LWP_VERSION, bytes([0x00, 0x03])), "3.0")
        self.assertEqual(lwp3.decode_hub_property(HubProperty.PRIMARY_MAC, bytes.fromhex("7c5b9c67ab52")), "7C:5B:9C:67:AB:52")

    def test_attached_io(self):
        payload = bytes([0x00, 0x01, 0x47, 0x00]) + bytes.fromhex("00 00 00 10") + bytes.fromhex("00 00 00 10")
        io = lwp3.decode_attached_io(payload)
        self.assertEqual((io.port, io.event, io.io_type, io.hw_version), (0, 1, 0x47, "1.0.00.0000"))
        text = lwp3.describe(lwp3.parse(lwp3.build(MessageType.HUB_ATTACHED_IO, payload)))
        self.assertIn("accelerometro", text)

    def test_port_information(self):
        info = lwp3.decode_port_information(bytes([1, 1, 0x06, 2, 0x03, 0x00, 0x00, 0x00]))
        self.assertEqual(info.capability_names, ["input", "combinabile"])
        self.assertEqual(info.input_mode_list, [0, 1])
        self.assertEqual(info.all_modes, [0, 1])
        port, combos = lwp3.decode_port_information(bytes([1, 2, 0x03, 0x00]))
        self.assertEqual((port, combos), (1, [[0, 1]]))

    def test_mode_information(self):
        port, mode, info_type, value = lwp3.decode_port_mode_information(b"\x02\x00\x00PANT\x00\x00")
        self.assertEqual((port, mode, info_type, value), (2, 0, ModeInfoType.NAME, "PANT"))
        _, _, _, vf = lwp3.decode_port_mode_information(bytes([2, 0, 0x80, 1, 0, 2, 0]))
        self.assertEqual(vf, ValueFormat(1, 0, 2, 0))
        self.assertEqual(vf.decode(bytes([0x21])), [0x21])
        _, _, _, rng = lwp3.decode_port_mode_information(bytes([2, 0, 1]) + bytes.fromhex("00000000 00007c42"))
        self.assertEqual(rng, (0.0, 63.0))

    def test_generic_error(self):
        text = lwp3.describe(lwp3.parse(bytes.fromhex("05 00 05 22 06")))
        self.assertIn("PORT_MODE_INFORMATION_REQUEST", text)
        self.assertIn("INVALID_USE", text)


class MarioCodesTests(unittest.TestCase):
    def lookup_factory(self, io_type, mode, vf):
        return lambda port: (io_type, mode, vf)

    def test_pants(self):
        msg = lwp3.parse(bytes.fromhex("05 00 45 02 21"))
        text = lwp3.describe(msg, self.lookup_factory(codes.IO_MARIO_PANTS, 0, ValueFormat(1, 0, 2, 0)), codes.decode_value)
        self.assertIn("Mario normale", text)
        self.assertIn("0b100001", text)

    def test_gesture_bits(self):
        self.assertEqual(codes.gesture_names(0x8001), ["urto (bump)", "salto (jump)"])
        self.assertIn("bit sconosciuti", codes.gesture_names(0x0002)[0])
        text = codes.decode_value(codes.IO_MARIO_ACCELEROMETER, 1, bytes([0x10, 0x00]), [])
        self.assertIn("scossa", text)

    def test_barcode_and_color(self):
        self.assertTrue(codes.decode_value(codes.IO_MARIO_SCANNER, 0, bytes.fromhex("02 00 ff ff"), [])
                        .startswith("barcode 0x0002 = Goomba"))
        self.assertEqual(codes.decode_value(codes.IO_MARIO_SCANNER, 0, bytes.fromhex("ff ff 25 00"), []),
                         "colore 0x0025 = verde (erba)")
        self.assertIn("sollevato", codes.decode_value(codes.IO_MARIO_SCANNER, 0, bytes.fromhex("ff ff ff ff"), []))
        self.assertEqual(codes.decode_value(codes.IO_MARIO_SCANNER, 1, bytes([1, 2, 3]), []), "RGB r=1 g=2 b=3")

    def test_barcode_table_from_pylegomario(self):
        # codici a 16 bit little endian: '0x2f01ffff' nel JSON = byte 2f 01 -> 0x012F
        self.assertIn("Dondolo", codes.BARCODES[0x012F])
        self.assertIn("Traguardo", codes.BARCODES[0x00B7])
        self.assertIn("Start Peach", codes.BARCODES[0x014D])
        self.assertGreaterEqual(len(codes.BARCODES), 55)
        self.assertEqual(codes.decode_value(codes.IO_MARIO_SCANNER, 0, bytes.fromhex("b8 00 ff ff"), []),
                         "barcode 0x00B8 = Start Mario (tubo di partenza)")

    def test_accelerometer_raw(self):
        self.assertEqual(codes.decode_value(codes.IO_MARIO_ACCELEROMETER, 0, bytes([0xFF, 0x01, 0x40]), []),
                         "accelerazione x=-1 y=1 z=64")

    def test_default_mode_by_name(self):
        # firmware 6.5: porta 4 ha un solo modo "Events" (numero 0), non il modo 1 delle note community
        self.assertEqual(codes.default_mode(codes.IO_MARIO_ALT_EVENTS, {0: "Events"}), 0)
        self.assertEqual(codes.default_mode(codes.IO_MARIO_ACCELEROMETER, {0: "RAW", 1: "GEST"}), 1)
        self.assertEqual(codes.default_mode(codes.IO_MARIO_ACCELEROMETER, {0: "RAW", 1: "GEST"}, ("RAW",)), 0)
        self.assertIsNone(codes.default_mode(codes.IO_MARIO_TELEMETRY, {0: "Telemetry"}))

    def test_si_scaling_voltage(self):
        scale = ((0.0, 2705.0), (0.0, 3300.0), "mV")
        self.assertEqual(lwp3.scale_values([2705], scale), [3300.0])
        self.assertIsNone(lwp3.scale_values([5], ((0.0, 63.0), (0.0, 63.0), "idx")))
        msg = lwp3.parse(bytes.fromhex("06 00 45 06 91 0a"))  # raw 0x0a91 = 2705
        text = lwp3.describe(msg, lambda port: (codes.IO_VOLTAGE, 0, ValueFormat(1, 1, 4, 0), scale), codes.decode_value)
        self.assertIn("SI: [3300.0] mV", text)
        self.assertIn("circa 3300 mV", text)

    def test_property_parsing(self):
        self.assertEqual(lwp3.parse_hub_property("volume"), 0x12)
        self.assertEqual(lwp3.parse_hub_property("BATTERY_VOLTAGE"), 0x06)
        self.assertEqual(lwp3.parse_hub_property("0x0b"), 0x0B)


if __name__ == "__main__":
    unittest.main()
