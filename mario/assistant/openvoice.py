"""
Voce IA dell'assistente: client del processo `openvoice_worker.py` (piper + OpenVoice V2).

Con una registrazione di riferimento il processo fa piper + OpenVoice; senza, solo piper, ma con il
modello sempre caricato: una frase in qualche decimo di secondo invece dei 2-3 s del comando `piper`.

Il processo gira con l'interprete di `.venv-openvoice` (vedi `tools/setup_openvoice.sh`) e tiene i
modelli caricati; qui lo si avvia, si aspetta che sia pronto e gli si chiedono le frasi, una riga
JSON per richiesta. Se il processo manca, si blocca o muore, chi lo usa (la classe `Speaker`) passa
alla voce di riserva: la risposta al bambino non si perde mai.
"""
from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parents[2]
VENV_DIR = PROJECT_DIR / ".venv-openvoice"
DEFAULT_PYTHON = VENV_DIR / "bin" / "python"
DEFAULT_CONVERTER = PROJECT_DIR / "third_party" / "OpenVoice" / "checkpoints_v2" / "converter"
VOICES_DIR = PROJECT_DIR / "third_party" / "piper-voices"
DEFAULT_VOICE = "it_IT-paola-medium.onnx"
CACHE_DIR = PROJECT_DIR / "data" / "openvoice-cache"
WORKER = Path(__file__).with_name("openvoice_worker.py")
LOG_FILE = Path(tempfile.gettempdir()) / "mario-assistant-sounds" / "openvoice-worker.log"


class OpenVoiceError(RuntimeError):
    pass


def find_piper() -> Optional[str]:
    """Il comando `piper`: nel PATH oppure quello installato da setup_openvoice.sh."""
    found = shutil.which("piper")
    if found:
        return found
    local = VENV_DIR / "bin" / "piper"
    return str(local) if local.is_file() else None


def default_piper_model() -> str:
    """La voce piper italiana scaricata da setup_openvoice.sh (paola, poi qualunque altra), o ''."""
    preferred = VOICES_DIR / DEFAULT_VOICE
    if preferred.is_file():
        return str(preferred)
    others = sorted(VOICES_DIR.glob("*.onnx")) if VOICES_DIR.is_dir() else []
    return str(others[0]) if others else ""


def problem(reference: str, piper_model: str = "", python: Optional[Path] = None,
            converter: Optional[Path] = None) -> str:
    """'' se la voce IA può partire, altrimenti il motivo (in italiano, per lo stato e i log)."""
    python = Path(python or DEFAULT_PYTHON)
    converter = Path(converter or DEFAULT_CONVERTER)
    if not reference:
        return "nessuna registrazione di riferimento"
    if not Path(reference).is_file():
        return f"registrazione di riferimento non trovata: {reference}"
    if not python.is_file():
        return f"ambiente OpenVoice assente ({python}): eseguire tools/setup_openvoice.sh"
    if not (converter / "config.json").is_file() or not (converter / "checkpoint.pth").is_file():
        return f"checkpoint di OpenVoice assenti in {converter}: eseguire tools/setup_openvoice.sh"
    model = piper_model or default_piper_model()
    if not model or not Path(model).is_file():
        return "nessuna voce piper italiana (.onnx): eseguire tools/setup_openvoice.sh"
    return ""


class OpenVoiceClient:
    """Processo della voce IA: avvio in background, richieste con tempo massimo, stato."""

    def __init__(self, reference: str, piper_model: str = "", python: Optional[Path] = None,
                 converter: Optional[Path] = None, cache_dir: Optional[Path] = None, watermark: bool = True,
                 threads: int = 0, command: Optional[list[str]] = None):
        self.reference = reference
        self.piper_model = piper_model or default_piper_model()
        self.python = Path(python or DEFAULT_PYTHON)
        self.converter = Path(converter or DEFAULT_CONVERTER)
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.watermark = watermark
        self.threads = threads
        self.command = command  # per i test: un processo finto che parla lo stesso protocollo
        self.state = "spento"   # spento | in avvio | pronto | errore
        self.last_error = ""
        self.load_s: Optional[float] = None
        self.started_at = 0.0
        self.info: dict = {}
        self.conversions = 0
        self.total_s = 0.0
        self.restarts = 0
        self._proc: Optional[subprocess.Popen] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, list]] = {}

    # ----- ciclo di vita ------------------------------------------------------------------

    def _argv(self) -> list[str]:
        if self.command:
            return list(self.command)
        # -I: la cartella dello script non entra nel percorso dei moduli (qui openvoice.py nasconderebbe il pacchetto)
        argv = [str(self.python), "-I", str(WORKER), "--piper-model", self.piper_model, "--cache", str(self.cache_dir)]
        if self.reference:  # senza riferimento: solo piper
            argv += ["--converter", str(self.converter), "--reference", self.reference]
        if not self.watermark:
            argv.append("--no-watermark")
        if self.threads:
            argv += ["--threads", str(self.threads)]
        return argv

    def start(self) -> None:
        """Avvia il processo senza aspettarlo: carica i modelli in background (alcuni secondi)."""
        self.stop()
        self._ready.clear()
        self.state, self.last_error, self.started_at = "in avvio", "", time.monotonic()
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "ab")
        try:
            self._proc = subprocess.Popen(self._argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                          text=True, encoding="utf-8", bufsize=1)
        except OSError as exc:
            self._fail(f"avvio del processo OpenVoice fallito: {exc}")
            return
        finally:
            log.close()
        threading.Thread(target=self._reader, args=(self._proc,), name="openvoice-reader", daemon=True).start()

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
            self._fail(self.last_error or f"processo OpenVoice terminato (codice {code}); log in {LOG_FILE}")

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
            self._fail(f"OpenVoice non si è avviato: {msg.get('error')}")
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
            raise OpenVoiceError(self.last_error or "processo OpenVoice non pronto")
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
            raise OpenVoiceError(f"processo OpenVoice non raggiungibile: {exc}") from None
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            # bloccato: si riavvia, la frase passa alla voce di riserva
            self.start()
            raise OpenVoiceError(f"OpenVoice non ha risposto in {timeout:g} s: riavviato")
        reply = box[0]
        if not reply.get("ok"):
            raise OpenVoiceError(str(reply.get("error") or "errore sconosciuto"))
        return reply

    def speak(self, text: str, out: str, length_scale: float = 1.0, tau: float = 0.3, gain: float = 1.0,
              timeout: float = 60.0) -> dict:
        t0 = time.monotonic()
        reply = self.request({"cmd": "speak", "text": text, "out": out, "length_scale": length_scale,
                              "tau": tau, "gain": gain}, timeout)
        self.conversions += 1
        self.total_s += time.monotonic() - t0
        return reply

    def set_reference(self, path: str, timeout: float = 60.0) -> None:
        self.request({"cmd": "reference", "path": path}, timeout)
        self.reference = path

    def status(self) -> dict:
        return {"state": self.state, "mode": "openvoice" if self.reference else "piper",
                "reference": self.reference, "piper_model": self.piper_model,
                "load_s": self.load_s, "watermark": bool(self.info.get("watermark")),
                "conversions": self.conversions, "restarts": self.restarts,
                "avg_s": round(self.total_s / self.conversions, 2) if self.conversions else None,
                "last_error": self.last_error}
