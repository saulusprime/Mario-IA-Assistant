"""
Ponte verso altri programmi: gli eventi di Mario, tradotti in fatti con un nome (gesto, basetta,
colore, pantaloni, fase del livello, pulsante, batteria, avventura), pubblicati su MQTT e/o su un
WebSocket. Solo libreria standard: client MQTT 3.1.1 (QoS 0) e server WebSocket minimi.

Sorgenti:
  - le righe di `./run.sh monitor --json` dallo standard input (schema in docs/monitor-json.md);
  - il flusso della dashboard (`--dashboard http://127.0.0.1:8765`), utile perché Mario accetta una
    sola connessione: se la dashboard è aperta, il ponte legge da lei (anche gli eventi delle avventure).

Uso:
  ./run.sh monitor --json | ./run.sh bridge --mqtt localhost
  ./run.sh bridge --dashboard http://127.0.0.1:8765 --ws 8766
  ./run.sh bridge --dashboard http://127.0.0.1:8765 --print
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import sys
import threading
import time
import urllib.request
from typing import Callable, Iterable, Iterator, Optional

from . import codes
from .adventures.engine import AdventureRunner
from .adventures.model import GESTURE_NAMES, LEVEL_EVENT, LEVEL_PHASE_NAMES
from .assistant.intents import short_name, tile_family

LEVEL_PHASES = {value: name for name, value in LEVEL_PHASE_NAMES.items()}
COINS_EVENT = 0x386B  # totale delle monete del livello (ASIS, sezione 2)
STATE_KINDS = {"pants", "battery", "level", "coins"}  # su MQTT restano come ultimo valore (retain)


# ----- traduzione degli eventi -------------------------------------------------------------

def translate(ev: dict) -> list[dict]:
    """Da un evento grezzo (riga di monitor --json o evento della dashboard) ai fatti con un nome."""
    ts = ev.get("ts") or time.time()
    if ev.get("type") == "ADVENTURE":
        return [{"kind": "adventure", "ts": ts, "event": ev.get("kind"), "adventure": ev.get("adventure"),
                 "step": ev.get("step"), "score": ev.get("score"), "text": ev.get("text", "")}]
    if ev.get("type") == "HUB_PROPERTIES" and ev.get("prop") == "BATTERY_VOLTAGE":
        return [{"kind": "battery", "ts": ts, "percent": ev.get("value")}]
    signal = AdventureRunner._signal(ev)
    if signal is None:
        return []
    kind, p = signal
    if kind == "gesture":
        bits = p["bits"]
        if not bits:
            return []
        names = [name for name, bit in GESTURE_NAMES.items() if bits & bit]
        unknown = bits & ~sum(GESTURE_NAMES.values())
        return [{"kind": "gesture", "ts": ts, "bits": f"0x{bits:04X}", "names": names,
                 **({"unknown_bits": f"0x{unknown:04X}"} if unknown else {})}]
    if kind == "scanner":
        barcode, color = p["barcode"], p["color"]
        if barcode == 0xFFFF and color == 0xFFFF:
            return [{"kind": "lifted", "ts": ts}]
        out = []
        if barcode != 0xFFFF:
            out.append({"kind": "tile", "ts": ts, "code": f"0x{barcode:04X}", "family": tile_family(barcode),
                        "name": short_name(codes.BARCODES.get(barcode, f"0x{barcode:04X}"))})
        if color != 0xFFFF:
            out.append({"kind": "color", "ts": ts, "code": f"0x{color:04X}",
                        "name": short_name(codes.COLORS.get(color, f"0x{color:04X}"))})
        return out
    if kind == "pants":
        code = p["code"]
        return [{"kind": "pants", "ts": ts, "code": f"0x{code:02X}",
                 "name": short_name(codes.PANTS.get(code, f"0x{code:02X}"))}]
    if kind == "button":
        return [{"kind": "button", "ts": ts}]
    if kind == "game_event":
        code, value = p["code"], p["value"]
        description = codes.GAME_EVENTS.get(code, "").split(" (")[0].split(":")[0][:80]
        out = [{"kind": "game_event", "ts": ts, "code": f"0x{code:04X}", "value": value,
                "description": description or None}]
        if code == LEVEL_EVENT and value in LEVEL_PHASES:
            out.insert(0, {"kind": "level", "ts": ts, "phase": LEVEL_PHASES[value]})
        elif code == COINS_EVENT:
            out.insert(0, {"kind": "coins", "ts": ts, "total": value})
        return out
    return []


# ----- sorgenti -----------------------------------------------------------------------------

def read_json_lines(lines: Iterable[str]) -> Iterator[dict]:
    """Righe JSON (monitor --json); le altre (messaggi, righe vuote) vengono ignorate."""
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def read_sse(lines: Iterable[str]) -> Iterator[dict]:
    """Eventi di un flusso Server-Sent Events (righe `data: {...}` separate da una riga vuota)."""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield json.loads("\n".join(data))
                except ValueError:
                    pass
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def dashboard_events(base_url: str, token: str = "", retry: float = 3.0,
                     log: Callable[[str], None] = lambda text: None) -> Iterator[dict]:
    """Flusso /api/stream della dashboard, con riconnessione se il server si riavvia."""
    url = base_url.rstrip("/") + "/api/stream"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
                log(f"collegato a {url}")
                yield from read_sse(line.decode("utf-8", "replace") for line in resp)
        except (OSError, ValueError) as exc:
            log(f"flusso della dashboard non disponibile ({exc}): riprovo tra {retry:g} s")
        time.sleep(retry)


# ----- MQTT 3.1.1, solo pubblicazione QoS 0 ------------------------------------------------------

def _mqtt_string(text: str) -> bytes:
    data = text.encode("utf-8")
    return len(data).to_bytes(2, "big") + data


def _mqtt_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


class MqttPublisher:
    def __init__(self, host: str, port: int = 1883, client_id: str = "mario-bridge", keepalive: int = 60,
                 username: str = "", password: str = ""):
        self.keepalive = keepalive
        self.sock = socket.create_connection((host, port), timeout=10)
        self._lock = threading.Lock()
        flags = 0x02 | (0x80 if username else 0) | (0x40 if password else 0)  # sessione pulita
        body = _mqtt_string("MQTT") + bytes([4, flags]) + keepalive.to_bytes(2, "big") + _mqtt_string(client_id)
        body += (_mqtt_string(username) if username else b"") + (_mqtt_string(password) if password else b"")
        try:
            self._send(0x10, body)
            ack = self._recv(4)
            if ack[0] != 0x20 or ack[3] != 0:
                raise ConnectionError(f"il broker MQTT ha rifiutato la connessione (codice {ack[3]})")
        except (OSError, ConnectionError):
            self.sock.close()
            raise
        self.sock.settimeout(None)
        self.closed = False
        threading.Thread(target=self._reader, name="mqtt-reader", daemon=True).start()
        threading.Thread(target=self._pinger, name="mqtt-ping", daemon=True).start()

    def _recv(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("il broker MQTT ha chiuso la connessione")
            data += chunk
        return data

    def _send(self, header: int, body: bytes) -> None:
        with self._lock:
            self.sock.sendall(bytes([header]) + _mqtt_length(len(body)) + body)
            self.last_sent = time.time()

    def _reader(self) -> None:
        """Legge (e scarta) le risposte ai ping; si accorge della chiusura."""
        try:
            while self.sock.recv(256):
                pass
        except OSError:
            pass
        self.closed = True

    def _pinger(self) -> None:
        while not self.closed:
            time.sleep(1.0)
            if time.time() - self.last_sent > self.keepalive / 2:
                try:
                    self._send(0xC0, b"")
                except OSError:
                    self.closed = True

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if self.closed:
            raise ConnectionError("connessione MQTT chiusa")
        self._send(0x30 | (0x01 if retain else 0), _mqtt_string(topic) + payload.encode("utf-8"))

    def close(self) -> None:
        try:
            self._send(0xE0, b"")  # DISCONNECT
            self.sock.close()
        except OSError:
            pass
        self.closed = True


# ----- server WebSocket: ogni fatto a tutti i client collegati --------------------------------------

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_frame(text: str) -> bytes:
    data = text.encode("utf-8")
    n = len(data)
    if n < 126:
        header = bytes([0x81, n])
    elif n < 65536:
        header = bytes([0x81, 126]) + n.to_bytes(2, "big")
    else:
        header = bytes([0x81, 127]) + n.to_bytes(8, "big")
    return header + data


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = conn.recv(min(n - len(data), 65536))
        if not chunk:
            raise ConnectionError("connessione chiusa")
        data += chunk
    return data


class WebSocketHub:
    def __init__(self, host: str = "127.0.0.1", port: int = 8766):
        self.server = socket.create_server((host, port))
        self.port = self.server.getsockname()[1]
        self.clients: list[socket.socket] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._accept, name="ws-accept", daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _addr = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), name="ws-client", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10)
            request = b""
            while b"\r\n\r\n" not in request and len(request) < 16384:
                chunk = conn.recv(4096)
                if not chunk:
                    conn.close()
                    return
                request += chunk
            headers = {}
            for line in request.split(b"\r\n")[1:]:
                key, _, value = line.decode("latin-1").partition(":")
                headers[key.strip().lower()] = value.strip()
            key = headers.get("sec-websocket-key")
            if not key or "websocket" not in headers.get("upgrade", "").lower():
                conn.sendall(b"HTTP/1.1 426 Upgrade Required\r\nContent-Type: text/plain; charset=utf-8\r\n"
                             b"Connection: close\r\n\r\nServe un client WebSocket.\n")
                conn.close()
                return
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            conn.settimeout(None)
            with self._lock:
                self.clients.append(conn)
            while True:  # i messaggi dei client non servono: si aspetta la chiusura
                head = _recv_exact(conn, 2)
                if head[0] & 0x0F == 0x8:
                    break
                n = head[1] & 0x7F
                if n >= 126:
                    n = int.from_bytes(_recv_exact(conn, 2 if n == 126 else 8), "big")
                _recv_exact(conn, n + (4 if head[1] & 0x80 else 0))
        except (OSError, ConnectionError):
            pass
        with self._lock:
            if conn in self.clients:
                self.clients.remove(conn)
        try:
            conn.close()
        except OSError:
            pass

    def broadcast(self, text: str) -> int:
        frame = ws_frame(text)
        with self._lock:
            clients = list(self.clients)
        sent = 0
        for conn in clients:
            try:
                conn.sendall(frame)
                sent += 1
            except OSError:
                with self._lock:
                    if conn in self.clients:
                        self.clients.remove(conn)
        return sent

    def close(self) -> None:
        self.server.close()
        with self._lock:
            for conn in self.clients:
                try:
                    conn.close()
                except OSError:
                    pass
            self.clients.clear()


# ----- ciclo principale ----------------------------------------------------------------------

def run(events: Iterable[dict], publish: list[Callable[[dict], None]], raw: bool = False) -> int:
    """Traduce ogni evento e lo passa a tutte le uscite; ritorna il numero di fatti pubblicati."""
    count = 0
    for ev in events:
        facts = translate(ev)
        if raw:
            facts = facts + [{"kind": "raw", "ts": ev.get("ts"), "event": ev}]
        for fact in facts:
            for out in publish:
                out(fact)
            count += 1
    return count


def main(args) -> int:
    def log(text: str) -> None:
        print(f"[bridge] {text}", file=sys.stderr, flush=True)

    outputs: list[Callable[[dict], None]] = []
    mqtt = hub = None
    if args.mqtt:
        host, _, port = args.mqtt.partition(":")
        mqtt = MqttPublisher(host, int(port or 1883), client_id=args.client_id,
                             username=args.mqtt_user or "", password=args.mqtt_password or "")
        log(f"MQTT: {host}:{port or 1883}, argomenti {args.topic}/<tipo>")
        outputs.append(lambda fact: mqtt.publish(f"{args.topic}/{fact['kind']}",
                                                 json.dumps(fact, ensure_ascii=False, default=str),
                                                 retain=fact["kind"] in STATE_KINDS))
    if args.ws is not None:
        hub = WebSocketHub(args.ws_host, args.ws)
        log(f"WebSocket: ws://{args.ws_host}:{hub.port}/")
        outputs.append(lambda fact: hub.broadcast(json.dumps(fact, ensure_ascii=False, default=str)))
    if args.print or not outputs:
        outputs.append(lambda fact: print(json.dumps(fact, ensure_ascii=False, default=str), flush=True))
    source = (dashboard_events(args.dashboard, args.token or "", log=log) if args.dashboard
              else read_json_lines(sys.stdin))
    if not args.dashboard:
        log("leggo le righe di `monitor --json` dallo standard input")
    try:
        run(source, outputs, raw=args.raw)
    except KeyboardInterrupt:
        pass
    finally:
        if mqtt:
            mqtt.close()
        if hub:
            hub.close()
    return 0
