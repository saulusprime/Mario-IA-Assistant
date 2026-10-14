"""
Uscita audio dal PC: voce (sintesi locale) ed effetti sonori sintetizzati.

Mario non ha un canale audio via Bluetooth, quindi la "voce di Mario" esce dagli altoparlanti
del computer. Motori, in ordine di preferenza:
  - `openvoice`: voce IA, cioè piper (voce neurale italiana) con il timbro di una registrazione di
    riferimento dato da OpenVoice V2; gira in un processo separato (`openvoice.py`) installato da
    `tools/setup_openvoice.sh`. Se il processo non è pronto o fallisce, la frase passa alla voce di
    riserva (piper da solo, poi spd-say): la risposta non si perde mai;
  - `piper` (voce neurale offline, con un modello .onnx);
  - `spd-say` (speech-dispatcher, sul PC di sviluppo con espeak-ng e voci italiane) ed `espeak-ng`.
Senza motore la classe resta muta ma funzionante (i test girano ovunque).

Le frasi passano da due thread: uno le prepara (file WAV per piper e OpenVoice) e uno le suona, così la
frase successiva si prepara mentre quella corrente è in riproduzione (al massimo due in anticipo).

Gli effetti sonori (moneta, power-up, allarme, ...) sono i suoni veri dei giochi se l'archivio è stato
scaricato (`soundpack.py`, `tools/fetch_mario_sounds.py`), altrimenti onde generate al volo con la
libreria standard; li suona `paplay` / `pw-play` / `aplay` / `ffplay`, il primo disponibile.
"""
from __future__ import annotations

import collections
import itertools
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

from . import openvoice as ov
from .emotions import EMOTIONS
from .soundpack import DEFAULT_STYLE, STYLES, SoundPack

SOUNDS_DIR = Path(tempfile.gettempdir()) / "mario-assistant-sounds"
SAMPLE_RATE = 22050
READY_AHEAD = 2  # frasi preparate in anticipo al massimo


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


def detect_engine(piper_model: str = "", openvoice_reference: str = "") -> Optional[str]:
    """`openvoice` (piper + timbro della registrazione di riferimento) se è installato e c'è il riferimento;
    poi `piper` se c'è il binario e il modello .onnx; poi spd-say ed espeak-ng."""
    if openvoice_reference and not ov.problem(openvoice_reference, piper_model):
        return "openvoice"
    if piper_model and ov.find_piper() and Path(piper_model).is_file():
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

    # punti di aggancio per i test (processo OpenVoice finto)
    openvoice_problem = staticmethod(ov.problem)
    openvoice_factory = ov.OpenVoiceClient
    piper_worker = True  # piper nel processo persistente quando c'è .venv-openvoice (altrimenti il comando)

    def __init__(self, enabled: bool = True, language: str = "it", voice: str = "", rate: int = 0,
                 pitch: int = 0, volume: int = 0, sounds: bool = True, engine: Optional[str] = None,
                 piper_model: str = "", openvoice_reference: str = "", openvoice_tau: float = 0.3,
                 openvoice_watermark: bool = False, openvoice_wait: float = 20.0,
                 sound_style: str = DEFAULT_STYLE, soundpack: Optional[SoundPack] = None):
        self.enabled = enabled
        self.language = language
        self.voice = voice
        self.rate = int(rate)
        self.pitch = int(pitch)
        self.volume = int(volume)
        self.sounds = sounds
        self.piper_model = piper_model
        self.openvoice_reference = openvoice_reference
        self.openvoice_tau = max(0.0, min(1.0, float(openvoice_tau)))
        self.openvoice_watermark = bool(openvoice_watermark)
        self.openvoice_wait = float(openvoice_wait)  # secondi massimi di attesa dell'avvio, poi voce di riserva
        self.sound_style = sound_style if sound_style in STYLES else DEFAULT_STYLE
        self.pack = soundpack if soundpack is not None else SoundPack()
        self.piper_model = piper_model
        self.engine = engine if engine is not None else self._detect()
        self.player = detect_player()
        self.spoken: list[tuple[str, Optional[str]]] = []
        self.on_start: Optional[Callable[[str, Optional[str]], None]] = None  # chiamato quando una frase inizia
        self.on_end: Optional[Callable[[str, Optional[str]], None]] = None
        self.last_error = ""
        self.timings: collections.deque = collections.deque(maxlen=50)  # tempi di preparazione delle frasi
        self._spoke_any = False
        self._queue: queue.Queue = queue.Queue()                    # frasi da preparare
        self._ready: queue.Queue = queue.Queue(maxsize=READY_AHEAD)  # frasi pronte da suonare
        self._pending = 0                                           # frasi accettate e non ancora finite
        self._count_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None          # riproduzione in corso
        self._render_proc: Optional[subprocess.Popen] = None   # piper che prepara una frase
        self._busy = False
        self._generation = 0
        self._serial = itertools.count(1)
        self._ov: Optional[ov.OpenVoiceClient] = None
        self._start_worker()
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()
        self._player_thread = threading.Thread(target=self._play_loop, name="tts-play", daemon=True)
        self._player_thread.start()

    # ----- configurazione -------------------------------------------------------------

    def configure(self, **opts) -> None:
        for key in ("enabled", "language", "voice", "rate", "pitch", "volume", "sounds"):
            if key in opts and opts[key] is not None:
                value = opts[key]
                if key in ("rate", "pitch", "volume"):
                    value = max(-100, min(100, int(value)))
                setattr(self, key, value)
        if opts.get("sound_style") in STYLES:
            self.sound_style = opts["sound_style"]
        if opts.get("openvoice_tau") not in (None, ""):
            self.openvoice_tau = max(0.0, min(1.0, float(opts["openvoice_tau"])))
        changed = False
        for key, cast in (("piper_model", str), ("openvoice_reference", str), ("openvoice_watermark", bool)):
            if key in opts and opts[key] is not None and cast(opts[key]) != getattr(self, key):
                setattr(self, key, cast(opts[key]))
                changed = True
        if changed:  # cambia motore se il modello o il riferimento ci sono (o non ci sono più)
            self._stop_openvoice()
            self.engine = self._detect()
            self._start_worker()
        elif self.enabled and self._ov is None:
            self._start_worker()  # voce riattivata
        if not self.enabled and self._ov is not None:
            self._stop_openvoice()  # voce spenta: libera la memoria di torch e dei modelli

    def _detect(self) -> Optional[str]:
        if self.openvoice_reference and not self.openvoice_problem(self.openvoice_reference, self.piper_model):
            return "openvoice"
        return detect_engine(self.piper_model)

    def _start_worker(self) -> None:
        """Processo della voce: piper + OpenVoice per la voce IA, oppure piper da solo con il modello sempre
        caricato (una frase in qualche decimo di secondo invece dei 2-3 s del comando `piper`).
        Parte solo con la voce attiva."""
        if not self.enabled or self._ov is not None:
            return
        if self.engine == "openvoice":
            reason = self.openvoice_problem(self.openvoice_reference, self.piper_model)
            if reason:
                self.last_error = f"voce IA non disponibile: {reason}"
                self.engine = detect_engine(self.piper_model)
                self._start_worker()
                return
            self._ov = self.openvoice_factory(self.openvoice_reference, self.piper_model,
                                              watermark=self.openvoice_watermark)
        elif (self.engine == "piper" and self.piper_worker and ov.DEFAULT_PYTHON.is_file()
              and self.piper_model and Path(self.piper_model).is_file()):
            self._ov = self.openvoice_factory("", self.piper_model, watermark=False)
        else:
            return
        self._ov.start()

    def _stop_openvoice(self) -> None:
        client, self._ov = self._ov, None
        if client is not None:
            client.stop()

    def warmup(self, timeout: float) -> bool:
        """Aspetta che la voce sia pronta: per la voce IA il caricamento di piper e OpenVoice (alcuni secondi,
        di più la prima volta per le impronte vocali); per gli altri motori True subito se ce n'è uno."""
        if self._ov is None:
            return self.engine is not None
        return self._ov.wait_ready(timeout)

    def openvoice_reason(self) -> str:
        """Perché la voce IA non è attiva ('' se è attiva o se non è stata chiesta)."""
        if not self.openvoice_reference or self.engine == "openvoice":
            return ""
        return self.openvoice_problem(self.openvoice_reference, self.piper_model) or self.last_error

    def close(self) -> None:
        """Ferma la voce e il processo OpenVoice (alla chiusura dell'applicazione)."""
        self.stop()
        self._stop_openvoice()

    def status(self) -> dict:
        voice_ai = self._ov.status() if self._ov else {
            "state": "spento",
            "problem": self.openvoice_problem(self.openvoice_reference, self.piper_model) if self.openvoice_reference else ""}
        renders = [t["render_s"] for t in self.timings if t.get("render_s") is not None]
        return {"engine": self.engine, "player": self.player[0] if self.player else None, "enabled": self.enabled,
                "busy": self._busy, "queued": self._queue.qsize() + self._ready.qsize(), "voice": self.voice,
                "rate": self.rate, "pitch": self.pitch, "sounds": self.sounds, "piper_model": self.piper_model,
                "openvoice_reference": self.openvoice_reference, "openvoice_tau": self.openvoice_tau,
                "openvoice": voice_ai, "last_timing": self.timings[-1] if self.timings else None,
                "sound_style": self.sound_style, "soundpack": {"files": len(self.pack.items),
                                                              "folder": str(self.pack.folder)},
                "avg_render_s": round(sum(renders) / len(renders), 2) if renders else None,
                "last_error": self.last_error}

    @property
    def available(self) -> bool:
        return self.engine is not None

    @property
    def idle(self) -> bool:
        """Vero se non sta parlando e non ha frasi in coda o in preparazione."""
        return self._pending == 0

    # ----- voce -------------------------------------------------------------------------

    def speak(self, text: str, emotion: Optional[str] = None) -> None:
        text = " ".join(text.split())
        if not text:
            return
        self.spoken.append((text, emotion))
        if self.enabled and self.engine:
            self._spoke_any = True
            with self._count_lock:
                self._pending += 1
            self._queue.put((self._generation, next(self._serial), text, emotion))

    def _done(self, count: int = 1) -> None:
        with self._count_lock:
            self._pending = max(0, self._pending - count)

    def stop(self) -> None:
        """Svuota le code e interrompe la frase in corso (e quella in preparazione con piper)."""
        self._generation += 1
        drained = 0
        for q in (self._queue, self._ready):
            try:
                while True:
                    item = q.get_nowait()
                    drained += 1
                    if q is self._ready and item[3] == "wav":
                        Path(item[4]).unlink(missing_ok=True)
            except queue.Empty:
                pass
        self._done(drained)
        for proc in (self._proc, self._render_proc):
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
            if self.idle:
                return True
            time.sleep(0.05)
        return False

    def _rate_pitch(self, emotion: Optional[str]) -> tuple[int, int]:
        emo = EMOTIONS.get(emotion or "")
        rate = max(-100, min(100, self.rate + (emo.rate if emo else 0)))
        pitch = max(-100, min(100, self.pitch + (emo.pitch if emo else 0)))
        return rate, pitch

    def _length_scale(self, emotion: Optional[str]) -> float:
        """Velocità per piper (1 = normale, più piccolo = più veloce); il tono non è regolabile."""
        rate, _pitch = self._rate_pitch(emotion)
        return round(max(0.6, min(1.6, 1.0 - rate / 200)), 2)

    def _command(self, text: str, emotion: Optional[str], engine: Optional[str] = None,
                 out: Optional[str] = None, model: Optional[str] = None) -> list[str]:
        engine = engine or self.engine
        rate, pitch = self._rate_pitch(emotion)
        if engine == "piper":
            # piper legge il testo dallo standard input e scrive un WAV; l'emozione si sente solo nel ritmo
            return [ov.find_piper() or "piper", "--model", model or self.piper_model, "--length_scale",
                    str(self._length_scale(emotion)), "--output_file", out or str(SOUNDS_DIR / "piper-out.wav")]
        if engine == "spd-say":
            cmd = ["spd-say", "-w", "-l", self.language, "-r", str(rate), "-p", str(pitch), "-i", str(self.volume)]
            if self.voice:
                cmd += ["-y", self.voice]
            return cmd + ["--", text]
        # espeak-ng: velocità in parole al minuto (80..450), tono 0..99, ampiezza 0..200
        wpm = int(175 + rate * 1.2)
        return [engine, "-v", self.language, "-s", str(max(80, min(450, wpm))),
                "-p", str(max(0, min(99, 50 + pitch // 2))), "-a", str(max(0, min(200, 100 + self.volume))), "--", text]

    # ----- preparazione (thread "tts") ------------------------------------------------------

    def _run(self) -> None:
        while True:
            generation, serial, text, emotion = self._queue.get()
            if generation != self._generation:
                self._done()
                continue
            t0 = time.monotonic()
            timing: dict = {"serial": serial, "chars": len(text)}
            try:
                kind, payload = self._render(text, emotion, serial, timing)
            except Exception as exc:  # il TTS non deve mai fermare la sessione
                self.last_error = str(exc)
                kind, payload = "error", ""
            timing["render_s"] = round(time.monotonic() - t0, 3)
            self.timings.append(timing)
            item = (generation, text, emotion, kind, payload)
            while True:  # posto tra le frasi pronte, ma senza sopravvivere a uno stop
                if generation != self._generation:
                    if kind == "wav":
                        Path(payload).unlink(missing_ok=True)
                    self._done()
                    break
                try:
                    self._ready.put(item, timeout=0.2)
                    break
                except queue.Full:
                    continue

    def _render(self, text: str, emotion: Optional[str], serial: int, timing: dict) -> tuple[str, object]:
        """Prepara una frase: ("wav", file) per piper e OpenVoice, ("cmd", riga di comando) per spd-say ed
        espeak-ng, che sintetizzano e suonano insieme."""
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        client = self._ov
        if client is not None and self.engine in ("openvoice", "piper"):
            label = "voce IA" if self.engine == "openvoice" else "processo piper"
            remaining = client.started_at + self.openvoice_wait - time.monotonic()
            if client.wait_ready(remaining):
                out = str(SOUNDS_DIR / f"tts-{serial}.wav")
                try:
                    info = client.speak(text, out, self._length_scale(emotion), self.openvoice_tau, self._gain())
                    timing.update(engine=self.engine, base_s=info.get("base_s"), convert_s=info.get("convert_s"),
                                  audio_s=info.get("audio_s"))
                    return "wav", out
                except ov.OpenVoiceError as exc:
                    self.last_error = f"{label}: {exc}"
            elif client.state == "errore":
                self.last_error = f"{label}: {client.last_error}"
                if client.revive():  # crash dopo il caricamento: riparte, intanto la voce di riserva
                    self.last_error += " (riavviato)"
            engine, model = self._fallback() if self.engine == "openvoice" else ("piper", self.piper_model)
            timing["fallback"] = engine if self.engine == "openvoice" else "piper (comando)"
            return self._render_with(engine, model, text, emotion, serial, timing)
        return self._render_with(self.engine, self.piper_model, text, emotion, serial, timing)

    def _fallback(self) -> tuple[Optional[str], str]:
        """Voce di riserva della voce IA: piper con la stessa voce di base, altrimenti spd-say/espeak-ng."""
        model = self.piper_model or ov.default_piper_model()
        if model and ov.find_piper() and Path(model).is_file():
            return "piper", model
        return detect_engine(), ""

    def _gain(self) -> float:
        return max(0.0, min(2.0, 1.0 + self.volume / 100))

    def _render_with(self, engine: Optional[str], model: str, text: str, emotion: Optional[str], serial: int,
                     timing: dict) -> tuple[str, object]:
        timing.setdefault("engine", engine)
        if engine is None:
            return "error", ""
        if engine != "piper":
            return "cmd", self._command(text, emotion, engine=engine)
        out = str(SOUNDS_DIR / f"tts-{serial}.wav")
        cmd = self._command(text, emotion, engine="piper", out=out, model=model)
        self._render_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE)
        try:
            _out, err = self._render_proc.communicate(input=text.encode("utf-8"), timeout=120)
            code = self._render_proc.returncode
        finally:
            self._render_proc = None
        if code not in (0, None):
            if code != -15:  # -15 = interrotto da stop()
                self.last_error = (err or b"").decode(errors="replace").strip()[:200] or "piper fallito"
            return "error", ""
        return "wav", out

    # ----- riproduzione (thread "tts-play") -------------------------------------------------

    def _play_loop(self) -> None:
        while True:
            generation, text, emotion, kind, payload = self._ready.get()
            if generation != self._generation:
                if kind == "wav":
                    Path(payload).unlink(missing_ok=True)
                self._done()
                continue
            self._busy = True
            try:
                if self.on_start:
                    self.on_start(text, emotion)
                if kind == "wav":
                    if self.player:
                        self._play_process(self.player + [payload])
                    else:
                        self.last_error = "nessun lettore audio (paplay, pw-play, aplay, ffplay)"
                elif kind == "cmd":
                    self._play_process(list(payload))
            except Exception as exc:  # il TTS non deve mai fermare la sessione
                self.last_error = str(exc)
            finally:
                if kind == "wav":
                    Path(payload).unlink(missing_ok=True)
                self._proc = None
                self._busy = False
                if self.on_end:
                    try:
                        self.on_end(text, emotion)
                    except Exception:
                        pass
                self._done()

    def _play_process(self, cmd: list[str]) -> None:
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _out, err = self._proc.communicate(timeout=120)
        if self._proc.returncode not in (0, None, -15) and err:
            self.last_error = err.decode(errors="replace").strip()[:200]

    # ----- effetti sonori ---------------------------------------------------------------

    def sound_path(self, name: str) -> Optional[Path]:
        """Il file di un effetto: scelta dell'utente o suono vero dei giochi (secondo lo stile) o, in mancanza,
        quello sintetizzato."""
        if self.sound_style == "sintetici":
            return ensure_sound(name)
        return self.pack.path(name, self.sound_style) or ensure_sound(name)

    def effect_command(self, path: Path) -> list[str]:
        """Riga di comando del lettore con il volume degli effetti della pagina Suoni (0-150%)."""
        cmd = list(self.player or [])
        volume = self.pack.volume
        if cmd and volume != 100:
            if cmd[0] == "paplay":
                cmd.append(f"--volume={int(65536 * volume / 100)}")
            elif cmd[0] == "pw-play":
                cmd.append(f"--volume={volume / 100:.2f}")
            elif cmd[0] == "ffplay":
                cmd += ["-volume", str(min(100, volume))]
        return cmd + [str(path)]

    def _play_path(self, path: Optional[Path]) -> bool:
        if path is None or not self.player:
            return False
        try:
            proc = subprocess.Popen(self.effect_command(path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            threading.Thread(target=proc.wait, daemon=True).start()  # niente processi zombie
            return True
        except OSError as exc:
            self.last_error = str(exc)
            return False

    def play(self, name: str) -> bool:
        if not (self.enabled and self.sounds and self.player):
            return False
        return self._play_path(self.sound_path(name))

    def preview(self, name: str, style: Optional[str] = None) -> Optional[Path]:
        """Suona un effetto nello stile indicato (prova dalla pagina Suoni), anche a voce spenta."""
        style = style if style in STYLES else self.sound_style
        path = ensure_sound(name) if style == "sintetici" else (self.pack.path(name, style) or ensure_sound(name))
        return path if self._play_path(path) else None

    def play_file(self, rel: str, seconds: Optional[float] = None) -> Optional[Path]:
        """Suona sul PC un file preciso dell'archivio (prova dalla pagina Suoni), anche a voce spenta."""
        path = self.pack.playable(rel, seconds)
        return path if self._play_path(path) else None
