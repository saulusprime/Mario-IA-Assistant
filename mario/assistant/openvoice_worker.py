"""
Processo della "voce IA": piper (voce neurale italiana) + OpenVoice V2 (convertitore di timbro).

Gira con l'interprete di `.venv-openvoice` (creato da `tools/setup_openvoice.sh`), non con il venv del
progetto: OpenVoice e torch hanno dipendenze proprie. Non importa nulla del pacchetto `mario`.
Tiene i modelli caricati e risponde a una richiesta per riga:

  avvio  -> {"event": "ready", "load_s": 6.1, "watermark": true, "openvoice": true}
            (oppure {"event": "error", "error": "..."} e uscita con codice 1)
  {"id": 1, "cmd": "speak", "text": "Ciao!", "out": "/tmp/x.wav", "length_scale": 1.0, "tau": 0.3, "gain": 1.0}
         -> {"id": 1, "ok": true, "base_s": 0.4, "convert_s": 1.2, "audio_s": 1.6}
  {"id": 2, "cmd": "reference", "path": "/percorso/voce.wav"}   -> {"id": 2, "ok": true}
  {"id": 3, "cmd": "ping"}                                       -> {"id": 3, "ok": true}
  {"cmd": "quit"}

OpenVoice V2 non parla italiano: piper legge la frase, OpenVoice ne sostituisce il timbro con quello
della registrazione di riferimento. L'impronta della voce di base (piper) si calcola una volta da tre
frasi di prova e resta in cache, come quella del riferimento. Il marchio d'acqua inaudibile di
OpenVoice (wavmark) è attivo salvo `--no-watermark`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import wave
from pathlib import Path
from typing import IO, Optional

# Lanciato come script, la sua cartella (mario/assistant) finirebbe in testa al percorso dei moduli e il
# client `openvoice.py` lì accanto nasconderebbe il pacchetto `openvoice` vero: la si toglie.
_HERE = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p and str(Path(p).resolve()) != _HERE]

CALIBRATION = [
    "Ciao! Sono pronto a giocare con te, andiamo all'avventura.",
    "Oggi il cielo è azzurro e le nuvole sembrano zucchero filato.",
    "Quante monete hai raccolto? Contiamole insieme, una alla volta.",
]
WATERMARK_MESSAGE = "@MyShell"


def file_key(path: Path, extra: str = "") -> str:
    """Impronta di un file (contenuto) per le cache delle impronte vocali."""
    h = hashlib.sha1(extra.encode())
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class VoiceEngine:
    """piper + OpenVoice veri (servono torch, openvoice e piper-tts)."""

    def __init__(self, piper_model: str, converter_dir: Optional[str], reference: Optional[str],
                 cache_dir: str, watermark: bool = True, threads: int = 0, device: str = "cpu"):
        self.piper_model = Path(piper_model)
        self.converter_dir = Path(converter_dir) if converter_dir else None
        self.reference = Path(reference) if reference else None
        self.cache = Path(cache_dir)
        self.watermark = watermark
        self.threads = threads
        self.device = device
        self.voice = None
        self.tcc = None
        self.src_se = None
        self.tgt_se = None

    def load(self) -> dict:
        from piper import PiperVoice
        self.voice = PiperVoice.load(str(self.piper_model))
        if self.converter_dir and self.reference:
            import torch
            if self.threads:
                torch.set_num_threads(self.threads)
            self.tcc = make_converter(str(self.converter_dir / "config.json"), self.device, self.watermark)
            self.tcc.load_ckpt(str(self.converter_dir / "checkpoint.pth"))
            self.src_se = self._source_embedding()
            self.set_reference(str(self.reference))
        self._warm_up()
        return {"openvoice": self.tcc is not None, "watermark": bool(self.tcc and self.watermark)}

    def _warm_up(self) -> None:
        """Una frase a vuoto prima di dirsi pronto: la prima sintesi (e la prima conversione) costa di più."""
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / "riscaldamento.wav"
        try:
            self.speak("Eccomi, sono pronto.", str(path))
        finally:
            path.unlink(missing_ok=True)

    # ----- impronte vocali (in cache su disco) -------------------------------------------

    def _embedding(self, wavs: list[str], cache_file: Path):
        import torch
        if cache_file.exists():
            return torch.load(str(cache_file), map_location=self.device)
        self.cache.mkdir(parents=True, exist_ok=True)
        return self.tcc.extract_se(wavs, se_save_path=str(cache_file))

    def _source_embedding(self):
        cache_file = self.cache / f"source-{file_key(self.piper_model, 'piper')}.pth"
        if cache_file.exists():
            return self._embedding([], cache_file)
        self.cache.mkdir(parents=True, exist_ok=True)
        wavs = []
        for i, text in enumerate(CALIBRATION):
            path = self.cache / f"calibrazione-{i}.wav"
            self._piper(text, path, 1.0)
            wavs.append(str(path))
        se = self._embedding(wavs, cache_file)
        for w in wavs:
            Path(w).unlink(missing_ok=True)
        return se

    def set_reference(self, path: str) -> None:
        ref = Path(path)
        if not ref.is_file():
            raise FileNotFoundError(f"registrazione di riferimento non trovata: {ref}")
        if self.tcc is None:
            raise RuntimeError("OpenVoice non caricato (avviato senza --converter)")
        self.tgt_se = self._embedding([str(ref)], self.cache / f"target-{file_key(ref, 'ref')}.pth")
        self.reference = ref

    # ----- sintesi ----------------------------------------------------------------------

    def _piper(self, text: str, path: Path, length_scale: float) -> None:
        with wave.open(str(path), "wb") as wf:
            if hasattr(self.voice, "synthesize_wav"):  # piper-tts >= 1.3
                from piper import SynthesisConfig
                self.voice.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=length_scale))
            else:  # piper-tts 1.2
                self.voice.synthesize(text, wf, length_scale=length_scale)

    def speak(self, text: str, out: str, length_scale: float = 1.0, tau: float = 0.3, gain: float = 1.0) -> dict:
        out_path = Path(out)
        t0 = time.monotonic()
        base = out_path.with_name(out_path.stem + "-base.wav") if self.tcc else out_path
        self._piper(text, base, length_scale)
        t1 = time.monotonic()
        if self.tcc is not None:
            self.tcc.convert(audio_src_path=str(base), src_se=self.src_se, tgt_se=self.tgt_se,
                             output_path=str(out_path), tau=tau, message=WATERMARK_MESSAGE)
            base.unlink(missing_ok=True)
        t2 = time.monotonic()
        if abs(gain - 1.0) > 0.01:
            apply_gain(out_path, gain)
        return {"base_s": round(t1 - t0, 3), "convert_s": round(t2 - t1, 3), "audio_s": round(wav_seconds(out_path), 3)}


def make_converter(config: str, device: str, watermark: bool):
    """ToneColorConverter di OpenVoice. Il suo costruttore passa `enable_watermark` anche alla classe base,
    che lo rifiuta (TypeError): qui si replicano le sue tre righe senza quel difetto."""
    from openvoice.api import OpenVoiceBaseClass, ToneColorConverter

    class Converter(ToneColorConverter):
        def __init__(self) -> None:
            OpenVoiceBaseClass.__init__(self, config, device=device)
            if watermark:
                import wavmark
                self.watermark_model = wavmark.load_model().to(self.device)
            else:
                self.watermark_model = None
            self.version = getattr(self.hps, "_version_", "v1")

    return Converter()


def wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (OSError, wave.Error, EOFError):
        return 0.0


def apply_gain(path: Path, gain: float) -> None:
    """Volume del file WAV a 16 bit (gain 0..2), con taglio dei picchi; solo libreria standard."""
    import array
    with wave.open(str(path), "rb") as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    if params.sampwidth != 2:
        return
    samples = array.array("h", frames)
    if sys.byteorder == "big":
        samples.byteswap()
    for i, s in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(s * gain)))
    if sys.byteorder == "big":
        samples.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())


# ----- protocollo --------------------------------------------------------------------------

def _send(out: IO[str], obj: dict) -> None:
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def serve(engine, inp: IO[str], out: IO[str]) -> int:
    """Carica il motore, annuncia "ready" e risponde alle richieste fino a "quit" o alla fine dell'input."""
    t0 = time.monotonic()
    try:
        info = engine.load() or {}
    except Exception as exc:  # modelli mancanti, librerie rotte: il chiamante passa alla voce di riserva
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
            elif cmd == "speak":
                res = engine.speak(str(req["text"]), str(req["out"]), float(req.get("length_scale", 1.0)),
                                   float(req.get("tau", 0.3)), float(req.get("gain", 1.0)))
                _send(out, {"id": rid, "ok": True, **res})
            elif cmd == "reference":
                engine.set_reference(str(req["path"]))
                _send(out, {"id": rid, "ok": True})
            else:
                _send(out, {"id": rid, "ok": False, "error": f"comando sconosciuto: {cmd}"})
        except Exception as exc:
            _send(out, {"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="voce IA dell'assistente: piper + OpenVoice in un processo separato")
    ap.add_argument("--piper-model", required=True, help="voce piper italiana (.onnx, con il .onnx.json accanto)")
    ap.add_argument("--converter", help="cartella checkpoints_v2/converter di OpenVoice (senza: solo piper)")
    ap.add_argument("--reference", help="registrazione WAV della voce da imitare")
    ap.add_argument("--cache", required=True, help="cartella delle impronte vocali calcolate")
    ap.add_argument("--no-watermark", action="store_true", help="niente marchio d'acqua inaudibile di OpenVoice")
    ap.add_argument("--threads", type=int, default=0, help="thread di torch (0 = automatico)")
    args = ap.parse_args(argv)
    proto = sys.stdout
    sys.stdout = sys.stderr  # le stampe di OpenVoice e torch non devono sporcare il protocollo
    engine = VoiceEngine(args.piper_model, args.converter, args.reference, args.cache,
                         watermark=not args.no_watermark, threads=args.threads)
    return serve(engine, sys.stdin, proto)


if __name__ == "__main__":
    raise SystemExit(main())
