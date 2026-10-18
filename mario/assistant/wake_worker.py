"""
Processo della parola di attivazione: ascolta il microfono di continuo e si accorge quando qualcuno dice
"Ehi Mario"; poi registra la frase fino al silenzio e la passa a chi lo usa (la trascrive Whisper).

Gira con l'interprete di `.venv-stt` (Vosk e il modello italiano piccolo li installa `tools/setup_stt.sh`),
sempre con `python -I`, e non importa nulla del pacchetto `mario`. Protocollo di `worker.py` più eventi
spontanei:

  avvio  -> {"event": "ready", "load_s": 0.9, "phrases": ["ehi mario", "hey mario"], "source": "pw-record"}
  {"event": "wake", "text": "ehi mario"}            primo filtro: forse qualcuno ha chiamato Mario
  {"event": "utterance", "path": "/tmp/.../chiamata-103512-1.wav", "audio_s": 3.4, "mode": "wake",
   "reason": "silenzio"}                            frase registrata (mode "listen" se chiesta con "listen")
  {"event": "no_speech", "mode": "listen"}          ascolto chiesto con "listen": nessuno ha parlato
  {"event": "cancelled", "mode": "wake"}            registrazione interrotta da "pause" o "cancel"
  {"event": "audio_end", "error": "..."}            il registratore si è chiuso (o il file è finito)
  {"id": 1, "cmd": "pause"}   -> l'audio arriva ancora ma viene buttato (Mario sta parlando)
  {"id": 2, "cmd": "resume"}  -> si torna ad ascoltare
  {"id": 3, "cmd": "listen", "wait_s": 6}  -> registra la prossima frase senza parola di attivazione
  {"id": 4, "cmd": "cancel"}, {"id": 5, "cmd": "ping"}, {"cmd": "quit"}

Come decide. Vosk riconosce con una grammatica ristretta: le frasi di attivazione, alcune parole che le
somigliano ("maria", "dario", "marco", "mare", "sono mario"...) e [unk] per tutto il resto. È un primo filtro
generoso: con le prove del 18/10 su 64 frasi sintetiche (piper, voci adulte e "da bambino") trova 21
chiamate su 24, ma scatta anche su 8 frasi su 40 scelte apposta per ingannarlo ("Ehi Maria", "Ehi Marco",
"Hai visto il film di Mario"). Il giudice è Whisper, che deve comunque trascrivere la frase: chi usa il
processo scarta le registrazioni la cui trascrizione non contiene la frase di attivazione, senza conservarne il
testo. Vosk costa circa il 3 % di un core.

Registrazione. Gli ultimi secondi di audio restano in memoria: la registrazione parte dall'inizio della frase
in cui Vosk ha sentito la chiamata (il primo parlato dopo almeno mezzo secondo di silenzio, più 0,3 s di
margine, al massimo 8 s indietro), quindi "Ehi Mario, raccontami una storia" detto tutto d'un fiato arriva
intero. L'inizio lo decide l'energia, non Vosk: nelle prove su un flusso continuo i risultati finali di Vosk
cadevano a metà frase o non cadevano tra due frasi, e la registrazione perdeva la "Ehi" o si portava dietro la
frase precedente. Finisce dopo `end_silence` secondi di silenzio dopo l'ultima parola (al massimo `max_s`).
Voce e silenzio si distinguono con l'energia di blocchi da 20 ms, con una soglia che segue il rumore di
fondo. L'audio resta in memoria; su disco va solo la frase registrata, che chi la trascrive cancella subito
dopo. Gli eventi riportano la posizione nel flusso (`at_s`, `start_s`), utile per le prove con un file.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import IO, Callable, Optional

RATE = 16000
FRAME = 320                 # campioni per blocco: 20 ms a 16 kHz
FRAME_BYTES = FRAME * 2
FRAMES_PER_S = RATE // FRAME
VOSK_FRAMES = 5             # a Vosk si passa un pezzo ogni 100 ms
PHRASE_GAP_S = 0.5          # silenzio che separa due frasi
LEAD_S = 0.3                # audio tenuto prima dell'inizio della frase
MAX_PREROLL_S = 8.0         # mai più di così all'indietro (rumore continuo: nessun silenzio da trovare)
INTERJECTIONS = ("ehi", "hey", "hei", "ei", "ehy")
# parole che somigliano a "mario" o lo contengono: senza alternative la grammatica fa diventare "ehi mario" anche
# "ehi maria" o "andiamo al mare" (prove del 18/10: da 17 a 8 falsi su 40)
DISTRACTORS = ["mario", "maria", "dario", "marco", "mare", "e mario", "di mario", "sono mario", "amico mario"]


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


def phrase_pattern(phrases: list[str]) -> re.Pattern:
    """Espressione che trova una delle frasi; le interiezioni (ehi, hey, hei...) valgono tutte uguali, e
    all'inizio del testo vale anche "e" ("e mario" è tra le parole simili della grammatica, e su alcune voci
    vince su "ehi mario": prove del 18/10). Whisper poi controlla la frase con la stessa regola."""
    alts = []
    for phrase in phrases:
        words = normalize(phrase).split()
        if not words:
            continue
        parts = ["(?:" + "|".join(INTERJECTIONS) + ")" if w in INTERJECTIONS else re.escape(w) for w in words]
        alts.append(r"\b" + r"\s+".join(parts))
        if words[0] in INTERJECTIONS and len(words) > 1:
            alts.append(r"^e\s+" + r"\s+".join(re.escape(w) for w in words[1:]))
    return re.compile(r"(?:" + "|".join(alts or ["(?!)"]) + r")\b")


def grammar_for(phrases: list[str]) -> list[str]:
    words = []
    for phrase in phrases:
        norm = normalize(phrase)
        first, _, rest = norm.partition(" ")
        variants = [f"{i} {rest}".strip() for i in INTERJECTIONS[:2]] if first in INTERJECTIONS else [norm]
        words += variants + [w for w in norm.split()]
    words += list(INTERJECTIONS[:2]) + DISTRACTORS + ["[unk]"]
    seen, out = set(), []
    for w in words:
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


class Vad:
    """Voce o silenzio per blocchi da 20 ms: energia (RMS) sopra una soglia che segue il rumore di fondo."""

    def __init__(self, ratio: float = 3.0, min_rms: float = 300.0):
        self.ratio = ratio
        self.min_rms = min_rms
        self.noise = min_rms / ratio

    @staticmethod
    def rms(frame: bytes) -> float:
        samples = array.array("h", frame[: len(frame) - len(frame) % 2])
        if sys.byteorder == "big":
            samples.byteswap()
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    @property
    def threshold(self) -> float:
        return max(self.min_rms, self.noise * self.ratio)

    def is_speech(self, frame: bytes) -> bool:
        level = self.rms(frame)
        speech = level >= self.threshold
        if not speech:  # il rumore di fondo si aggiorna solo sul silenzio
            self.noise = 0.95 * self.noise + 0.05 * level
        return speech


class WakeEngine:
    """Macchina a stati: ascolto -> (chiamata) registrazione -> frase; pausa; ascolto senza chiamata (listen).

    `recognizer` ha `accept(pcm) -> (finale: bool, testo: str)` e `reset()`; `emit(dict)` riceve gli eventi."""

    def __init__(self, recognizer, phrases: list[str], out_dir: Path, emit: Callable[[dict], None],
                 end_silence: float = 1.0, max_s: float = 15.0, buffer_s: float = 15.0, vad: Optional[Vad] = None):
        self.rec = recognizer
        self.pattern = phrase_pattern(phrases)
        self.out_dir = Path(out_dir)
        self.emit = emit
        self.end_frames = max(1, int(end_silence * RATE / FRAME))
        self.max_frames = int(max_s * RATE / FRAME)
        self.vad = vad or Vad()
        self.mode = "ascolto"          # ascolto | registrazione | attesa (listen) ; la pausa è a parte
        self.paused = False
        self.history: deque = deque(maxlen=max(1, int(buffer_s * FRAMES_PER_S)))  # ultimi secondi di audio
        self.phrase_len = 0            # blocchi dall'inizio della frase in corso (primo parlato dopo una pausa)
        self.quiet_run = 0             # blocchi di silenzio di fila
        self.position = 0              # blocchi ricevuti dall'avvio (per la posizione negli eventi)
        self.chunk: list[bytes] = []   # blocchi non ancora passati a Vosk
        self.recording: list[bytes] = []
        self.silence = 0
        self.speech_run = 0
        self.heard = False
        self.wait_frames = 0
        self.waited = 0
        self.rest = b""
        self.count = 0
        self.lock = threading.Lock()

    # ----- comandi --------------------------------------------------------------------------

    def pause(self) -> None:
        with self.lock:
            if self.mode != "ascolto":
                self.emit({"event": "cancelled", "mode": self._public_mode()})
            self._back_to_listening()
            self.paused = True

    def resume(self) -> None:
        with self.lock:
            if self.paused and self.mode == "ascolto":
                self._back_to_listening()
            self.paused = False

    def cancel(self) -> None:
        with self.lock:
            if self.mode != "ascolto":
                self.emit({"event": "cancelled", "mode": self._public_mode()})
            self._back_to_listening()

    def listen(self, wait_s: float = 6.0) -> None:
        """Registra la prossima frase anche senza parola di attivazione (dopo "Eccomi, dimmi!")."""
        with self.lock:
            self.paused = False
            self._start("attesa", [], already_speaking=False)
            self.wait_frames = max(1, int(wait_s * RATE / FRAME))

    # ----- audio ----------------------------------------------------------------------------

    def feed(self, pcm: bytes) -> None:
        with self.lock:
            data = self.rest + pcm
            whole = len(data) - len(data) % FRAME_BYTES
            self.rest = data[whole:]
            for i in range(0, whole, FRAME_BYTES):
                self._frame(data[i:i + FRAME_BYTES])

    def _frame(self, frame: bytes) -> None:
        self.position += 1
        if self.paused:
            return
        speech = self.vad.is_speech(frame)
        if self.mode == "ascolto":
            self.history.append(frame)
            if speech:
                if self.quiet_run >= PHRASE_GAP_S * FRAMES_PER_S:
                    self.phrase_len = 0  # comincia una frase nuova
                self.quiet_run = 0
            else:
                self.quiet_run += 1
            self.phrase_len += 1
            self.chunk.append(frame)
            if len(self.chunk) < VOSK_FRAMES:
                return
            pcm, self.chunk = b"".join(self.chunk), []
            final, text = self.rec.accept(pcm)
            match = self.pattern.search(normalize(text))
            if match:
                self.emit({"event": "wake", "text": match.group(0), "at_s": round(self.position / FRAMES_PER_S, 2)})
                back = min(len(self.history), self.phrase_len + int(LEAD_S * FRAMES_PER_S),
                           int(MAX_PREROLL_S * FRAMES_PER_S))
                preroll = list(self.history)[len(self.history) - back:]
                quiet = self.quiet_run
                self._start("registrazione", preroll, already_speaking=not final)
                if final:  # la frase è già finita: il silenzio dopo l'ultima parola conta (fino a metà dell'attesa)
                    self.silence = min(quiet, self.end_frames // 2)
            return
        self.recording.append(frame)
        if speech:
            self.speech_run += 1
            if self.speech_run >= 3:
                self.heard, self.silence = True, 0
        else:
            self.speech_run = 0
            self.silence += 1
        if len(self.recording) >= self.max_frames:
            self._finish("tempo massimo")
        elif self.mode == "attesa" and not self.heard:
            self.waited += 1
            if self.waited >= self.wait_frames:
                self.emit({"event": "no_speech", "mode": "listen"})
                self._back_to_listening()
        elif self.silence >= self.end_frames:
            self._finish("silenzio")

    # ----- interni --------------------------------------------------------------------------

    def _public_mode(self) -> str:
        return "listen" if self.mode == "attesa" else "wake"

    def _start(self, mode: str, preroll: list[bytes], already_speaking: bool) -> None:
        self.mode = mode
        self.recording = list(preroll)
        self.silence = 0
        self.speech_run = 0
        self.heard = already_speaking
        self.waited = 0
        self.chunk = []
        self.history.clear()
        self.phrase_len = self.quiet_run = 0

    def _back_to_listening(self) -> None:
        self.mode = "ascolto"
        self.recording, self.chunk = [], []
        self.history.clear()
        self.silence = self.speech_run = self.waited = self.phrase_len = self.quiet_run = 0
        self.heard = False
        self.rec.reset()

    def _finish(self, reason: str) -> None:
        self.count += 1
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"chiamata-{time.strftime('%H%M%S')}-{self.count}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(self.recording))
        seconds = len(self.recording) * FRAME / RATE
        self.emit({"event": "utterance", "path": str(path), "audio_s": round(seconds, 2),
                   "mode": self._public_mode(), "reason": reason,
                   "start_s": round((self.position - len(self.recording)) / FRAMES_PER_S, 2)})
        self._back_to_listening()


class VoskRecognizer:
    def __init__(self, model_path: str, grammar: list[str]):
        import vosk
        vosk.SetLogLevel(-1)
        self.model = vosk.Model(model_path)
        self.rec = vosk.KaldiRecognizer(self.model, RATE, json.dumps(grammar, ensure_ascii=False))

    def accept(self, pcm: bytes) -> tuple[bool, str]:
        if self.rec.AcceptWaveform(pcm):
            return True, json.loads(self.rec.Result()).get("text", "")
        return False, json.loads(self.rec.PartialResult()).get("partial", "")

    def reset(self) -> None:
        self.rec.Reset()


# ----- processo ---------------------------------------------------------------------------

class Output:
    def __init__(self, out: IO[str]):
        self.out = out
        self.lock = threading.Lock()

    def send(self, msg: dict) -> None:
        with self.lock:
            self.out.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.out.flush()


def read_source(engine: WakeEngine, args, output: Output, stop: threading.Event) -> None:
    """Legge l'audio (registratore o file WAV) e lo passa al motore, a pezzi da 100 ms."""
    chunk = FRAME_BYTES * VOSK_FRAMES
    error = ""
    try:
        if args.file:
            with wave.open(args.file) as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
                    raise ValueError("serve un WAV mono a 16 bit e 16 kHz")
                while not stop.is_set():
                    data = w.readframes(chunk // 2)
                    if not data:
                        break
                    engine.feed(data)
                    if args.realtime:
                        time.sleep(len(data) / 2 / RATE)
            for _ in range(int(2.5 * RATE * 2 / chunk)):  # silenzio finale: chiude la frase in corso
                engine.feed(b"\0" * chunk)
        else:
            proc = subprocess.Popen(json.loads(args.recorder_cmd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                while not stop.is_set():
                    data = proc.stdout.read(chunk)
                    if not data:
                        error = f"registratore terminato (codice {proc.poll()})"
                        break
                    engine.feed(data)
            finally:
                proc.kill()
                proc.wait(timeout=3)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    output.send({"event": "audio_end", "error": error})
    if not args.file and not stop.is_set():
        os._exit(2)  # senza microfono il processo non serve: chi lo usa vede la chiusura e lo segnala


def serve(args, inp: IO[str], out: IO[str], recognizer=None) -> int:
    output = Output(out)
    phrases = [p.strip() for p in args.phrases.split("|") if p.strip()]
    t0 = time.monotonic()
    try:
        recognizer = recognizer or VoskRecognizer(args.model, grammar_for(phrases))
    except Exception as exc:  # vosk o modello mancanti: chi usa il processo lo segnala e continua senza
        output.send({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    engine = WakeEngine(recognizer, phrases, Path(args.out_dir), output.send, end_silence=args.end_silence,
                        max_s=args.max_seconds)
    stop = threading.Event()
    source = "file" if args.file else json.loads(args.recorder_cmd)[0]
    output.send({"event": "ready", "load_s": round(time.monotonic() - t0, 2), "phrases": phrases, "source": source})
    threading.Thread(target=read_source, args=(engine, args, output, stop), name="audio", daemon=True).start()
    commands = {"pause": engine.pause, "resume": engine.resume, "cancel": engine.cancel}
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            output.send({"ok": False, "error": "richiesta non JSON"})
            continue
        rid, cmd = req.get("id"), req.get("cmd")
        try:
            if cmd == "quit":
                break
            if cmd == "ping":
                pass
            elif cmd == "listen":
                engine.listen(float(req.get("wait_s") or 6.0))
            elif cmd in commands:
                commands[cmd]()
            else:
                output.send({"id": rid, "ok": False, "error": f"comando sconosciuto: {cmd}"})
                continue
            output.send({"id": rid, "ok": True, "mode": engine.mode, "paused": engine.paused})
        except Exception as exc:
            output.send({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    stop.set()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="parola di attivazione dell'assistente: Vosk in un processo separato")
    ap.add_argument("--model", required=True, help="cartella del modello Vosk (third_party/vosk-models/...)")
    ap.add_argument("--phrases", default="ehi mario|hey mario", help="frasi di attivazione separate da |")
    ap.add_argument("--out-dir", required=True, help="dove scrivere le frasi registrate (le cancella chi le trascrive)")
    ap.add_argument("--recorder-cmd", help="comando JSON del registratore che scrive PCM grezzo s16 mono 16 kHz")
    ap.add_argument("--file", help="al posto del microfono: un WAV mono 16 bit 16 kHz (prove)")
    ap.add_argument("--realtime", action="store_true", help="con --file: legge alla velocità del parlato")
    ap.add_argument("--end-silence", type=float, default=1.0)
    ap.add_argument("--max-seconds", type=float, default=15.0)
    args = ap.parse_args(argv)
    if not args.file and not args.recorder_cmd:
        ap.error("serve --recorder-cmd oppure --file")
    return serve(args, sys.stdin, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
