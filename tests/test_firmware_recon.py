"""tools/firmware_recon.py: estrazione da APK annidati, intestazione OAD di TI, entropia, stringhe, versione."""
import importlib.util
import io
import os
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def load_tool():
    spec = importlib.util.spec_from_file_location("firmware_recon", PROJECT / "tools" / "firmware_recon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_firmware() -> bytes:
    """Immagine finta: intestazione CC26x2R1 + segmento contiguo + tabella dei vettori + stringhe del firmware."""
    vectors = struct.pack("<IIII", 0x20004000, 0x00000101, 0x00000201, 0x00000301)
    body = vectors + bytes(range(256)) * 8 + b"LEAF 0x65030000 Jan  1 2026 00:00:00\x00Game identifier: LEAFGAMEFILE\x00" \
        + b"Events\x00Telemetry\x00" + bytes(64)
    segment = struct.pack("<BHBI", 1, 0x0001, 0, 8 + len(body))
    length = 44 + len(segment) + len(body)
    rest = struct.pack("<BBHBBBBIII4sIHH", 1, 1, 0x00FE, 0xFF, 0xFC, 1, 0, 0xFFFFFFFF, length, 0x000000D1,
                       b"0001", length, 44, 0xFFFF)
    image = b"CC26x2R1" + b"\x00" * 4 + rest + segment + body
    crc = zlib.crc32(image[12:]) & 0xFFFFFFFF
    return image[:8] + struct.pack("<I", crc) + image[12:]


def zip_bytes(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


class FirmwareReconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_extract_from_nested_containers_and_analyze(self):
        firmware = fake_firmware()
        noise = os.urandom(64 * 1024)
        apk = zip_bytes({"assets/Firmware/LEAF_FW_V65030000_GE.bin": firmware, "classes.dex": b"dex",
                         "assets/AssetPack/Mario/Mario_AssetPack-6-0-0-211.bin": noise})
        xapk = zip_bytes({"com.lego.supermario.apk": apk, "manifest.json": b"{}"})
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "app.xapk"
            src.write_bytes(xapk)
            out = Path(tmp) / "out"
            files = self.tool.extract([src], out)
            self.assertEqual(sorted(f.name for f in files), ["LEAF_FW_V65030000_GE.bin", "Mario_AssetPack-6-0-0-211.bin"])
            fw = self.tool.analyze(out / "LEAF_FW_V65030000_GE.bin")
            self.assertEqual(fw["version_from_name"], "6.5.03")
            self.assertEqual(fw["oad"]["chip"], "CC26x2 (SimpleLink)")
            self.assertTrue(fw["oad"]["crc_ok"])
            self.assertEqual(fw["oad"]["image_type"], "applicazione")
            self.assertEqual(fw["oad"]["segments"][0]["type"], "contiguous")
            self.assertEqual(fw["vector_tables"][0]["sp"], "0x20004000")
            self.assertIn("LEAF 0x65030000 Jan  1 2026 00:00:00", fw["leaf_strings"])
            self.assertTrue(fw["verdict"].startswith("in chiaro"))
            pack = self.tool.analyze(out / "Mario_AssetPack-6-0-0-211.bin")
            self.assertIsNone(pack["oad"])
            self.assertGreater(pack["entropy"], 7.9)
            self.assertTrue(pack["verdict"].startswith("quasi tutto ad alta entropia"))

    def test_nothing_found_and_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "altro.apk"
            src.write_bytes(zip_bytes({"assets/readme.txt": b"niente"}))
            self.assertEqual(self.tool.extract([src], Path(tmp) / "out"), [])
        self.assertEqual(self.tool.version_from_name("LEAF_FW_V50070000_GE.bin"), "5.0.07")
        self.assertIsNone(self.tool.version_from_name("Mario_OadFull-3-3-0-199.bin"))
        self.assertEqual(self.tool.entropy(bytes(100)), 0.0)
        self.assertAlmostEqual(self.tool.entropy(bytes(range(256))), 8.0)
        self.assertIsNone(self.tool.oad_header(b"xx" * 40))


if __name__ == "__main__":
    unittest.main()
