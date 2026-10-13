"""
Uscita audio dal PC: voce (sintesi locale) ed effetti sonori sintetizzati.

Mario non ha un canale audio via Bluetooth, quindi la "voce di Mario" esce dagli altoparlanti
del computer. Motori supportati, in ordine di preferenza: `spd-say` (speech-dispatcher, sul PC
di sviluppo con espeak-ng e voci italiane) e `espeak-ng` diretto. Senza motore la classe resta
muta ma funzionante (i test girano ovunque).

Gli effetti sonori (moneta, power-up, allarme, ...) sono onde generate al volo con la libreria
standard e suonati con `paplay` / `pw-play` / `aplay` / `ffplay`, il primo disponibile.
"""
from __future__ import annotations

import math
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from .emotions import EMOTIONS

SOUNDS_DIR = Path(tempfile.gettempdir()) / "mario-assistant-sounds"
SAMPLE_RATE = 22050


def _tone(freq: float, duration: float, volume: float = 0.5, decay: float = 1.0) -> list[float]:
    n = int(SAMPLE_RATE * duration)
    return [volume * math.sin(2 * math.pi * freq * i / SAMPLE_RATE) * (1 - decay * i / n) for i in range(n)]


def _glide(f0: float, f1: float, duration: float, volume: float = 0.4) -> list[float]:
    n = int(SAMPLE_RATE * duration)
    out, phase = [], 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * i / n
        phase += 2 * math.pi * f / SAMPLE_RATE
        out.append(volume * math.sin(phase) * (1 - 0.6 * i / n))
    return out


def _silence(duration: float) -> list[float]:
    return [0.0] * int(SAMPLE_RATE * duration)


SOUND_RECIPES = {
    "coin": lambda: _tone(988, 0.08, 0.5, 0.2) + _tone(1319, 0.35, 0.5),
    "powerup": lambda: sum((_tone(f, 0.07, 0.45, 0.3) for f in (523, 659, 784, 1047, 1319, 1568)), []),
    "think": lambda: _tone(330, 0.12, 0.25) + _silence(0.08) + _tone(392, 0.18, 0.25),
    "alarm": lambda: sum((_tone(880, 0.09, 0.45, 0.2) + _tone(660, 0.09, 0.45, 0.2) for _ in range(3)), []),
    "sad": lambda: _tone(440, 0.18, 0.4, 0.3) + _tone(392, 0.18, 0.4, 0.3) + _tone(330, 0.4, 0.4),
    "yawn": lambda: _glide(420, 220, 0.9, 0.35),
    "tick": lambda: _tone(1200, 0.03, 0.3) + _silence(0.12) + _tone(1200, 0.03, 0.2),
    "hello": lambda: _tone(659, 0.1, 0.4, 0.2) + _tone(784, 0.1, 0.4, 0.2) + _tone(1047, 0.3, 0.4),
    "listen_on": lambda: _tone(660, 0.08, 0.35, 0.2) + _tone(990, 0.2, 0.35),
    "listen_off": lambda: _tone(990, 0.08, 0.35, 0.2) + _tone(660, 0.2, 0.35),
}


def ensure_sound(name: str) -> Optional[Path]:
    recipe = SOUND_RECIPES.get(name)
    if recipe is None:
        return None
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    path = SOUNDS_DIR / f"{name}.wav"
    if not path.exists():
        samples = recipe()
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
    return path


def detect_engine(piper_model: str = "") -> Optional[str]:
    """`piper` (voci neurali, offline) vince se c'è il binario e un modello .onnx; poi spd-say ed espeak-ng."""
    if piper_model and shutil.which("piper") and Path(piper_model).is_file():
        return "piper"
    for name in ("spd-say", "espeak-ng", "espeak"):
        if shutil.which(name):
            return name
    return None


def detect_player() -> Optional[list[str]]:
    for cmd in (["paplay"], ["pw-play"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


class Speaker:
    """Coda di frasi da pronunciare, una alla volta, con velocità e tono legati all'emozione."""

    def __init__(self, enabled: bool = True, language: str = "it", voice: str = "", rate: int = 0,
                 pitch: int = 0, volume: int = 0, sounds: bool = True, engine: Optional[str] = None,
                 piper_model: str = ""):
        self.enabled = enabled
        self.language = language
        self.voice = voice
        self.rate = int(rate)
        self.pitch = int(pitch)
        self.volume = int(volume)
        self.sounds = sounds
        self.piper_model = piper_model
        self.engine = engine if engine is not None else detect_engine(piper_model)
        self.player = detect_player()
        self.spoken: list[tuple[str, Optional[str]]] = []
        self.on_start: Optional[Callable[[str, Optional[str]], None]] = None  # chiamato quando una frase inizia
        self.on_end: Optional[Callable[[str, Optional[str]], None]] = None
        self.last_error = ""
        self._spoke_any = False
        self._queue: queue.Queue = queue.Queue()
        self._proc: Optional[subprocess.Popen] = None
        self._busy = False
        self._generation = 0
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    # ----- configurazione -------------------------------------------------------------

    def configure(self, **opts) -> None:
        for key in ("enabled", "language", "voice", "rate", "pitch", "volume", "sounds"):
            if key in opts and opts[key] is not None:
                value = opts[key]
                if key in ("rate", "pitch", "volume"):
                    value = max(-100, min(100, int(value)))
                setattr(self, key, value)
        if "piper_model" in opts and opts["piper_model"] is not None and opts["piper_model"] != self.piper_model:
            self.piper_model = str(opts["piper_model"])
            self.engine = detect_engine(self.piper_model)  # cambia motore se il modello c'è (o non c'è più)

    def status(self) -> dict:
        return {"engine": self.engine, "player": self.player[0] if self.player else None, "enabled": self.enabled,
                "busy": self._busy, "queued": self._queue.qsize(), "voice": self.voice, "rate": self.rate,
                "pitch": self.pitch, "sounds": self.sounds, "piper_model": self.piper_model, "last_error": self.last_error}

    @property
    def available(self) -> bool:
        return self.engine is not None

    @property
    def idle(self) -> bool:
        """Vero se non sta parlando e non ha frasi in coda."""
        return not self._busy and self._queue.empty()

    # ----- voce -------------------------------------------------------------------------

    def speak(self, text: str, emotion: Optional[str] = None) -> None:
        text = " ".join(text.split())
        if not text:
            return
        self.spoken.append((text, emotion))
        if self.enabled and self.engine:
            self._spoke_any = True
            self._queue.put((self._generation, text, emotion))

    def stop(self) -> None:
        """Svuota la coda e interrompe la frase in corso."""
        self._generation += 1
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        if self.engine == "spd-say" and self._spoke_any:
            try:
                subprocess.run(["spd-say", "-C"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def wait(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.empty() and not self._busy:
                return True
            time.sleep(0.05)
        return False

    def _command(self, text: str, emotion: Optional[str]) -> list[str]:
        emo = EMOTIONS.get(emotion or "")
        rate = max(-100, min(100, self.rate + (emo.rate if emo else 0)))
        pitch = max(-100, min(100, self.pitch + (emo.pitch if emo else 0)))
        if self.engine == "piper":
            # piper legge il testo dallo standard input e scrive un WAV; velocità = length_scale (1 = normale,
            # più piccolo = più veloce). Niente controllo del tono: l'emozione si sente solo nel ritmo.
            length_scale = round(max(0.6, min(1.6, 1.0 - rate / 200)), 2)
            return ["piper", "--model", self.piper_model, "--length_scale", str(length_scale), "--output_file",
                    str(SOUNDS_DIR / "piper-out.wav")]
        if self.engine == "spd-say":
            cmd = ["spd-say", "-w", "-l", self.language, "-r", str(rate), "-p", str(pitch), "-i", str(self.volume)]
            if self.voice:
                cmd += ["-y", self.voice]
            return cmd + ["--", text]
        # espeak-ng: velocità in parole al minuto (80..450), tono 0..99, ampiezza 0..200
        wpm = int(175 + rate * 1.2)
        return [self.engine, "-v", self.language, "-s", str(max(80, min(450, wpm))),
                "-p", str(max(0, min(99, 50 + pitch // 2))), "-a", str(max(0, min(200, 100 + self.volume))), "--", text]

    def _run(self) -> None:
        while True:
            generation, text, emotion = self._queue.get()
            if generation != self._generation:
                continue
            self._busy = True
            try:
                if self.on_start:
                    self.on_start(text, emotion)
                if self.engine == "piper":
                    self._run_piper(text, emotion)
                else:
                    self._proc = subprocess.Popen(self._command(text, emotion), stdout=subprocess.DEVNULL,
                                                  stderr=subprocess.PIPE)
                    _out, err = self._proc.communicate(timeout=120)
                    if self._proc.returncode not in (0, None, -15) and err:
                        self.last_error = err.decode(errors="replace").strip()[:200]
            except Exception as exc:  # il TTS non deve mai fermare la sessione
                self.last_error = str(exc)
            finally:
                self._proc = None
                self._busy = False
                if self.on_end:
                    try:
                        self.on_end(text, emotion)
                    except Exception:
                        pass

    def _run_piper(self, text: str, emotion: Optional[str]) -> None:
        """Sintesi con piper (testo su stdin -> WAV) e riproduzione con il lettore trovato."""
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        cmd = self._command(text, emotion)
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _out, err = self._proc.communicate(input=text.encode("utf-8"), timeout=120)
        if self._proc.returncode not in (0, None, -15):
            self.last_error = (err or b"").decode(errors="replace").strip()[:200] or "piper fallito"
            return
        if self.player:
            self._proc = subprocess.Popen(self.player + [cmd[-1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._proc.communicate(timeout=120)

    # ----- effetti sonori ---------------------------------------------------------------

    def play(self, name: str) -> bool:
        if not (self.enabled and self.sounds and self.player):
            return False
        path = ensure_sound(name)
        if path is None:
            return False
        try:
            proc = subprocess.Popen(self.player + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            threading.Thread(target=proc.wait, daemon=True).start()  # niente processi zombie
            return True
        except OSError as exc:
            self.last_error = str(exc)
            return False
