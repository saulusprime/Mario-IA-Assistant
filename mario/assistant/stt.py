"""
Ingresso vocale dell'assistente: microfono del PC -> testo, offline (faster-whisper in `.venv-stt`).

Si parla tenendo premuto il pulsante sulla schiena di Mario (o il pulsante "Tieni premuto e parla"
della pagina Assistente): `VoiceInput.start()` avvia la registrazione a 16 kHz con pw-record (o
parecord, arecord), `stop()` la chiude e la manda al processo `stt_worker.py`, che tiene il modello
caricato; il testo arriva alla funzione indicata, dal thread della trascrizione. Il file audio vive solo
il tempo della trascrizione e poi viene cancellato: nulla viene salvato né inviato in rete, al modello
arriva solo il testo. Se manca qualcosa (ambiente, modello, registratore) `problem()` dice cosa e
come installarlo (`./tools/setup_stt.sh`).

In alternativa al pulsante, "Ehi Mario": `WakeListener` tiene acceso il processo `wake_worker.py`, che
ascolta il microfono di continuo con Vosk e registra la frase di chi chiama Mario; la frase passa da
`VoiceInput.transcribe_file()` e `split_wake()` controlla che la trascrizione di Whisper contenga davvero la
frase di attivazione (altrimenti la registrazione si scarta senza conservarne il testo).
"""
from __future__ import annotations

import json
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

# ----- parola di attivazione ("Ehi Mario") -----
VOSK_MODEL = PROJECT_DIR / "third_party" / "vosk-models" / "vosk-model-small-it-0.22"
WAKE_WORKER = Path(__file__).with_name("wake_worker.py")
WAKE_LOG_FILE = TMP_DIR / "wake-worker.log"
DEFAULT_WAKE_PHRASES = ("ehi mario", "hey mario")
INTERJECTIONS = ("ehi", "hey", "hei", "ei", "ehy", "eh")
# come Whisper scrive a volte un "ehi" detto in fretta ("E Mario", "Ai Mario", "Hai Mario", "È Mario": prove del
# 18/10 con piper): valgono solo all'inizio della frase, perché "Luigi e Mario" non è una chiamata
WEAK_INTERJECTIONS = ("e", "è", "ai", "hai", "a", "i", "oh", "ah")
# registratori che scrivono PCM grezzo sullo standard output, per l'ascolto continuo
RAW_RECORDERS = {
    "pw-record": ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
    "parecord": ["parecord", "--raw", f"--rate={RATE}", "--channels=1", "--format=s16le"],
    "arecord": ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"],
}


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


def parse_wake_phrases(text) -> list[str]:
    """'ehi mario, hey mario' (o una lista) -> frasi pulite; vuoto = quelle predefinite."""
    items = text if isinstance(text, (list, tuple)) else re.split(r"[,;|\n]", str(text or ""))
    phrases = []
    for item in items:
        words = re.sub(r"[^\w\s]", " ", str(item).lower()).split()
        if words and " ".join(words) not in phrases:
            phrases.append(" ".join(words))
    return phrases or list(DEFAULT_WAKE_PHRASES)


def wake_pattern(phrases) -> re.Pattern:
    """Trova la frase di attivazione nel testo di Whisper ("Ehi, Mario!", "Hey Mario"): tra le parole possono
    esserci virgole e punti, le interiezioni (ehi, hey, eh...) valgono tutte uguali, e all'inizio della frase
    valgono anche le forme in cui Whisper trascrive un "ehi" detto in fretta (e, ai, hai, è...)."""
    sep = r"[\s,.!?;:]+"
    alts = []
    for phrase in parse_wake_phrases(phrases):
        first, *rest = phrase.split()
        tail = "".join(sep + re.escape(w) for w in rest)
        if first in INTERJECTIONS:
            alts.append(r"(?<!\w)(?:" + "|".join(INTERJECTIONS) + ")" + tail)
            alts.append(r"^\W*(?:" + "|".join(WEAK_INTERJECTIONS) + ")" + tail)
        else:
            alts.append(r"(?<!\w)" + re.escape(first) + tail)
    return re.compile("(?:" + "|".join(alts) + r")(?!\w)[\s,.!?;:]*", re.IGNORECASE)


def split_wake(text: str, phrases=DEFAULT_WAKE_PHRASES) -> tuple[bool, str]:
    """(c'è la frase di attivazione?, testo dopo la frase). "Ehi Mario, raccontami una storia" -> (True,
    "raccontami una storia"); "Ehi Mario!" -> (True, ""); "Ehi Maria, dove sei?" -> (False, "")."""
    m = wake_pattern(phrases).search(text or "")
    if not m:
        return False, ""
    rest = text[m.end():].strip()
    return True, rest[:1].upper() + rest[1:] if rest else ""


def wake_problem(python: Optional[Path] = None, recorder: Optional[str] = "auto") -> str:
    """Perché l'attivazione con la voce non può funzionare ('' se è tutto a posto)."""
    python = Path(python or DEFAULT_PYTHON)
    if not python.is_file():
        return "manca l'ambiente del riconoscimento vocale: ./tools/setup_stt.sh"
    if not any(python.parent.parent.glob("lib/python*/site-packages/vosk/__init__.py")):
        return "manca Vosk per la parola di attivazione: ./tools/setup_stt.sh"
    if not (VOSK_MODEL / "am" / "final.mdl").is_file():
        return "manca il modello italiano di Vosk: ./tools/setup_stt.sh"
    if recorder == "auto" and find_recorder() is None:
        return "nessun registratore audio (pw-record, parecord o arecord)"
    return ""


def ensure_wav16(path: Path) -> Path:
    """Un WAV mono a 16 bit e 16 kHz, come lo legge il processo di ascolto: se il file non lo è (registrazione del
    telefono, altro WAV) ne fa una copia convertita con ffmpeg nella cartella temporanea."""
    try:
        with wave.open(str(path)) as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE):
                return path
    except (OSError, EOFError, wave.Error):
        pass
    if not shutil.which("ffmpeg"):
        raise SpeechError("serve un WAV mono a 16 bit e 16 kHz, oppure ffmpeg per convertirlo")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / f"convertito-{path.stem}.wav"
    try:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-ar", str(RATE), "-ac", "1",
                        "-sample_fmt", "s16", str(out)], check=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SpeechError(f"conversione di {path.name} non riuscita: {exc}") from None
    return out


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

    def transcribe_file(self, path: Path, seconds: float, on_text: Callable[[str, dict], None],
                        private: bool = True) -> None:
        """Trascrive in un thread una frase già registrata (dall'ascolto di "Ehi Mario"); il file viene cancellato.
        Con `private` il testo non resta nello stato (`last`): può essere una frase non rivolta a Mario."""
        self.warmup()
        self._busy += 1  # conta da subito: chi controlla `transcribing` non deve vedere un buco
        threading.Thread(target=self._transcribe, args=(Path(path), seconds, on_text, private, True),
                         name="voice-input", daemon=True).start()

    @property
    def transcribing(self) -> bool:
        return self._busy > 0

    def _transcribe(self, path: Optional[Path], seconds: float, on_text: Callable[[str, dict], None],
                    private: bool = False, counted: bool = False) -> None:
        info: dict = {"audio_s": round(seconds, 2)}
        text = ""
        if not counted:
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
        self.last = {"private": True, **{k: v for k, v in info.items() if k != "raw"}} if private else {"text": text, **info}
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


class WakeListener(WorkerProcess):
    """Ascolto continuo di "Ehi Mario" (processo `wake_worker.py` con Vosk, vedi lì come decide).

    Gli eventi spontanei del processo (wake, utterance, no_speech, cancelled, audio_end) vanno a `on_event`,
    dal thread che legge il processo. `set_gate(funzione)` mette in pausa l'ascolto quando la funzione dice di
    no (per esempio mentre Mario parla dagli altoparlanti: sentirebbe il proprio nome) e lo riprende dopo."""

    label = "parola di attivazione"
    error_class = SpeechError
    log_file = WAKE_LOG_FILE
    EVENTS = ("wake", "utterance", "no_speech", "cancelled", "audio_end")

    def __init__(self, phrases=DEFAULT_WAKE_PHRASES, python: Optional[Path] = None, recorder: Optional[str] = "auto",
                 source_file: Optional[Path] = None, command: Optional[list[str]] = None,
                 on_event: Optional[Callable[[dict], None]] = None, end_silence: float = 1.0,
                 realtime: bool = False):
        super().__init__(command)
        self.phrases = parse_wake_phrases(phrases)
        self.python = Path(python or DEFAULT_PYTHON)
        self.recorder = find_recorder() if recorder == "auto" else recorder
        self.source_file = source_file
        self.realtime = realtime  # con source_file: legge il file alla velocità del parlato, come un microfono
        self.on_event = on_event or (lambda ev: None)
        self.end_silence = end_silence
        self.paused = False
        self.counts = {"wake": 0, "utterance": 0, "accepted": 0, "rejected": 0}
        self._gate: Optional[Callable[[], bool]] = None
        self._gate_thread: Optional[threading.Thread] = None
        self._closing = threading.Event()

    def problem(self) -> str:
        if self.command:  # test: processo finto
            return ""
        return wake_problem(self.python, "file" if self.source_file else (self.recorder or "auto"))

    def _argv(self) -> list[str]:
        argv = [str(self.python), "-I", str(WAKE_WORKER), "--model", str(VOSK_MODEL), "--phrases", "|".join(self.phrases),
                "--out-dir", str(TMP_DIR), "--end-silence", str(self.end_silence)]
        if self.source_file:
            return argv + ["--file", str(self.source_file)] + (["--realtime"] if self.realtime else [])
        return argv + ["--recorder-cmd", json.dumps(RAW_RECORDERS[self.recorder or "pw-record"])]

    def start(self) -> None:
        self.paused = False  # un processo nuovo parte sempre in ascolto
        super().start()

    def _handle(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        event = msg.get("event")
        if event not in self.EVENTS:
            super()._handle(line)
            return
        if event in self.counts:
            self.counts[event] += 1
        try:
            self.on_event(msg)
        except Exception:  # chi riceve l'evento non deve fermare la lettura del processo
            pass

    def send_command(self, cmd: str, timeout: float = 3.0, **fields) -> bool:
        try:
            self.request({"cmd": cmd, **fields}, timeout)
            return True
        except WorkerError:
            return False

    def pause(self) -> bool:
        ok = self.send_command("pause")
        if ok:
            self.paused = True
        return ok

    def resume(self) -> bool:
        ok = self.send_command("resume")
        if ok:
            self.paused = False
        return ok

    def listen(self, wait_s: float = 6.0) -> bool:
        """Registra la prossima frase anche senza "Ehi Mario" (dopo che Mario ha risposto "Eccomi!")."""
        ok = self.send_command("listen", wait_s=wait_s)
        if ok:
            self.paused = False
        return ok

    def cancel(self) -> bool:
        return self.send_command("cancel")

    def set_gate(self, gate: Optional[Callable[[], bool]], interval: float = 0.1) -> None:
        self._gate = gate
        if gate is not None and self._gate_thread is None:
            self._gate_thread = threading.Thread(target=self._gate_loop, args=(interval,), name="wake-gate", daemon=True)
            self._gate_thread.start()

    def _gate_loop(self, interval: float) -> None:
        while not self._closing.wait(interval):
            gate = self._gate
            if gate is None or not self.ready:
                continue
            try:
                wanted = bool(gate())
            except Exception:
                wanted = False
            if wanted == self.paused:
                (self.resume if wanted else self.pause)()

    def close(self) -> None:
        self._closing.set()
        self.stop()

    def status(self) -> dict:
        return {"state": self.state, "phrases": self.phrases, "paused": self.paused, "recorder": self.recorder or "",
                "source": str(self.source_file or ""), "problem": self.problem(), "load_s": self.load_s,
                "last_error": self.last_error, **self.counts}
