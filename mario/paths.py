"""
Cartelle del programma. Dal sorgente (il clone con `run.sh`, anche installato con `pip install -e .`)
tutto sta nella cartella del progetto: `data/`, `logs/`, `docs/`, `third_party/`, `.venv-stt`, `.env`.
Installato come pacchetto normale, le stesse cartelle stanno in `~/.local/share/mario-ble`
(o `$XDG_DATA_HOME/mario-ble`), così database, log e modelli non finiscono dentro site-packages.
`MARIO_HOME` sceglie un'altra cartella in entrambi i casi.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_DATA = PACKAGE_DIR / "data"          # file distribuiti con il pacchetto (tabelle, esempi)
_SOURCE = PACKAGE_DIR.parent
IN_SOURCE_TREE = (_SOURCE / "pyproject.toml").is_file() and (_SOURCE / "run.sh").is_file()


def _home() -> Path:
    if os.environ.get("MARIO_HOME"):
        return Path(os.environ["MARIO_HOME"]).expanduser()
    if IN_SOURCE_TREE:
        return _SOURCE
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "mario-ble"


PROJECT_DIR = _home()
DATA_DIR = PROJECT_DIR / "data"
LOGS_DIR = PROJECT_DIR / "logs"
DOCS_DIR = PROJECT_DIR / "docs"
THIRD_PARTY = PROJECT_DIR / "third_party"
