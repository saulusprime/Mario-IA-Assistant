#!/bin/sh
# Cattura il traffico HCI con btmon (serve sudo) mentre si tenta la connessione a Mario,
# poi estrae gli eventi di connessione/disconnessione con il motivo riportato dal controller.
# Uso: ./tools/capture_btmon.sh        (dal terminale di VS Code o da un terminale normale)
DIR=$(cd "$(dirname "$0")/.." && pwd)
if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
    exec flatpak-spawn --host "$DIR/tools/capture_btmon.sh" "$@"
fi
mkdir -p "$DIR/logs"
STAMP=$(date +%Y%m%d-%H%M%S)
SNOOP="$DIR/logs/mario-$STAMP.btsnoop"
TXT="$DIR/logs/mario-$STAMP.btmon.txt"

echo "Serve la password di sudo per btmon."
sudo -v || exit 1
sudo btmon -w "$SNOOP" >/dev/null 2>&1 &
BTMON=$!
sleep 1
echo
echo ">>> Premi ORA il tasto Bluetooth di Mario: tento la connessione (60 s)..."
"$DIR/run.sh" -t 60 info
sleep 2
sudo kill "$BTMON" 2>/dev/null
wait "$BTMON" 2>/dev/null
sudo chown "$(id -u):$(id -g)" "$SNOOP" 2>/dev/null
btmon -r "$SNOOP" > "$TXT" 2>/dev/null || sudo btmon -r "$SNOOP" > "$TXT"
sudo chown "$(id -u):$(id -g)" "$TXT" 2>/dev/null

echo
echo "=== Eventi rilevanti (log completo: $TXT) ==="
grep -nE "Connection Complete|Disconnect Complete|Reason:|LE Read Remote|Connection Update|Data Length|Exchange MTU|Read By Group Type|Read By Type|Find Information|Error Response|Command Status|Hardware Error|Write Request|Write Response|Write Command|Handle Value|Client Characteristic Configuration|Acquire|StartNotify|Notifying" "$TXT" \
  | grep -vE "Command Status.*Status: Success" | tail -70
echo
echo "=== Ultime 25 righe del log ==="
tail -25 "$TXT"
