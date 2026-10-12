#!/usr/bin/env python3
"""
Verifiche dal vivo guidate a voce (TODO, sezione 1): pulsante, pantaloni, gesti con accelerometro
in modalità combinata, scanner RGB sui colori noti, RSSI, allarmi dell'hub, disconnessione e
spegnimento comandati dal PC.

Per ogni passo il PC dice cosa fare, ascolta per un tempo fissato e registra tutto quello che Mario
manda. Alla fine stampa un riepilogo con esito e dati raccolti, salvato anche in
logs/live-checks-<data>.txt. L'ultimo passo spegne Mario: per saltarlo usare --steps senza "off".

Uso (sull'host):
  .venv/bin/python tools/live_checks.py                      # tutti i passi
  .venv/bin/python tools/live_checks.py --steps gesti,rgb    # solo alcuni
  passi: pulsante, pantaloni, gesti, rgb, rssi, allarmi, disconnect, off
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mario import codes, lwp3  # noqa: E402
from mario.device import LWPError, MarioDevice  # noqa: E402
from mario.lwp3 import (  # noqa: E402
    CombinedModeSubcommand, HubAction, HubAlertOperation, HubAlertType, HubProperty, HubPropertyOperation,
    MessageType, le16,
)

PROJECT = Path(__file__).resolve().parent.parent
ALL_STEPS = ["pulsante", "pantaloni", "gesti", "rgb", "rssi", "allarmi", "disconnect", "off"]

GESTURE_TASKS = [
    ("fallo saltare: sollevalo di scatto e riappoggialo", 6),
    ("scuotilo avanti e indietro", 6),
    ("fallo girare su se stesso, in piedi", 6),
    ("capovolgilo a testa in giù, poi rimettilo dritto", 8),
    ("inclinalo in avanti e indietro, lentamente", 8),
    ("lascialo cadere sul tavolo da pochi centimetri", 6),
    ("appoggialo delicatamente sul tavolo", 6),
    ("dagli un colpetto sul cappello", 6),
    ("tienilo fermo in mano senza muoverlo", 6),
]
COLOR_TASKS = [("bianca", 8), ("nera", 8), ("gialla", 8), ("verde", 8), ("rossa, se ce l'hai", 8), ("blu, se ce l'hai", 8)]


def speak(text: str, enabled: bool, wait: bool = True) -> None:
    if not (enabled and shutil.which("spd-say")):
        return
    cmd = ["spd-say", "-l", "it", "-r", "10"] + (["-w"] if wait else []) + ["--", text]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except subprocess.TimeoutExpired:
        pass


class Recorder:
    def __init__(self, path: Path):
        self.fh = open(path, "a", encoding="utf-8")
        self.messages: list[tuple[float, lwp3.Message, str]] = []
        self.results: list[tuple[str, str, str]] = []  # (passo, esito, dettagli)
        self.dev: MarioDevice | None = None

    def log(self, text: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def on_message(self, msg: lwp3.Message, text: str) -> None:
        self.messages.append((time.time(), msg, text))
        self.fh.write(f"[{time.strftime('%H:%M:%S')}] <- {text}   [{msg.raw.hex(' ')}]\n")
        self.fh.flush()
        if msg.msg_type in (MessageType.GENERIC_ERROR, MessageType.HUB_ACTIONS, MessageType.HUB_ALERTS,
                            MessageType.PORT_INPUT_FORMAT_COMBINED):
            print(f"    <- {text}", flush=True)

    def since(self, t0: float, types=None) -> list[tuple[float, lwp3.Message, str]]:
        return [m for m in self.messages if m[0] >= t0 and (types is None or m[1].msg_type in types)]

    def result(self, step: str, outcome: str, details: str) -> None:
        self.results.append((step, outcome, details))
        self.log(f"ESITO {step}: {outcome}. {details}")


def parse_combined(payload: bytes, layout: list[str]) -> dict[int, int]:
    """payload = [porta][mask][valori]; layout = formato struct di ogni dataset nell'ordine della combinazione.
    Mario (fw 6.5.01) manda la maschera dei dataset con il byte alto per primo: `00 1f` = dataset 0..4."""
    mask, data, out, pos = int.from_bytes(payload[1:3], "big"), payload[3:], {}, 0
    for i, fmt in enumerate(layout):
        if mask & (1 << i):
            size = struct.calcsize("<" + fmt)
            if pos + size > len(data):
                break
            out[i] = struct.unpack("<" + fmt, data[pos:pos + size])[0]
            pos += size
    return out


async def setup_combined(dev: MarioDevice, port: int, entries: list[tuple[int, int]], deltas: dict[int, int],
                         rec: Recorder) -> bool:
    """Come MarioDevice.set_combined_mode ma con un delta per modo; True se Mario non risponde con errore."""
    t0 = time.time()
    await dev.send(lwp3.port_input_format_setup_combined(port, CombinedModeSubcommand.LOCK))
    for mode in sorted({m for m, _ in entries}):
        await dev.send(lwp3.port_input_format_setup(port, mode, deltas.get(mode, 1), True))
    await dev.send(lwp3.port_combination_set(port, 0, entries))
    await dev.send(lwp3.port_input_format_setup_combined(port, CombinedModeSubcommand.UNLOCK_MULTI_UPDATE_ENABLED))
    await asyncio.sleep(1.5)
    errors = [t for _, m, t in rec.since(t0, {MessageType.GENERIC_ERROR})]
    fmt = [t for _, m, t in rec.since(t0, {MessageType.PORT_INPUT_FORMAT_COMBINED})]
    if errors:
        rec.log(f"modalità combinata porta {port} rifiutata: {errors}")
        return False
    rec.log(f"modalità combinata porta {port} attiva: {fmt[-1] if fmt else 'nessuna conferma esplicita'}")
    return True


async def step_pulsante(dev, rec, voice):
    await dev.property_updates(HubProperty.BUTTON, True)
    t0 = time.time()
    speak("Passo uno, pulsante. Premi e rilascia il tasto sulla schiena di Mario. Hai venti secondi.", voice)
    seen = []
    deadline = time.time() + 20
    while time.time() < deadline:
        await asyncio.sleep(0.2)
        seen = [m for _, m, _ in rec.since(t0, {MessageType.HUB_PROPERTIES})
                if m.payload and m.payload[0] == HubProperty.BUTTON and m.payload[1] == HubPropertyOperation.UPDATE]
        states = [bool(m.payload[2]) for m in seen if len(m.payload) > 2]
        if True in states and states and states[-1] is False:
            break
    states = [bool(m.payload[2]) for m in seen if len(m.payload) > 2]
    if True in states and False in states:
        rec.result("pulsante", "OK", f"arrivati {len(states)} aggiornamenti: {states}")
    elif states:
        rec.result("pulsante", "PARZIALE", f"aggiornamenti {states} (manca premuto o rilasciato)")
    else:
        rec.result("pulsante", "NESSUN DATO", "nessun aggiornamento della proprietà BUTTON in 20 s")
    await dev.property_updates(HubProperty.BUTTON, False)


async def step_pantaloni(dev, rec, voice):
    await dev.subscribe(2, 0, 1)
    await asyncio.sleep(0.5)
    t0 = time.time()
    speak("Passo due, pantaloni. Se hai dei pantaloni, cambiali adesso, anche più volte. Hai trenta secondi. "
          "Se non li hai, aspetta.", voice)
    await asyncio.sleep(30)
    vals = []
    for _, m, _ in rec.since(t0, {MessageType.PORT_VALUE_SINGLE}):
        if m.payload and m.payload[0] == 2 and len(m.payload) > 1:
            code = m.payload[1] & 0x3F
            if not vals or vals[-1] != code:
                vals.append(code)
    names = [f"0x{c:02X}={codes.PANTS.get(c, 'SCONOSCIUTO')}" for c in vals]
    unknown = [c for c in vals if c not in codes.PANTS]
    if len(vals) >= 2:
        rec.result("pantaloni", "OK" if not unknown else "CODICI NUOVI",
                   f"sequenza: {', '.join(names)}" + (f"; da aggiungere a codes.PANTS: {unknown}" if unknown else ""))
    elif vals:
        rec.result("pantaloni", "NESSUN CAMBIO", f"solo il valore iniziale {names[0]}")
    else:
        rec.result("pantaloni", "NESSUN DATO", "la porta 2 non ha trasmesso")


async def step_gesti(dev, rec, voice):
    layout = ["b", "b", "b", "h", "h"]  # RAW x,y,z (int8) + GEST (2 x int16)
    combined = await setup_combined(dev, 0, [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)], {0: 4, 1: 1}, rec)
    if not combined:
        await dev.reset_combined_mode(0)
        await dev.subscribe(0, 1, 1)
    speak("Passo tre, gesti. Ti dirò un movimento alla volta.", voice)
    report = []
    unknown_bits_by_task: dict[str, set] = {}
    for task, seconds in GESTURE_TASKS:
        t0 = time.time()
        speak(f"Ora: {task}.", voice)
        await asyncio.sleep(seconds)
        gest, raw = [], []
        for _, m, _ in rec.since(t0, {MessageType.PORT_VALUE_COMBINED, MessageType.PORT_VALUE_SINGLE}):
            if not m.payload or m.payload[0] != 0:
                continue
            if m.msg_type == MessageType.PORT_VALUE_COMBINED:
                v = parse_combined(bytes(m.payload), layout)
                if 3 in v and v[3]:
                    gest.append(v[3] & 0xFFFF)
                if 0 in v and 1 in v and 2 in v:
                    raw.append((v[0], v[1], v[2]))
            elif len(m.payload) >= 3:
                g = le16(bytes(m.payload[1:3]))
                if g:
                    gest.append(g)
        bits = 0
        for g in gest:
            bits |= g
        names = codes.gesture_names(bits) if bits else ["nessun gesto"]
        unknown = bits & ~sum(codes.GESTURES)
        if unknown:
            unknown_bits_by_task[task] = {hex(unknown)}
        raw_txt = ""
        if raw:
            xs, ys, zs = zip(*raw)
            raw_txt = f"; accelerometro x {min(xs)}..{max(xs)} y {min(ys)}..{max(ys)} z {min(zs)}..{max(zs)} ({len(raw)} campioni)"
        line = f"{task}: bit 0x{bits:04X} = {', '.join(names)} ({len(gest)} eventi){raw_txt}"
        report.append(line)
        rec.log("   " + line)
    await dev.reset_combined_mode(0)
    await asyncio.sleep(0.5)
    await dev.subscribe(0, 1, 1)
    rec.result("gesti", "OK" if combined else "SOLO GESTI (combinata rifiutata)",
               "\n      " + "\n      ".join(report) + (f"\n      bit sconosciuti: {unknown_bits_by_task}" if unknown_bits_by_task else ""))
    rec.result("combinata", "OK" if combined else "RIFIUTATA", "porta 0 RAW+GEST" + (" con messaggi 0x46 decodificati" if combined else ""))


async def step_rgb(dev, rec, voice):
    layout = ["h", "h", "B", "B", "B"]  # TAG barcode, colore + RGB
    combined = await setup_combined(dev, 1, [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2)], {0: 1, 1: 2}, rec)
    if not combined:
        await dev.reset_combined_mode(1)
        await dev.subscribe(1, 1, 2)
    speak("Passo quattro, colori. Appoggia Mario su una superficie del colore che dico, finché non ti dico il prossimo.", voice)
    report = []
    state: dict[int, int] = {}  # Mario manda solo i dataset cambiati: teniamo l'ultimo valore di ciascuno
    for color, seconds in COLOR_TASKS:
        t0 = time.time()
        speak(f"Superficie {color}.", voice)
        await asyncio.sleep(seconds)
        rgb, tags = [], []
        for _, m, _ in rec.since(t0, {MessageType.PORT_VALUE_COMBINED, MessageType.PORT_VALUE_SINGLE}):
            if not m.payload or m.payload[0] != 1:
                continue
            if m.msg_type == MessageType.PORT_VALUE_COMBINED:
                state.update(parse_combined(bytes(m.payload), layout))
                if 2 in state:
                    rgb.append((state[2], state.get(3, -1), state.get(4, -1)))
                if 1 in state and (state[1] & 0xFFFF) != 0xFFFF:
                    tags.append(state[1] & 0xFFFF)
            elif len(m.payload) >= 4:
                rgb.append((m.payload[1], m.payload[2], m.payload[3]))
        if rgb:
            rs, gs, bs = zip(*rgb)
            med = (sorted(rs)[len(rs) // 2], sorted(gs)[len(gs) // 2], sorted(bs)[len(bs) // 2])
            tag_txt = ", ".join(f"0x{t:04X}={codes.COLORS.get(t, '?')}" for t in sorted(set(tags))) or "nessun codice colore"
            line = f"{color}: RGB mediana {med}, min ({min(rs)},{min(gs)},{min(bs)}) max ({max(rs)},{max(gs)},{max(bs)}), {len(rgb)} campioni; TAG: {tag_txt}"
        else:
            line = f"{color}: nessun campione RGB"
        report.append(line)
        rec.log("   " + line)
    await dev.reset_combined_mode(1)
    await asyncio.sleep(0.5)
    await dev.subscribe(1, 0, 1)
    rec.result("rgb", "OK" if any("RGB mediana" in r for r in report) else "NESSUN DATO", "\n      " + "\n      ".join(report))


async def step_rssi(dev, rec, voice):
    t0 = time.time()
    try:
        value = await dev.get_property(HubProperty.RSSI)
    except Exception as exc:
        value = f"errore {exc}"
    await dev.property_updates(HubProperty.RSSI, True)
    speak("Passo cinque, segnale radio. Allontana Mario dal computer di qualche metro e poi riavvicinalo. Dieci secondi.", voice)
    await asyncio.sleep(10)
    await dev.property_updates(HubProperty.RSSI, False)
    updates = [m.payload[2] if len(m.payload) > 2 else None for _, m, _ in rec.since(t0, {MessageType.HUB_PROPERTIES})
               if m.payload and m.payload[0] == HubProperty.RSSI and m.payload[1] == HubPropertyOperation.UPDATE]
    vals = [struct.unpack("b", bytes([u]))[0] for u in updates if u is not None]
    rec.result("rssi", "AGGIORNAMENTI" if len(vals) > 1 else "NESSUN AGGIORNAMENTO",
               f"lettura diretta: {value}; aggiornamenti in 10 s: {vals}")


async def step_allarmi(dev, rec, voice):
    speak("Passo sei, allarmi dell'hub. Non devi fare nulla.", voice)
    details = []
    for alert in (HubAlertType.LOW_VOLTAGE, HubAlertType.HIGH_CURRENT, HubAlertType.LOW_SIGNAL_STRENGTH,
                  HubAlertType.OVER_POWER_CONDITION):
        try:
            reply = await dev.alert(alert, HubAlertOperation.REQUEST_UPDATE, timeout=2.0)
            details.append(f"{alert.name}: {dev.describe(reply) if reply else 'nessuna risposta'}")
        except (LWPError, asyncio.TimeoutError) as exc:
            details.append(f"{alert.name}: {exc}")
        try:
            await dev.alert(alert, HubAlertOperation.ENABLE_UPDATES)
        except Exception as exc:
            details.append(f"{alert.name} abilitazione: {exc}")
        await asyncio.sleep(0.3)
    rec.result("allarmi", "RISPONDE" if any(": Allarme" in d or "HUB_ALERTS" in d or "0x00" in d for d in details) else "VEDI DETTAGLI",
               "; ".join(details))


async def step_disconnect(dev, rec, voice):
    speak("Passo sette, disconnessione comandata dal computer. Non toccare Mario.", voice)
    t0 = time.time()
    await dev.hub_action(HubAction.DISCONNECT)
    try:
        await asyncio.wait_for(dev.disconnected.wait(), 10)
        gone = f"scollegato dopo {time.time() - t0:.1f} s"
    except asyncio.TimeoutError:
        gone = "ancora connesso dopo 10 s"
    notices = [t for _, m, t in rec.since(t0, {MessageType.HUB_ACTIONS})]
    rec.result("disconnect", "OK" if "scollegato" in gone and notices else ("SENZA AVVISO" if "scollegato" in gone else "FALLITA"),
               f"{gone}; avvisi: {notices or 'nessun WILL_DISCONNECT'}")


async def step_off(dev, rec, voice):
    speak("Ultimo passo: spengo Mario dal computer.", voice)
    t0 = time.time()
    await dev.hub_action(HubAction.SWITCH_OFF)
    try:
        await asyncio.wait_for(dev.disconnected.wait(), 10)
        gone = f"scollegato dopo {time.time() - t0:.1f} s"
    except asyncio.TimeoutError:
        gone = "ancora connesso dopo 10 s"
    notices = [t for _, m, t in rec.since(t0, {MessageType.HUB_ACTIONS})]
    rec.result("off", "OK" if "scollegato" in gone and notices else ("SENZA AVVISO" if "scollegato" in gone else "FALLITO"),
               f"{gone}; avvisi: {notices or 'nessun WILL_SWITCH_OFF'}; verificare che Mario sia spento")


async def connect(args, rec: Recorder, say) -> MarioDevice:
    dev = MarioDevice(args.address)
    await dev.connect(args.timeout, progress=say)
    await dev.discover(full=False)
    dev.add_listener(rec.on_message)
    rec.dev = dev
    return dev


async def main() -> int:
    ap = argparse.ArgumentParser(description="verifiche dal vivo guidate")
    ap.add_argument("--steps", default=",".join(ALL_STEPS), help="passi separati da virgola")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("-t", "--timeout", type=float, default=180.0)
    ap.add_argument("-a", "--address")
    args = ap.parse_args()
    voice = not args.no_voice
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        print(f"passi sconosciuti: {bad}; disponibili: {ALL_STEPS}", file=sys.stderr)
        return 2
    logs = PROJECT / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"live-checks-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    rec = Recorder(path)
    say = lambda t: print(f"[{time.strftime('%H:%M:%S')}] {t}", file=sys.stderr, flush=True)
    print(f"Accendi Mario e premi il tasto Bluetooth: cerco per {int(args.timeout)} s...", file=sys.stderr, flush=True)
    try:
        dev = await connect(args, rec, say)
    except Exception as exc:
        print(f"Connessione non riuscita: {exc}", file=sys.stderr)
        rec.fh.close()
        return 1
    rec.log(f"connesso; log {path}; passi: {steps}")
    speak("Collegato. Iniziamo le verifiche.", voice)
    handlers = {"pulsante": step_pulsante, "pantaloni": step_pantaloni, "gesti": step_gesti, "rgb": step_rgb,
                "rssi": step_rssi, "allarmi": step_allarmi, "disconnect": step_disconnect, "off": step_off}
    try:
        for step in steps:
            if not dev.connected:
                if step == "off":
                    speak("Mario è scollegato: lo ricollego per spegnerlo. Se l'icona non lampeggia, premi il tasto.", voice)
                else:
                    speak("Mario è scollegato: lo ricollego. Se l'icona non lampeggia, premi il tasto.", voice)
                try:
                    dev = await connect(args, rec, say)
                except Exception as exc:
                    rec.result(step, "SALTATO", f"riconnessione fallita: {exc}")
                    continue
            rec.log(f"=== PASSO: {step}")
            try:
                await handlers[step](dev, rec, voice)
            except Exception as exc:
                rec.result(step, "ERRORE", f"{type(exc).__name__}: {exc}")
        speak("Verifiche finite. Grazie.", voice)
    finally:
        if dev.connected:
            await dev.disconnect()
        lines = ["", "=== RIEPILOGO VERIFICHE ==="]
        for step, outcome, details in rec.results:
            lines.append(f"- {step}: {outcome}. {details}")
        text = "\n".join(lines)
        print(text)
        rec.fh.write(text + "\n")
        rec.fh.close()
        print(f"\nLog: {path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
