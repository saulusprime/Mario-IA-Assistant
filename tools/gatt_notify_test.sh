#!/bin/sh
# Test con il solo bluetoothctl (senza bleak): connette Mario, attiva le notifiche sulla
# caratteristica LEGO 1624, invia la richiesta del nome (05 00 01 01 05) e mostra cosa arriva.
# Uso: ./tools/gatt_notify_test.sh   (premi il tasto Bluetooth di Mario quando richiesto)
DIR=$(cd "$(dirname "$0")/.." && pwd)
if [ -f /.flatpak-info ] && command -v flatpak-spawn >/dev/null 2>&1; then
    exec flatpak-spawn --host "$DIR/tools/gatt_notify_test.sh" "$@"
fi
strip() { sed "s/\x1b\[[0-9;]*m//g"; }

echo ">>> Premi ORA il tasto Bluetooth di Mario. Cerco per 40 s..."
bluetoothctl --timeout 40 scan le >/dev/null 2>&1 &
SCAN=$!
ADDR=""
for i in $(seq 1 40); do
    ADDR=$(bluetoothctl devices 2>/dev/null | strip | grep -iE "mario|luigi|peach" | head -1 | awk '{print $2}')
    [ -n "$ADDR" ] && break
    sleep 1
done
kill $SCAN 2>/dev/null; wait $SCAN 2>/dev/null
[ -z "$ADDR" ] && { echo "Mario non trovato"; exit 1; }
echo "Mario: $ADDR"

echo "=== connect ==="
timeout 40 bluetoothctl connect "$ADDR" 2>&1 | strip | grep -E "Connection|Connected|Failed|ServicesResolved" | tail -5
sleep 1
echo "=== attributi GATT ==="
ATTRS=$(bluetoothctl gatt.list-attributes "$ADDR" 2>&1 | strip)
echo "$ATTRS" | grep -B1 -iE "00001624|00001623|f000ffc" | grep -vE "^--$"
CHAR=$(echo "$ATTRS" | grep -B1 -i "00001624-1212-efde-1623-785feabcd123" | grep -oE "/org/bluez/[^ ]*char[0-9a-f]+" | head -1)
echo "caratteristica LEGO: ${CHAR:-NON TROVATA}"
[ -z "$CHAR" ] && { bluetoothctl disconnect "$ADDR" >/dev/null 2>&1; exit 1; }

echo "=== notify on + richiesta nome (05 00 01 01 05), 12 s di ascolto ==="
(
  echo "menu gatt"
  echo "select-attribute $CHAR"
  sleep 1
  echo "notify on"
  sleep 4
  echo "write \"0x05 0x00 0x01 0x01 0x05\""
  sleep 4
  echo "write \"0x05 0x00 0x01 0x06 0x05\""
  sleep 3
  echo "notify off"
  echo "back"
  echo "disconnect $ADDR"
  sleep 1
  echo "quit"
) | timeout 30 bluetoothctl 2>&1 | strip | grep -vE "^\s*$|Agent registered|^\[bluetooth\]|^\[Mario" | tail -40
