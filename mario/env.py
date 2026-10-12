"""
Caricamento del file `.env` nella radice del progetto (solo libreria standard).

Formato accettato: righe `CHIAVE=valore`, anche `export CHIAVE=valore`, valori tra apici
singoli o doppi, righe vuote e commenti con `#`. Le variabili già presenti nell'ambiente
non vengono sovrascritte, così `ANTHROPIC_API_KEY=... ./run.sh ...` vince sul file.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_DIR / ".env"

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()  # commento in coda, solo fuori dagli apici
        values[key] = value
    return values


def load_dotenv(path: Optional[str | Path] = None, override: bool = False) -> list[str]:
    """Carica il file nell'ambiente del processo; restituisce i nomi delle variabili impostate."""
    file = Path(path) if path else DEFAULT_ENV_FILE
    if not file.is_file():
        return []
    loaded = []
    for key, value in parse_dotenv(file.read_text(encoding="utf-8")).items():
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
