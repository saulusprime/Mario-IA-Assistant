"""Pagine della dashboard in un Firefox headless vero (WebDriver BiDi, vedi tests/browser.py).

Il server gira in un thread con un database temporaneo e senza Bluetooth; i test aprono le pagine,
controllano che il JavaScript non dia errori e provano i giri principali (editor delle avventure,
registrazione di un percorso, partita simulata, replay). Saltati se Firefox non c'è.
"""
import importlib.util
import logging
import tempfile
import threading
import unittest
from pathlib import Path

from tests.browser import Browser, BrowserError, firefox_command

FIREFOX = firefox_command()


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
@unittest.skipUnless(FIREFOX, "Firefox non disponibile")
class DashboardInBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from werkzeug.serving import make_server
        from mario.web import create_app
        logging.getLogger("werkzeug").setLevel(logging.ERROR)   # niente riga per ogni richiesta
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(db_path=Path(cls.tmp.name) / "b.sqlite3", start_worker=False, quiet_assistant=True)
        cls.server = make_server("127.0.0.1", 0, cls.app, threaded=True)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        try:
            cls.browser = Browser(FIREFOX)
        except BrowserError as exc:
            cls.server.shutdown()
            cls.tmp.cleanup()
            raise unittest.SkipTest(f"Firefox non parte: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.server.shutdown()
        cls.tmp.cleanup()

    def open(self, path):
        b = self.browser
        b.errors()
        b.goto(self.base + path)
        return b

    def assertNoErrors(self, where):
        self.assertEqual(self.browser.errors(), [], where)

    def test_every_page_loads_without_javascript_errors(self):
        from mario.web.app import MENU
        for _key, _label, href in MENU + [("play", "", "/adventures/play")]:
            b = self.open(href)
            self.assertTrue(b.js("document.querySelector('main') !== null"), href)
            b.js("new Promise((ok) => setTimeout(ok, 400))")   # richieste asincrone della pagina
            self.assertNoErrors(href)
        # il controllo vede davvero gli errori: uno provocato apposta deve comparire
        b.js("setTimeout(() => { throw new Error('errore di prova'); }, 0) && true")
        b.js("new Promise((ok) => setTimeout(ok, 200))")
        self.assertTrue(any("errore di prova" in e for e in b.errors()))

    def test_adventure_editor_and_record_mode(self):
        b = self.open("/adventures")
        b.wait_for("document.querySelectorAll('#library .element').length > 20")
        self.assertTrue(b.js("[...document.querySelectorAll('#library .element')].some((e) => e.textContent.includes('fine del livello'))"))
        b.click(".adv-edit")
        b.wait_for("document.querySelector('#ed-name').value === 'Caccia al tesoro dei colori'")
        self.assertEqual(b.js("document.querySelectorAll('#steps-table tbody tr').length"), 4)
        # un passo selezionato prende il trigger dall'elemento cliccato
        b.js("document.querySelector('#steps-table tbody tr').click()")
        b.js("[...document.querySelectorAll('#library .element')].find((e) => e.textContent.includes('fine del livello')).click()")
        self.assertEqual(b.js("document.querySelector('#steps-table tbody tr .st-kind').value"), "level")
        self.assertEqual(b.js("document.querySelector('#steps-table tbody tr .st-param').value"), "fine")
        # registrazione di un percorso con le basette simulate
        b.click("#adv-record")
        b.wait_for("!document.querySelector('#rec-box').classList.contains('hidden')")
        b.click("[data-recsim='basetta_start']")
        b.wait_for("document.querySelector('#rec-tiles').textContent.includes('Start')")
        b.click("[data-recsim='basetta_goomba']")
        b.wait_for("document.querySelector('#rec-tiles').textContent.includes('Goomba')")
        b.click("#adv-record")
        b.wait_for("document.querySelector('#ed-name').value.startsWith('Percorso registrato')")
        self.assertEqual(b.js("[...document.querySelectorAll('#steps-table tbody .st-title')].map((e) => e.value)"),
                         ["Start Mario", "Goomba"])
        self.assertNoErrors("editor")

    def test_play_and_replay(self):
        client = self.app.test_client()
        r = client.post("/api/adventures", json={"data": {"name": "Prova browser", "steps": [
            {"id": "uno", "text": "Premi il pulsante", "trigger": {"kind": "button"}, "points": 7},
            {"id": "due", "text": "Fine del livello", "trigger": {"kind": "level", "name": "fine"}, "points": 3}]}})
        aid = r.get_json()["id"]
        b = self.open(f"/adventures/play?id={aid}")
        b.click("#play-start")
        b.wait_for("document.querySelector('#pl-text').textContent === 'Premi il pulsante'")
        b.click("[data-advsim='pulsante']")
        b.wait_for("document.querySelector('#pl-score').textContent === '7'")
        b.click("[data-advsim='livello_fine']")
        b.wait_for("document.querySelector('#pl-text').textContent.startsWith('Finita: completata')")
        self.assertNoErrors("gioco")
        run_id = client.get("/api/adventure/runs").get_json()["runs"][0]["id"]
        b = self.open(f"/adventures/runs/{run_id}")
        self.assertEqual(b.js("document.querySelector('#rp-score').textContent"), "0")
        b.js("document.querySelector('#rp-speed').value = '10'")
        b.click("#rp-play")
        b.wait_for("document.querySelector('#rp-text').textContent.startsWith('Finita: completata')", timeout=15)
        self.assertEqual(b.js("document.querySelector('#rp-score').textContent"), "10")
        self.assertEqual(b.js("document.querySelectorAll('#rp-results li.ok').length"), 2)
        self.assertNoErrors("replay")


if __name__ == "__main__":
    unittest.main()
