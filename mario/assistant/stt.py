"""
Ingresso vocale dell'assistente: microfono del PC -> testo, offline (faster-whisper in `.venv-stt`).

Si parla tenendo premuto il pulsante sulla schiena di Mario (o il pulsante "Tieni premuto e parla"
della pagina Assistente): `VoiceInput.start()` avvia la registrazione a 16 kHz con pw-record (o
parecord, arecord), `stop()` la chiude e la manda al processo `stt_worker.py`, che tiene il modello
caricato; il testo arriva alla funzione indicata, dal thread della trascrizione. Il file audio vive solo
il tempo della trascrizione e poi viene cancellato: nulla viene salvato né inviato in rete, al modello
arriva solo il testo. Se manca qualcosa (ambiente, modello, registratore) `problem()` dice cosa e
come installarlo (`./tools/setup_stt.sh`).
"""
from __future__ import annotations

import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from .worker import WorkerError, WorkerProcess

PROJECT_DIR = Path(__file__).resolve().parents[2]
VENV_DIR = PROJECT_DIR / ".venv-stt"
DEFAULT_PYTHON = VENV_DIR / "bin" / "python"
MODELS_DIR = PROJECT_DIR / "third_party" / "whisper-models"
MODELS = ("small", "base")  # small: circa 3 s per frase e buona precisione; base: circa 1 s, sbaglia di più
DEFAULT_MODEL = "small"
WORKER = Path(__file__).with_name("stt_worker.py")
TMP_DIR = Path(tempfile.gettempdir()) / "mario-voice-input"
LOG_FILE = TMP_DIR / "stt-worker.log"
RATE = 16000
MAX_SECONDS = 20.0   # registrazione massima: il pulsante tenuto premuto per sbaglio non registra per sempre
MIN_SECONDS = 0.35   # più corta: pressione accidentale, si scarta senza trascrivere
# parole del mondo di Mario: aiutano Whisper a scriverle giuste (Goomba, Bowser, basetta...)
PROMPT = ("Mario, Luigi, Peach, Bowser, Goomba, Koopa, Yoshi, Toad, Wario, basetta, pantaloni, salto, "
          "stella, moneta, tubo, fungo.")
RECORDERS = {
    "pw-record": ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "{path}"],
    "parecord": ["parecord", f"--rate={RATE}", "--channels=1", "--format=s16le", "--file-format=wav", "{path}"],
    "arecord": ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "wav", "{path}"],
}
# frasi che Whisper "sente" nel silenzio o nel rumore (dai sottotitoli con cui è stato addestrato)
HALLUCINATIONS = re.compile(r"sottotitoli|amara\.org|qtss|grazie per (la|l'|aver) (visione|ascolto|guardato)|"
                            r"iscriviti al canale|alla prossima puntata", re.IGNORECASE)


class SpeechError(WorkerError):
    pass


def find_recorder() -> Optional[str]:
    for name in RECORDERS:
        if shutil.which(name):
            return name
    return None


def model_dir(name: str) -> Path:
    return MODELS_DIR / name


def problem(model: str = DEFAULT_MODEL, python: Optional[Path] = None, recorder: Optional[str] = "auto") -> str:
    """Perché l'ingresso vocale non può funzionare ('' se è tutto a posto)."""
    if not Path(python or DEFAULT_PYTHON).is_file():
        return "manca l'ambiente del riconoscimento vocale: ./tools/setup_stt.sh"
    if not (model_dir(model) / "model.bin").is_file():
        return f"manca il modello Whisper '{model}': ./tools/setup_stt.sh {model}"
    if (find_recorder() if recorder == "auto" else recorder) is None:
        return "nessun registratore audio (pw-record, parecord o arecord)"
    return ""


def clean_transcript(text: str) -> str:
    """Testo pulito; '' se è vuoto o è una delle frasi che Whisper inventa nel silenzio."""
    text = " ".join(str(text or "").split()).strip()
    if not re.search(r"\w", text) or HALLUCINATIONS.search(text):
        return ""
    return text


def wav_seconds(path: Path) -> float:
    """Durata di un WAV; se l'intestazione non è stata chiusa (registratore interrotto) la stima dai byte."""
    try:
        with wave.open(str(path)) as w:
            frames, rate = w.getnframes(), w.getframerate()
        if frames > 0:
            return frames / float(rate)
    except (OSError, EOFError, wave.Error):
        pass
    try:
        return max(0, path.stat().st_size - 44) / (RATE * 2.0)
    except OSError:
        return 0.0


class SpeechClient(WorkerProcess):
    """Processo di faster-whisper (vedi `worker.py` e `stt_worker.py`)."""

    label = "riconoscimento vocale"
    error_class = SpeechError
    log_file = LOG_FILE

    def __init__(self, model: str = DEFAULT_MODEL, python: Optional[Path] = None, threads: int = 2,
                 command: Optional[list[str]] = None):
        super().__init__(command)
        self.model = model
        self.python = Path(python or DEFAULT_PYTHON)
        self.threads = threads
        self.transcriptions = 0
        self.total_s = 0.0

    def _argv(self) -> list[str]:
        return [str(self.python), "-I", str(WORKER), "--model", str(model_dir(self.model)),
                "--threads", str(self.threads)]

    def transcribe(self, path: str, prompt: str = PROMPT, timeout: float = 60.0) -> dict:
        t0 = time.monotonic()
        reply = self.request({"cmd": "transcribe", "path": path, "prompt": prompt}, timeout)
        self.transcriptions += 1
        self.total_s += time.monotonic() - t0
        return reply

    def status(self) -> dict:
        return {"state": self.state, "model": self.model, "load_s": self.load_s, "restarts": self.restarts,
                "transcriptions": self.transcriptions, "last_error": self.last_error,
                "avg_s": round(self.total_s / self.transcriptions, 2) if self.transcriptions else None}


class Recorder:
    """Registrazione dal microfono predefinito in un WAV a 16 kHz mono, fino a stop() (o MAX_SECONDS)."""

    def __init__(self, name: Optional[str] = None, command: Optional[list[str]] = None):
        self.name = name or find_recorder()
        self.command = command  # per i test: un comando finto che scrive il file
        self._proc: Optional[subprocess.Popen] = None
        self.path: Optional[Path] = None
        self.started = 0.0

    @property
    def active(self) -> bool:
        return self._proc is not None

    def start(self, path: Path) -> None:
        if self._proc is not None:
            raise SpeechError("registrazione già in corso")
        template = self.command or RECORDERS.get(self.name or "")
        if not template:
            raise SpeechError("nessun registratore audio (pw-record, parecord o arecord)")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.started = path, time.monotonic()
        try:
            self._proc = subprocess.Popen([part.replace("{path}", str(path)) for part in template],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self._proc = None
            raise SpeechError(f"registratore non avviato: {exc}") from None

    def stop(self) -> tuple[Optional[Path], float]:
        """Chiude la registrazione (SIGINT: il registratore completa l'intestazione del WAV)."""
        proc, self._proc = self._proc, None
        if proc is None:
            return None, 0.0
        held = time.monotonic() - self.started
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        path = self.path
        if path is None or not path.is_file():
            return None, held
        return path, wav_seconds(path)


class VoiceInput:
    """Push-to-talk: start() registra, stop() trascrive in un thread e chiama `on_text(testo, info)`."""

    def __init__(self, model: str = DEFAULT_MODEL, client: Optional[SpeechClient] = None,
                 recorder: Optional[Recorder] = None, max_seconds: float = MAX_SECONDS):
        self.model = model
        self.client = client or SpeechClient(model)
        self.recorder = recorder or Recorder()
        self.max_seconds = max_seconds
        self.last: dict = {}
        self._timer: Optional[threading.Timer] = None
        self._on_auto_stop: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()
        self._busy = 0  # trascrizioni in corso

    def problem(self) -> str:
        if self.client.command and self.recorder.command:  # test: tutto finto
            return ""
        return problem(self.model, self.client.python, self.recorder.name)

    def warmup(self) -> None:
        """Avvia il processo in anticipo: il modello si carica mentre si gioca (circa 1-2 s)."""
        if not self.problem() and self.client.state in ("spento",):
            self.client.start()
        elif self.client.state == "errore":
            self.client.revive()

    @property
    def listening(self) -> bool:
        return self.recorder.active

    def start(self, on_auto_stop: Optional[Callable[[], None]] = None) -> None:
        reason = self.problem()
        if reason:
            raise SpeechError(reason)
        self.warmup()
        with self._lock:
            path = TMP_DIR / f"voce-{time.strftime('%H%M%S')}-{id(self) % 1000}.wav"
            self.recorder.start(path)
            self._on_auto_stop = on_auto_stop
            self._timer = threading.Timer(self.max_seconds, self._auto_stop)
            self._timer.daemon = True
            self._timer.start()

    def _auto_stop(self) -> None:
        callback = self._on_auto_stop
        if self.recorder.active and callback is not None:
            callback()  # chi ha avviato decide (di solito chiama stop() con la sua funzione per il testo)

    def stop(self, on_text: Callable[[str, dict], None]) -> bool:
        """Chiude la registrazione; False se non ce n'era una. La trascrizione gira in un thread."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self.recorder.active:
                return False
            path, seconds = self.recorder.stop()
        threading.Thread(target=self._transcribe, args=(path, seconds, on_text), name="voice-input",
                         daemon=True).start()
        return True

    def _transcribe(self, path: Optional[Path], seconds: float, on_text: Callable[[str, dict], None]) -> None:
        info: dict = {"audio_s": round(seconds, 2)}
        text = ""
        self._busy += 1
        try:
            if path is None or seconds < MIN_SECONDS:
                info["error"] = "registrazione troppo corta"
            else:
                if not self.client.wait_ready(30.0) and not (self.client.revive() and self.client.wait_ready(30.0)):
                    raise SpeechError(self.client.last_error or "riconoscimento vocale non pronto")
                reply = self.client.transcribe(str(path))
                info.update({k: reply[k] for k in ("audio_s", "speech_s", "elapsed_s") if k in reply})
                info["raw"] = reply.get("text", "")
                text = clean_transcript(info["raw"])
        except WorkerError as exc:
            info["error"] = str(exc)
        finally:
            self._busy -= 1
            if path is not None:
                path.unlink(missing_ok=True)  # l'audio non resta sul disco
        self.last = {"text": text, **info}
        on_text(text, info)

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            if self.recorder.active:
                path, _ = self.recorder.stop()
                if path is not None:
                    path.unlink(missing_ok=True)
        self.client.stop()

    def status(self) -> dict:
        return {"model": self.model, "recorder": self.recorder.name, "listening": self.listening,
                "transcribing": self._busy > 0, "problem": self.problem(), "worker": self.client.status(),
                "last": self.last}
