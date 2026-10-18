"""Dashboard: pagina Eventi, esportazioni, grafici, accesso con token, scheda dispositivo (test client Flask)."""
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
class EventsPageTests(unittest.TestCase):
    def setUp(self):
        from mario.web import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tmp.name) / "t.sqlite3", start_worker=False, quiet_assistant=True)
        self.db = self.app.extensions["db"]
        self.client = self.app.test_client()
        self.did = self.db.upsert_device("AA:BB:CC", name="Mario test", fw_version="6.5.01.0000", volume=50)
        t0 = time.time() - 600
        rows = [
            ("HUB_PROPERTIES", None, None, "06 00 01 06 06 4b", "Proprietà BATTERY_VOLTAGE (0x06) [UPDATE]: 75 %", None),
            ("PORT_VALUE_SINGLE", 6, 0, "06 00 45 06 a0 08", "Valore porta 6", "batteria circa 2694 mV (raw 2208)"),
            ("HUB_PROPERTIES", None, None, "06 00 01 06 06 46", "Proprietà BATTERY_VOLTAGE (0x06) [UPDATE]: 70 %", None),
            ("PORT_VALUE_SINGLE", 0, 1, "08 00 45 00 00 80 00 80", "Valore porta 0", "gesto: salto (jump) (0x8000)"),
            ("PORT_VALUE_SINGLE", 0, 1, "08 00 45 00 01 80 01 80", "Valore porta 0", "gesto: urto (bump), salto (jump) (0x8001)"),
            ("PORT_VALUE_SINGLE", 0, 1, "08 00 45 00 00 00 00 00", "Valore porta 0", "gesto: nessuno (0x0000)"),
            ("PORT_VALUE_SINGLE", 1, 0, "08 00 45 01 02 00 25 00", "Valore porta 1", "barcode 0x0002 = Goomba (verificato); colore 0x0025 = verde (erba)"),
            ("PORT_VALUE_SINGLE", 1, 0, "08 00 45 01 b8 00 ff ff", "Valore porta 1", "barcode 0x00B8 = Start Mario (tubo di partenza)"),
            ("PORT_VALUE_SINGLE", 1, 0, "08 00 45 01 02 00 ff ff", "Valore porta 1", "barcode 0x0002 = Goomba (verificato)"),
        ]
        for i, (typ, port, mode, raw, text, decoded) in enumerate(rows):
            self.db.add_event(self.did, {"ts": t0 + i * 10, "type": typ, "port": port, "mode": mode, "raw": raw,
                                         "text": text, "decoded": decoded})

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_and_charts(self):
        r = self.client.get("/events")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Esporta CSV", r.data)
        self.assertIn(b"Mario test", r.data)
        charts = self.client.get("/api/charts").get_json()
        self.assertEqual([p["value"] for p in charts["battery"]["percent"]], [75, 70])
        self.assertEqual(charts["battery"]["millivolt"][0]["value"], 2694)
        self.assertEqual(charts["gestures"], {"salto (jump)": 2, "urto (bump)": 1})
        self.assertEqual(charts["tiles"], {"Goomba": 2, "Start Mario": 1})
        self.assertEqual(charts["types"]["PORT_VALUE_SINGLE"], 7)
        self.assertEqual(self.client.get("/api/charts?device_id=999").get_json()["gestures"], {})
        self.assertIn(b"/events", self.client.get("/").data)  # voce di menù

    def test_codes_page_and_api(self):
        html = self.client.get("/codes").data.decode("utf-8")
        for needle in ("Eventi di gioco", "0x1803", "fase del livello", "legomario.py", "ipotesi", "/codes",
                       "Basette senza nome", 'class="stripe" style="background:pink"', "al contrario di bricklife"):
            self.assertIn(needle, html)
        self.assertIn('href="/codes"', self.client.get("/accessories").data.decode("utf-8"))
        tables = self.client.get("/api/codes").get_json()["tables"]
        self.assertEqual(tables["gestures"]["0x8000"]["source"], "legomario.py")
        self.assertIn("_descrizione", tables)

    def test_scan_page_system_checks(self):
        from mario import syscheck
        fake = [syscheck.Check("autosospensione", syscheck.WARN, "hci0: attiva", "echo on | sudo tee x"),
                syscheck.Check("Wi-Fi", syscheck.OK, "wlp3s0: 5 GHz (5200 MHz)")]
        orig = syscheck.run_checks
        syscheck.run_checks = lambda *a, **k: fake  # niente comandi veri nei test
        try:
            html = self.client.get("/scan").get_data(as_text=True)
            r = self.client.get("/api/system/checks").get_json()
        finally:
            syscheck.run_checks = orig
        self.assertIn("Controllo del PC", html)
        self.assertIn("da sistemare", html)
        self.assertIn("<code>echo on | sudo tee x</code>", html)
        self.assertEqual((r["worst"], r["checks"][0]["fix"], r["checks"][1]["status"]),
                         ("attenzione", "echo on | sudo tee x", "ok"))

    def test_exports(self):
        r = self.client.get("/api/history.csv?port=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment; filename=mario-eventi-", r.headers["Content-Disposition"])
        lines = r.data.decode("utf-8-sig").splitlines()
        self.assertEqual(lines[0].split(";")[:5], ["id", "data_ora", "ts", "dispositivo", "tipo"])
        self.assertEqual(len(lines), 4)
        self.assertIn("Goomba", lines[1])
        r = self.client.get("/api/history.json?q=salto")
        rows = json.loads(r.data)
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[0]["ts"], rows[1]["ts"])  # ordine cronologico
        since = time.time() - 600 + 65
        r = self.client.get(f"/api/history.json?from={since}")
        self.assertEqual(len(json.loads(r.data)), 2)
        r = self.client.get("/api/history?limit=3")
        self.assertEqual(len(r.get_json()["events"]), 3)

    def test_time_range_and_combined_modes(self):
        t0 = time.time() - 600
        # da 35 s a 55 s dall'inizio: tre eventi (batteria 70 %, due gesti)
        rows = self.client.get(f"/api/history?from={t0 + 15}&to={t0 + 45}").get_json()["events"]
        self.assertEqual(len(rows), 3)
        charts = self.client.get(f"/api/charts?from={t0 + 15}&to={t0 + 45}").get_json()
        self.assertEqual([p["value"] for p in charts["battery"]["percent"]], [70])
        self.assertEqual(charts["gestures"], {"salto (jump)": 2, "urto (bump)": 1})
        self.assertEqual(charts["range"], {"since_ts": t0 + 15, "until_ts": t0 + 45})
        # evento in modalità combinata: i valori per modo restano nel database e nell'esportazione
        self.db.add_event(self.did, {"ts": t0 + 200, "type": "PORT_VALUE_COMBINED", "port": 0, "raw": "0b 00 46 00 00 07 01 02 03 00 80",
                                     "text": "Valori combinati porta 0", "decoded": "RAW: x=1 | GEST: salto",
                                     "modes": {"0": {"name": "RAW", "data": "01 02 03", "decoded": "accelerazione x=1 y=2 z=3"},
                                               "1": {"name": "GEST", "data": "00 80", "decoded": "gesto: salto (jump) (0x8000)"}}})
        lines = self.client.get("/api/history.csv?port=0").data.decode("utf-8-sig").splitlines()
        header = lines[0].split(";")
        self.assertEqual(header[-2:], ["modo RAW", "modo GEST"])
        self.assertEqual(lines[-1].split(";")[-2:], ["accelerazione x=1 y=2 z=3", "gesto: salto (jump) (0x8000)"])
        self.assertEqual(lines[1].split(";")[-2:], ["", ""])                   # eventi semplici: colonne vuote
        rows = json.loads(self.client.get("/api/history.json?type=PORT_VALUE_COMBINED").data)
        self.assertEqual(rows[0]["modes"]["1"]["name"], "GEST")
        self.assertIn('id="evp-reset"', self.client.get("/events").get_data(as_text=True))

    def test_device_update_volume_and_name(self):
        r = self.client.post(f"/api/devices/{self.did}", json={"name": "Nuovo", "volume": "35"}).get_json()
        self.assertTrue(r["ok"])
        row = self.db.get_device(self.did)
        self.assertEqual((row["name"], row["volume"]), ("Nuovo", 35))
        page = self.client.get(f"/devices/{self.did}")
        self.assertIn(b'id="dev-vol-set"', page.data)
        self.assertEqual(self.client.post("/api/volume", json={"level": 10}).status_code, 503)  # non connesso


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
class TokenTests(unittest.TestCase):
    def setUp(self):
        from mario.web import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tmp.name) / "t.sqlite3", start_worker=False, quiet_assistant=True,
                              token="segreto-123")
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pages_and_api_require_token(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.get_json()["ok"])
        css = self.client.get("/static/app.css")
        self.assertEqual(css.status_code, 200)
        css.close()  # send_file lascia il file aperto fino alla chiusura della risposta
        r = self.client.get("/api/status", headers={"Authorization": "Bearer segreto-123"})
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/status", headers={"Authorization": "Bearer sbagliato"})
        self.assertEqual(r.status_code, 401)

    def test_login_flow_and_query_token(self):
        r = self.client.post("/login", data={"token": "no", "next": "/monitor"})
        self.assertEqual(r.status_code, 403)
        self.assertIn(b"token errato", r.data)
        r = self.client.post("/login", data={"token": "segreto-123", "next": "/monitor"})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/monitor"))
        cookies = r.headers.getlist("Set-Cookie")
        self.assertTrue(any(c.startswith("mario_session=") for c in cookies))
        self.assertFalse(any("segreto-123" in c for c in cookies))          # il token non finisce nel cookie
        self.assertEqual(self.client.get("/monitor").status_code, 200)  # il cookie resta nel client di test
        self.assertEqual(self.client.post("/login", data={"token": "x", "next": "//altro.sito"}).status_code, 403)
        r = self.client.post("/login", data={"token": "segreto-123", "next": "//altro.sito"})
        self.assertEqual(r.headers["Location"], "/")                   # niente redirect verso altri siti
        fresh = self.app.test_client()
        r = fresh.get("/events?token=segreto-123")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/events"))
        self.assertEqual(fresh.get("/events").status_code, 200)
        self.assertEqual(fresh.get("/login").status_code, 200)

    def test_sessions_list_revoke_expire_and_token_change(self):
        from mario.web import create_app
        db = self.app.extensions["db"]
        phone, laptop = self.app.test_client(), self.app.test_client()
        phone.get("/?token=segreto-123", headers={"User-Agent": "Telefono"})
        laptop.post("/login", data={"token": "segreto-123", "next": "/"}, headers={"User-Agent": "Portatile"})
        sessions = laptop.get("/api/sessions").get_json()["sessions"]
        self.assertEqual(len(sessions), 2)
        mine = [x for x in sessions if x["current"]]
        self.assertTrue(mine and "Portatile" in mine[0]["client"])
        self.assertIn("Accessi alla dashboard", laptop.get("/").data.decode("utf-8"))
        other = [x for x in sessions if not x["current"]][0]
        self.assertEqual(laptop.post("/api/sessions/revoke", json={"id": other["id"]}).get_json()["revoked"], 1)
        self.assertEqual(phone.get("/api/status").status_code, 401)        # il telefono è fuori
        self.assertEqual(phone.get("/monitor").status_code, 302)
        self.assertEqual(laptop.get("/api/status").status_code, 200)
        # scadenza: la sessione del portatile portata nel passato
        row = db.list_web_sessions()[0]
        db._exec("UPDATE web_sessions SET expires=? WHERE id=?", (time.time() - 1, row["id"]))
        self.assertEqual(laptop.get("/api/status").status_code, 401)
        # un token nuovo chiude le sessioni aperte con il vecchio
        laptop.post("/login", data={"token": "segreto-123"})
        self.assertEqual(laptop.get("/api/status").status_code, 200)
        app2 = create_app(db_path=Path(self.tmp.name) / "t.sqlite3", start_worker=False, quiet_assistant=True,
                          token="nuovo-456", session_days=0.5)
        client2 = app2.test_client()
        client2.set_cookie("mario_session", laptop.get_cookie("mario_session").value)
        self.assertEqual(client2.get("/api/status").status_code, 401)
        self.assertEqual(db.list_web_sessions(), [])                       # tolte all'avvio
        r = client2.post("/login", data={"token": "nuovo-456"})
        self.assertIn("Max-Age=43200", r.headers["Set-Cookie"])            # mezza giornata
        # uscita
        r = client2.get("/logout")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(client2.get("/api/status").status_code, 401)
        self.assertEqual(laptop.post("/api/sessions/revoke", json={"id": "x"}).status_code, 401)

    def test_no_token_app_has_open_login(self):
        from mario.web import create_app
        app = create_app(db_path=Path(self.tmp.name) / "u.sqlite3", start_worker=False, quiet_assistant=True)
        self.assertEqual(app.test_client().get("/login").status_code, 302)
        self.assertEqual(app.test_client().get("/").status_code, 200)


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
class HttpsTests(unittest.TestCase):
    def test_self_signed_certificate_and_https_server(self):
        import shutil
        import ssl
        import threading
        import urllib.request
        from werkzeug.serving import make_server
        from mario import cli
        from mario.web import create_app
        if not shutil.which("openssl"):
            self.skipTest("openssl non disponibile")
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        with tempfile.TemporaryDirectory() as tmp:
            pair = cli.self_signed_certificate(Path(tmp) / "tls", "192.168.1.20")
            self.assertIsNotNone(pair)
            cert, key = pair
            self.assertEqual(cli.self_signed_certificate(Path(tmp) / "tls"), pair)       # riusato
            self.assertEqual(oct(key.stat().st_mode & 0o777), "0o600")
            self.assertRegex(cli.certificate_fingerprint(cert), r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
            app = create_app(db_path=Path(tmp) / "h.sqlite3", start_worker=False, quiet_assistant=True, token="t-789")
            server = make_server("127.0.0.1", 0, app, threaded=True, ssl_context=(str(cert), str(key)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                ctx = ssl.create_default_context(cafile=str(cert))   # fiducia solo in questo certificato
                req = urllib.request.Request(f"https://127.0.0.1:{server.server_port}/login",
                                             data=b"token=t-789&next=/", method="POST")
                opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                                     NoRedirect())
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(req, timeout=10)
                self.assertEqual(caught.exception.code, 302)
                cookie = caught.exception.headers["Set-Cookie"]
                self.assertIn("Secure", cookie)                                 # in HTTPS solo su HTTPS
                with opener.open(urllib.request.Request(f"https://127.0.0.1:{server.server_port}/api/status",
                                                        headers={"Authorization": "Bearer t-789"}), timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
            finally:
                server.shutdown()


class NoRedirect(__import__("urllib.request").request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
