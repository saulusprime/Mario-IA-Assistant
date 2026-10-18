#!/bin/sh
# Installa l'ingresso vocale dell'assistente: riconoscimento della voce offline con faster-whisper
# (Whisper di OpenAI su CTranslate2, licenza MIT; modelli Systran, licenza MIT), in un ambiente
# Python separato. Tutto gira in locale, sulla CPU: l'audio del microfono non esce dal PC.
#
# Per l'attivazione con la voce ("Ehi Mario") installa anche Vosk (Apache 2.0) con il modello italiano
# piccolo di Alpha Cephei (Apache 2.0, circa 50 MB scaricati, 88 MB estratti): ascolta il microfono di
# continuo con poca CPU e annuncia le chiamate, che poi Whisper controlla.
#
# Cosa crea (tutto ignorato da git):
#   .venv-stt/                       Python 3.11 con faster-whisper (ctranslate2, onnxruntime per il VAD) e vosk
#   third_party/whisper-models/      modelli base (circa 140 MB) e small (circa 480 MB)
#   third_party/vosk-models/         modello vosk-model-small-it-0.22
# Si toglie tutto cancellando le tre cartelle.
#
# Uso:  ./tools/setup_stt.sh               (dal sandbox di VS Code salta da solo sull'host)
#       ./tools/setup_stt.sh medium        (anche un altro modello: tiny, base, small, medium)
set -eu
DIR=$(cd "$(dirname "$0")/.." && pwd)
if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
    exec flatpak-spawn --host "$DIR/tools/setup_stt.sh" "$@"
fi

VENV="$DIR/.venv-stt"
MODELS="$DIR/third_party/whisper-models"
PY="$VENV/bin/python"

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

UV=$(command -v uv 2>/dev/null || true)
for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.var/app/com.visualstudio.code/bin/uv"; do
    [ -z "$UV" ] && [ -x "$c" ] && UV="$c"
done

# 1. ambiente Python separato (ctranslate2 e onnxruntime pesano circa 300 MB: fuori dal venv della dashboard)
if [ ! -x "$PY" ]; then
    if [ -n "$UV" ]; then
        say "Creo $VENV con Python 3.11 (uv scarica l'interprete se manca)"
        "$UV" venv --python 3.11 "$VENV"
    else
        say "uv non trovato: creo $VENV con $(python3 --version)"
        python3 -m venv "$VENV"
        "$PY" -m pip install -q --upgrade pip
    fi
fi
if ! "$PY" -c "import faster_whisper" 2>/dev/null; then
    say "Installo faster-whisper"
    if [ -n "$UV" ]; then "$UV" pip install --python "$PY" faster-whisper; else "$PY" -m pip install -q faster-whisper; fi
fi

# 2. modelli multilingue (italiano compreso) da Hugging Face, convertiti per CTranslate2
mkdir -p "$MODELS"
for name in base small "$@"; do
    if [ -s "$MODELS/$name/model.bin" ]; then
        continue
    fi
    say "Scarico il modello Whisper $name"
    "$PY" -I -c "import sys; from faster_whisper import download_model; download_model(sys.argv[1], output_dir=sys.argv[2])" \
        "$name" "$MODELS/$name"
done

# 3. "Ehi Mario": Vosk e il modello italiano piccolo (zip controllato con SHA-256)
if ! "$PY" -c "import vosk" 2>/dev/null; then
    say "Installo vosk (parola di attivazione)"
    if [ -n "$UV" ]; then "$UV" pip install --python "$PY" vosk; else "$PY" -m pip install -q vosk; fi
fi
VOSK_DIR="$DIR/third_party/vosk-models"
VOSK_NAME="vosk-model-small-it-0.22"
VOSK_SHA256="9ec65e75861d1c6c2e457cccd932705340dcdf233f5b239f00733b4de0bf3267"
if [ ! -s "$VOSK_DIR/$VOSK_NAME/am/final.mdl" ]; then
    mkdir -p "$VOSK_DIR"
    say "Scarico il modello Vosk $VOSK_NAME"
    curl -sSfL -o "$VOSK_DIR/$VOSK_NAME.zip" "https://alphacephei.com/vosk/models/$VOSK_NAME.zip"
    echo "$VOSK_SHA256  $VOSK_DIR/$VOSK_NAME.zip" | sha256sum -c --quiet
    "$PY" -I -c "
import pathlib, sys, zipfile
dest = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(sys.argv[1]) as z:
    for name in z.namelist():  # niente percorsi fuori dalla cartella
        if not (dest / name).resolve().is_relative_to(dest):
            sys.exit(f'percorso non valido nello zip: {name}')
    z.extractall(dest)
" "$VOSK_DIR/$VOSK_NAME.zip" "$VOSK_DIR"
    rm -f "$VOSK_DIR/$VOSK_NAME.zip"
fi

say "Fatto. Prova: ./run.sh listen --file registrazione.wav   oppure   ./run.sh listen (5 s dal microfono)"
say "«Ehi Mario»: ./run.sh listen --wake (ascolta il microfono finché non premi Ctrl+C)"
