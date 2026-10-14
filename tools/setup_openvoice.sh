#!/bin/sh
# Installa la "voce IA" dell'assistente: OpenVoice V2 (MyShell, licenza MIT) come convertitore di timbro
# e piper (voce neurale italiana) come voce di base, in un ambiente Python separato.
#
# OpenVoice V2 non parla italiano da solo (inglese, spagnolo, francese, cinese, giapponese, coreano):
# qui piper legge la frase in italiano e OpenVoice ne cambia il timbro con quello di una registrazione
# di riferimento (pochi secondi di voce, file WAV). Tutto gira in locale, sulla CPU.
#
# Cosa crea (tutto ignorato da git):
#   .venv-openvoice/                      Python 3.11 con torch (CPU), OpenVoice, piper-tts
#   third_party/OpenVoice/                clone del repository e checkpoint V2 (checkpoints_v2/converter)
#   third_party/piper-voices/             voci italiane di piper (paola medium, riccardo x_low)
# Spazio: circa 1,5 GB. Si toglie tutto cancellando le tre cartelle.
#
# Uso:  ./tools/setup_openvoice.sh        (dal sandbox di VS Code salta da solo sull'host)
set -eu
DIR=$(cd "$(dirname "$0")/.." && pwd)
if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
    exec flatpak-spawn --host "$DIR/tools/setup_openvoice.sh" "$@"
fi

VENV="$DIR/.venv-openvoice"
OV="$DIR/third_party/OpenVoice"
VOICES="$DIR/third_party/piper-voices"
PY="$VENV/bin/python"
# checkpoint del convertitore V2 dal repository ufficiale su Hugging Face (lo zip su S3 citato nella
# documentazione di OpenVoice risponde 404 dal 2026); revisione fissata per avere sempre gli stessi file
CKPT_HF="https://huggingface.co/myshell-ai/OpenVoiceV2/resolve/f36e7edfe1684461a8343844af60babc2efbb727"
PIPER_HF="https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT"

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

UV=$(command -v uv 2>/dev/null || true)
for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.var/app/com.visualstudio.code/bin/uv"; do
    [ -z "$UV" ] && [ -x "$c" ] && UV="$c"
done

# 1. ambiente Python separato (OpenVoice fissa versioni vecchie delle librerie)
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
pip_install() {
    if [ -n "$UV" ]; then "$UV" pip install --python "$PY" "$@"; else "$PY" -m pip install -q "$@"; fi
}

# 2. torch per la sola CPU (niente CUDA: risparmia più di un gigabyte)
if ! "$PY" -c "import torch" 2>/dev/null; then
    say "Installo torch (CPU)"
    pip_install --index-url https://download.pytorch.org/whl/cpu torch
fi

# 3. OpenVoice senza le sue dipendenze fissate (gradio, faster-whisper, ... non servono): solo quelle
#    che il convertitore usa davvero, più piper per la voce italiana di base
if [ ! -d "$OV/.git" ]; then
    say "Clono OpenVoice in $OV"
    mkdir -p "$DIR/third_party"
    git clone --depth 1 https://github.com/myshell-ai/OpenVoice.git "$OV"
fi
say "Installo le dipendenze del convertitore e piper"
pip_install "numpy<2" librosa soundfile scipy eng_to_ipa inflect unidecode pypinyin jieba cn2an wavmark piper-tts
pip_install --no-deps -e "$OV"

# 4. checkpoint V2 (serve solo checkpoints_v2/converter, circa 130 MB)
mkdir -p "$OV/checkpoints_v2/converter"
for f in config.json checkpoint.pth; do
    if [ ! -s "$OV/checkpoints_v2/converter/$f" ]; then
        say "Scarico il convertitore di OpenVoice V2: $f"
        curl -L --fail --progress-bar -o "$OV/checkpoints_v2/converter/$f.part" "$CKPT_HF/converter/$f"
        mv "$OV/checkpoints_v2/converter/$f.part" "$OV/checkpoints_v2/converter/$f"
    fi
done

# 5. voci italiane di piper: paola (femminile, qualità media) e riccardo (maschile, leggera)
mkdir -p "$VOICES"
for v in paola/medium/it_IT-paola-medium riccardo/x_low/it_IT-riccardo-x_low; do
    name=$(basename "$v")
    for ext in onnx onnx.json; do
        if [ ! -s "$VOICES/$name.$ext" ]; then
            say "Scarico la voce piper $name.$ext"
            curl -L --fail --progress-bar -o "$VOICES/$name.$ext" "$PIPER_HF/$v.$ext"
        fi
    done
done

# 6. prova di importazione
say "Verifica"
"$PY" -c "import torch, openvoice.api, piper; print('torch', torch.__version__, '| OpenVoice e piper importati')"
ls "$OV/checkpoints_v2/converter"
say "Fatto. Prova:  ./run.sh voice \"Ciao, sono Mario!\" --openvoice-ref /percorso/voce.wav"
