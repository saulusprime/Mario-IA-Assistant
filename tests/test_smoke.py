"""Test di fumo: il pacchetto si importa senza Bluetooth e la CLI risponde a --help (anche in CI)."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SmokeTests(unittest.TestCase):
    def test_import_without_ble(self):
        import mario
        from mario import codes, lwp3, hubstate, decode  # noqa: F401
        self.assertRegex(mario.__version__, r"^\d+\.\d+\.\d+$")
        self.assertIn("volume", lwp3.HUB_PROPERTY_ALIASES)
        self.assertGreater(len(codes.BARCODES), 55)

    def test_pyproject_version_matches_package(self):
        import mario
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{mario.__version__}"', text)

    @unittest.skipUnless(importlib.util.find_spec("bleak"), "la CLI importa bleak")
    def test_cli_help_and_decode(self):
        res = subprocess.run([sys.executable, "-m", "mario", "--help"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(res.returncode, 0, res.stderr)
        for cmd in ("scan", "monitor", "shell", "assistant", "web", "decode", "live-checks"):
            self.assertIn(cmd, res.stdout)
        res = subprocess.run([sys.executable, "-m", "mario", "decode", "05 00 45 02 21"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Mario normale", res.stdout)


if __name__ == "__main__":
    unittest.main()
