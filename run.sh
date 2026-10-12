#!/bin/sh
# Avvia l'applicazione LEGO Mario.
# Se siamo dentro un sandbox Flatpak (es. il terminale di VS Code) BlueZ non e'
# raggiungibile: in quel caso rilanciamo lo stesso script sull'host.
DIR=$(cd "$(dirname "$0")" && pwd)

if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
    exec flatpak-spawn --host "$DIR/run.sh" "$@"
fi

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Creo il virtualenv in $DIR/.venv e installo le dipendenze..." >&2
    python3 -m venv "$DIR/.venv" && "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt" || exit 1
fi

cd "$DIR" && exec "$DIR/.venv/bin/python" -m mario "$@"
