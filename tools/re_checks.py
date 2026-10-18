#!/usr/bin/env python3
"""
Verifiche di reverse engineering dal vivo (TODO, sezione 2), guidate a voce dal PC.

Passi (--steps, separati da virgola; default tutti):
  annuncio  prima della connessione: manufacturer data dell'advertisement (tipo di sistema 0x43,
            capacità 0x03 = central + peripheral, ultima rete, stato, opzione)
  gatt      tabella dei servizi GATT letta da bleak alla connessione; il servizio OAD di Texas
            Instruments viene solo elencato, mai letto né scritto
  sistema   proprietà 0x0B (system type) confrontata con il valore dell'advertisement (0x40 contro 0x43)
  sonno     Mario fermo per 45 s: conto alla rovescia 0x3861, 0x3862 = 2, 0x3866 / 0x1802; poi ripreso
            in mano (risveglio)
  porta3    modi CHAL (0), VERS (1) e DEBUG (3) della porta 3, uno alla volta: valore letto e 20 s di
            ascolto con pulsante (pressione breve, registrata nel log), basetta e sollevamento; alla fine la
            porta torna in EVENTS (2). --port3-modes DEBUG ripete un solo modo. Il 14/10 il modo DEBUG ha
            mandato una traccia del firmware (testo "LEAF 0x65010000 ...") e Mario si è spento a metà
  gioco     porte 3 (EVENTS), 4 (Events) e 7 (Telemetry) sottoscritte insieme a scanner, gesti e
            pantaloni durante un mini livello guidato (Start, salti, nemico, monete, fine del tempo):
            sequenza della porta 3 con gli orari e conteggio di quanto arriva dalle porte 4 e 7

Tutto finisce in logs/re-checks-<data>.txt, nel formato che `./run.sh decode` rilegge. Nessun comando
distruttivo: niente azione 0x2F, niente OAD, solo letture e sottoscrizioni.

Uso (sull'host):
  ./run.sh re-checks                        # tutti i passi (circa 6 minuti con 'gioco')
  ./run.sh re-checks --steps sonno,porta3   # solo alcuni
  ./run.sh re-checks --steps porta3 --port3-modes DEBUG
  ./run.sh re-checks --no-voice
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mario import codes, lwp3  # noqa: E402
from mario.device import LWPError, MarioDevice  # noqa: E402
from mario.lwp3 import HubProperty, MessageType  # noqa: E402

PROJECT = Path(__file__).resolve().parent.parent
ALL_STEPS = ["annuncio", "gatt", "sistema", "sonno", "porta3", "gioco"]
PORT3_MODES = [(0, "CHAL"), (1, "VERS"), (3, "DEBUG")]
GAME_PORTS = [(3, 2), (4, 0), (7, 0), (1, 0), (0, 1), (2, 0)]
GAME_TASKS = [
    ("Appoggia Mario sulla basetta Start e lascialo lì", 15),
    ("Fallo saltare tre volte", 10),
    ("Appoggialo su un nemico, per esempio il Goomba", 12),
    ("Appoggialo su una basetta che dà monete", 12),
    ("Se hai altri pantaloni, cambiali adesso; altrimenti aspetta", 15),
    ("Ora lascialo fermo sul tavolo finché il livello finisce, circa un minuto", 75),
]
ADV_CAPABILITIES = {0x01: "central", 0x02: "peripheral", 0x04: "connettori LPF2", 0x08: "telecomando"}


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
        self.results: list[tuple[str, str, str]] = []
        self.adv_system_type: int | None = None
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
        if msg.msg_type in (MessageType.GENERIC_ERROR, MessageType.HUB_ACTIONS, MessageType.HUB_ALERTS):
            print(f"    <- {text}", flush=True)
        elif msg.msg_type == MessageType.PORT_VALUE_SINGLE and msg.payload and msg.payload[0] in (3, 4, 7):
            print(f"    <- {text[:120]}", flush=True)

    def port_values(self, t0: float, port: int) -> list[tuple[float, bytes]]:
        return [(t, bytes(m.payload[1:])) for t, m, _ in self.messages
                if t >= t0 and m.msg_type == MessageType.PORT_VALUE_SINGLE and m.payload and m.payload[0] == port]

    def port_events(self, t0: float, port: int = 3) -> list[tuple[float, int, int]]:
        """Eventi [codice LE16][valore LE16] della porta 3 (o di una porta con lo stesso formato)."""
        return [(t, int.from_bytes(raw[0:2], "little"), int.from_bytes(raw[2:4], "little"))
                for t, raw in self.port_values(t0, port) if len(raw) >= 4]

    def result(self, step: str, outcome: str, details: str) -> None:
        self.results.append((step, outcome, details))
        self.log(f"ESITO {step}: {outcome}. {details}")


def fmt_events(events: list[tuple[float, int, int]], t0: float) -> str:
    return ", ".join(f"{t - t0:.0f}s 0x{code:04X}={value}" for t, code, value in events) or "nessuno"


# ----- passi ---------------------------------------------------------------------------

async def step_annuncio(args, rec: Recorder, voice: bool) -> None:
    """Prima della connessione: legge il manufacturer data dell'advertisement."""
    speak("Accendi Mario e premi il tasto Bluetooth: leggo l'annuncio.", voice)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        found = await MarioDevice.scan(5.0)
        for device, adv in found:
            if args.address and device.address.lower() != args.address.lower():
                continue
            data = adv.manufacturer_data.get(lwp3.LEGO_COMPANY_ID) or b""
            rec.log(f"advertisement: {device.address} RSSI {adv.rssi} dBm, UUID {adv.service_uuids}, "
                    f"manufacturer data {data.hex(' ') or '-'}")
            if len(data) >= 6:
                caps = ", ".join(name for bit, name in ADV_CAPABILITIES.items() if data[2] & bit) or "-"
                rec.adv_system_type = data[1]
                rec.result("annuncio", "OK",
                           f"pulsante {'premuto' if data[0] else 'rilasciato'}; tipo di sistema 0x{data[1]:02X} "
                           f"({lwp3.SYSTEM_TYPE_NAMES.get(data[1], '?')}); capacità 0x{data[2]:02X} ({caps}); "
                           f"ultima rete 0x{data[3]:02X}; stato 0x{data[4]:02X}; opzione 0x{data[5]:02X}")
            else:
                rec.result("annuncio", "PARZIALE", f"manufacturer data corto o assente: {data.hex(' ') or '-'}")
            return
    rec.result("annuncio", "SALTATO", "Mario non visto in scansione")


async def step_gatt(dev: MarioDevice, rec: Recorder, voice: bool) -> None:
    for line in dev.gatt_lines():
        rec.log(line)
    oad = [s for s in dev.gatt if s["uuid"] == lwp3.OAD_SERVICE_UUID]
    unknown = [s["uuid"] for s in dev.gatt if lwp3.gatt_name(s["uuid"]) == "?"]
    details = f"{len(dev.gatt)} servizi"
    details += (f"; OAD presente con {len(oad[0]['characteristics'])} caratteristiche "
                f"({', '.join(c['uuid'][4:8] + '[' + '/'.join(c['properties']) + ']' for c in oad[0]['characteristics'])})"
                if oad else "; OAD assente")
    if unknown:
        details += f"; servizi senza nome: {unknown}"
    rec.result("gatt", "OK" if dev.gatt else "VUOTO", details)


async def step_sistema(dev: MarioDevice, rec: Recorder, voice: bool) -> None:
    raw = await dev.get_property_raw(HubProperty.SYSTEM_TYPE_ID)
    prop = raw[0] if raw else None
    adv = rec.adv_system_type
    adv_text = f"0x{adv:02X}" if adv is not None else "non letto (passo 'annuncio' saltato)"
    prop_text = f"0x{prop:02X} ({lwp3.SYSTEM_TYPE_NAMES.get(prop, '?')})" if prop is not None else "vuota"
    rec.result("sistema", "OK", f"proprietà 0x0B = {prop_text}; advertisement = {adv_text}")


async def step_sonno(dev: MarioDevice, rec: Recorder, voice: bool) -> None:
    await dev.subscribe(3, 2, 1, True)
    await dev.subscribe(0, 1, 1, True)
    speak("Appoggia Mario sul tavolo, in piedi, e non toccarlo per quarantacinque secondi. Ti dico io quando riprenderlo.", voice)
    t0 = time.time()
    await asyncio.sleep(45)
    still = rec.port_events(t0, 3)
    speak("Ora prendilo in mano e muovilo un po'.", voice)
    t1 = time.time()
    await asyncio.sleep(10)
    wake = rec.port_events(t1, 3)
    rec.result("sonno", "OK" if still or wake else "NIENTE",
               f"da fermo: {fmt_events(still, t0)} | ripreso in mano: {fmt_events(wake, t0)}")


async def step_porta3(dev: MarioDevice, rec: Recorder, voice: bool, modes=PORT3_MODES) -> None:
    try:
        await dev.property_updates(HubProperty.BUTTON, True)  # le pressioni del pulsante finiscono nel log
    except Exception as exc:
        rec.log(f"aggiornamenti del pulsante non attivati: {exc}")
    outcomes = []
    for mode, name in modes:
        try:
            await dev.subscribe(3, mode, 1, True)
        except (LWPError, asyncio.TimeoutError) as exc:
            outcomes.append(f"{name}: sottoscrizione rifiutata ({exc})")
            continue
        try:
            value = await dev.request_value(3)
            rec.log(f"porta 3 modo {mode} {name}: valore letto {value.hex(' ')}")
        except (LWPError, asyncio.TimeoutError) as exc:
            value = None
            rec.log(f"porta 3 modo {mode} {name}: lettura fallita ({exc})")
        # Pressione breve: tenuto premuto, il pulsante spegne Mario (il 14/10 si è spento durante DEBUG).
        speak(f"Modo {name} attivo. Premi brevemente il pulsante di Mario, poi appoggialo su una basetta, "
              "poi sollevalo.", voice)
        t0 = time.time()
        await asyncio.sleep(20)
        vals = rec.port_values(t0, 3)
        sample = ", ".join(raw.hex(" ") for _, raw in vals[:6]) + ("…" if len(vals) > 6 else "")
        text = codes.printable_runs(b"".join(raw for _, raw in vals), minimum=6)
        outcomes.append(f"{name}: letto {value.hex(' ') if value else '-'}; {len(vals)} notifiche"
                        + (f" ({sample})" if vals else "") + (f"; testo {text}" if text else ""))
        if not dev.connected:
            rec.result("porta3", "INTERROTTO", " | ".join(outcomes) + f" | Mario si è scollegato durante il modo {name}")
            return
    await dev.subscribe(3, 2, 1, True)
    rec.log("porta 3 di nuovo in modo 2 EVENTS")
    rec.result("porta3", "OK", " | ".join(outcomes))


async def step_gioco(dev: MarioDevice, rec: Recorder, voice: bool) -> None:
    for port, mode in GAME_PORTS:
        try:
            await dev.subscribe(port, mode, 1, True)
        except (LWPError, asyncio.TimeoutError) as exc:
            rec.log(f"porta {port} modo {mode}: sottoscrizione fallita ({exc})")
    t_start = time.time()
    speak("Giochiamo un piccolo livello. Tieni pronte le basette.", voice)
    for text, seconds in GAME_TASKS:
        speak(text, voice)
        rec.log(f"-- {text} ({seconds} s)")
        t0 = time.time()
        await asyncio.sleep(seconds)
        rec.log(f"   eventi porta 3: {fmt_events(rec.port_events(t0, 3), t0)}")
    p3, p4, p7 = rec.port_events(t_start, 3), rec.port_values(t_start, 4), rec.port_values(t_start, 7)
    details = f"porta 3: {len(p3)} eventi; porta 4: {len(p4)} notifiche; porta 7: {len(p7)} notifiche"
    if p4:
        details += f"; porta 4 = {[raw.hex(' ') for _, raw in p4[:5]]}"
    if p7:
        details += f"; porta 7 = {[raw.hex(' ') for _, raw in p7[:5]]}"
    rec.result("gioco", "OK", details)


# ----- programma -----------------------------------------------------------------------

async def connect(args, rec: Recorder, say) -> MarioDevice:
    dev = MarioDevice(args.address)
    await dev.connect(args.timeout, progress=say)
    await dev.discover(full=False)
    dev.add_listener(rec.on_message)
    rec.dev = dev
    return dev


async def main() -> int:
    ap = argparse.ArgumentParser(description="verifiche di reverse engineering dal vivo (TODO, sezione 2)")
    ap.add_argument("--steps", default=",".join(ALL_STEPS), help="passi separati da virgola")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("-t", "--timeout", type=float, default=180.0)
    ap.add_argument("-a", "--address")
    ap.add_argument("--port3-modes", default=",".join(name for _, name in PORT3_MODES),
                    help="modi della porta 3 per il passo porta3, separati da virgola (CHAL, VERS, DEBUG)")
    args = ap.parse_args()
    voice = not args.no_voice
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        print(f"passi sconosciuti: {bad}; disponibili: {ALL_STEPS}", file=sys.stderr)
        return 2
    wanted = {m.strip().upper() for m in args.port3_modes.split(",") if m.strip()}
    port3_modes = [(mode, name) for mode, name in PORT3_MODES if name in wanted]
    if not port3_modes or len(port3_modes) != len(wanted):
        print(f"modi della porta 3 sconosciuti: {args.port3_modes}; disponibili: CHAL, VERS, DEBUG", file=sys.stderr)
        return 2
    logs = PROJECT / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"re-checks-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    rec = Recorder(path)
    say = lambda t: print(f"[{time.strftime('%H:%M:%S')}] {t}", file=sys.stderr, flush=True)
    rec.log(f"log {path}; passi: {steps}")
    if "annuncio" in steps:
        await step_annuncio(args, rec, voice)
        steps.remove("annuncio")
        if not steps:
            rec.fh.close()
            return 0
    print(f"Se Mario non lampeggia più, premi di nuovo il tasto Bluetooth: cerco per {int(args.timeout)} s...",
          file=sys.stderr, flush=True)
    try:
        dev = await connect(args, rec, say)
    except Exception as exc:
        print(f"Connessione non riuscita: {exc}", file=sys.stderr)
        rec.fh.close()
        return 1
    speak("Collegato. Iniziamo.", voice)
    handlers = {"gatt": step_gatt, "sistema": step_sistema, "sonno": step_sonno,
                "porta3": functools.partial(step_porta3, modes=port3_modes), "gioco": step_gioco}
    try:
        for step in steps:
            if not dev.connected:
                speak("Mario è scollegato: lo ricollego. Se l'icona non lampeggia, premi il tasto.", voice)
                await dev.disconnect()  # chiude il vecchio client: altrimenti ogni messaggio arriva due volte
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
