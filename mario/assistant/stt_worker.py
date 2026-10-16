"""
Processo dell'ingresso vocale: faster-whisper (Whisper su CTranslate2) con il modello sempre caricato.

Gira con l'interprete di `.venv-stt` (creato da `tools/setup_stt.sh`), non con il venv del progetto, e non
importa nulla del pacchetto `mario`. Protocollo di `worker.py`, una richiesta per riga:

  avvio  -> {"event": "ready", "load_s": 1.4, "model": "small", "threads": 2}
            (oppure {"event": "error", "error": "..."} e uscita con codice 1)
  {"id": 1, "cmd": "transcribe", "path": "/tmp/voce.wav", "prompt": "Mario, Goomba, ..."}
         -> {"id": 1, "ok": true, "text": "Facciamo un salto!", "audio_s": 1.9, "elapsed_s": 3.1}
  {"id": 2, "cmd": "ping"}  -> {"id": 2, "ok": true}
  {"cmd": "quit"}

Misure del 16/10 su un i7-5557U (2 core) con frasi sintetiche: small circa 3 s per frase (12-18% di parole
sbagliate, anche con voce da bambino), base circa 1 s (25-50%). Due thread vanno meglio di quattro.
Whisper elabora sempre una finestra di 30 s: il tempo non dipende dalla lunghezza della frase.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import IO, Optional


def _send(out: IO[str], msg: dict) -> None:
    out.write(json.dumps(msg, ensure_ascii=False) + "\n")
    out.flush()


class SpeechEngine:
    def __init__(self, model: str, threads: int = 2, language: str = "it", beam_size: int = 1):
        self.model_path = model
        self.threads = threads
        self.language = language
        self.beam_size = beam_size
        self.model = None

    def load(self) -> dict:
        import numpy as np
        from faster_whisper import WhisperModel
        self.model = WhisperModel(self.model_path, device="cpu", compute_type="int8", cpu_threads=self.threads)
        # riscaldamento: la prima trascrizione inizializza parti pigre del modello (circa un secondo in più)
        list(self.model.transcribe(np.zeros(16000, dtype=np.float32), language=self.language, beam_size=1,
                                   without_timestamps=True)[0])
        return {"model": self.model_path.rstrip("/").rsplit("/", 1)[-1], "threads": self.threads}

    def transcribe(self, path: str, prompt: str = "") -> dict:
        t0 = time.monotonic()
        segments, info = self.model.transcribe(path, language=self.language, beam_size=self.beam_size,
                                               vad_filter=True, without_timestamps=True,
                                               condition_on_previous_text=False, initial_prompt=prompt or None)
        text = " ".join(s.text.strip() for s in segments).strip()
        return {"text": text, "audio_s": round(info.duration, 2), "speech_s": round(info.duration_after_vad, 2),
                "elapsed_s": round(time.monotonic() - t0, 2)}


def serve(engine, inp: IO[str], out: IO[str]) -> int:
    """Carica il modello, annuncia "ready" e risponde alle richieste fino a "quit" o alla fine dell'input."""
    t0 = time.monotonic()
    try:
        info = engine.load() or {}
    except Exception as exc:  # modello o librerie mancanti: chi usa il processo lo segnala e continua senza
        _send(out, {"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    _send(out, {"event": "ready", "load_s": round(time.monotonic() - t0, 2), **info})
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _send(out, {"ok": False, "error": "richiesta non JSON"})
            continue
        rid, cmd = req.get("id"), req.get("cmd")
        try:
            if cmd == "quit":
                break
            if cmd == "ping":
                _send(out, {"id": rid, "ok": True})
            elif cmd == "transcribe":
                _send(out, {"id": rid, "ok": True, **engine.transcribe(str(req["path"]), str(req.get("prompt") or ""))})
            else:
                _send(out, {"id": rid, "ok": False, "error": f"comando sconosciuto: {cmd}"})
        except Exception as exc:
            _send(out, {"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ingresso vocale dell'assistente: faster-whisper in un processo separato")
    ap.add_argument("--model", required=True, help="cartella del modello CTranslate2 (third_party/whisper-models/small)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--language", default="it")
    ap.add_argument("--beam", type=int, default=1)
    args = ap.parse_args(argv)
    return serve(SpeechEngine(args.model, args.threads, args.language, args.beam), sys.stdin, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
