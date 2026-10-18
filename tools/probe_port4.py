#!/usr/bin/env python3
"""
Prova a scatola nera: eventi "riflessi" sulla porta 4 di LEGO Mario (TODO, sezione 7).

Ipotesi: la porta 4 ("Events", 2 x int16, ingresso E uscita) accetta gli stessi codici che la
porta 3 (EVENTS) emette durante il gioco; se è così, rimandarli a Mario potrebbe far partire
una clip sonora o un'animazione del display. Lo script:

  1. si connette, attiva le notifiche delle porte 3, 4 e 7 e porta il volume a 100;
  2. per ogni prova annuncia a voce dal PC "Prova N: ..." (così chi guarda Mario sa cosa
     sta succedendo), invia un Port Output Command sulla porta 4 e per 3 secondi registra
     tutto ciò che Mario risponde (feedback 0x82, errori 0x05, valori di porta);
  3. alla fine prova l'indicazione "occupato" (azioni hub 0x05 e 0x06), riporta il volume
     al valore iniziale e si scollega.

Tutto finisce in logs/probe-port4-<data>.txt. Chi osserva annota, prova per prova, cosa
ha fatto Mario (suono? display? niente?). Nessun comando distruttivo: niente 0x2F, niente OAD.

Risultati del 12/10/2026 07:34 (fw 6.5.01): WriteDirectModeData (0x51) accettato per tutti i codici
con feedback "comando completato"; WriteDirect (0x50) -> INVALID_USE; sottocomando 0x00 -> COMMAND_NOT_RECOGNIZED;
azioni hub 0x05/0x06 -> INVALID_USE (Mario non ha l'indicazione "occupato"). Nessun eco sulle porte 3, 4, 7.

Uso (sull'host, con il venv del progetto):
  .venv/bin/python tools/probe_port4.py [--quick] [--no-voice] [--readback]
  .venv/bin/python tools/probe_port4.py --in-level      # con Mario sulla basetta Start: monete e Goomba
  .venv/bin/python tools/probe_port4.py --port7         # 37 sequenze sulla porta 7 (Telemetry) con rilettura
  .venv/bin/python tools/probe_port4.py --baseline 180  # nessuna scrittura: Mario fermo, tempo scandito a voce
  .venv/bin/python tools/probe_port4.py --port7 --only 15-25 --times 3   # ripete solo alcune prove
  .venv/bin/python tools/probe_port4.py --scan          # sottocomandi 0x01 0x02 0x05-0x08 su porta 4 e 7 (36 prove)
  .venv/bin/python tools/probe_port4.py --sweep --no-voice --ports 4   # mappa di tutti i sottocomandi 0x00-0xFF

Con --scan e --sweep il riepilogo finale (stampato e in fondo al log) è una mappa degli esiti per porta:
errore LWP3 (COMMAND_NOT_RECOGNIZED, INVALID_USE, ...), feedback 0x82 o silenzio, più i valori spontanei
delle porte 3, 4 e 7 arrivati subito dopo la scrittura.

Risultato della prova --scan (18/10 16:32, firmware 6.5.03): i sottocomandi 0x01, 0x02, 0x05, 0x06, 0x07 e 0x08,
senza payload, con un byte zero e con la forma dello standard, rispondono tutti COMMAND_NOT_RECOGNIZED sia sulla
porta 4 sia sulla porta 7, senza valori spontanei delle porte 3, 4 e 7 e senza reazioni visibili di Mario.
Risultato della prova --port7 (12/10 17:39): 37 scritture WriteDirectModeData sulla porta 7 senza alcun feedback
0x82, senza errori e senza effetto (rilettura sempre 16 zeri): la porta 7 ignora le scritture in silenzio.
Risultato della prova --in-level (12/10 17:32): la porta 4 non conserva il valore scritto (rilettura sempre
00 00 00 00), nessun eco, il contatore monete (0x3861) della porta 3 non si muove; gli eventi della porta 3
visti durante la prova sono il normale ciclo del livello (vedi codes.GAME_EVENTS).
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
from mario.device import LWPError, MarioDevice  # noqa: E402
from mario.lwp3 import HubAction, MessageType  # noqa: E402

# (descrizione, codice evento come visto sulla porta 3, valore)
EVENT_CODES = [
    ("basetta Start Mario letta: codice 0x0113, valore 0xB8", 0x0113, 0xB8),
    ("basetta Goomba letta: codice 0x0113, valore 0x02", 0x0113, 0x02),
    ("contatore monete uno: codice 0x3861", 0x3861, 1),
    ("contatore monete cinque: codice 0x3861", 0x3861, 5),
    ("codice 0x3004 valore 601", 0x3004, 601),
    ("codice 0x3803 valore 100", 0x3803, 100),
    ("codice 0x200E valore 2", 0x200E, 2),
    ("codice 0x1802 valore 1", 0x1802, 1),
    ("codice 0x1801 valore 1", 0x1801, 1),
    ("codice 0x1803 valore 2", 0x1803, 2),
    ("codice 0x2009 valore 1", 0x2009, 1),
    ("codice 0x200C valore 2", 0x200C, 2),
    ("codice 0x3812 valore 1", 0x3812, 1),
    ("codice 0x3857 valore 1", 0x3857, 1),
    ("codice 0x3862 valore 2", 0x3862, 2),
    ("codice 0x3866 valore 1", 0x3866, 1),
    ("codice 0x386B valore 3", 0x386B, 3),
    ("codice 0x386E valore 0", 0x386E, 0),
    ("codice 0x3852 valore 0", 0x3852, 0),
]
# codici per la prova "in livello" (dopo la basetta Start): monete crescenti e un nemico
IN_LEVEL_CODES = [
    ("contatore monete uno", 0x3861, 1), ("contatore monete due", 0x3861, 2), ("contatore monete tre", 0x3861, 3),
    ("contatore monete dieci", 0x3861, 10), ("basetta Goomba letta: codice 0x0113, valore 0x02", 0x0113, 0x02),
    ("codice 0x3004 valore 601", 0x3004, 601), ("codice 0x3803 valore 100", 0x3803, 100),
    ("codice 0x386B valore 5", 0x386B, 5), ("codice 0x1803 valore 4", 0x1803, 4),
    ("basetta Traguardo letta: codice 0x0113, valore 0xB7", 0x0113, 0xB7),
]
# sequenze per la porta 7 (Telemetry, 16 x int8): valori semplici, un byte alla volta, poi tutti
PORT7_PATTERNS = [("tutti zero", bytes(16)), ("tutti 255", bytes([0xFF] * 16)), ("tutti 1", bytes([1] * 16))] + \
    [(f"byte {i} a 1", bytes([1 if j == i else 0 for j in range(16)])) for i in range(16)] + \
    [(f"byte {i} a 255", bytes([0xFF if j == i else 0 for j in range(16)])) for i in range(16)] + \
    [("sequenza 0..15", bytes(range(16))), ("sequenza 15..0", bytes(range(15, -1, -1)))]
# sottocomandi del Port Output Command da provare; per quelli non standard solo i primi codici
SUBCOMMANDS = [(0x51, "WriteDirectModeData", None), (0x50, "WriteDirect", 4), (0x00, "sottocomando zero", 4)]
WINDOW = 3.0  # secondi di ascolto dopo ogni invio
# --scan: i sottocomandi dei motori dello standard LWP3 sulle porte 4 e 7, ciascuno con tre payload corti
# (vuoto, un byte zero, la forma prevista dallo standard con valori moderati)
SCAN_PORTS = (4, 7)
SCAN_PAYLOADS = {
    0x01: bytes([50]),              # StartPower(Power = 50 %)
    0x02: bytes([50, 50]),          # StartPower(Power1, Power2)
    0x05: bytes([100, 0, 0]),       # SetAccTime(Time = 100 ms, ProfileNo = 0)
    0x06: bytes([100, 0, 0]),       # SetDecTime(Time = 100 ms, ProfileNo = 0)
    0x07: bytes([50, 100, 0]),      # StartSpeed(Speed = 50, MaxPower = 100, UseProfile = 0)
    0x08: bytes([50, 50, 100, 0]),  # StartSpeed(Speed1, Speed2, MaxPower, UseProfile)
}
# --sweep: tutti i sottocomandi 0x00-0xFF con un byte zero; si passa al successivo appena Mario risponde
SWEEP_ANSWER = 1.0  # secondi massimi di attesa della risposta (errore o feedback)
SWEEP_SETTLE = 0.2  # secondi di ascolto dopo la risposta, per eventuali echi
LEAD_IN = 3.0  # secondi fra l'annuncio "inizio" e la prima prova di --scan/--sweep


def scan_plan(ports, subcommands) -> list[tuple[int, int, bytes, str]]:
    """Le prove di --scan: (porta, sottocomando, payload, descrizione del payload)."""
    plan = []
    for port in ports:
        for sub in subcommands:
            variants = [(b"", "senza payload"), (bytes([0]), "un byte zero")]
            shaped = SCAN_PAYLOADS.get(sub)
            if shaped and shaped not in (b"", bytes([0])):
                variants.append((shaped, f"payload dello standard {shaped.hex(' ')}"))
            plan += [(port, sub, payload, what) for payload, what in variants]
    return plan


def outcome(msgs: list[lwp3.Message], port: int) -> str:
    """Esito di una prova dai messaggi arrivati nella finestra: errore LWP3, feedback 0x82 della porta o
    silenzio, più gli eventuali valori spontanei delle porte 3, 4 e 7 (un'eco della scrittura)."""
    result = "silenzio"
    for msg in msgs:
        p = msg.payload
        if msg.msg_type == MessageType.GENERIC_ERROR and len(p) >= 2 and p[0] == MessageType.PORT_OUTPUT_COMMAND:
            result = lwp3.enum_name(lwp3.ErrorCode, p[1])
            break
        if msg.msg_type == MessageType.PORT_OUTPUT_COMMAND_FEEDBACK:
            hit = [p[i + 1] for i in range(0, len(p) - 1, 2) if p[i] == port]
            if hit:
                result = f"feedback 0x{hit[0]:02X}"
                break
    echoes = sorted({msg.payload[0] for msg in msgs
                     if msg.msg_type == MessageType.PORT_VALUE_SINGLE and msg.payload and msg.payload[0] in (3, 4, 7)})
    return result + "".join(f" + valore porta {e}" for e in echoes)


def compress(results: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """[(sottocomando, esito)] in ordine -> intervalli consecutivi con lo stesso esito [(da, a, esito)]."""
    ranges: list[tuple[int, int, str]] = []
    for sub, text in results:
        if ranges and ranges[-1][2] == text and ranges[-1][1] == sub - 1:
            ranges[-1] = (ranges[-1][0], sub, text)
        else:
            ranges.append((sub, sub, text))
    return ranges


def parse_hex_list(text: str) -> list[int]:
    """'01,02,0x05' -> [1, 2, 5] (valori esadecimali, con o senza 0x)."""
    return [int(part, 16) for part in text.replace(" ", "").split(",") if part]


def speak(text: str, enabled: bool) -> None:
    if enabled and shutil.which("spd-say"):
        subprocess.run(["spd-say", "-w", "-l", "it", "-r", "10", "--", text], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=20)


def number_words(n: int) -> str:
    return str(n)


class Recorder:
    def __init__(self, dev: MarioDevice, path: Path):
        self.dev = dev
        self.path = path
        self.fh = open(path, "a", encoding="utf-8")
        self.window: list[str] = []
        self.msgs: list[lwp3.Message] = []
        self.answered = asyncio.Event()  # errore o feedback del Port Output Command nella finestra
        self.capturing = False

    def log(self, text: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def on_message(self, msg: lwp3.Message, text: str) -> None:
        line = f"<- {text}   [{msg.raw.hex(' ')}]"
        self.fh.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
        self.fh.flush()
        if self.capturing:
            self.window.append(line)
            self.msgs.append(msg)
            if msg.msg_type == MessageType.PORT_OUTPUT_COMMAND_FEEDBACK or (
                    msg.msg_type == MessageType.GENERIC_ERROR and msg.payload[:1] == bytes([MessageType.PORT_OUTPUT_COMMAND])):
                self.answered.set()
        if msg.msg_type in (MessageType.GENERIC_ERROR, MessageType.PORT_OUTPUT_COMMAND_FEEDBACK) or \
                (msg.msg_type == MessageType.PORT_VALUE_SINGLE and msg.payload and msg.payload[0] in (3, 4, 7)):
            print(f"    {line}", flush=True)


async def probe(dev: MarioDevice, rec: Recorder, number: int, title: str, say_text: str, send, voice: bool,
                answer_within: float | None = None) -> list[str]:
    """Una prova: annuncio, invio, ascolto. Con `answer_within` l'ascolto finisce poco dopo la prima
    risposta al Port Output Command (o allo scadere), altrimenti dura WINDOW secondi."""
    rec.log(f"=== PROVA {number}: {title}")
    if say_text:
        speak(f"Prova {number}. {say_text}", voice)
    rec.window, rec.msgs, rec.capturing = [], [], True
    rec.answered.clear()
    try:
        sent = await send()
        rec.log(f"-> inviato {sent.hex(' ') if isinstance(sent, (bytes, bytearray)) else sent}")
    except LWPError as exc:
        rec.log(f"-> Mario ha risposto con errore immediato: {exc}")
    except Exception as exc:
        rec.log(f"-> invio fallito: {exc}")
    if answer_within is None:
        await asyncio.sleep(WINDOW)
    else:
        try:
            await asyncio.wait_for(rec.answered.wait(), answer_within)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(SWEEP_SETTLE)
    rec.capturing = False
    if not rec.window:
        rec.log("   nessuna risposta da Mario nella finestra")
    return list(rec.window)


def is_rejected(msgs: list[lwp3.Message]) -> bool:
    """Mario ha risposto con un errore generico al Port Output Command."""
    return any(msg.msg_type == MessageType.GENERIC_ERROR and msg.payload[:1] == bytes([MessageType.PORT_OUTPUT_COMMAND])
               for msg in msgs)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--quick", action="store_true", help="solo i primi 4 codici per sottocomando")
    ap.add_argument("--no-voice", action="store_true", help="niente annunci vocali dal PC")
    ap.add_argument("-t", "--timeout", type=float, default=180.0, help="secondi di attesa per trovare Mario")
    ap.add_argument("-a", "--address")
    ap.add_argument("--in-level", action="store_true",
                    help="aspetta che Mario legga la basetta Start (livello in corso), poi invia solo monete e "
                         "Goomba ripetuti, per vedere se il contatore sul display cambia")
    ap.add_argument("--readback", action="store_true", help="dopo ogni scrittura rilegge il valore della porta 4")
    ap.add_argument("--port7", action="store_true",
                    help="prova la porta 7 (Telemetry, 16 byte): sequenze semplici con WriteDirectModeData e rilettura")
    ap.add_argument("--baseline", type=float, metavar="SECONDI",
                    help="nessuna scrittura: osserva Mario fermo per N secondi, annunciando il tempo ogni 30 s")
    ap.add_argument("--only", metavar="DA-A", help="esegue solo le prove dalla DA alla A (numeri della lista attiva)")
    ap.add_argument("--times", type=int, default=1, help="ripete ogni scrittura N volte (default 1)")
    ap.add_argument("--pause", type=float, default=WINDOW, help=f"secondi di ascolto dopo ogni invio (default {WINDOW})")
    ap.add_argument("--scan", action="store_true",
                    help="scansione dei sottocomandi dei motori (0x01 0x02 0x05-0x08) sulle porte 4 e 7, "
                         "con tre payload corti ciascuno, una prova annunciata alla volta")
    ap.add_argument("--sweep", action="store_true",
                    help="mappa di tutti i sottocomandi 0x00-0xFF (un byte zero) sulle porte scelte: niente annuncio "
                         "per prova, solo a ogni blocco di 16; si passa avanti appena Mario risponde")
    ap.add_argument("--ports", default=",".join(map(str, SCAN_PORTS)), help="porte per --scan/--sweep (default 4,7)")
    ap.add_argument("--subs", default=",".join(f"{s:02X}" for s in SCAN_PAYLOADS),
                    help="sottocomandi esadecimali per --scan (default 01,02,05,06,07,08)")
    args = ap.parse_args()
    globals()["WINDOW"] = args.pause
    only = None
    if args.only:
        a, _, b = args.only.partition("-")
        only = (int(a), int(b or a))
    voice = not args.no_voice

    logs = Path(__file__).resolve().parent.parent / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"probe-port4-{time.strftime('%Y%m%d-%H%M%S')}.txt"

    dev = MarioDevice(args.address)
    print("Accendi Mario e premi il tasto Bluetooth sulla schiena: cerco per "
          f"{int(args.timeout)} secondi...", file=sys.stderr, flush=True)
    await dev.connect(args.timeout, progress=lambda t: print(f"[{time.strftime('%H:%M:%S')}] {t}", file=sys.stderr, flush=True))
    rec = Recorder(dev, path)
    rec.log(f"Connesso a {dev.ble_device.name if dev.ble_device else dev.address}; log in {path}")
    dev.add_listener(rec.on_message)
    original_volume = None
    summary: list[tuple[int, str, list[str]]] = []
    try:
        await dev.discover(full=False)
        for port, mode in ((3, 2), (4, 0), (7, 0)):
            try:
                await dev.subscribe(port, mode, 1)
                rec.log(f"notifiche attive: porta {port} modo {mode}")
            except Exception as exc:
                rec.log(f"porta {port} modo {mode}: notifiche non attivate ({exc})")
        try:
            original_volume = await dev.get_volume()
            await dev.set_volume(100)
            rec.log(f"volume: era {original_volume}, ora 100")
        except Exception as exc:
            rec.log(f"volume non impostabile: {exc}")

        if args.baseline:
            await baseline(dev, rec, args.baseline, voice)
            return 0
        if args.scan or args.sweep:
            ports = [int(p) for p in args.ports.split(",") if p.strip()]
            extra = await run_scan(dev, rec, ports, parse_hex_list(args.subs), args, voice, summary)
            speak("Fine delle prove. Grazie.", voice)
            return_summary(summary, path, extra)
            return 0
        if args.in_level:
            await wait_for_start_tile(dev, rec, voice)
        speak("Inizio tra tre secondi. Guarda e ascolta Mario.", voice)
        await asyncio.sleep(3)
        n = 0
        if args.port7:
            for desc, payload in PORT7_PATTERNS:
                n += 1
                if only and not (only[0] <= n <= only[1]):
                    continue

                async def send7(payload=payload):
                    frame = lwp3.port_output_command(7, 0x51, bytes([0]) + payload, 0x1, 0x1)
                    await dev.send(frame)
                    await asyncio.sleep(0.4)
                    try:
                        value = await dev.request_value(7, timeout=1.5)
                        rec.log(f"   rilettura porta 7: {value.hex(' ')}")
                    except Exception as exc:
                        rec.log(f"   rilettura porta 7 fallita: {exc}")
                    return frame

                for rep in range(args.times):
                    label = f"porta 7, WriteDirectModeData, {desc}" + (f" (ripetizione {rep + 1}/{args.times})" if args.times > 1 else "")
                    responses = await probe(dev, rec, n, label, desc, send7, voice)
                    summary.append((n, label, responses))
                if is_rejected(rec.msgs):
                    rec.log("   la porta 7 rifiuta WriteDirectModeData: mi fermo")
                    break
            speak("Fine delle prove. Grazie.", voice)
            return_summary(summary, path)
            return 0
        plan = SUBCOMMANDS
        if args.in_level:
            plan = [(0x51, "WriteDirectModeData", None)]
        for sub, sub_name, limit in plan:
            codes_to_try = EVENT_CODES[: (4 if args.quick else (limit or len(EVENT_CODES)))]
            if args.in_level:
                codes_to_try = IN_LEVEL_CODES
            rejected = False
            for desc, code, value in codes_to_try:
                n += 1
                if only and not (only[0] <= n <= only[1]):
                    continue
                data = code.to_bytes(2, "little") + (value & 0xFFFF).to_bytes(2, "little")
                payload = (bytes([0]) + data) if sub == 0x51 else data

                async def send(sub=sub, payload=payload):
                    frame = lwp3.port_output_command(4, sub, payload, 0x1, 0x1)
                    await dev.send(frame)
                    if args.readback or args.in_level:
                        await asyncio.sleep(0.4)
                        try:
                            value = await dev.request_value(4, timeout=1.5)
                            rec.log(f"   rilettura porta 4: {value.hex(' ')}")
                        except Exception as exc:
                            rec.log(f"   rilettura porta 4 fallita: {exc}")
                    return frame

                title = f"porta 4, {sub_name} (0x{sub:02X}), {desc}"
                say_text = desc.split(":")[0]
                for rep in range(args.times):
                    label = title + (f" (ripetizione {rep + 1}/{args.times})" if args.times > 1 else "")
                    responses = await probe(dev, rec, n, label, say_text, send, voice)
                    summary.append((n, label, responses))
                if is_rejected(rec.msgs):
                    rec.log(f"   il sottocomando 0x{sub:02X} viene rifiutato da Mario: passo al prossimo")
                    speak("Comando rifiutato, passo al prossimo.", voice)
                    rejected = True
                    break
            if rejected:
                continue

        if args.in_level:
            speak("Fine delle prove. Grazie.", voice)
            return_summary(summary, path)
            return 0
        n += 1
        responses = await probe(dev, rec, n, "azione hub ACTIVATE_BUSY (0x05)", "indicazione occupato accesa",
                                lambda: _action(dev, HubAction.ACTIVATE_BUSY), voice)
        summary.append((n, "azione hub ACTIVATE_BUSY (0x05)", responses))
        await asyncio.sleep(3)
        n += 1
        responses = await probe(dev, rec, n, "azione hub RESET_BUSY (0x06)", "indicazione occupato spenta",
                                lambda: _action(dev, HubAction.RESET_BUSY), voice)
        summary.append((n, "azione hub RESET_BUSY (0x06)", responses))
        speak("Fine delle prove. Grazie.", voice)
    finally:
        if original_volume is not None:
            try:
                await dev.set_volume(original_volume)
                rec.log(f"volume riportato a {original_volume}")
            except Exception as exc:
                rec.log(f"volume non ripristinato: {exc}")
        await dev.disconnect()
        rec.log("disconnesso")
        rec.fh.close()

    return_summary(summary, path)
    return 0


def return_summary(summary, path, extra: list[str] | None = None) -> None:
    """Stampa il riepilogo e lo aggiunge in fondo al log (le righe `extra` sono la mappa di --scan/--sweep)."""
    lines = ["", "=== RIEPILOGO (da affiancare a ciò che hai visto e sentito su Mario) ==="]
    for number, title, responses in summary:
        short = "; ".join(r.split("   [")[0].replace("<- ", "") for r in responses) or "nessuna risposta"
        lines.append(f"{number:>2}. {title}\n    risposta: {short}")
    lines += extra or []
    print("\n".join(lines))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nLog completo: {path}")


async def run_scan(dev: MarioDevice, rec: Recorder, ports: list[int], subs: list[int], args, voice: bool,
                   summary: list) -> list[str]:
    """--scan: ogni sottocomando con i payload di scan_plan, annunciato e osservato per WINDOW secondi;
    --sweep: tutti i sottocomandi 0x00-0xFF con un byte zero, a ritmo della risposta di Mario. In entrambi
    i casi, dopo ogni invio, la rilettura facoltativa (--readback) e alla fine la mappa degli esiti."""
    async def send(port: int, sub: int, payload: bytes) -> bytes:
        frame = lwp3.port_output_command(port, sub, payload, 0x1, 0x1)
        await dev.send(frame)
        if args.readback:
            await asyncio.sleep(0.4)
            try:
                rec.log(f"   rilettura porta {port}: {(await dev.request_value(port, timeout=1.5)).hex(' ')}")
            except Exception as exc:
                rec.log(f"   rilettura porta {port} fallita: {exc}")
        return frame

    speak("Inizio tra tre secondi. Guarda e ascolta Mario.", voice)
    await asyncio.sleep(LEAD_IN)
    only = None
    if args.only:
        a, _, b = args.only.partition("-")
        only = (int(a), int(b or a))
    results: dict[int, list[tuple[int, str]]] = {}
    n = 0
    if args.scan:
        for port, sub, payload, what in scan_plan(ports, subs):
            n += 1
            if only and not (only[0] <= n <= only[1]):
                continue
            name = lwp3.OUTPUT_SUBCOMMANDS.get(sub, "non standard")
            label = f"porta {port}, sottocomando 0x{sub:02X} ({name}), {what}"
            say = f"porta {port}, sottocomando {sub}, " + {"senza payload": "vuoto", "un byte zero": "zero"}.get(what, "standard")
            responses = await probe(dev, rec, n, label, say, lambda p=port, s=sub, d=payload: send(p, s, d), voice)
            result = outcome(rec.msgs, port)
            rec.log(f"   esito: {result}")
            summary.append((n, label, responses))
            results.setdefault(port, []).append((sub, f"{what}: {result}"))
    if args.sweep:
        for port in ports:
            speak(f"Mappa dei sottocomandi sulla porta {port}.", voice)
            for sub in range(0x100):
                n += 1
                if only and not (only[0] <= n <= only[1]):
                    continue
                if sub % 16 == 0:
                    speak(f"Blocco {sub // 16 + 1} di 16.", voice)
                await probe(dev, rec, n, f"porta {port}, sottocomando 0x{sub:02X}, un byte zero", "",
                            lambda p=port, s=sub: send(p, s, bytes([0])), voice, answer_within=SWEEP_ANSWER)
                result = outcome(rec.msgs, port)
                rec.log(f"   esito: {result}")
                results.setdefault(port, []).append((sub, result))
    extra = ["", "=== MAPPA DEGLI ESITI ==="]
    for port, items in results.items():
        if args.sweep:
            for lo, hi, text in compress(items):
                span = f"0x{lo:02X}" if lo == hi else f"0x{lo:02X}-0x{hi:02X}"
                extra.append(f"porta {port}  {span:<11} {text}")
        else:
            extra += [f"porta {port}  0x{sub:02X}  {text}" for sub, text in items]
    return extra


async def baseline(dev: MarioDevice, rec: Recorder, seconds: float, voice: bool) -> None:
    """Nessuna scrittura: Mario fermo e connesso. Il PC scandisce il tempo ogni 30 s, così chi guarda
    può dire a quale marcatore è comparsa un'animazione; gli eventi della porta 3 restano nel log."""
    for port, mode in ((1, 0), (0, 1)):
        try:
            await dev.subscribe(port, mode, 1)
        except Exception:
            pass
    rec.log(f"=== OSSERVAZIONE: nessun comando per {int(seconds)} secondi. Lascia Mario fermo.")
    speak("Osservazione. Nessun comando. Lascia Mario fermo e guardalo. Zero secondi.", voice)
    start = time.time()
    next_mark = 30
    while time.time() - start < seconds:
        await asyncio.sleep(0.5)
        elapsed = time.time() - start
        if elapsed >= next_mark:
            rec.log(f"--- marcatore {next_mark} secondi")
            speak(f"{next_mark} secondi.", voice)
            next_mark += 30
    rec.log("=== fine osservazione")
    speak("Fine osservazione. Grazie.", voice)


async def wait_for_start_tile(dev: MarioDevice, rec: Recorder, voice: bool, timeout: float = 120.0) -> None:
    """Aspetta che lo scanner legga la basetta Start (0xB8): da lì Mario è "in livello" col timer."""
    seen = asyncio.Event()

    def on_message(msg: lwp3.Message, text: str) -> None:
        p = msg.payload
        if msg.msg_type == MessageType.PORT_VALUE_SINGLE and p and p[0] == 1 and len(p) >= 3 \
                and lwp3.le16(p[1:3]) in (0x00B8, 0x00AF, 0x014D):
            seen.set()

    try:
        await dev.subscribe(1, 0, 1)
    except Exception as exc:
        rec.log(f"porta 1: notifiche non attivate ({exc})")
    dev.add_listener(on_message)
    rec.log("Aspetto che Mario venga appoggiato sulla basetta Start (tubo di partenza)...")
    speak("Metti Mario sulla basetta di partenza, il tubo verde, e lascialo partire.", voice)
    try:
        await asyncio.wait_for(seen.wait(), timeout)
        rec.log("Basetta Start letta: livello in corso. Aspetto 4 secondi per l'animazione di partenza.")
        await asyncio.sleep(4)
    except asyncio.TimeoutError:
        rec.log("Basetta Start non vista entro il tempo: continuo lo stesso")
    finally:
        dev.remove_listener(on_message)


async def _action(dev: MarioDevice, action: int) -> bytes:
    frame = lwp3.hub_action(action)
    await dev.send(frame)
    return frame


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrotto.")
        raise SystemExit(130)
