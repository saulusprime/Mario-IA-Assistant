#!/usr/bin/env python3
"""
Sessione guidata con le basette reali: verifica dei codici barcode e catalogo delle reazioni.

Collega Mario, attiva scanner (porta 1), eventi di gioco (porta 3) e pantaloni (porta 2). Ogni
volta che lo scanner legge una basetta nuova il PC ne dice il nome a voce (o "sconosciuta" con
il codice), poi registra per alcuni secondi gli eventi della porta 3 che seguono: chi guarda
annota cosa fa Mario (frase, suono, animazione). Alla fine stampa una tabella per basetta e
segna le basette viste come possedute nel database della dashboard (data/mario.sqlite3).

Consiglio: iniziare dalla basetta Start (Mario entra "in livello" e reagisce alle altre come
nel gioco), poi una basetta alla volta per 8-10 secondi, e chiudere con il Traguardo se c'è.

Uso (sull'host):  .venv/bin/python tools/catalog_tiles.py [--duration 360] [--no-voice] [--no-db]
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mario import codes, lwp3  # noqa: E402
from mario.device import MarioDevice  # noqa: E402
from mario.lwp3 import MessageType, le16  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
AFTER_WINDOW = 6.0  # secondi di eventi attribuiti alla basetta appena letta


def speak(text: str, enabled: bool) -> None:
    if enabled and shutil.which("spd-say"):
        subprocess.Popen(["spd-say", "-l", "it", "-r", "10", "--", text], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def short(name: str) -> str:
    return name.split(" (")[0]


class Session:
    def __init__(self, voice: bool, log_path: Path):
        self.voice = voice
        self.fh = open(log_path, "a", encoding="utf-8")
        self.tiles: dict[int, dict] = {}       # codice -> {name, count, first, events: [..], colors: set}
        self.current: int | None = None
        self.current_since = 0.0
        self.last_color: int | None = None
        self.pants: int | None = None
        self.order: list[int] = []

    def log(self, text: str, raw: bytes = b"") -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {text}" + (f"   [{raw.hex(' ')}]" if raw else "")
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def on_message(self, msg: lwp3.Message, text: str) -> None:
        p = msg.payload
        if msg.msg_type != MessageType.PORT_VALUE_SINGLE or not p:
            if msg.msg_type in (MessageType.GENERIC_ERROR, MessageType.HUB_ATTACHED_IO):
                self.log("<- " + text, msg.raw)
            return
        port, data = p[0], bytes(p[1:])
        now = time.time()
        if port == 1 and len(data) >= 4:
            barcode, color = le16(data[0:2]), le16(data[2:4])
            if color != 0xFFFF and color != self.last_color:
                self.last_color = color
                self.log(f"colore: 0x{color:04X} = {codes.COLORS.get(color, 'sconosciuto')}", msg.raw)
                if self.current is not None and now - self.current_since < AFTER_WINDOW:
                    self.tiles[self.current]["colors"].add(color)
            if barcode == 0xFFFF:
                if self.current is not None:
                    self.log("scanner sollevato", msg.raw)
                self.current = None
                return
            if barcode != self.current:
                self._new_tile(barcode, now, msg.raw)
        elif port == 3 and len(data) >= 4:
            code, value = le16(data[0:2]), le16(data[2:4])
            name = codes.GAME_EVENTS.get(code, "")
            self.log(f"evento porta 3: 0x{code:04X} = {value}" + (f"  ({name})" if name else ""), msg.raw)
            if self.current is not None and now - self.current_since < AFTER_WINDOW:
                self.tiles[self.current]["events"].append((round(now - self.current_since, 1), code, value))
        elif port == 2 and data:
            self.pants = data[0] & 0x3F
            self.log(f"pantaloni: {codes.PANTS.get(self.pants, f'sconosciuti 0x{self.pants:02X}')}", msg.raw)
        elif port == 0 and len(data) >= 2:
            g = le16(data)
            if g:
                self.log(f"gesto: {', '.join(codes.gesture_names(g))}", msg.raw)

    def _new_tile(self, barcode: int, now: float, raw: bytes) -> None:
        self.current, self.current_since = barcode, now
        name = codes.BARCODES.get(barcode)
        entry = self.tiles.setdefault(barcode, {"name": name, "count": 0, "first": now, "events": [], "colors": set()})
        entry["count"] += 1
        if barcode not in self.order:
            self.order.append(barcode)
        n = self.order.index(barcode) + 1  # numero stabile anche nelle riletture
        if name:
            self.log(f"=== BASETTA {n}: 0x{barcode:04X} = {name}", raw)
            speak(f"Basetta {n}: {short(name)}.", self.voice)
        else:
            self.log(f"=== BASETTA {n}: 0x{barcode:04X} = SCONOSCIUTA (non in tabella)", raw)
            speak(f"Basetta {n}: sconosciuta, codice {barcode}.", self.voice)

    def summary(self) -> str:
        lines = ["", "=== RIEPILOGO BASETTE ==="]
        for i, code in enumerate(self.order, 1):
            t = self.tiles[code]
            name = t["name"] or "SCONOSCIUTA: da aggiungere a codes.BARCODES"
            lines.append(f"{i}. 0x{code:04X}  {name}  (letta {t['count']} volte)")
            if t["events"]:
                ev = ", ".join(f"+{dt}s 0x{c:04X}={v}" for dt, c, v in t["events"][:12])
                lines.append(f"     eventi porta 3: {ev}")
            else:
                lines.append("     eventi porta 3: nessuno nei 6 s successivi")
            if t["colors"]:
                lines.append("     colori: " + ", ".join(codes.COLORS.get(c, f"0x{c:04X}") for c in sorted(t["colors"])))
            lines.append("     Mario ha fatto: ______________________")
        return "\n".join(lines)

    def save_to_db(self) -> str:
        from mario.web.db import Database
        db = Database(PROJECT / "data" / "mario.sqlite3")
        db.seed_accessories(codes.PANTS, codes.BARCODES, codes.COLORS)
        updated = 0
        for code, t in self.tiles.items():
            db.mark_seen("tile", code)
            rows = [a for a in db.list_accessories("tile") if a["code"] == code]
            if rows:
                db.update_accessory(rows[0]["id"], owned=True, quantity=max(1, rows[0].get("quantity") or 0),
                                    notes=(rows[0].get("notes") or "") or f"verificata dal vivo il {time.strftime('%d/%m/%Y')}")
                updated += 1
        return f"database: {updated} basette segnate come possedute e viste"


async def main() -> int:
    ap = argparse.ArgumentParser(description="catalogo guidato delle basette reali")
    ap.add_argument("--duration", type=float, default=360.0, help="secondi di sessione (default 360)")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--no-db", action="store_true", help="non aggiornare il database della dashboard")
    ap.add_argument("-t", "--timeout", type=float, default=180.0)
    ap.add_argument("-a", "--address")
    args = ap.parse_args()
    voice = not args.no_voice

    logs = PROJECT / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / f"tiles-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    dev = MarioDevice(args.address)
    print(f"Accendi Mario e premi il tasto Bluetooth: cerco per {int(args.timeout)} s...", file=sys.stderr, flush=True)
    await dev.connect(args.timeout, progress=lambda t: print(f"[{time.strftime('%H:%M:%S')}] {t}", file=sys.stderr, flush=True))
    session = Session(voice, log_path)
    session.log(f"connesso a {dev.ble_device.name if dev.ble_device else dev.address}; log {log_path}")
    try:
        await dev.discover(full=False)
        for port, mode in ((1, 0), (3, 2), (2, 0), (0, 1)):
            try:
                await dev.subscribe(port, mode, 1)
            except Exception as exc:
                session.log(f"porta {port} modo {mode}: notifiche non attivate ({exc})")
        dev.add_listener(session.on_message)
        session.log(f"pronto: appoggia Mario su una basetta alla volta ({int(args.duration)} s, Ctrl+C per finire prima)")
        speak("Pronto. Appoggia Mario sulla basetta di partenza, poi sulle altre, una alla volta.", voice)
        start = time.time()
        while time.time() - start < args.duration and dev.connected:
            await asyncio.sleep(0.5)
            remaining = args.duration - (time.time() - start)
            if 29.5 < remaining <= 30.0:
                speak("Trenta secondi alla fine.", voice)
        speak("Fine della sessione. Grazie.", voice)
    except KeyboardInterrupt:
        pass
    finally:
        print(session.summary())
        session.fh.write(session.summary() + "\n")
        if not args.no_db and session.tiles:
            try:
                session.log(session.save_to_db())
            except Exception as exc:
                session.log(f"database non aggiornato: {exc}")
        session.fh.close()
        await dev.disconnect()
        print(f"\nLog: {log_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
