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

import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..paths import PROJECT_DIR
from .worker import WorkerError, WorkerProcess

VENV_DIR = PROJECT_DIR / ".venv-openvoice"
DEFAULT_PYTHON = VENV_DIR / "bin" / "python"
DEFAULT_CONVERTER = PROJECT_DIR / "third_party" / "OpenVoice" / "checkpoints_v2" / "converter"
VOICES_DIR = PROJECT_DIR / "third_party" / "piper-voices"
DEFAULT_VOICE = "it_IT-paola-medium.onnx"
CACHE_DIR = PROJECT_DIR / "data" / "openvoice-cache"
WORKER = Path(__file__).with_name("openvoice_worker.py")
LOG_FILE = Path(tempfile.gettempdir()) / "mario-assistant-sounds" / "openvoice-worker.log"


class OpenVoiceError(WorkerError):
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


def mario_base_voice() -> str:
    """Voce maschile di piper usata come base dello stile Mario (più acuta e veloce con l'effetto), o ''."""
    male = VOICES_DIR / "it_IT-riccardo-x_low.onnx"
    return str(male) if male.is_file() else ""


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


class OpenVoiceClient(WorkerProcess):
    """Processo della voce IA: avvio in background, richieste con tempo massimo, stato (vedi `worker.py`)."""

    label = "OpenVoice"
    error_class = OpenVoiceError
    log_file = LOG_FILE

    def __init__(self, reference: str, piper_model: str = "", python: Optional[Path] = None,
                 converter: Optional[Path] = None, cache_dir: Optional[Path] = None, watermark: bool = True,
                 threads: int = 0, command: Optional[list[str]] = None):
        super().__init__(command)
        self.reference = reference
        self.piper_model = piper_model or default_piper_model()
        self.python = Path(python or DEFAULT_PYTHON)
        self.converter = Path(converter or DEFAULT_CONVERTER)
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.watermark = watermark
        self.threads = threads
        self.conversions = 0
        self.total_s = 0.0

    def _argv(self) -> list[str]:
        # -I: la cartella dello script non entra nel percorso dei moduli (qui openvoice.py nasconderebbe il pacchetto)
        argv = [str(self.python), "-I", str(WORKER), "--piper-model", self.piper_model, "--cache", str(self.cache_dir)]
        if self.reference:  # senza riferimento: solo piper
            argv += ["--converter", str(self.converter), "--reference", self.reference]
        if not self.watermark:
            argv.append("--no-watermark")
        if self.threads:
            argv += ["--threads", str(self.threads)]
        return argv

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
