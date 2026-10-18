"""
Interfaccia a riga di comando.

  scan      cerca i LEGO Mario nelle vicinanze
  info      si connette e scopre tutte le funzioni (proprietà, porte, modalità, formati)
  monitor   attiva i sensori e stampa in tempo reale gli eventi decodificati
  shell     console interattiva bidirezionale (comandi + messaggi in arrivo)
  assistant Mario come ingresso/uscita di una sessione con un modello di linguaggio (voce dal PC)
  voice     prova della voce dal PC (spd-say, piper o voce IA con OpenVoice) con i tempi di preparazione
  web       dashboard Flask
  decode    decodifica catture btsnoop, bugreport Android, PacketLogger iOS, log o byte esadecimali (senza Bluetooth)
  adventure avventure del designer: elenco, prova con eventi simulati, partita con il Mario fisico
  volume / name / prop / raw / off   comandi rapidi one-shot
  live-checks / catalog-tiles / probe-port4 / diag-connect / re-checks   strumenti dal vivo di tools/
  fetch-sounds   scarica suoni e voci di Mario (themushroomkingdom.net) per gli effetti dell'assistente
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from bleak.exc import BleakError

from . import codes, lwp3
from .device import LWPError, MarioDevice, describe_advertisement
from .lwp3 import (
    HubAction, HubAlertOperation, HubAlertType, HubProperty, MessageType, enum_name,
    parse_hub_property, parse_int,
)

# Messaggi "spontanei" che vale la pena mostrare in monitor / shell silenziosa
INTERESTING = {
    MessageType.PORT_VALUE_SINGLE, MessageType.PORT_VALUE_COMBINED, MessageType.HUB_PROPERTIES,
    MessageType.HUB_ATTACHED_IO, MessageType.HUB_ACTIONS, MessageType.HUB_ALERTS, MessageType.GENERIC_ERROR,
}


def ts() -> str:
    return time.strftime("%H:%M:%S")


class SessionLog:
    """Scrive su file (logs/<nome>-<data>.txt) ogni riga mostrata, con i byte grezzi: comodo da rileggere dopo."""

    LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

    def __init__(self, kind: str, path: Optional[str | Path] = None):
        self.path: Optional[Path] = None
        self._fh = None
        self.switch(path or self.LOGS_DIR / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.txt")

    def switch(self, path: Optional[str | Path]) -> Optional[Path]:
        """Chiude il file corrente e, se `path` non è None, ne apre un altro (relativo a logs/ se senza cartella)."""
        self.close()
        if path is None:
            return None
        target = Path(path)
        if not target.is_absolute() and target.parent == Path("."):
            target = self.LOGS_DIR / target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(target, "a", encoding="utf-8")
            self.path = target
        except OSError:
            self.path, self._fh = None, None
        return self.path

    def write(self, text: str, raw: bytes = b"") -> None:
        fh = self._fh
        if fh is None or fh.closed:
            return
        suffix = f"   [{raw.hex(' ')}]" if raw else ""
        try:
            fh.write(f"[{ts()}] {text}{suffix}\n")
            fh.flush()
        except ValueError:  # chiuso da un altro thread nel frattempo
            pass

    def close(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            fh.close()


def say(text: str) -> None:
    """Messaggi di avanzamento: su stderr, cosi' restano visibili anche con lo stdout rediretto."""
    print(f"[{ts()}] {text}", file=sys.stderr, flush=True)


async def connect_device(args) -> MarioDevice:
    dev = MarioDevice(args.address, any_lego=args.any_lego, debug=args.debug, adapter=args.adapter,
                      pair=not args.no_pair)
    say("Cerco Mario... (accendilo e premi il tasto Bluetooth sulla schiena)")
    await dev.connect(args.timeout, progress=say)
    bond = {True: "associato", False: "non associato", None: "stato associazione sconosciuto"}[dev.bonded]
    say(f"Connesso ({bond}). Porte annunciate: {sorted(dev.state.ports)}")
    return dev


# ---------------------------------------------------------------------------
# scan / info / monitor
# ---------------------------------------------------------------------------

async def cmd_scan(args) -> int:
    say(f"Scansione Bluetooth LE per {args.duration:g} s...")
    found = await MarioDevice.scan(args.duration, args.any_lego, args.adapter)
    if not found:
        print("Nessun LEGO Mario trovato. Accendilo e premi il tasto Bluetooth sulla schiena, "
              "poi riprova (l'annuncio dura circa 30 secondi).")
        return 1
    for device, adv in found:
        print("  " + describe_advertisement(device, adv))
    return 0


def save_capabilities(data: dict, docs_dir: Optional[Path] = None) -> Path:
    """Scrive la mappa delle funzioni in docs/capabilities-fw-<versione>.json (il nome Bluetooth,
    che è personale, non viene salvato: la cartella docs/ è pubblicata)."""
    docs_dir = docs_dir or Path(__file__).resolve().parent.parent / "docs"
    fw = str((data.get("properties") or {}).get("FW_VERSION", {}).get("value") or "sconosciuta")
    short = fw.rsplit(".", 1)[0] if fw.count(".") == 3 else fw
    clean = json.loads(json.dumps(data))
    if "ADVERTISING_NAME" in (clean.get("properties") or {}):
        clean["properties"]["ADVERTISING_NAME"] = {"id": "0x01", "raw": "", "value": "(nome omesso)"}
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / f"capabilities-fw-{short}.json"
    path.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


async def cmd_info(args) -> int:
    dev = await connect_device(args)
    try:
        await dev.discover(full=not args.quick, progress=say)
        data = dev.to_dict()
        if args.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print()
            print(dev.report())
        if not args.quick and not args.no_save:
            try:
                say(f"Mappa delle funzioni salvata in {save_capabilities(data)}")
            except OSError as exc:
                say(f"Mappa delle funzioni non salvata: {exc}")
    finally:
        await dev.disconnect()
    return 0


def parse_subscription(text: str) -> tuple[int, int, int]:
    """'1:0' oppure '1:0:5' -> (porta, modo, delta)."""
    parts = text.split(":")
    port = parse_int(parts[0])
    mode = parse_int(parts[1]) if len(parts) > 1 else 0
    delta = parse_int(parts[2]) if len(parts) > 2 else 1
    return port, mode, delta


async def _monitor_subscribe(dev: MarioDevice, args, session: SessionLog) -> None:
    """Attiva le notifiche richieste (o quelle di default); viene ripetuta dopo una riconnessione."""
    subs = [parse_subscription(x) for x in args.sub] if args.sub else []
    if not subs:
        for port, ps in sorted(dev.state.ports.items()):
            if args.no_battery and ps.io_type == codes.IO_VOLTAGE:
                continue
            names = {m: mi.name for m, mi in ps.modes.items()}
            if args.combined and ps.info and ps.info.capabilities & 0x04 and ps.combinations:
                modes = ps.combinations[0]
                entries, deltas = [], {}
                for mode in modes:
                    vf = ps.value_format(mode)
                    entries += [(mode, ds) for ds in range(vf.datasets if vf else 1)]
                    deltas[mode] = 4 if (ps.io_type == codes.IO_MARIO_ACCELEROMETER and mode == 0) else 1
                try:
                    await dev.set_combined_mode(port, 0, entries, deltas=deltas)
                    text = (f"Modalità combinata: porta {port} modi {modes} "
                            f"({', '.join(names.get(m, '?') for m in modes)}) insieme")
                except (LWPError, asyncio.TimeoutError, BleakError) as exc:
                    text = f"Porta {port}: modalità combinata non attivata ({exc})"
                say(text)
                session.write(text)
                continue
            prefer = ("RAW",) if args.raw_accel and ps.io_type == codes.IO_MARIO_ACCELEROMETER else ()
            mode = codes.default_mode(ps.io_type, names, prefer)
            if mode is not None:
                subs.append((port, mode, codes.DEFAULT_DELTAS.get(ps.io_type, 1)))
    for port, mode, delta in subs:
        ps = dev.state.ports.get(port)
        mode_name = ps.modes[mode].name if ps and mode in ps.modes else "?"
        try:
            await dev.subscribe(port, mode, delta)
            text = f"Notifiche attive: porta {port} modo {mode} ({mode_name}) delta {delta}"
        except (LWPError, asyncio.TimeoutError, BleakError) as exc:
            text = f"Porta {port} modo {mode} ({mode_name}): impossibile attivare ({exc})"
        say(text)
        session.write(text)
    props = [HubProperty.BUTTON] + ([] if args.no_battery else [HubProperty.BATTERY_VOLTAGE])
    if args.rssi:
        props.append(HubProperty.RSSI)
    for prop in props:
        try:
            await dev.property_updates(prop, True)
        except (LWPError, asyncio.TimeoutError, BleakError) as exc:
            say(f"Aggiornamenti di {prop.name} non attivati ({exc})")


async def cmd_monitor(args) -> int:
    dev = await connect_device(args)
    session = SessionLog("monitor")
    if session.path:
        say(f"Log della sessione: {session.path}")
    started = time.monotonic()

    def on_message(msg, text):
        if msg.msg_type not in INTERESTING:
            return
        if args.json:
            print(json.dumps(dev.event(msg, text), ensure_ascii=False, default=str), flush=True)
        else:
            print(f"[{ts()}] {text}", flush=True)
        session.write(text, msg.raw)

    dev.add_listener(on_message)
    first = True
    try:
        while True:
            if first or not all(ps.modes for ps in dev.state.ports.values()):
                await dev.discover(full=False, progress=say)
            first = False
            await _monitor_subscribe(dev, args, session)
            say("In ascolto: muovi Mario, cambia i pantaloni, appoggialo sui tile. Ctrl+C per uscire."
                + (f" Mi fermo dopo {args.duration:g} s." if args.duration else ""))
            remaining = None
            if args.duration:
                remaining = max(0.0, args.duration - (time.monotonic() - started))
            try:
                await asyncio.wait_for(dev.disconnected.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                say(f"Durata di {args.duration:g} s raggiunta: chiudo.")
                session.write(f"fine della sessione dopo {args.duration:g} s")
                break
            say("Mario si è disconnesso.")
            session.write("Mario si è disconnesso")
            if args.no_reconnect or (args.duration and time.monotonic() - started >= args.duration):
                break
            say("Aspetto che Mario torni visibile per riagganciarlo (premi il tasto Bluetooth; Ctrl+C per uscire)...")
            try:
                await dev.connect(args.timeout, progress=say)
            except BleakError as exc:
                say(f"Riconnessione non riuscita: {exc}")
                break
            session.write("riconnesso")
    finally:
        await dev.disconnect()
        session.close()
    return 0


# ---------------------------------------------------------------------------
# shell interattiva
# ---------------------------------------------------------------------------

SHELL_HELP = """\
Comandi disponibili (i messaggi in arrivo da Mario vengono mostrati con '<-'):
  help                          questo aiuto
  props                         legge tutte le proprietà dell'hub
  prop <nome|id>                legge una proprietà (es. prop name, prop battery, prop 0x12)
  watch <nome|id> [on|off]      aggiornamenti automatici di una proprietà (es. watch button)
  name [nuovo nome]             legge o imposta il nome Bluetooth
  volume [0-100]                legge o imposta il volume
  ports                         porte annunciate e loro stato
  modes <porta>                 scopre e mostra tutte le modalità di una porta
  sub <porta> <modo> [delta] [on|off]   attiva (o disattiva) le notifiche della porta
  unsub <porta> <modo>          disattiva le notifiche
  value <porta>                 chiede il valore corrente della porta
  combo <porta> <indice> <modo:dataset> ...   modalità combinata (porte combinabili)
  combo-reset <porta>           azzera la modalità combinata
  alert <tipo> [on|off|req]     allarmi hub: low_voltage, high_current, low_signal, over_power
  busy on|off                   indicatore "occupato" dell'hub
  action <id>                   Hub Action grezza (es. action 0x05)
  out <porta> <subcmd> <hex>    Port Output Command generico
  msg <tipo> <payload hex>      invia un messaggio LWP3 costruendo l'header
  raw <byte hex>                invia byte grezzi così come sono (header incluso)
  discover                      ripete la scoperta completa e stampa il report
  report                        stampa il report delle funzioni scoperte
  quiet [on|off]                mostra solo i messaggi spontanei (valori, proprietà, errori)
  debug [on|off]                mostra i byte grezzi TX/RX
  log [FILE|off]                mostra, cambia o sospende il file di log della sessione
  off                           spegne Mario
  disconnect                    chiede a Mario di disconnettersi
  quit                          esce dalla shell
Scrivi 'help <comando>' per i dettagli di un comando; frecce su/giù e Tab (cronologia e completamento) se c'è readline."""

SHELL_DETAILS = {
    "sub": "Imposta il modo della porta e accende le notifiche sui cambi di valore. Una porta ha un solo modo attivo "
           "alla volta (porta 3: 0 CHAL, 1 VERS, 2 EVENTS, 3 DEBUG). 'delta' è la variazione minima che genera una "
           "notifica (batteria: 25). Esempi: sub 1 0 | sub 3 2 | sub 6 0 25 | sub 0 1 1 off",
    "combo": "Modalità combinata (porte 0 e 1 sono 'combinabili'): più modi insieme sulla stessa porta. "
             "Esempio accelerometro + gesti: combo 0 0 0:0 0:1 0:2 1:0 1:1 (modo:dataset). Mario manda i valori nel "
             "messaggio 0x46 con la maschera dei dataset a byte alto per primo; la shell li ricompone per modo.",
    "out": "Port Output Command (0x81) grezzo: out <porta> <sottocomando> <hex>. Su Mario fw 6.5.01 la porta 4 accetta "
           "solo 0x51 (WriteDirectModeData: primo byte = modo) e la porta 7 ignora tutto. Esempio: out 4 0x51 00 13 01 b8 00",
    "msg": "Costruisce l'header LWP3 e invia: msg <tipo> <payload hex>. Esempio: msg 0x01 12 05 (legge il volume).",
    "raw": "Invia i byte così come sono, header compreso. Esempio: raw 05 00 01 06 05 (legge la batteria).",
    "alert": "Allarmi dell'hub: alert low_voltage req (richiesta), alert low_voltage on (aggiornamenti spontanei), off.",
    "watch": "Aggiornamenti automatici di una proprietà: watch button, watch battery, watch rssi off.",
    "action": "Hub Action grezza. Mai 0x2F (spegnimento di fabbrica). 0x05/0x06 (busy) su Mario rispondono INVALID_USE.",
    "log": "log -> mostra il file di log; log nome.txt -> scrive in logs/nome.txt; log /percorso/file -> file assoluto; "
           "log off -> sospende la scrittura.",
    "modes": "Interroga la porta su tutte le modalità (nome, formato, intervalli, unità) e stampa il report.",
    "value": "Chiede il valore corrente della porta (Port Information Request, tipo 0): utile per porte senza notifiche.",
}
SHELL_COMMANDS = sorted({line.split()[0] for line in SHELL_HELP.splitlines() if line.startswith("  ")}
                        | {"exit", "q", "combo-reset", "unsub"})


def shell_help(topic: str = "") -> str:
    topic = topic.strip().lower()
    if not topic:
        return SHELL_HELP
    lines = [line.strip() for line in SHELL_HELP.splitlines() if line.startswith(f"  {topic} ") or line.strip() == topic]
    if topic in SHELL_DETAILS:
        lines.append(SHELL_DETAILS[topic])
    return "\n".join(lines) if lines else f"Nessun comando '{topic}'. Scrivi 'help'."

ALERT_ALIASES = {
    "low_voltage": HubAlertType.LOW_VOLTAGE, "voltage": HubAlertType.LOW_VOLTAGE,
    "high_current": HubAlertType.HIGH_CURRENT, "current": HubAlertType.HIGH_CURRENT,
    "low_signal": HubAlertType.LOW_SIGNAL_STRENGTH, "signal": HubAlertType.LOW_SIGNAL_STRENGTH,
    "over_power": HubAlertType.OVER_POWER_CONDITION, "power": HubAlertType.OVER_POWER_CONDITION,
}


PROMPT = "mario> "
_readline_active = False  # vero quando il prompt lo disegna input() con readline (terminale interattivo)


def _history_file(name: str) -> Optional[Path]:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "mario"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{name}_history"
    except OSError:
        return None


def _stdin_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, prompt: str = PROMPT,
                  completions: Optional[list[str]] = None, history: str = "") -> None:
    """Legge le righe dall'utente in un thread. Su un terminale usa readline (cronologia, completamento)."""
    global _readline_active
    readline = None
    if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("MARIO_NO_READLINE") != "1":
        try:
            import readline as _rl
            readline = _rl
        except ImportError:
            readline = None
    hist = _history_file(history) if (readline and history) else None
    if readline:
        words = list(completions or [])

        def complete(text, state):
            matches = [w for w in words if w.startswith(text)]
            return matches[state] + " " if state < len(matches) else None

        readline.set_completer(complete)
        readline.set_completer_delims(" \t\n")
        readline.parse_and_bind("tab: complete")
        if hist and hist.exists():
            try:
                readline.read_history_file(hist)
            except OSError:
                pass
        readline.set_history_length(500)
        _readline_active = True
    try:
        if readline:
            while True:
                try:
                    line = input(prompt)
                except EOFError:
                    break
                loop.call_soon_threadsafe(queue.put_nowait, line + "\n")
        else:
            for line in sys.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, line)
    finally:
        if readline and hist:
            try:
                readline.write_history_file(hist)
            except OSError:
                pass
        _readline_active = False
        loop.call_soon_threadsafe(queue.put_nowait, None)


def show_prompt() -> None:
    """Stampa il prompt solo quando non lo fa già input() con readline."""
    if not _readline_active:
        print(PROMPT, end="", flush=True)


def echo(text: str) -> None:
    """Stampa una riga arrivata mentre l'utente sta scrivendo, ridisegnando il prompt e il testo digitato."""
    if _readline_active:
        try:
            import readline
            buffer = readline.get_line_buffer()
        except ImportError:  # pragma: no cover
            buffer = ""
        sys.stdout.write(f"\r\x1b[2K{text}\n{PROMPT}{buffer}")
        sys.stdout.flush()
    else:
        print(f"\r{text}\n{PROMPT}", end="", flush=True)


def _on_off(args: list[str], index: int, default: bool = True) -> bool:
    if len(args) <= index:
        return default
    return args[index].lower() not in ("off", "0", "no", "false")


class Shell:
    def __init__(self, dev: MarioDevice):
        self.dev = dev
        self.quiet = False
        self.closed = False  # True quando l'utente chiude (quit, off, disconnect, fine input)
        self.session = SessionLog("shell")
        self._listening = False

    def on_message(self, msg, text) -> None:
        self.session.write("<- " + text, msg.raw)
        if self.quiet and msg.msg_type not in INTERESTING:
            return
        echo(f"[{ts()}] <- {text}")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        if not self._listening:
            self.dev.add_listener(self.on_message)
            self._listening = True
            if self.session.path:
                say(f"Log della sessione: {self.session.path}")
            print(SHELL_HELP)
        threading.Thread(target=_stdin_reader, args=(loop, queue),
                         kwargs={"completions": SHELL_COMMANDS, "history": "shell"}, daemon=True).start()
        while self.dev.connected:
            show_prompt()
            getter = asyncio.ensure_future(queue.get())
            waiter = asyncio.ensure_future(self.dev.disconnected.wait())
            done, _ = await asyncio.wait({getter, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if getter not in done:
                getter.cancel()
                print("\nMario si è disconnesso.")
                break
            waiter.cancel()
            line = getter.result()
            if line is None:
                print()
                self.closed = True
                break
            line = line.strip()
            if not line:
                continue
            self.session.write("-> " + line)
            try:
                if await self.handle(line):
                    self.closed = True
                    break
            except LWPError as exc:
                print(f"Mario risponde con un errore: {exc}")
            except asyncio.TimeoutError:
                print("Nessuna risposta da Mario (timeout).")
            except (ValueError, IndexError) as exc:
                print(f"Argomenti non validi ({exc}). Scrivi 'help'.")
            except BleakError as exc:
                print(f"Errore Bluetooth: {exc}")
            except Exception as exc:  # un bug in un comando non deve chiudere la sessione
                print(f"Errore interno nel comando {line.split()[0]!r}: {type(exc).__name__}: {exc}")
                logging.getLogger("mario").debug("traceback comando shell", exc_info=True)

    async def handle(self, line: str) -> bool:
        """Esegue un comando; ritorna True se la shell deve terminare."""
        argv = shlex.split(line)
        cmd, args = argv[0].lower(), argv[1:]
        dev = self.dev

        if cmd in ("quit", "exit", "q"):
            return True
        if cmd == "help":
            print(shell_help(" ".join(args)))
        elif cmd == "log":
            if not args:
                print(f"  log: {self.session.path or 'sospeso'}")
            elif args[0].lower() in ("off", "no", "0"):
                self.session.switch(None)
                print("  log sospeso")
            else:
                path = self.session.switch(args[0])
                print(f"  log: {path}" if path else f"  impossibile aprire {args[0]}")
        elif cmd == "props":
            for prop in HubProperty:
                try:
                    raw = await dev.get_property_raw(prop, timeout=1.5)
                    print(f"  {prop.name:<18} (0x{prop:02X}): {lwp3.format_hub_property(prop, raw)}   [{raw.hex(' ')}]")
                except (LWPError, asyncio.TimeoutError) as exc:
                    print(f"  {prop.name:<18} (0x{prop:02X}): non disponibile ({exc})")
        elif cmd == "prop":
            prop = parse_hub_property(args[0])
            raw = await dev.get_property_raw(prop)
            print(f"  {enum_name(HubProperty, prop)} = {lwp3.format_hub_property(prop, raw)}   [{raw.hex(' ')}]")
        elif cmd == "watch":
            prop = parse_hub_property(args[0])
            enable = _on_off(args, 1)
            await dev.property_updates(prop, enable)
            print(f"  aggiornamenti {enum_name(HubProperty, prop)}: {'attivi' if enable else 'disattivati'}")
        elif cmd == "name":
            if args:
                print(f"  nuovo nome: {await dev.set_name(' '.join(args))!r}")
            else:
                print(f"  nome: {await dev.get_property(HubProperty.ADVERTISING_NAME)!r}")
        elif cmd == "volume":
            if args:
                print(f"  volume impostato: {await dev.set_volume(parse_int(args[0]))} %")
            else:
                print(f"  volume: {await dev.get_volume()} %")
        elif cmd == "ports":
            for port, ps in sorted(dev.state.ports.items()):
                print(f"  porta {port}: {ps.io_name} (0x{ps.io_type:04X}) | modo attuale {ps.current_mode} | "
                      f"notifiche {ps.notify} | ultimo valore {ps.last_value.hex(' ') if ps.last_value else '-'}")
        elif cmd == "modes":
            port = parse_int(args[0])
            await dev.discover_port(port, full=True, progress=say)
            print(dev.port_report(port))
        elif cmd == "sub":
            port, mode = parse_int(args[0]), parse_int(args[1])
            delta = parse_int(args[2]) if len(args) > 2 else 1
            reply = await dev.subscribe(port, mode, delta, _on_off(args, 3))
            print("  " + dev.describe(reply))
        elif cmd == "unsub":
            reply = await dev.unsubscribe(parse_int(args[0]), parse_int(args[1]))
            print("  " + dev.describe(reply))
        elif cmd == "value":
            data = await dev.request_value(parse_int(args[0]))
            print(f"  valore grezzo: {data.hex(' ')}")
        elif cmd == "combo":
            port, index = parse_int(args[0]), parse_int(args[1])
            pairs = [tuple(parse_int(x) for x in item.split(":")) for item in args[2:]]
            await dev.set_combined_mode(port, index, [(m, d) for m, d in pairs])
            print(f"  modalità combinata inviata su porta {port}: {pairs}")
        elif cmd == "combo-reset":
            await dev.reset_combined_mode(parse_int(args[0]))
        elif cmd == "alert":
            key = args[0].lower()
            alert = ALERT_ALIASES[key] if key in ALERT_ALIASES else parse_int(args[0])
            op = {"on": HubAlertOperation.ENABLE_UPDATES, "off": HubAlertOperation.DISABLE_UPDATES,
                  "req": HubAlertOperation.REQUEST_UPDATE}[args[1].lower() if len(args) > 1 else "req"]
            reply = await dev.alert(alert, op)
            print("  " + (dev.describe(reply) if reply else "inviato"))
        elif cmd == "busy":
            await dev.hub_action(HubAction.ACTIVATE_BUSY if _on_off(args, 0) else HubAction.RESET_BUSY)
        elif cmd == "action":
            await dev.hub_action(parse_int(args[0]))
        elif cmd == "out":
            await dev.port_output(parse_int(args[0]), parse_int(args[1]), bytes.fromhex("".join(args[2:])))
        elif cmd == "msg":
            await dev.send_message(parse_int(args[0]), bytes.fromhex("".join(args[1:])))
        elif cmd == "raw":
            await dev.send_hex("".join(args))
        elif cmd == "discover":
            await dev.discover(full=True, progress=say)
            print(dev.report())
        elif cmd == "report":
            print(dev.report())
        elif cmd == "quiet":
            self.quiet = _on_off(args, 0) if args else not self.quiet
            print(f"  modalità silenziosa: {'on' if self.quiet else 'off'}")
        elif cmd == "debug":
            dev.debug = _on_off(args, 0) if args else not dev.debug
            print(f"  debug byte grezzi: {'on' if dev.debug else 'off'}")
        elif cmd == "off":
            await dev.switch_off()
            print("  spegnimento inviato")
            return True
        elif cmd == "disconnect":
            await dev.request_disconnect()
            return True
        else:
            print(f"Comando sconosciuto: {cmd!r}. Scrivi 'help'.")
        return False


async def cmd_shell(args) -> int:
    dev = await connect_device(args)
    shell = Shell(dev)
    try:
        await dev.discover(full=False, progress=say)
        print()
        print(dev.report())
        print()
        while True:
            await shell.run()
            if shell.closed or args.no_reconnect or dev.connected:
                break
            say("Mario si è disconnesso: aspetto che torni visibile per riagganciarlo (premi il tasto Bluetooth; Ctrl+C per uscire)...")
            try:
                await dev.connect(args.timeout, progress=say)
            except BleakError as exc:
                say(f"Riconnessione non riuscita: {exc}")
                break
            shell.session.write("riconnesso")
            say("Riconnesso: le notifiche vanno riattivate con 'sub'.")
    finally:
        shell.session.close()
        await dev.disconnect()
    return 0


# ---------------------------------------------------------------------------
# comandi rapidi
# ---------------------------------------------------------------------------

async def cmd_quick(args) -> int:
    dev = await connect_device(args)
    try:
        if args.command == "volume":
            if args.level is None:
                print(f"Volume: {await dev.get_volume()} %")
            else:
                print(f"Volume impostato: {await dev.set_volume(args.level)} %")
        elif args.command == "name":
            if args.new_name is None:
                print(f"Nome: {await dev.get_property(HubProperty.ADVERTISING_NAME)!r}")
            else:
                print(f"Nuovo nome: {await dev.set_name(args.new_name)!r}")
        elif args.command == "prop":
            prop = parse_hub_property(args.property)
            raw = await dev.get_property_raw(prop)
            print(f"{enum_name(HubProperty, prop)} = {lwp3.format_hub_property(prop, raw)}   [{raw.hex(' ')}]")
        elif args.command == "pair":
            print("Mario associato." if dev.bonded else "Associazione non confermata: controlla con 'bluetoothctl devices Bonded'.")
        elif args.command == "off":
            await dev.switch_off()
            print("Spegnimento inviato.")
        elif args.command == "raw":
            dev.add_listener(lambda msg, text: print(f"[{ts()}] <- {text}"))
            await dev.send_hex("".join(args.hex))
            await asyncio.sleep(args.wait)
    finally:
        await dev.disconnect()
    return 0



async def cmd_unpair(args) -> int:
    dev = MarioDevice(args.address, any_lego=args.any_lego, debug=args.debug, adapter=args.adapter, pair=False)
    say("Cerco Mario per rimuoverne l'associazione... (premi il tasto Bluetooth)")
    device = await dev.find(args.timeout)
    ok = await dev.unpair(device)
    print(f"Associazione di {device.address} {'rimossa' if ok else 'non rimossa (vedi --debug)'}.")
    return 0 if ok else 1



# ---------------------------------------------------------------------------
# assistant: Mario come I/O di una sessione con un LLM
# ---------------------------------------------------------------------------

ASSISTANT_HELP = """Comandi: una riga di testo = messaggio per il modello.
  /stop            ferma la risposta e la voce        /ripeti        ripete l'ultima risposta
  /nuova           nuova conversazione                /ascolto on|off  sensori -> modello
  /emozione NOME   prova un'emozione (gioia, sorpresa, pensieroso, allarme, tristezza, sonno, calma)
  /di TESTO        fa dire un testo con la voce       /stato         stato della sessione
  /ricordi         ricordi salvati                    /ricorda TESTO  aggiunge un ricordo
  /dimentica [N|tutto]  cancella un ricordo o tutti   /regole        regole sensori -> intenti
  /parla           parla nel microfono (con --voice), Invio per finire
  /profilo [NOME]  bambino che gioca (i suoi ricordi più quelli comuni)
  /aiuto           questo elenco                      /esci"""
ASSISTANT_COMMANDS = ["/stop", "/ripeti", "/nuova", "/ascolto", "/emozione", "/di", "/stato", "/ricordi", "/ricorda",
                      "/dimentica", "/regole", "/parla", "/profilo", "/aiuto", "/esci"]


class AssistantPrinter:
    """Testo del modello in streaming; le altre righe (intenti, comportamenti) aspettano la fine della risposta."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.streaming = False
        self.side: list[str] = []

    def _write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def line(self, text: str) -> None:
        if self.streaming:
            self.side.append(text)
        elif self.out is sys.stdout:
            echo(text)
        else:
            self._write(f"\n{text}\n{PROMPT}")

    def start(self, header: str) -> None:
        self.streaming = True
        self._write(("\r\x1b[2K" if _readline_active else "") + header)

    def delta(self, text: str) -> None:
        self._write(text)

    def end(self, summary: str) -> None:
        self._write(f"\n{summary}\n")
        for text in self.side:
            self._write(text + "\n")
        self.side = []
        self.streaming = False
        self._write(PROMPT)


def handle_assistant_command(session, speaker, line: str) -> tuple[bool, str]:
    """Esegue un comando '/...' della CLI dell'assistente. Ritorna (uscire?, testo da mostrare)."""
    from .assistant.emotions import EMOTIONS, SAMPLES
    parts = line[1:].split(maxsplit=1)
    cmd, rest = (parts[0].lower() if parts else ""), (parts[1] if len(parts) > 1 else "")
    if cmd in ("esci", "quit", "exit"):
        return True, ""
    if cmd == "stop":
        session.stop()
        return False, ""
    if cmd == "parla":
        if session.voice_input is None:
            return False, "ingresso vocale spento: rilancia con --voice (serve ./tools/setup_stt.sh)"
        if session.voice_input.listening:
            session.listen_stop()
            return False, ""
        return False, "🎤 parla ora, poi premi Invio" if session.listen_start("tastiera") else ""
    if cmd == "ripeti":
        return False, "" if session.repeat() else "niente da ripetere"
    if cmd == "nuova":
        session.reset()
        return False, ""
    if cmd == "ascolto":
        session.set_listening(_on_off([rest], 0), source="tastiera")
        return False, ""
    if cmd == "emozione":
        key = rest.strip().lower()
        if key in EMOTIONS:
            session.say(SAMPLES[key], key)
            return False, f"{EMOTIONS[key].emoji} {key}: \"{SAMPLES[key]}\"" + ("" if speaker.idle else " (in coda dopo le frasi in corso)")
        return False, "emozioni: " + ", ".join(EMOTIONS)
    if cmd == "di":
        if rest.strip():
            session.say(rest)
            return False, ""
        return False, "uso: /di TESTO"
    if cmd == "stato":
        st = session.status()
        keys = ("listening", "busy", "speaking", "pending", "emotion", "backend", "context", "speaker", "behaviours")
        return False, json.dumps({k: st[k] for k in keys if k in st}, ensure_ascii=False, indent=1, default=str)
    if cmd == "regole":
        return False, "\n".join(f"  {'on ' if r['enabled'] else 'off'} {r['name']:<10} {r['kind']:<7} pausa {r['cooldown']:>4}s  {r['text']}"
                                for r in session.mapper.rules_dict())
    if cmd == "ricordi":
        items = session.memory.list()
        if not items:
            return False, "nessun ricordo" + ("" if session.memory_enabled else " (memoria disattivata)")
        return False, "\n".join(f"  {m['id']:>3}  {time.strftime('%d/%m %H:%M', time.localtime(m['ts']))}  "
                                f"{('[' + m['profile'] + '] ') if m.get('profile') else ''}{m['text']}"
                                + (f"  ({m['source']})" if m.get("source") else "")
                                + (f"  scade il {time.strftime('%d/%m', time.localtime(m['expires']))}" if m.get("expires") else "")
                                for m in items)
    if cmd == "ricorda":
        if not rest.strip():
            return False, "uso: /ricorda TESTO"
        mid = session.remember(rest)
        return False, f"ricordo {mid} salvato" if mid else "ricordo non salvato"
    if cmd == "profilo":
        if rest.strip():
            session.set_profile(rest)
        return False, f"profilo: {session.profile or 'nessuno (solo ricordi comuni)'}"
    if cmd == "dimentica":
        arg = rest.strip().lower()
        if arg in ("", "tutto", "tutti"):
            return False, f"{session.forget()} ricordi cancellati"
        try:
            return False, f"{session.forget(int(arg))} ricordo cancellato"
        except ValueError:
            return False, "uso: /dimentica [numero|tutto]"
    return False, ASSISTANT_HELP


async def cmd_assistant(args) -> int:
    from .assistant import AssistantBusy, AssistantSession, LLMConfig, Speaker, event_from_message, make_backend
    from .assistant.behaviours import BehaviourEngine, NullActions
    from .assistant.safety import parse_themes

    loop = asyncio.get_running_loop()
    dev: Optional[MarioDevice] = None
    if not args.no_mario:
        dev = await connect_device(args)
        await dev.discover(full=False, progress=say)
        for port, ps in sorted(dev.state.ports.items()):
            names = {m: mi.name for m, mi in ps.modes.items()}
            mode = codes.default_mode(ps.io_type, names)
            if mode is None:
                continue
            try:
                await dev.subscribe(port, mode, codes.DEFAULT_DELTAS.get(ps.io_type, 1))
            except (LWPError, asyncio.TimeoutError, BleakError) as exc:
                say(f"Porta {port} modo {mode}: impossibile attivare ({exc})")
        for prop in (HubProperty.BUTTON, HubProperty.BATTERY_VOLTAGE):
            try:
                await dev.property_updates(prop, True)
            except (LWPError, asyncio.TimeoutError, BleakError):
                pass

    class LoopActions:
        """Esegue le coroutine del dispositivo dal thread dei comportamenti."""

        def _run(self, coro, timeout: float = 10.0):
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

        def connected(self) -> bool:
            return dev is not None and dev.connected

        def get_volume(self):
            return self._run(dev.get_volume())

        def set_volume(self, level: int) -> int:
            return self._run(dev.set_volume(level))

        def hub_action(self, action: int) -> None:
            self._run(dev.hub_action(action))

        def port_output(self, port: int, subcommand: int, payload: bytes) -> None:
            self._run(dev.port_output(port, subcommand, payload))

    session_log = SessionLog("assistant")
    verbose = bool(args.debug or args.verbose)
    out = AssistantPrinter()

    def emit(ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "user":
            who = "sensore" if ev.get("sensor") else "tu"
            out.line(f"[{ts()}] {who} ({ev.get('source')}): {ev['text']}")
            session_log.write(f"{who} ({ev.get('source')}): {ev['text']}")
        elif kind == "start":
            out.start(f"[{ts()}] Mario ({ev.get('backend')}): ")
        elif kind == "emotion" and out.streaming:
            out.delta(f"{ev.get('emoji', '')} ")
        elif kind == "delta":
            out.delta(ev["text"])
        elif kind == "sound":
            out.delta(f" {ev['text']} ")
            session_log.write(ev["text"])
        elif kind == "done":
            session_log.write(f"Mario: {ev['text']}")
            first = f", prima frase dopo {ev['first_sentence']} s" if ev.get("first_sentence") else ""
            out.end(f"[{ts()}] (risposta in {ev.get('elapsed')} s{first}, emozioni: {', '.join(ev.get('emotions') or []) or 'nessuna'})")
        elif kind == "error":
            session_log.write("errore: " + ev["text"])
            out.end(f"[{ts()}] !! {ev['text']}")
        elif kind == "stopped" and out.streaming:
            out.end(f"[{ts()}] (interrotto)")
        elif kind in ("intent", "intent_dropped", "listening", "stopped", "reset", "repeat", "time_up", "blocked",
                      "memory_forgotten", "challenge", "challenge_done", "challenge_missed", "challenge_timeout",
                      "challenge_invalid", "voice_listening", "voice_processing", "voice_text", "voice_empty",
                      "voice_error", "blocked_output", "profile", "memory_summarized", "voice_wake",
                      "voice_wake_rejected"):
            out.line(f"[{ts()}] · {ev['text']}")
            session_log.write(ev["text"])
        elif kind == "memory":
            out.line(f"[{ts()}] · ricordo salvato: {ev['text']}")
            session_log.write("ricordo: " + ev["text"])
        elif kind == "behaviour":
            session_log.write(ev["text"])
            if verbose:
                out.line(f"[{ts()}] · Mario: {ev['text']}")

    cfg = LLMConfig(backend=args.backend, model=args.model or "", base_url=args.base_url or "",
                    sensor_model=args.sensor_model)
    speaker = Speaker(enabled=not args.no_tts, sounds=not args.no_tts, piper_model=effect_piper_model(args),
                      openvoice_reference=args.openvoice_ref or "", openvoice_tau=args.openvoice_tau,
                      openvoice_watermark=args.openvoice_watermark, sound_style=args.sound_style,
                      voice_effect="" if args.effect == "nessuno" else args.effect, effect_semitones=args.semitones,
                      effect_tempo=args.tempo, effect_formant=args.formant)
    if args.openvoice_ref and speaker.engine != "openvoice":
        say(f"Voce IA non disponibile, uso {speaker.engine or 'nessun motore'}: {speaker.openvoice_reason()}")
    engine = BehaviourEngine(LoopActions() if dev else NullActions(), emit=emit, enabled=not args.no_behaviours)
    voice_input = wake = None
    if args.voice or args.wake:
        from .assistant.stt import VoiceInput, WakeListener
        voice_input = VoiceInput(args.stt_model)
        if voice_input.problem():
            say(f"Ingresso vocale non disponibile: {voice_input.problem()}")
            voice_input = None
        else:
            voice_input.warmup()
        if voice_input is not None and args.wake:
            wake = WakeListener(args.wake_phrase)
            if wake.problem():
                say(f"Attivazione con «Ehi Mario» non disponibile: {wake.problem()}")
                wake = None
            else:
                wake.start()
    # ricordi e diario nel database della dashboard (data/mario.sqlite3), condivisi con la pagina Assistente
    memory, on_message = None, None
    if not args.no_db:
        try:
            from .assistant.memory import DatabaseMemory
            from .web.db import Database
            db = Database(Path(__file__).resolve().parent.parent / "data" / "mario.sqlite3")
            memory = DatabaseMemory(db, args.profile or "", args.memory_days)
            db.purge_memories()

            def on_message(msg: dict, _db=db) -> None:
                try:
                    _db.add_assistant_message(msg["role"], msg["content"], emotion=msg.get("emotion"),
                                              source=msg.get("source"), turn=msg.get("turn"), ts=msg.get("ts"))
                except Exception:
                    pass
        except Exception as exc:
            say(f"Database non disponibile ({exc}): ricordi solo in memoria, nessun diario")
    session = AssistantSession(lambda: make_backend(cfg), speaker, engine, emit=emit, persona=args.persona or "",
                               sensor_backend_factory=lambda: make_backend(cfg.for_sensors()),
                               challenges=not args.no_mario and not args.no_challenges, wait_seconds=args.wait_seconds,
                               memory=memory, memory_enabled=not args.no_memory, on_message=on_message,
                               session_limit=max(0.0, float(args.minutes or 0)) * 60,
                               blocked_words=[w.strip() for w in (args.block or "").split(",") if w.strip()],
                               themes=parse_themes(args.themes), session_turns=args.max_turns,
                               exclamations=not args.no_exclamations,
                               voice_input=voice_input, voice_button=dev is not None and not args.no_voice_button,
                               wake=wake)
    if dev is not None:
        def on_message(msg, text):
            ev = event_from_message(dev, msg)
            if ev:
                session.handle_event(ev)
        dev.add_listener(on_message)
    say(f"Backend: {cfg.resolved_backend()}" + (f" ({cfg.model})" if cfg.model else "")
        + f" · voce: {speaker.engine or 'nessun motore trovato'}"
        + (f" con effetto {args.effect}" if args.effect != "nessuno" else "")
        + (f" · {len(session.available_exclamations())} esclamazioni di Mario" if session.available_exclamations() else "")
        + (" · senza Mario" if dev is None else ""))
    if speaker.engine == "openvoice":
        say("Voce IA in caricamento in background (10-25 s): fino ad allora parla la voce di riserva")
    if voice_input is not None:
        say(f"Ingresso vocale ({args.stt_model}): " + (f"di' «{wake.phrases[0].capitalize()}» e poi la domanda, oppure "
                                                        if wake is not None else "")
            + ("tieni premuto il pulsante di Mario e parla, oppure " if session.voice_button else "")
            + "/parla e Invio per finire")
    if session_log.path:
        say(f"Log della sessione: {session_log.path}")
    print(ASSISTANT_HELP)
    queue: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_stdin_reader, args=(loop, queue),
                     kwargs={"completions": ASSISTANT_COMMANDS, "history": "assistant"}, daemon=True).start()
    if dev is not None:
        async def watch():
            await dev.disconnected.wait()
            queue.put_nowait(None)
        loop.create_task(watch())

        async def ticker():  # pantaloni e colore valgono solo se stabili: serve un orologio
            while dev.connected:
                await asyncio.sleep(0.5)
                session.tick()
        loop.create_task(ticker())
    show_prompt()
    try:
        while True:
            line = await queue.get()
            if line is None:
                if dev is not None and not dev.connected:
                    say("Mario si è disconnesso.")
                break
            line = line.strip()
            if not line:
                if session.voice_input is not None and session.voice_input.listening:
                    session.listen_stop()  # Invio dopo /parla: fine della frase
                show_prompt()
                continue
            if line.startswith("/"):
                leave, text = handle_assistant_command(session, speaker, line)
                if leave:
                    break
                if text:
                    print(text)
                show_prompt()
                continue
            try:
                session.ask(line)
            except AssistantBusy as exc:
                print(exc)
                show_prompt()
    finally:
        say("Chiudo: fermo la voce e riporto il volume di Mario al valore iniziale...")
        await loop.run_in_executor(None, session.shutdown)  # aspetta il ripristino prima di scollegarsi
        speaker.close()  # chiude anche il processo della voce IA
        if dev is not None:
            await dev.disconnect()
        session_log.close()
    return 0


SOUND_TOKEN = re.compile(r"\{([A-Za-z0-9_./#-]+)\}")


def voice_segments(text: str) -> list[tuple[str, str]]:
    """'{letsago} Ciao! Sono Mario. {wahoo}' -> [("sound", "letsago"), ("text", "Ciao!"), ("text", "Sono Mario."),
    ("sound", "wahoo")]: le parole tra graffe sono effetti (esclamazioni originali, file dell'archivio)."""
    from .assistant.emotions import SentenceBuffer
    out: list[tuple[str, str]] = []
    pos = 0
    for m in list(SOUND_TOKEN.finditer(text)) + [None]:
        chunk = text[pos:m.start() if m else len(text)]
        buf = SentenceBuffer()
        out += [("text", s) for s in buf.feed(chunk) + [x for x in [buf.flush()] if x]]
        if m:
            out.append(("sound", m.group(1)))
            pos = m.end()
    return out


def concat_wavs(files: list[Path], target: Path) -> float:
    """Unisce i pezzi di un messaggio in un solo WAV (44,1 kHz stereo) con ffmpeg; restituisce la durata."""
    import wave as _wave
    if not files:
        raise ValueError("nessun pezzo da salvare (motore senza file, come spd-say?)")
    inputs, chains = [], []
    for i, f in enumerate(files):
        inputs += ["-i", str(f)]
        chains.append(f"[{i}:a]aresample=44100,aformat=sample_fmts=s16:channel_layouts=stereo[a{i}]")
    graph = ";".join(chains) + ";" + "".join(f"[a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[out]"
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", str(target)],
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip()[:300])
    with _wave.open(str(target), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def cmd_voice(args) -> int:
    """Prova della voce dal PC: dice il testo frase per frase (con gli effetti tra graffe) e stampa i tempi."""
    import tempfile
    from .assistant import openvoice as ov
    from .assistant.tts import Speaker
    piper_model = effect_piper_model(args)  # con l'effetto mario: voce maschile come base
    speaker = Speaker(enabled=True, sounds=bool(args.play), piper_model=piper_model,
                      openvoice_reference=args.openvoice_ref or "", openvoice_tau=args.openvoice_tau,
                      openvoice_watermark=args.openvoice_watermark, openvoice_wait=args.wait,
                      sound_style=args.sound_style, voice_effect="" if args.effect == "nessuno" else args.effect,
                      effect_semitones=args.semitones, effect_tempo=args.tempo, effect_formant=args.formant)
    speaker.playback = not args.no_play
    if args.openvoice_ref and speaker.engine != "openvoice":
        print(f"Voce IA non disponibile: {speaker.openvoice_reason()}")
    print(f"Motore: {speaker.engine or 'nessuno'}" + (f" ({Path(piper_model).stem})" if piper_model else "")
          + (f" · effetto {args.effect} ({args.semitones:+g} semitoni, velocità ×{args.tempo:g}, formanti {args.formant})"
             if args.effect != "nessuno" else "")
          + f" · lettore: {speaker.player[0] if speaker.player else 'nessuno'}")
    for name in args.play or []:  # effetti prima della frase, uno dopo l'altro
        path = speaker.sound_path(name)
        print(f"Suono {name!r} ({args.sound_style}): {path or 'non trovato'}")
        if path and speaker.player and not args.no_play:
            subprocess.run(speaker.player + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    if not args.text:
        return 0
    if speaker.engine is None:
        print("Nessun motore vocale: installare speech-dispatcher/espeak-ng, piper o eseguire tools/setup_openvoice.sh")
        return 1
    record_dir = Path(tempfile.mkdtemp(prefix="mario-voce-")) if args.save else None
    try:
        if speaker.status()["openvoice"]["state"] != "spento":  # voce IA, o piper nel processo persistente
            ai = speaker.engine == "openvoice"
            say("Carico piper e OpenVoice (la prima volta calcola anche le impronte vocali)..." if ai
                else "Carico la voce piper...")
            t0 = time.monotonic()
            if speaker.warmup(args.wait):
                print(f"{'Voce IA' if ai else 'Voce piper'} pronta in {time.monotonic() - t0:.1f} s")
            else:
                st = speaker.status()["openvoice"]
                print(f"Voce non pronta ({st['state']}): {st['last_error'] or 'tempo scaduto'}; parla la voce di riserva")
        if record_dir:
            speaker.start_recording(record_dir)
        speaker.timings.clear()
        heard: list[float] = []
        t0 = time.monotonic()
        speaker.on_start = lambda _text, _emo: heard.append(time.monotonic() - t0)
        for kind, value in voice_segments(" ".join(args.text)):
            if kind == "sound":
                speaker.speak_sound(value, "voce")
            else:
                speaker.speak(value, args.emotion)
        speaker.wait(600)
        for timing in list(speaker.timings):
            if timing.get("sound"):
                print(f"- {{{timing['sound']}}}: {timing.get('file') or 'non trovato'}")
                continue
            parts = [f"motore {timing.get('fallback') or timing.get('engine')}", f"preparata in {timing['render_s']:.2f} s"]
            if timing.get("engine") == "openvoice" and timing.get("convert_s") is not None:
                parts.append(f"(piper {timing['base_s']:.2f} s + OpenVoice {timing['convert_s']:.2f} s, audio {timing['audio_s']:.1f} s)")
            if timing.get("effect_s") is not None:
                parts.append(f"effetto {timing['effect_s']:.2f} s")
            print(f"- {timing.get('text', '')[:60]}: " + ", ".join(parts))
        if heard:
            print(f"Prima frase udibile dopo {heard[0]:.2f} s")
        if record_dir:
            pieces = speaker.stop_recording()
            seconds = concat_wavs(pieces, Path(args.save))
            print(f"Messaggio salvato in {args.save} ({len(pieces)} pezzi, {seconds:.1f} s)")
        if speaker.last_error:
            print(f"Ultimo errore della voce: {speaker.last_error}")
    finally:
        speaker.close()
        if record_dir:
            shutil.rmtree(record_dir, ignore_errors=True)
    return 0

def cmd_listen(args) -> int:
    """Prova dell'ingresso vocale: trascrive un file, oppure registra qualche secondo dal microfono."""
    from .assistant import stt
    reason = stt.problem(args.model, recorder="file" if args.file else "auto")
    if not reason and args.wake:
        reason = stt.wake_problem(recorder="file" if args.file else "auto")
    if reason:
        print(f"Ingresso vocale non disponibile: {reason}", file=sys.stderr)
        return 1
    client = stt.SpeechClient(args.model)
    started = time.monotonic()
    client.start()
    try:
        if not client.wait_ready(90):
            print(f"Il riconoscimento vocale non è partito: {client.last_error}", file=sys.stderr)
            return 1
        say(f"Modello {args.model} pronto in {time.monotonic() - started:.1f} s")
        if args.wake:
            return _listen_wake(args, client, stt)
        args.seconds = args.seconds or 5.0
        if args.file:
            path = Path(args.file).resolve()
        else:
            recorder = stt.Recorder()
            path = stt.TMP_DIR / "prova-microfono.wav"
            say(f"Parla adesso: registro {args.seconds:g} s dal microfono ({recorder.name})...")
            recorder.start(path)
            time.sleep(max(0.5, args.seconds))
            path, _seconds = recorder.stop()
            if path is None:
                print("Registrazione fallita: nessun file", file=sys.stderr)
                return 1
        reply = client.transcribe(str(path))
        text = stt.clean_transcript(reply.get("text", ""))
        print(f"Testo: {text or '(niente)'}" + ("" if text or not reply.get("text") else f"  [scartato: {reply['text']}]"))
        print(f"(audio {reply.get('audio_s')} s, parlato {reply.get('speech_s')} s, trascritto in {reply.get('elapsed_s')} s)")
        if not args.file:
            if args.keep:
                say(f"Registrazione tenuta in {path}")
            else:
                path.unlink(missing_ok=True)
        return 0
    finally:
        client.stop()


def _listen_wake(args, client, stt) -> int:
    """Prova di «Ehi Mario»: ascolta il microfono (o legge un file) e per ogni chiamata del primo filtro (Vosk)
    stampa che cosa ha capito Whisper e se la chiamata è valida. Nulla viene salvato: le frasi si cancellano."""
    import queue
    phrases = stt.parse_wake_phrases(args.phrases)
    source = None
    if args.file:
        source = stt.ensure_wav16(Path(args.file).resolve())
    events: "queue.Queue[dict]" = queue.Queue()
    listener = stt.WakeListener(phrases, source_file=source, on_event=events.put)
    listener.start()
    if not listener.wait_ready(30):
        print(f"L'ascolto di «Ehi Mario» non è partito: {listener.last_error}", file=sys.stderr)
        return 1
    say(f"Vosk pronto in {listener.load_s} s. " + (f"Leggo {args.file}" if args.file else
        f"Di' «{phrases[0].capitalize()}» e poi una domanda" + (f" (per {args.seconds:g} s)" if args.seconds else "")
        + "; Ctrl+C per finire."))
    deadline = time.monotonic() + args.seconds if args.seconds and not args.file else None
    calls = accepted = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            try:
                ev = events.get(timeout=0.5)
            except queue.Empty:
                if listener.state == "errore":
                    print(f"Ascolto fermo: {listener.last_error}", file=sys.stderr)
                    return 1
                continue
            kind = ev.get("event")
            if kind == "wake":
                say(f"👂 primo filtro (Vosk): «{ev.get('text')}»")
            elif kind == "utterance":
                calls += 1
                path = Path(ev["path"])
                try:
                    reply = client.transcribe(str(path))
                finally:
                    path.unlink(missing_ok=True)
                text = stt.clean_transcript(reply.get("text", ""))
                found, rest = stt.split_wake(text, phrases)
                accepted += found
                say(("✓ chiamata" if found else "✗ scartata, non era una chiamata") + f": «{text or '…'}»"
                    + (f" → domanda: {rest or '(solo la chiamata)'}" if found else "")
                    + f"  (frase di {ev.get('audio_s')} s, Whisper {reply.get('elapsed_s')} s)")
            elif kind == "audio_end":
                if ev.get("error"):
                    print(f"Audio interrotto: {ev['error']}", file=sys.stderr)
                break
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()
    say(f"Chiamate sentite da Vosk: {calls}, confermate da Whisper: {accepted}")
    return 0


def cmd_doctor(args) -> int:
    """Controllo del PC (mario/syscheck.py); esce con 1 se il Bluetooth non è utilizzabile."""
    from . import syscheck
    checks = syscheck.run_checks()
    if args.json:
        print(json.dumps(syscheck.as_dicts(checks), ensure_ascii=False, indent=1))
    else:
        print(syscheck.format_checks(checks))
    return 1 if syscheck.worst(checks) == syscheck.ERROR else 0


def cmd_decode(args) -> int:
    """Decodifica offline: file btsnoop, log di testo, '-' (stdin) o byte esadecimali."""
    from . import decode
    paths, hex_args = [], []
    for item in args.inputs:
        if item == "-" or Path(item).is_file():
            paths.append(item)
        elif decode._hex_bytes(item.replace("->", "").replace("<-", "")) is not None and len(item.strip()) >= 5:
            hex_args.append(item)
        else:
            print(f"Ingresso non riconosciuto (né file né byte esadecimali): {item}", file=sys.stderr)
            return 2
    if not paths and not hex_args:
        print("Indica almeno un file (btsnoop o log di testo), '-' per lo standard input o dei byte esadecimali.",
              file=sys.stderr)
        return 2
    only_types = decode.parse_type_filter(args.type) if args.type else None
    return decode.run(paths, hex_args, caps=args.caps, as_json=args.json, only_types=only_types, only_port=args.port,
                      summary=args.summary, downstream=not args.no_tx, assume_default_modes=not args.no_default_modes)


TOOLS = {"live-checks": ("live_checks.py", "verifiche dal vivo guidate a voce (pulsante, pantaloni, gesti, RGB, ...)"),
         "catalog-tiles": ("catalog_tiles.py", "sessione guidata con le basette reali (codici, eventi, database)"),
         "probe-port4": ("probe_port4.py", "scritture a scatola nera su porta 4 e 7, osservazione di standby"),
         "diag-connect": ("diag_connect.py", "diagnostica di connessione: come BlueZ vede Mario"),
         "fetch-sounds": ("fetch_mario_sounds.py", "scarica suoni e voci di Mario da themushroomkingdom.net in data/sounds/tmk (uso privato)"),
         "re-checks": ("re_checks.py", "reverse engineering dal vivo: annuncio, GATT, tipo di sistema, sonno, porta 3 CHAL/VERS/DEBUG, porte 4 e 7 nel gioco")}


def run_tool(name: str, argv: list[str]) -> int:
    """Esegue uno script di tools/ come se fosse lanciato dal venv (`./run.sh live-checks --steps gesti`)."""
    import runpy
    script = Path(__file__).resolve().parent.parent / "tools" / TOOLS[name][0]
    sys.argv = [str(script)] + list(argv)
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


def tool_argv(argv: list[str], name: str) -> list[str]:
    """Gli argomenti che seguono il nome dello strumento, nell'ordine scritto dall'utente."""
    return list(argv[argv.index(name) + 1:]) if name in argv else []


def cmd_tool(args) -> int:  # raggiunto solo se main() non ha già smistato lo strumento
    return run_tool(args.command, args.tool_args)


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


# ---------------------------------------------------------------------------
# adventure: le avventure del designer dalla riga di comando
# ---------------------------------------------------------------------------

ADVENTURE_SIM_HELP = """Eventi simulabili (una riga ciascuno): salto, scossa, urto, rotazione, caduta, pulsante,
  basetta <codice>  (es. basetta 0x02),  colore <codice>  (es. colore 0x25),  pantaloni <codice>  (es. pantaloni 0x12),
  evento <codice> <valore>  (es. evento 0x386B 7),  livello inizio|ultimi|fine|uscita,  stato, stop, esci"""


def adventure_sim_event(line: str) -> Optional[dict]:
    """Traduce un comando di simulazione in un evento come quelli del worker."""
    parts = line.split()
    if not parts:
        return None
    cmd = parts[0].lower()
    gestures = {"salto": 0x8000, "scossa": 0x0010, "urto": 0x0001, "rotazione": 0x0100, "caduta": 0x0800}
    now = time.time()
    if cmd in gestures:
        return {"ts": now, "type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1, "data": gestures[cmd].to_bytes(2, "little")}
    if cmd == "pulsante":
        return {"ts": now, "type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True}
    if cmd in ("basetta", "colore") and len(parts) > 1:
        code = parse_int(parts[1])
        bar, col = (code, 0xFFFF) if cmd == "basetta" else (0xFFFF, code)
        return {"ts": now, "type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0,
                "data": bar.to_bytes(2, "little") + col.to_bytes(2, "little")}
    if cmd == "pantaloni" and len(parts) > 1:
        return {"ts": now, "type": "PORT_VALUE_SINGLE", "io_type": 0x4A, "mode": 0, "data": bytes([parse_int(parts[1]) & 0x3F])}
    if cmd == "livello" and len(parts) > 1:
        phases = {"inizio": 1, "ultimi": 4, "ultimi_secondi": 4, "fine": 3, "uscita": 0}
        if parts[1].lower() not in phases:
            return None
        return {"ts": now, "type": "PORT_VALUE_SINGLE", "io_type": 0x46, "mode": 2,
                "data": (0x1803).to_bytes(2, "little") + phases[parts[1].lower()].to_bytes(2, "little")}
    if cmd == "evento" and len(parts) > 2:
        return {"ts": now, "type": "PORT_VALUE_SINGLE", "io_type": 0x46, "mode": 2,
                "data": parse_int(parts[1]).to_bytes(2, "little") + parse_int(parts[2]).to_bytes(2, "little")}
    return None


def _pick_adventure(selector: str, db) -> tuple[Optional[int], dict]:
    """Avventura per id, per nome (anche parziale) o da file JSON."""
    from .adventures.model import Adventure, load_examples
    path = Path(selector)
    if path.is_file():
        return None, Adventure.from_json(path.read_text(encoding="utf-8")).to_dict()
    rows = db.list_adventures() if db is not None else []
    if not rows:
        rows = [{"id": i + 1, "name": a.name, "_data": a.to_dict()} for i, a in enumerate(load_examples())]
    for r in rows:
        if selector.isdigit() and int(selector) == r["id"]:
            return r["id"], r.get("_data") or db.get_adventure(r["id"])["data"]
    for r in rows:
        if selector.lower() in r["name"].lower():
            return r["id"], r.get("_data") or db.get_adventure(r["id"])["data"]
    raise ValueError(f"nessuna avventura corrisponde a {selector!r}")


async def cmd_adventure(args) -> int:
    from .adventures import AdventureRunner
    from .adventures.model import Adventure, load_examples, validate
    db = None
    if not args.no_db:
        try:
            from .web.db import Database
            db = Database(Path(__file__).resolve().parent.parent / "data" / "mario.sqlite3")
            from .adventures.service import seed_examples
            seed_examples(db)
        except Exception as exc:
            say(f"Database non disponibile ({exc}): uso le avventure di esempio")
    if args.list or not args.play:
        rows = db.list_adventures() if db is not None else [{"id": i + 1, "name": a.name, "steps": len(a.steps), "description": a.description} for i, a in enumerate(load_examples())]
        for r in rows:
            print(f"  {r['id']:>3}  {r['name']:<34} {r.get('steps', '?'):>2} passi  {r.get('description', '')[:70]}")
        if not args.play:
            print("\nPer giocare: ./run.sh adventure --play <id|nome|file.json> [--no-mario]")
            return 0
    adventure_id, data = _pick_adventure(args.play, db)
    adv = Adventure.from_dict(data)
    problems = validate(adv)
    if problems:
        print("Avventura non valida: " + "; ".join(problems), file=sys.stderr)
        return 2
    loop = asyncio.get_running_loop()
    dev: Optional[MarioDevice] = None
    if not args.no_mario:
        dev = await connect_device(args)
        await dev.discover(full=False, progress=say)
        for port, ps in sorted(dev.state.ports.items()):
            mode = codes.default_mode(ps.io_type, {m: mi.name for m, mi in ps.modes.items()})
            if mode is None or ps.io_type == codes.IO_VOLTAGE:
                continue
            try:
                await dev.subscribe(port, mode, codes.DEFAULT_DELTAS.get(ps.io_type, 1))
            except (LWPError, asyncio.TimeoutError, BleakError) as exc:
                say(f"Porta {port} modo {mode}: impossibile attivare ({exc})")
        try:
            await dev.property_updates(HubProperty.BUTTON, True)
        except (LWPError, asyncio.TimeoutError, BleakError):
            pass
    from .assistant import Speaker
    speaker = Speaker(enabled=not args.no_tts, sounds=not args.no_tts)
    session_log = SessionLog("adventure")

    def emit(ev: dict) -> None:
        text = f"[{ts()}] {ev['kind']}: {ev.get('text', '')}" + (f"  (punti {ev['score']})" if ev["kind"] in ("success", "finish", "score") else "")
        echo(text) if _readline_active else print(text, flush=True)
        session_log.write(f"{ev['kind']}: {ev.get('text', '')}")

    def set_volume(level: int) -> None:
        if dev is not None:
            asyncio.run_coroutine_threadsafe(dev.set_volume(level), loop)

    runner = AdventureRunner(adv, emit=emit, speaker=speaker, volume_setter=set_volume if dev else None, webhooks=args.webhooks)
    say(f"Avventura: {adv.name} ({len(adv.steps)} passi)" + (" · senza Mario: simula con la tastiera" if dev is None else ""))
    if session_log.path:
        say(f"Log della partita: {session_log.path}")
    if dev is not None:
        dev.add_listener(lambda msg, text: runner.handle_event(dev.event(msg, text)))
    print(ADVENTURE_SIM_HELP)
    runner.start()
    queue: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_stdin_reader, args=(loop, queue), kwargs={"history": "adventure"}, daemon=True).start()

    async def ticker():
        while runner.state == "running":
            await asyncio.sleep(0.5)
            runner.tick()
        queue.put_nowait(None)
    loop.create_task(ticker())
    show_prompt()
    try:
        while runner.state == "running":
            line = await queue.get()
            if line is None:
                if dev is not None and not dev.connected:
                    say("Mario si è disconnesso.")
                break
            line = line.strip()
            if not line:
                show_prompt()
                continue
            low = line.lower()
            if low in ("esci", "quit", "exit"):
                runner.stop("abbandonata")
                break
            if low == "stop":
                runner.stop("abbandonata")
                break
            if low == "stato":
                print(json.dumps(runner.status(), ensure_ascii=False, indent=1, default=str))
            else:
                ev = adventure_sim_event(line)
                if ev is None:
                    print(ADVENTURE_SIM_HELP)
                else:
                    print("  riconosciuto" if runner.handle_event(ev) else "  nessun progresso")
            show_prompt()
    finally:
        if runner.state == "running":
            runner.stop("abbandonata")
        if db is not None:
            try:
                db.add_run(adventure_id, adv.name, runner.started, runner.ended or time.time(), runner.score, runner.outcome, runner.log)
            except Exception as exc:
                say(f"partita non salvata: {exc}")
        speaker.wait(10)
        if dev is not None:
            await dev.disconnect()
        session_log.close()
    print(f"\nEsito: {runner.outcome} · {runner.score} punti · {len(runner.step_results)} passi")
    return 0


def cmd_web(args) -> int:
    """Avvia la dashboard web. Gira sull'host perché usa BlueZ. Server: waitress se installato
    (`pip install waitress`), altrimenti quello di sviluppo di Flask. Oltre 127.0.0.1 l'accesso è
    protetto da un token (generato se non indicato)."""
    import importlib.util
    import secrets
    import webbrowser
    from .web import create_app
    token = args.token or os.environ.get("MARIO_WEB_TOKEN", "")
    exposed = args.host not in LOOPBACK_HOSTS
    if exposed and not token and not args.no_token:
        token = secrets.token_urlsafe(12)
    url = f"http://{args.host}:{args.port}/"
    app = create_app(db_path=args.db, adapter=args.adapter, pair=not args.no_pair, token=token or None)
    server = args.server
    if server == "auto":
        server = "waitress" if importlib.util.find_spec("waitress") else "flask"
    print(f"Dashboard LEGO Mario su {url}  (server {server}, Ctrl+C per uscire)", file=sys.stderr)
    if token:
        print(f"Accesso protetto: apri {url}?token={token}  oppure inserisci il token nella pagina di accesso "
              f"(--token per sceglierlo, --no-token per disattivare)", file=sys.stderr)
    elif exposed:
        print("Attenzione: dashboard raggiungibile dalla rete senza token (--no-token).", file=sys.stderr)
    if args.open:
        webbrowser.open(url + (f"?token={token}" if token else ""))
    if server == "waitress":
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=args.threads, ident="mario-dashboard")
    else:
        app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


# ---------------------------------------------------------------------------

def add_effect_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--effect", default="nessuno", choices=["nessuno", "mario"],
                   help="mario: voce più acuta e veloce da personaggio (base piper riccardo se non indicata)")
    p.add_argument("--semitones", type=float, default=6.0, help="effetto mario: semitoni in più (default 6)")
    p.add_argument("--tempo", type=float, default=1.1, help="effetto mario: velocità (default 1.1)")
    p.add_argument("--formant", default="shifted", choices=["shifted", "preserved"],
                   help="effetto mario: formanti spostate (cartone) o conservate (più naturale)")


def effect_piper_model(args) -> str:
    """Il modello piper indicato; con l'effetto mario e nessuna voce scelta, la voce maschile di base."""
    from .assistant.openvoice import mario_base_voice
    if args.piper_model or args.effect != "mario" or getattr(args, "openvoice_ref", None):
        return args.piper_model or ""
    return mario_base_voice()


def add_openvoice_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--openvoice-ref", metavar="WAV",
                   help="voce IA: registrazione della voce da imitare (WAV, 10-30 s); serve tools/setup_openvoice.sh")
    p.add_argument("--openvoice-tau", type=float, default=0.3,
                   help="voce IA: variabilità della conversione 0..1 (0 = stabile e ripetibile; default 0.3 come OpenVoice)")
    p.add_argument("--openvoice-watermark", action="store_true",
                   help="voce IA: marchio d'acqua inaudibile di OpenVoice (circa +40%% di tempo di conversione)")


def build_parser() -> argparse.ArgumentParser:
    from .assistant.emotions import EMOTIONS
    parser = argparse.ArgumentParser(prog="mario", description="Comunicazione Bluetooth LE con LEGO Mario / Luigi / Peach.")
    parser.add_argument("-a", "--address", help="indirizzo Bluetooth del dispositivo (altrimenti primo Mario trovato)")
    parser.add_argument("-t", "--timeout", type=float, default=30.0, help="secondi di attesa per trovare Mario (default 30)")
    parser.add_argument("--any-lego", action="store_true", help="accetta qualsiasi hub LEGO, non solo Mario/Luigi/Peach")
    parser.add_argument("-i", "--adapter", help="adattatore Bluetooth da usare, es. hci1 (default: quello predefinito)")
    parser.add_argument("--no-pair", action="store_true",
                        help="non eseguire il pairing automatico (Mario lo richiede per il GATT)")
    parser.add_argument("-d", "--debug", action="store_true", help="mostra i byte grezzi inviati e ricevuti")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="cerca i LEGO Mario nelle vicinanze")
    p.add_argument("--duration", type=float, default=10.0, help="durata della scansione in secondi")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("info", help="scopre tutte le funzioni del dispositivo")
    p.add_argument("--json", action="store_true", help="output in JSON")
    p.add_argument("--quick", action="store_true", help="solo nomi e formati delle modalità (più veloce)")
    p.add_argument("--no-save", action="store_true",
                   help="non aggiornare docs/capabilities-fw-<versione>.json (salvato dopo la scoperta completa)")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("monitor", help="mostra in tempo reale gli eventi dei sensori")
    p.add_argument("--sub", action="append", metavar="PORTA:MODO[:DELTA]",
                   help="porta e modo da attivare (ripetibile, es. --sub 7:0 per la telemetria); "
                        "default: gesti, scanner, pantaloni, eventi porta 3 e 4, batteria")
    p.add_argument("--raw-accel", action="store_true", help="accelerometro grezzo invece dei gesti")
    p.add_argument("--combined", action="store_true",
                   help="porte combinabili in modalità combinata: accelerometro + gesti (porta 0), barcode + RGB (porta 1)")
    p.add_argument("--rssi", action="store_true", help="mostra anche gli aggiornamenti RSSI")
    p.add_argument("--no-battery", action="store_true", help="niente tensione né percentuale della batteria")
    p.add_argument("--json", action="store_true", help="una riga JSON per evento (porta, modo, valori, decodifica)")
    p.add_argument("--duration", type=float, default=0.0, metavar="SECONDI", help="si ferma da solo dopo N secondi")
    p.add_argument("--no-reconnect", action="store_true",
                   help="esci quando Mario si disconnette invece di aspettare che torni visibile")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("shell", help="console interattiva bidirezionale")
    p.add_argument("--no-reconnect", action="store_true",
                   help="esci se Mario cade invece di aspettare che torni visibile")
    p.set_defaults(func=cmd_shell)

    p = sub.add_parser("decode", help="decodifica catture btsnoop, bugreport Android (zip/btsnooz), PacketLogger di iOS, log di testo o byte esadecimali (senza Bluetooth)")
    p.add_argument("inputs", nargs="*", metavar="FILE|HEX",
                   help="file btsnoop (btmon -w, HCI snoop Android) o log di testo; '-' = stdin; oppure byte, es. '05 00 45 02 21'")
    p.add_argument("--caps", help="JSON delle funzioni per lo stato iniziale (default docs/capabilities-fw-6.5.01.json)")
    p.add_argument("--json", action="store_true", help="una riga JSON per messaggio")
    p.add_argument("--type", help="solo questi tipi di messaggio: numeri esadecimali o nomi, es. 45,46 oppure hub_properties")
    p.add_argument("--port", type=int, help="solo i valori di questa porta")
    p.add_argument("--summary", action="store_true", help="riepilogo finale: conteggi per tipo e porta, eventi della porta 3")
    p.add_argument("--no-tx", action="store_true", help="nasconde i messaggi inviati a Mario")
    p.add_argument("--no-default-modes", action="store_true",
                   help="non presumere i modi di default di monitor per le porte senza sottoscrizione nel file")
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("doctor", help="controlla il PC per il Bluetooth di Mario: adattatore, servizio, autosospensione "
                                      "USB, Wi-Fi a 2,4 GHz, pairing (senza sudo, senza Mario)")
    p.add_argument("--json", action="store_true", help="risultati in JSON")
    p.set_defaults(func=cmd_doctor)

    for name, (_script, help_text) in TOOLS.items():
        p = sub.add_parser(name, help=f"{help_text} (tools/{_script}; opzioni dopo il nome)")
        p.add_argument("tool_args", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_tool)

    p = sub.add_parser("volume", help="legge o imposta il volume")
    p.add_argument("level", type=int, nargs="?", help="0-100")
    p.set_defaults(func=cmd_quick)

    p = sub.add_parser("name", help="legge o imposta il nome Bluetooth")
    p.add_argument("new_name", nargs="?")
    p.set_defaults(func=cmd_quick)

    p = sub.add_parser("prop", help="legge una proprietà dell'hub")
    p.add_argument("property", help="nome (name, battery, fw, volume...) o id (0x06)")
    p.set_defaults(func=cmd_quick)

    p = sub.add_parser("pair", help="associa Mario a questo PC (pairing Just Works) e si scollega")
    p.set_defaults(func=cmd_quick)

    p = sub.add_parser("unpair", help="rimuove l'associazione di Mario da BlueZ")
    p.set_defaults(func=cmd_unpair)

    p = sub.add_parser("off", help="spegne Mario")
    p.set_defaults(func=cmd_quick)

    p = sub.add_parser("assistant", help="Mario come ingresso/uscita di una sessione con un modello (voce dal PC)")
    p.add_argument("--no-mario", action="store_true", help="senza Bluetooth: solo tastiera, modello e voce")
    p.add_argument("--backend", default="auto", choices=["auto", "anthropic", "openai", "demo"],
                   help="auto = Claude se c'è ANTHROPIC_API_KEY, altrimenti demo")
    p.add_argument("--model", help="nome del modello (default claude-sonnet-5-5 per Claude)")
    p.add_argument("--profile", help="bambino che gioca: i suoi ricordi più quelli comuni (default nessuno)")
    p.add_argument("--memory-days", type=float, default=0.0,
                   help="i ricordi scritti dal modello scadono dopo tanti giorni (0 = mai; quelli dei genitori mai)")
    p.add_argument("--themes", default="violenza,adulti,dati_personali",
                   help="temi da evitare (nel testo del bambino e nelle risposte): violenza, adulti, dati_personali, "
                        "paura; '' per nessuno")
    p.add_argument("--max-turns", type=int, default=0, help="messaggi al modello per sessione (0 = nessun limite)")
    p.add_argument("--voice", action="store_true",
                   help="ingresso vocale dal microfono del PC (offline, faster-whisper; serve ./tools/setup_stt.sh): "
                        "si parla tenendo premuto il pulsante di Mario, oppure con /parla")
    p.add_argument("--stt-model", default="small", choices=["small", "base"],
                   help="modello Whisper: small (circa 3 s per frase, preciso) o base (circa 1 s)")
    p.add_argument("--no-voice-button", action="store_true",
                   help="con --voice: il pulsante di Mario resta per l'ascolto dei sensori, si parla solo con /parla")
    p.add_argument("--wake", action="store_true",
                   help="attivazione con la voce: il microfono resta in ascolto e si parla a Mario dicendo «Ehi Mario» "
                        "(offline, Vosk + Whisper; implica --voice; serve ./tools/setup_stt.sh)")
    p.add_argument("--wake-phrase", default="ehi mario, hey mario",
                   help="frasi di attivazione separate da virgola (default: 'ehi mario, hey mario')")
    p.add_argument("--no-challenges", action="store_true",
                   help="il modello non può chiedere azioni con [aspetta: ...] (storia interattiva; mai senza Mario)")
    p.add_argument("--wait-seconds", type=float, default=20.0, help="attesa massima di un'azione chiesta (default 20)")
    p.add_argument("--sensor-model", default="auto",
                   help="modello per le reazioni ai sensori: auto (claude-haiku-5-5 con Claude, altrimenti lo stesso), "
                        "'' per lo stesso della conversazione, oppure un nome")
    p.add_argument("--base-url", help="endpoint compatibile OpenAI, es. http://localhost:11434/v1 per Ollama")
    p.add_argument("--persona", help="istruzioni in più per il modello")
    p.add_argument("--no-tts", action="store_true", help="senza voce ed effetti sonori")
    p.add_argument("--no-behaviours", action="store_true", help="nessun comportamento sul Mario fisico")
    p.add_argument("--piper-model", help="voce neurale piper: percorso del modello .onnx (serve il binario piper)")
    add_openvoice_args(p)
    p.add_argument("--sound-style", default="giochi", choices=["giochi", "voce", "sintetici"],
                   help="effetti sonori: dei giochi (./run.sh fetch-sounds), esclamazioni di Mario o sintetizzati")
    add_effect_args(p)
    p.add_argument("--no-exclamations", action="store_true",
                   help="il modello non può far suonare le esclamazioni di Mario ({letsago}, {wahoo}, ...)")
    p.add_argument("--minutes", type=float, default=0.0, help="minuti di gioco per sessione (0 = nessun limite)")
    p.add_argument("--block", help="parole bloccate, separate da virgola")
    p.add_argument("--no-memory", action="store_true", help="niente ricordi tra le sessioni")
    p.add_argument("--no-db", action="store_true", help="non usare data/mario.sqlite3 per ricordi e diario")
    p.add_argument("-v", "--verbose", action="store_true", help="mostra anche i comportamenti (volume, busy) riga per riga")
    p.set_defaults(func=cmd_assistant)

    p = sub.add_parser("adventure", help="avventure del designer: elenco, partita con Mario o simulata")
    p.add_argument("--list", action="store_true", help="elenca le avventure (database o esempi)")
    p.add_argument("--play", metavar="ID|NOME|FILE", help="gioca un'avventura (id, parte del nome o file JSON)")
    p.add_argument("--no-mario", action="store_true", help="senza Bluetooth: eventi simulati da tastiera")
    p.add_argument("--no-tts", action="store_true", help="senza voce ed effetti sonori")
    p.add_argument("--no-db", action="store_true", help="non usare data/mario.sqlite3 (niente partite salvate)")
    p.add_argument("--webhooks", action="store_true", help="consente alle azioni webhook di fare POST verso URL esterni")
    p.set_defaults(func=cmd_adventure)

    p = sub.add_parser("listen", help="prova dell'ingresso vocale: trascrive un file o qualche secondo dal microfono "
                                      "(offline; serve ./tools/setup_stt.sh)")
    p.add_argument("--file", help="file audio da trascrivere (altrimenti registra dal microfono)")
    p.add_argument("--seconds", type=float, default=None,
                   help="secondi da registrare dal microfono (default 5); con --wake durata dell'ascolto (default: fino a Ctrl+C)")
    p.add_argument("--model", default="small", choices=["small", "base"], help="modello Whisper (default small)")
    p.add_argument("--keep", action="store_true", help="tiene la registrazione (cartella temporanea) invece di cancellarla")
    p.add_argument("--wake", action="store_true",
                   help="prova di «Ehi Mario»: ascolta di continuo (o legge --file) e mostra le chiamate riconosciute")
    p.add_argument("--phrases", default="ehi mario, hey mario", help="con --wake: frasi di attivazione separate da virgola")
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("voice", help="prova della voce dal PC con i tempi (spd-say, piper o voce IA OpenVoice)")
    p.add_argument("text", nargs="*", help="testo da dire, con gli effetti tra graffe: \"{letsago} Ciao! {wahoo}\"")
    p.add_argument("--play", action="append", metavar="SUONO",
                   help="suona prima un effetto: coin, powerup, alarm, letsago, ... o gioco/file.wav (ripetibile)")
    p.add_argument("--sound-style", default="giochi", choices=["giochi", "voce", "sintetici"],
                   help="effetti dei giochi, esclamazioni di Mario o sintetizzati (default giochi)")
    p.add_argument("--emotion", choices=sorted(EMOTIONS), help="emozione (velocità e tono della voce)")
    p.add_argument("--piper-model", help="voce piper (.onnx); con la voce IA è la voce di base")
    add_openvoice_args(p)
    p.add_argument("--wait", type=float, default=120.0, help="secondi massimi di attesa del caricamento della voce IA")
    add_effect_args(p)
    p.add_argument("--save", metavar="FILE.wav", help="salva il messaggio intero (frasi ed effetti) in un WAV")
    p.add_argument("--no-play", action="store_true", help="non suonare (utile con --save)")
    p.set_defaults(func=cmd_voice)

    p = sub.add_parser("web", help="avvia la dashboard web")
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 per esporla sulla rete locale (con token)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--db", help="percorso del database SQLite (default data/mario.sqlite3)")
    p.add_argument("--open", action="store_true", help="apre il browser")
    p.add_argument("--server", choices=["auto", "flask", "waitress"], default="auto",
                   help="auto = waitress se installato (pip install waitress), altrimenti il server di sviluppo Flask")
    p.add_argument("--threads", type=int, default=16, help="thread di waitress (ogni pagina aperta tiene un flusso SSE)")
    p.add_argument("--token", help="token di accesso (anche MARIO_WEB_TOKEN); generato se l'host non è locale")
    p.add_argument("--no-token", action="store_true", help="nessun token anche se esposta oltre 127.0.0.1")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("raw", help="invia byte grezzi e mostra le risposte")
    p.add_argument("hex", nargs="+", help="byte esadecimali, es. 05 00 01 12 05")
    p.add_argument("--wait", type=float, default=2.0, help="secondi di attesa delle risposte")
    p.set_defaults(func=cmd_quick)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    from .env import load_dotenv
    load_dotenv()  # .env nella radice del progetto (es. ANTHROPIC_API_KEY), senza sovrascrivere l'ambiente
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in TOOLS:  # gli strumenti hanno le proprie opzioni: non passano da argparse
        return run_tool(argv[0], argv[1:])
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    if args.command in TOOLS:  # es. `mario -t 60 live-checks --steps gesti`: tutto ciò che segue il nome va allo strumento
        return run_tool(args.command, tool_argv(argv, args.command))
    if unknown:
        parser.error("argomenti non riconosciuti: " + " ".join(unknown))
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        if asyncio.iscoroutinefunction(args.func):
            return asyncio.run(args.func(args)) or 0
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\nInterrotto.")
        return 130
    except BleakError as exc:
        print(f"Errore Bluetooth: {exc}", file=sys.stderr)
        return 2
