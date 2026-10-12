"""
Interfaccia a riga di comando.

  scan      cerca i LEGO Mario nelle vicinanze
  info      si connette e scopre tutte le funzioni (proprietà, porte, modalità, formati)
  monitor   attiva i sensori e stampa in tempo reale gli eventi decodificati
  shell     console interattiva bidirezionale (comandi + messaggi in arrivo)
  assistant Mario come ingresso/uscita di una sessione con un modello di linguaggio (voce dal PC)
  web       dashboard Flask
  volume / name / prop / raw / off   comandi rapidi one-shot
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shlex
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

    def __init__(self, kind: str):
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        try:
            logs_dir.mkdir(exist_ok=True)
            self.path = logs_dir / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
            self._fh = open(self.path, "a", encoding="utf-8")
        except OSError:
            self.path, self._fh = None, None

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


async def cmd_info(args) -> int:
    dev = await connect_device(args)
    try:
        await dev.discover(full=not args.quick, progress=say)
        if args.json:
            print(json.dumps(dev.to_dict(), indent=2, ensure_ascii=False))
        else:
            print()
            print(dev.report())
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


async def cmd_monitor(args) -> int:
    dev = await connect_device(args)
    try:
        await dev.discover(full=False, progress=say)
        subs = [parse_subscription(s) for s in args.sub] if args.sub else []
        if not subs:
            for port, ps in sorted(dev.state.ports.items()):
                names = {m: mi.name for m, mi in ps.modes.items()}
                prefer = ("RAW",) if args.raw_accel and ps.io_type == codes.IO_MARIO_ACCELEROMETER else ()
                mode = codes.default_mode(ps.io_type, names, prefer)
                if mode is not None:
                    subs.append((port, mode, codes.DEFAULT_DELTAS.get(ps.io_type, 1)))
        session = SessionLog("monitor")
        if session.path:
            say(f"Log della sessione: {session.path}")
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
        props = [HubProperty.BUTTON, HubProperty.BATTERY_VOLTAGE]
        if args.rssi:
            props.append(HubProperty.RSSI)
        for prop in props:
            await dev.property_updates(prop, True)
        say("In ascolto: muovi Mario, cambia i pantaloni, appoggialo sui tile. Ctrl+C per uscire.")

        def on_message(msg, text):
            if msg.msg_type in INTERESTING:
                print(f"[{ts()}] {text}", flush=True)
                session.write(text, msg.raw)

        dev.add_listener(on_message)
        await dev.disconnected.wait()
        say("Mario si è disconnesso.")
    finally:
        await dev.disconnect()
        try:
            session.close()
        except NameError:
            pass
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
  off                           spegne Mario
  disconnect                    chiede a Mario di disconnettersi
  quit                          esce dalla shell"""

ALERT_ALIASES = {
    "low_voltage": HubAlertType.LOW_VOLTAGE, "voltage": HubAlertType.LOW_VOLTAGE,
    "high_current": HubAlertType.HIGH_CURRENT, "current": HubAlertType.HIGH_CURRENT,
    "low_signal": HubAlertType.LOW_SIGNAL_STRENGTH, "signal": HubAlertType.LOW_SIGNAL_STRENGTH,
    "over_power": HubAlertType.OVER_POWER_CONDITION, "power": HubAlertType.OVER_POWER_CONDITION,
}


def _stdin_reader(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
    try:
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)


def _on_off(args: list[str], index: int, default: bool = True) -> bool:
    if len(args) <= index:
        return default
    return args[index].lower() not in ("off", "0", "no", "false")


class Shell:
    def __init__(self, dev: MarioDevice):
        self.dev = dev
        self.quiet = False
        self.session = SessionLog("shell")

    def on_message(self, msg, text) -> None:
        self.session.write("<- " + text, msg.raw)
        if self.quiet and msg.msg_type not in INTERESTING:
            return
        print(f"\r[{ts()}] <- {text}\nmario> ", end="", flush=True)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        threading.Thread(target=_stdin_reader, args=(loop, queue), daemon=True).start()
        self.dev.add_listener(self.on_message)
        if self.session.path:
            say(f"Log della sessione: {self.session.path}")
        print(SHELL_HELP)
        while self.dev.connected:
            print("mario> ", end="", flush=True)
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
                break
            line = line.strip()
            if not line:
                continue
            self.session.write("-> " + line)
            try:
                if await self.handle(line):
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
            print(SHELL_HELP)
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
    try:
        await dev.discover(full=False, progress=say)
        print()
        print(dev.report())
        print()
        shell = Shell(dev)
        try:
            await shell.run()
        finally:
            shell.session.close()
    finally:
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
  /regole          regole sensori -> intenti          /aiuto         questo elenco      /esci"""


async def cmd_assistant(args) -> int:
    from .assistant import AssistantBusy, AssistantSession, LLMConfig, Speaker, event_from_message, make_backend
    from .assistant.behaviours import BehaviourEngine, NullActions
    from .assistant.emotions import EMOTIONS, SAMPLES

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

    class Printer:
        """Testo del modello in streaming; le altre righe (intenti, comportamenti) aspettano la fine."""
        streaming = False
        side: list[str] = []

        def line(self, text: str) -> None:
            if self.streaming:
                self.side.append(text)
            else:
                print(f"\n{text}\nmario> ", end="", flush=True)

        def end(self, summary: str) -> None:
            print(f"\n{summary}")
            for text in self.side:
                print(text)
            self.side = []
            self.streaming = False
            print("mario> ", end="", flush=True)

    out = Printer()

    def emit(ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "user":
            who = "sensore" if ev.get("sensor") else "tu"
            out.line(f"[{ts()}] {who} ({ev.get('source')}): {ev['text']}")
            session_log.write(f"{who} ({ev.get('source')}): {ev['text']}")
        elif kind == "start":
            out.streaming = True
            print(f"[{ts()}] Mario ({ev.get('backend')}): ", end="", flush=True)
        elif kind == "emotion" and out.streaming:
            print(f"{ev.get('emoji', '')} ", end="", flush=True)
        elif kind == "delta":
            print(ev["text"], end="", flush=True)
        elif kind == "done":
            session_log.write(f"Mario: {ev['text']}")
            out.end(f"[{ts()}] (risposta in {ev.get('elapsed')} s, emozioni: {', '.join(ev.get('emotions') or []) or 'nessuna'})")
        elif kind == "error":
            session_log.write("errore: " + ev["text"])
            out.end(f"[{ts()}] !! {ev['text']}")
        elif kind == "stopped" and out.streaming:
            out.end(f"[{ts()}] (interrotto)")
        elif kind in ("intent", "intent_dropped", "listening", "stopped", "reset", "repeat"):
            out.line(f"[{ts()}] · {ev['text']}")
            session_log.write(ev["text"])
        elif kind == "behaviour":
            session_log.write(ev["text"])
            if verbose:
                out.line(f"[{ts()}] · Mario: {ev['text']}")

    cfg = LLMConfig(backend=args.backend, model=args.model or "", base_url=args.base_url or "")
    speaker = Speaker(enabled=not args.no_tts, sounds=not args.no_tts)
    engine = BehaviourEngine(LoopActions() if dev else NullActions(), emit=emit, enabled=not args.no_behaviours)
    session = AssistantSession(lambda: make_backend(cfg), speaker, engine, emit=emit, persona=args.persona or "")
    if dev is not None:
        def on_message(msg, text):
            ev = event_from_message(dev, msg)
            if ev:
                session.handle_event(ev)
        dev.add_listener(on_message)
    say(f"Backend: {cfg.resolved_backend()}" + (f" ({cfg.model})" if cfg.model else "")
        + f" · voce: {speaker.engine or 'nessun motore trovato'}" + (" · senza Mario" if dev is None else ""))
    if session_log.path:
        say(f"Log della sessione: {session_log.path}")
    print(ASSISTANT_HELP)
    queue: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_stdin_reader, args=(loop, queue), daemon=True).start()
    if dev is not None:
        async def watch():
            await dev.disconnected.wait()
            queue.put_nowait(None)
        loop.create_task(watch())
    print("mario> ", end="", flush=True)
    try:
        while True:
            line = await queue.get()
            if line is None:
                if dev is not None and not dev.connected:
                    say("Mario si è disconnesso.")
                break
            line = line.strip()
            if not line:
                print("mario> ", end="", flush=True)
                continue
            if line.startswith("/"):
                parts = line[1:].split(maxsplit=1)
                cmd, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
                if cmd in ("esci", "quit", "exit"):
                    break
                elif cmd == "stop":
                    session.stop()
                elif cmd == "ripeti":
                    if not session.repeat():
                        print("niente da ripetere")
                elif cmd == "nuova":
                    session.reset()
                elif cmd == "ascolto":
                    session.set_listening(_on_off([rest], 0), source="tastiera")
                elif cmd == "emozione":
                    key = rest.strip().lower()
                    if key in EMOTIONS:
                        session.say(SAMPLES[key], key)
                        print(f"{EMOTIONS[key].emoji} {key}: \"{SAMPLES[key]}\"" + ("" if speaker.idle else " (in coda dopo le frasi in corso)"))
                    else:
                        print("emozioni: " + ", ".join(EMOTIONS))
                elif cmd == "di":
                    session.say(rest)
                elif cmd == "stato":
                    st = session.status()
                    print(json.dumps({k: st[k] for k in ("listening", "busy", "speaking", "pending", "emotion", "backend",
                                                         "context", "speaker", "behaviours")},
                                     ensure_ascii=False, indent=1, default=str))
                elif cmd == "regole":
                    for r in session.mapper.rules_dict():
                        print(f"  {'on ' if r['enabled'] else 'off'} {r['name']:<10} {r['kind']:<7} pausa {r['cooldown']:>4}s  {r['text']}")
                else:
                    print(ASSISTANT_HELP)
                print("mario> ", end="", flush=True)
                continue
            try:
                session.ask(line)
            except AssistantBusy as exc:
                print(f"{exc}\nmario> ", end="", flush=True)
    finally:
        say("Chiudo: fermo la voce e riporto il volume di Mario al valore iniziale...")
        await loop.run_in_executor(None, session.shutdown)  # aspetta il ripristino prima di scollegarsi
        if dev is not None:
            await dev.disconnect()
        session_log.close()
    return 0

def cmd_web(args) -> int:
    """Avvia la dashboard web (Flask). Gira sull'host perché usa BlueZ."""
    import webbrowser
    from .web import create_app
    app = create_app(db_path=args.db, adapter=args.adapter, pair=not args.no_pair)
    url = f"http://{args.host}:{args.port}/"
    print(f"Dashboard LEGO Mario su {url}  (Ctrl+C per uscire)", file=sys.stderr)
    if args.open:
        webbrowser.open(url)
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
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
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("monitor", help="mostra in tempo reale gli eventi dei sensori")
    p.add_argument("--sub", action="append", metavar="PORTA:MODO[:DELTA]",
                   help="porta e modo da attivare (ripetibile, es. --sub 7:0 per la telemetria); "
                        "default: gesti, scanner, pantaloni, eventi porta 3 e 4, batteria")
    p.add_argument("--raw-accel", action="store_true", help="accelerometro grezzo invece dei gesti")
    p.add_argument("--rssi", action="store_true", help="mostra anche gli aggiornamenti RSSI")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("shell", help="console interattiva bidirezionale")
    p.set_defaults(func=cmd_shell)

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
    p.add_argument("--base-url", help="endpoint compatibile OpenAI, es. http://localhost:11434/v1 per Ollama")
    p.add_argument("--persona", help="istruzioni in più per il modello")
    p.add_argument("--no-tts", action="store_true", help="senza voce ed effetti sonori")
    p.add_argument("--no-behaviours", action="store_true", help="nessun comportamento sul Mario fisico")
    p.add_argument("-v", "--verbose", action="store_true", help="mostra anche i comportamenti (volume, busy) riga per riga")
    p.set_defaults(func=cmd_assistant)

    p = sub.add_parser("web", help="avvia la dashboard web (Flask)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--db", help="percorso del database SQLite (default data/mario.sqlite3)")
    p.add_argument("--open", action="store_true", help="apre il browser")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("raw", help="invia byte grezzi e mostra le risposte")
    p.add_argument("hex", nargs="+", help="byte esadecimali, es. 05 00 01 12 05")
    p.add_argument("--wait", type=float, default=2.0, help="secondi di attesa delle risposte")
    p.set_defaults(func=cmd_quick)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    from .env import load_dotenv
    load_dotenv()  # .env nella radice del progetto (es. ANTHROPIC_API_KEY), senza sovrascrivere l'ambiente
    args = build_parser().parse_args(argv)
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
