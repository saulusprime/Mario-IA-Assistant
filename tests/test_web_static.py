"""Controlli statici della dashboard: sintassi del JavaScript (node, se c'è) e coerenza id/azioni fra app.js e template."""
import importlib.util
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "mario" / "web"
JS = WEB / "static" / "app.js"
TEMPLATES = list((WEB / "templates").glob("*.html"))


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.js = JS.read_text(encoding="utf-8")
        self.html = "\n".join(t.read_text(encoding="utf-8") for t in TEMPLATES)

    @unittest.skipUnless(shutil.which("node"), "node non disponibile")
    def test_javascript_syntax(self):
        res = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_ids_used_in_js_exist_in_templates(self):
        used = set(re.findall(r"""\$\$?\(\s*["']#([\w-]+)""", self.js))
        used |= set(re.findall(r"""getElementById\(\s*["']([\w-]+)""", self.js))
        defined = set(re.findall(r"""\bid=["']([\w-]+)["']""", self.html))
        missing = sorted(i for i in used if i not in defined)
        self.assertEqual(missing, [], f"id usati in app.js ma assenti nei template: {missing}")

    def test_data_actions_in_templates_are_handled(self):
        actions = set(re.findall(r"""data-action=["']([\w-]+)["']""", self.html))
        handled = set(re.findall(r"""name === ["']([\w-]+)["']""", self.js))
        self.assertTrue(actions, "nessun data-action nei template?")
        self.assertEqual(sorted(actions - handled), [], f"data-action senza gestore in doAction: {actions - handled}")

    @unittest.skipUnless(importlib.util.find_spec("bleak") and importlib.util.find_spec("flask"), "servono bleak e flask")
    def test_simulations_match_buttons(self):
        from mario.web.app import SIMULATIONS
        buttons = set(re.findall(r"""data-sim=["']([\w-]+)["']""", self.html))
        self.assertEqual(buttons, set(SIMULATIONS), "pulsanti di simulazione e SIMULATIONS devono coincidere")

    def test_pages_in_js_have_templates(self):
        pages = set(re.findall(r"""page === ["'](\w+)["']""", self.js))
        names = {t.stem for t in TEMPLATES}
        self.assertEqual(sorted(pages - names), [], f"pagine gestite in app.js senza template: {pages - names}")


if __name__ == "__main__":
    unittest.main()
