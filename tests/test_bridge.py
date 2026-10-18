"""Ponte MQTT/WebSocket: traduzione degli eventi, broker MQTT finto, client WebSocket, flusso della dashboard."""
import importlib.util
import io
import itertools
import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from mario import bridge
from tests.browser import WebSocket


def single(io_type, mode, data_hex, **extra):
    return {"ts": 1.0, "type": "PORT_VALUE_SINGLE", "io_type": io_type, "mode": mode, "data": data_hex, **extra}


class TranslateTests(unittest.TestCase):
    def kinds(self, ev):
        return [f["kind"] for f in bridge.translate(ev)]

    def test_facts_from_monitor_lines(self):
        gesture = bridge.translate(single(0x47, 1, "00 80"))[0]
        self.assertEqual((gesture["kind"], gesture["names"], gesture["bits"]), ("gesture", ["salto"], "0x8000"))
        self.assertEqual(bridge.translate(single(0x47, 1, "04 00"))[0]["unknown_bits"], "0x0004")
        self.assertEqual(bridge.translate(single(0x47, 1, "00 00")), [])
        tile, color = bridge.translate(single(0x49, 0, "02 00 25 00"))
        self.assertEqual((tile["name"], tile["family"], tile["code"]), ("Goomba", "enemy", "0x0002"))
        self.assertEqual((color["kind"], color["code"]), ("color", "0x0025"))
        self.assertEqual(self.kinds(single(0x49, 0, "ff ff ff ff")), ["lifted"])
        self.assertEqual(bridge.translate(single(0x4A, 0, "12"))[0]["name"], "Fuoco")
        level = bridge.translate(single(0x46, 2, "03 18 04 00"))
        self.assertEqual((level[0]["kind"], level[0]["phase"], level[1]["kind"]), ("level", "ultimi_secondi", "game_event"))
        self.assertEqual(bridge.translate(single(0x46, 2, "6b 38 07 00"))[0], {"kind": "coins", "ts": 1.0, "total": 7})
        self.assertEqual(self.kinds({"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True}), ["button"])
        self.assertEqual(bridge.translate({"type": "HUB_PROPERTIES", "prop": "BATTERY_VOLTAGE", "value": 80})[0]["percent"], 80)
        combined = {"type": "PORT_VALUE_COMBINED", "io_type": 0x47, "modes": {"0": {"data": "01 02 03"}, "1": {"data": "10 00"}}}
        self.assertEqual(bridge.translate(combined)[0]["names"], ["scossa"])
        adventure = {"type": "ADVENTURE", "kind": "success", "adventure": "Quiz", "step": "a", "score": 10, "text": "Bravo!"}
        self.assertEqual(bridge.translate(adventure)[0]["event"], "success")
        self.assertEqual(self.kinds({"type": "HUB_ATTACHED_IO", "port": 1}), [])

    def test_readers_and_run(self):
        lines = ['[12:00:00] messaggio', json.dumps(single(0x47, 1, "00 80")), "{rotto", ""]
        events = list(bridge.read_json_lines(lines))
        self.assertEqual(len(events), 1)
        sse = ["retry: 2000", "", ": ping", "", "id: 1", 'data: {"type": "HUB_PROPERTIES",', 'data: "prop": "BUTTON", "value": true}', ""]
        self.assertEqual(list(bridge.read_sse(sse)), [{"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True}])
        got = []
        self.assertEqual(bridge.run(events, [got.append], raw=True), 2)
        self.assertEqual([f["kind"] for f in got], ["gesture", "raw"])


class FakeBroker:
    """Broker MQTT finto: accetta una connessione, risponde al CONNECT, raccoglie i PUBLISH."""

    def __init__(self, return_code=0):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.return_code = return_code
        self.connect = None
        self.publishes = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _read_packet(self, conn):
        header = conn.recv(1)
        if not header:
            return None, b""
        n, shift = 0, 0
        while True:
            byte = conn.recv(1)[0]
            n |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        body = b""
        while len(body) < n:
            body += conn.recv(n - len(body))
        return header[0], body

    def _serve(self):
        conn, _ = self.server.accept()
        self.server.close()
        _h, self.connect = self._read_packet(conn)
        conn.sendall(bytes([0x20, 0x02, 0x00, self.return_code]))
        while True:
            header, body = self._read_packet(conn)
            if header is None or header == 0xE0:
                break
            if header & 0xF0 == 0x30:
                n = int.from_bytes(body[:2], "big")
                self.publishes.append((body[2:2 + n].decode(), body[2 + n:].decode(), bool(header & 0x01)))
        conn.close()


class MqttTests(unittest.TestCase):
    def test_publish_to_fake_broker(self):
        broker = FakeBroker()
        pub = bridge.MqttPublisher("127.0.0.1", broker.port, client_id="prova", username="u", password="p")
        self.assertIn(b"MQTT\x04\xc2", broker.connect)        # protocollo 3.1.1, utente, password, sessione pulita
        pub.publish("mario/gesture", '{"kind": "gesture"}')
        pub.publish("mario/pants", "x" * 300, retain=True)    # lunghezza su due byte
        pub.close()
        deadline = time.time() + 3
        while len(broker.publishes) < 2 and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(broker.publishes[0], ("mario/gesture", '{"kind": "gesture"}', False))
        self.assertEqual((broker.publishes[1][0], len(broker.publishes[1][1]), broker.publishes[1][2]), ("mario/pants", 300, True))

    def test_refused_connection(self):
        broker = FakeBroker(return_code=5)
        with self.assertRaises(ConnectionError):
            bridge.MqttPublisher("127.0.0.1", broker.port)


class WebSocketTests(unittest.TestCase):
    def test_broadcast_to_clients(self):
        hub = bridge.WebSocketHub("127.0.0.1", 0)
        try:
            client = WebSocket(f"ws://127.0.0.1:{hub.port}/")
            deadline = time.time() + 3
            while not hub.clients and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(hub.broadcast('{"kind": "button"}'), 1)
            self.assertEqual(json.loads(client.recv()), {"kind": "button"})
            long_text = json.dumps({"kind": "raw", "text": "x" * 70000})
            hub.broadcast(long_text)
            self.assertEqual(client.recv(), long_text)
            client.send("ciao")                                  # i messaggi dei client vengono ignorati
            client.close()
            with socket.create_connection(("127.0.0.1", hub.port)) as plain:
                plain.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
                self.assertIn(b"426", plain.recv(200))
        finally:
            hub.close()


@unittest.skipUnless(importlib.util.find_spec("flask") and importlib.util.find_spec("bleak"),
                     "servono flask e bleak (ambiente host)")
class DashboardSourceTests(unittest.TestCase):
    def test_reads_the_dashboard_stream(self):
        from werkzeug.serving import make_server
        from mario.web import create_app
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(db_path=Path(tmp) / "b.sqlite3", start_worker=False, quiet_assistant=True)
            server = make_server("127.0.0.1", 0, app, threaded=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            worker = app.extensions["worker"]

            def feed():
                time.sleep(0.5)
                worker.push({"ts": 2.0, "type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True, "text": "pulsante"})
            threading.Thread(target=feed, daemon=True).start()
            events = bridge.dashboard_events(f"http://127.0.0.1:{server.server_port}")
            facts = list(itertools.islice((f for ev in events for f in bridge.translate(ev)), 1))
            events.close()
            server.shutdown()
        self.assertEqual(facts[0]["kind"], "button")

    def test_main_prints_facts_from_stdin(self):
        args = SimpleNamespace(mqtt=None, ws=None, print=False, raw=False, dashboard=None, token=None,
                               client_id="x", mqtt_user=None, mqtt_password=None, topic="mario", ws_host="127.0.0.1")
        out = io.StringIO()
        import sys
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(single(0x4A, 0, "11")) + "\n")
        try:
            with redirect_stdout(out):
                self.assertEqual(bridge.main(args), 0)
        finally:
            sys.stdin = stdin
        self.assertEqual(json.loads(out.getvalue())["name"], "Gatto")


if __name__ == "__main__":
    unittest.main()
