"""
Processo di lavoro con i modelli sempre caricati, interrogato con una riga JSON per richiesta.

Lo usano la voce IA (`openvoice.py`: piper + OpenVoice in `.venv-openvoice`) e l'ingresso vocale
(`stt.py`: faster-whisper in `.venv-stt`): ognuno gira con il proprio interprete, carica i modelli in
background e risponde così:

  avvio  -> {"event": "ready", "load_s": 1.2, ...}   oppure   {"event": "error", "error": "..."}
  {"id": 1, "cmd": "...", ...}  -> {"id": 1, "ok": true, ...}   oppure   {"id": 1, "ok": false, "error": "..."}
  {"cmd": "quit"}

Qui c'è il ciclo di vita comune: avvio senza attesa, richieste con tempo massimo (se il processo si
blocca viene riavviato), ripartenza dopo un crash, chiusura ordinata. Chi lo usa ha sempre una via di
riserva: una richiesta fallita non deve mai fermare la sessione.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional


class WorkerError(RuntimeError):
    pass


class WorkerProcess:
    """Avvio in background, richieste con tempo massimo, stato. Le sottoclassi danno `_argv()`, `label`,
    `log_file` ed eventualmente `error_class`."""

    label = "processo"
    error_class = WorkerError
    log_file: Path = Path("/dev/null")

    def __init__(self, command: Optional[list[str]] = None):
        self.command = command  # per i test: un processo finto che parla lo stesso protocollo
        self.state = "spento"   # spento | in avvio | pronto | errore
        self.last_error = ""
        self.load_s: Optional[float] = None
        self.started_at = 0.0
        self.info: dict = {}
        self.restarts = 0
        self._proc: Optional[subprocess.Popen] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, list]] = {}

    # ----- ciclo di vita ------------------------------------------------------------------

    def _argv(self) -> list[str]:  # pragma: no cover - sempre ridefinito
        raise NotImplementedError

    def start(self) -> None:
        """Avvia il processo senza aspettarlo: carica i modelli in background (alcuni secondi)."""
        self.stop()
        self._ready.clear()
        self.state, self.last_error, self.started_at = "in avvio", "", time.monotonic()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_file, "ab")
        try:
            self._proc = subprocess.Popen(self.command or self._argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=log, text=True, encoding="utf-8", bufsize=1)
        except OSError as exc:
            self._fail(f"avvio del processo {self.label} fallito: {exc}")
            return
        finally:
            log.close()
        threading.Thread(target=self._reader, args=(self._proc,), name=f"{self.label}-reader", daemon=True).start()

    def _fail(self, error: str) -> None:
        self.state, self.last_error = "errore", error
        self._ready.set()
        with self._lock:
            pending, self._pending = self._pending, {}
        for event, box in pending.values():
            box.append({"ok": False, "error": error})
            event.set()

    def _reader(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                self._handle(line)
        except (OSError, ValueError):  # pipe chiusa da stop()
            return
        if proc is self._proc and self.state != "spento":
            code = proc.wait()
            self._fail(self.last_error or f"processo {self.label} terminato (codice {code}); log in {self.log_file}")

    def _handle(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if msg.get("event") == "ready":
            self.load_s, self.info = msg.get("load_s"), msg
            self.state = "pronto"
            self._ready.set()
        elif msg.get("event") == "error":
            self._fail(f"{self.label} non si è avviato: {msg.get('error')}")
        elif "id" in msg:
            with self._lock:
                waiter = self._pending.pop(msg["id"], None)
            if waiter:
                waiter[1].append(msg)
                waiter[0].set()

    def revive(self) -> bool:
        """Dopo un crash con i modelli già caricati almeno una volta riparte (al massimo 3 volte); un errore
        di caricamento (librerie o file mancanti) invece resta: ripartire non servirebbe."""
        if self.state == "errore" and self.load_s is not None and self.restarts < 3:
            self.restarts += 1
            self.start()
            return True
        return False

    def wait_ready(self, timeout: float) -> bool:
        """True se il processo è pronto entro `timeout` secondi (False subito se è in errore o spento)."""
        if self.state in ("spento", "errore"):
            return False
        self._ready.wait(max(0.0, timeout))
        return self.state == "pronto"

    @property
    def ready(self) -> bool:
        return self.state == "pronto"

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self.state = "spento"
        self._ready.set()
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    # ----- richieste ----------------------------------------------------------------------

    def request(self, payload: dict, timeout: float) -> dict:
        proc = self._proc
        if proc is None or not self.ready:
            raise self.error_class(self.last_error or f"processo {self.label} non pronto")
        rid = next(self._ids)
        event, box = threading.Event(), []
        with self._lock:
            self._pending[rid] = (event, box)
        try:
            proc.stdin.write(json.dumps({"id": rid, **payload}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            with self._lock:
                self._pending.pop(rid, None)
            raise self.error_class(f"processo {self.label} non raggiungibile: {exc}") from None
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            self.start()  # bloccato: si riavvia, chi chiedeva passa alla via di riserva
            raise self.error_class(f"{self.label} non ha risposto in {timeout:g} s: riavviato")
        reply = box[0]
        if not reply.get("ok"):
            raise self.error_class(str(reply.get("error") or "errore sconosciuto"))
        return reply
