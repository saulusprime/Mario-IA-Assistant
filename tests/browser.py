"""Firefox headless comandato con WebDriver BiDi, per i test delle pagine della dashboard.

Niente Playwright né Selenium: Firefox (dalla 129) parla BiDi su un WebSocket aperto con
`--remote-debugging-port`, e il client WebSocket qui sotto usa solo la libreria standard.
Dentro un sandbox Flatpak (il terminale di VS Code) Firefox viene avviato sull'host con
`flatpak-spawn --host`: la rete è condivisa, quindi raggiunge il server di prova del sandbox.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
from typing import Any, Optional


class BrowserError(RuntimeError):
    pass


def firefox_command() -> Optional[list[str]]:
    """Comando di Firefox, o None se non c'è (i test vengono saltati)."""
    for name in ("firefox-esr", "firefox"):
        if shutil.which(name):
            return [name]
    if os.path.exists("/.flatpak-info") and shutil.which("flatpak-spawn"):
        for name in ("firefox-esr", "firefox"):
            res = subprocess.run(["flatpak-spawn", "--host", "which", name], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return ["flatpak-spawn", "--host", name]
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebSocket:
    """Client WebSocket minimo (RFC 6455): messaggi di testo, frammenti, ping."""

    def __init__(self, url: str, timeout: float = 30.0):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("risposta incompleta all'apertura del WebSocket")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.decode(errors="replace"))

    def _frame(self, opcode: int, data: bytes) -> None:
        n = len(data)
        header = bytearray([0x80 | opcode])
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            header += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode("utf-8"))

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("WebSocket chiuso")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self) -> str:
        message = b""
        while True:
            b0, b1 = self._read(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            mask = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("WebSocket chiuso dal browser")
            if opcode == 0x9:
                self._frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            message += payload
            if b0 & 0x80:
                return message.decode("utf-8")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """Una scheda di Firefox headless: navigazione, JavaScript nella pagina, errori della console."""

    def __init__(self, command: list[str], start_timeout: float = 40.0):
        self.port = free_port()
        # profilo temporaneo creato e cancellato dalla shell dove gira Firefox (host o sandbox)
        script = ('d=$(mktemp -d); "$0" --headless -no-remote -profile "$d" --remote-debugging-port "$1" '
                  '>/dev/null 2>&1; rm -rf "$d"')
        prefix, name = command[:-1], command[-1]
        self.proc = subprocess.Popen(prefix + ["sh", "-c", script, name, str(self.port)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + start_timeout
        while True:
            try:
                self.ws = WebSocket(f"ws://127.0.0.1:{self.port}/session")
                break
            except OSError:
                if time.time() > deadline or self.proc.poll() is not None:
                    self.proc.kill()
                    raise BrowserError("Firefox non risponde sulla porta BiDi")
                time.sleep(0.3)
        self.next_id = 0
        self.events: list[dict] = []
        self.command("session.new", {"capabilities": {}})
        self.command("session.subscribe", {"events": ["log.entryAdded"]})
        self.context = self.command("browsingContext.getTree", {})["contexts"][0]["context"]

    def command(self, method: str, params: dict) -> dict:
        self.next_id += 1
        my_id = self.next_id
        self.ws.send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("type") == "event":
                self.events.append(msg)
            elif msg.get("id") == my_id:
                if msg.get("type") == "error":
                    raise BrowserError(f"{method}: {msg.get('error')}: {msg.get('message')}")
                return msg.get("result") or {}

    def goto(self, url: str) -> None:
        self.command("browsingContext.navigate", {"context": self.context, "url": url, "wait": "complete"})

    def js(self, expression: str) -> Any:
        """Valuta un'espressione nella pagina (anche una Promise) e ne restituisce il valore via JSON."""
        wrapped = f"(async () => JSON.stringify(await ({expression})))()"
        res = self.command("script.evaluate", {"expression": wrapped, "target": {"context": self.context},
                                               "awaitPromise": True, "resultOwnership": "none"})
        if res.get("type") == "exception":
            raise BrowserError(res["exceptionDetails"].get("text", "eccezione nella pagina"))
        value = res.get("result", {}).get("value")
        return json.loads(value) if isinstance(value, str) else None

    def wait_for(self, expression: str, timeout: float = 8.0) -> Any:
        deadline = time.time() + timeout
        while True:
            value = self.js(expression)
            if value:
                return value
            if time.time() > deadline:
                raise BrowserError(f"condizione mai vera: {expression}")
            time.sleep(0.1)

    def click(self, selector: str) -> None:
        found = self.js(f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
                        f"if (!el) return false; el.click(); return true; }})()")
        if not found:
            raise BrowserError(f"elemento non trovato: {selector}")

    def screenshot(self, path: str, whole_page: bool = False) -> None:
        """Foto della scheda in PNG (per controllare a occhio una pagina dopo qualche clic)."""
        res = self.command("browsingContext.captureScreenshot",
                           {"context": self.context, "origin": "document" if whole_page else "viewport"})
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(res["data"]))

    def errors(self) -> list[str]:
        """Errori della console e del JavaScript arrivati finora (poi la lista riparte da zero)."""
        out = [f"{e['params'].get('type')}: {e['params'].get('text')}" for e in self.events
               if e.get("method") == "log.entryAdded" and e["params"].get("level") == "error"]
        self.events.clear()
        return out

    def close(self) -> None:
        try:
            self.command("browser.close", {})
        except (BrowserError, OSError, ValueError):
            pass
        self.ws.close()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
