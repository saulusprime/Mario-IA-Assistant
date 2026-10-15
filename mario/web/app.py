"""Applicazione Flask: pagine HTML (menu) e API JSON usate dalle pagine."""
from __future__ import annotations

import concurrent.futures
import csv
import hmac
import io
import json
import queue
import time
from pathlib import Path
from typing import Any, Callable, Optional

from bleak.exc import BleakError
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, url_for

from .. import codes, lwp3, syscheck
from ..assistant import AssistantBusy
from ..assistant.emotions import EMOTIONS, SAMPLES
from ..assistant.llm import ANTHROPIC_MODELS
from ..assistant.service import AssistantService
from ..assistant.soundpack import BROWSER, EDIT_STYLES
from ..adventures.model import ACTION_KINDS, TRIGGER_KINDS
from ..adventures.service import AdventureService
from ..device import LWPError
from ..env import load_dotenv
from ..lwp3 import HubAction, HubAlertOperation, HubAlertType, HubProperty, parse_hub_property, parse_int
from .ble_worker import BleWorker
from .db import ACCESSORY_KINDS, KIND_LABELS, Database

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = PROJECT_DIR / "data" / "mario.sqlite3"
VOICE_MAX_BYTES = 20_000_000
VOICE_MIN_SECONDS = 3.0


def wav_info(path: Path) -> Optional[float]:
    """Durata in secondi se `path` è un WAV PCM leggibile, altrimenti None."""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError, OSError):
        return None


def save_voice_reference(data: bytes, filename: str, folder: Path) -> tuple[Path, float]:
    """Salva la registrazione come WAV (convertendo con ffmpeg i formati del telefono) e ne controlla la durata."""
    import shutil
    import subprocess
    import tempfile
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"riferimento-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    suffix = Path(filename).suffix.lower()[:8] or ".bin"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"upload{suffix}"
        src.write_bytes(data)
        seconds = wav_info(src)
        if seconds is None:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise ValueError("serve un file WAV (per m4a, ogg o mp3 installare ffmpeg)")
            converted = Path(tmp) / "convertito.wav"
            proc = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "22050", str(converted)],
                                  capture_output=True, timeout=120)
            seconds = wav_info(converted) if proc.returncode == 0 else None
            if seconds is None:
                raise ValueError("file audio non leggibile: " + proc.stderr.decode(errors="replace").strip()[:160])
            src = converted
        if seconds < VOICE_MIN_SECONDS:
            raise ValueError(f"registrazione troppo corta ({seconds:.1f} s): servono almeno "
                             f"{VOICE_MIN_SECONDS:g} s, meglio 10-30 s di voce pulita")
        shutil.copyfile(src, target)
    return target, seconds

MENU = [
    ("index", "Panoramica", "/"),
    ("scan", "Scansione", "/scan"),
    ("devices", "Dispositivi", "/devices"),
    ("monitor", "Monitor", "/monitor"),
    ("events", "Eventi", "/events"),
    ("console", "Console", "/console"),
    ("accessories", "Accessori", "/accessories"),
    ("assistant", "Assistente", "/assistant"),
    ("adventures", "Avventure", "/adventures"),
    ("sounds", "Suoni", "/sounds"),
    ("report", "Funzioni", "/report"),
    ("docs", "Documentazione", "/docs"),
]

# Eventi finti per provare l'assistente senza il Mario fisico (pagina Assistente)
SIMULATIONS = {
    "salto": {"type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1, "hex": "00 80"},
    "caduta": {"type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1, "hex": "00 08"},
    "scossa": {"type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1, "hex": "10 00"},
    "pantaloni_fuoco": {"type": "PORT_VALUE_SINGLE", "io_type": 0x4A, "mode": 0, "hex": "12", "baseline": "21"},
    "pantaloni_gatto": {"type": "PORT_VALUE_SINGLE", "io_type": 0x4A, "mode": 0, "hex": "11", "baseline": "21"},
    "basetta_start": {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "hex": "b8 00 25 00"},
    "basetta_goomba": {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "hex": "02 00 25 00"},
    "basetta_traguardo": {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "hex": "b7 00 25 00"},
    "terreno_lava": {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "hex": "ff ff 15 00", "baseline": "ff ff 25 00"},
    "rotazione": {"type": "PORT_VALUE_SINGLE", "io_type": 0x47, "mode": 1, "hex": "00 01"},
    "sollevato": {"type": "PORT_VALUE_SINGLE", "io_type": 0x49, "mode": 0, "hex": "ff ff ff ff"},
    "pulsante": {"type": "HUB_PROPERTIES", "prop": "BUTTON", "value": True},
    "batteria": {"type": "HUB_PROPERTIES", "prop": "BATTERY_VOLTAGE", "value": 10},
}

ALERT_NAMES = {"low_voltage": HubAlertType.LOW_VOLTAGE, "high_current": HubAlertType.HIGH_CURRENT,
               "low_signal": HubAlertType.LOW_SIGNAL_STRENGTH, "over_power": HubAlertType.OVER_POWER_CONDITION}


def parse_enum(enum_cls, text: str) -> int:
    key = str(text).strip()
    for member in enum_cls:
        if member.name.lower() == key.lower():
            return int(member)
    return parse_int(key)


def fmt_time(ts: Optional[float]) -> str:
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts)) if ts else "-"


def capabilities_text(caps: dict) -> str:
    """Report testuale a partire dal JSON delle capacità salvato nel database."""
    lines = ["=== Proprietà dell'hub ==="]
    for name, info in (caps.get("properties") or {}).items():
        lines.append(f"  {name:<18} ({info.get('id')}): {info.get('value')}")
    lines += ["", "=== Porte e sensori ==="]
    for port, ps in (caps.get("ports") or {}).items():
        lines.append(f"Porta {port}: {ps.get('io_name')} ({ps.get('io_type')}) HW {ps.get('hw_version')} SW {ps.get('sw_version')}")
        lines.append(f"  capacità: {', '.join(ps.get('capabilities') or []) or '-'} | {ps.get('mode_count')} modalità | "
                     f"input {ps.get('input_modes')} | output {ps.get('output_modes')}")
        if ps.get("combinations"):
            lines.append(f"  combinazioni possibili: {ps['combinations']}")
        for mode, mi in (ps.get("modes") or {}).items():
            vf = mi.get("value_format") or {}
            fmt = f"{vf.get('datasets')} x {vf.get('type')}, {vf.get('figures')} cifre, {vf.get('decimals')} decimali" if vf else "formato ?"
            lines.append(f"  modo {mode}: {mi.get('name') or '?':<10} {fmt}")
            details = []
            for key in ("raw", "pct", "si"):
                if mi.get(key):
                    details.append(f"{key} {mi[key][0]:g}..{mi[key][1]:g}")
            if mi.get("symbol"):
                details.append(f"unità '{mi['symbol']}'")
            if details:
                lines.append("          " + " | ".join(details))
        if ps.get("hint"):
            lines.append(f"  significato noto: {ps['hint']}")
    return "\n".join(lines)


EVENT_TYPES = ["PORT_VALUE_SINGLE", "PORT_VALUE_COMBINED", "HUB_PROPERTIES", "HUB_ATTACHED_IO", "HUB_ACTIONS",
               "HUB_ALERTS", "GENERIC_ERROR"]
TOKEN_COOKIE = "mario_token"


def create_app(db_path: Optional[str | Path] = None, adapter: Optional[str] = None,
               pair: bool = True, start_worker: bool = True, quiet_assistant: bool = False,
               token: Optional[str] = None) -> Flask:
    """`token`: se impostato, ogni pagina e API richiede il token (cookie dopo /login, ?token=... o
    header Authorization: Bearer). Serve quando la dashboard viene esposta oltre 127.0.0.1."""
    load_dotenv()
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config["MARIO_TOKEN"] = token or ""
    db = Database(db_path or DEFAULT_DB)
    db.seed_accessories(codes.PANTS, codes.BARCODES, codes.COLORS)
    worker = BleWorker(db, adapter=adapter, pair=pair)
    if start_worker:
        worker.start()
    assistant = AssistantService(worker, db, quiet=quiet_assistant)
    adventures = AdventureService(worker, db, speaker=assistant.speaker, quiet=quiet_assistant)
    app.extensions["db"] = db
    app.extensions["worker"] = worker
    app.extensions["assistant"] = assistant
    app.extensions["adventures"] = adventures

    @app.context_processor
    def inject_globals():
        return {"menu": MENU, "kind_labels": KIND_LABELS, "fmt_time": fmt_time, "protected": bool(app.config["MARIO_TOKEN"])}

    # ----- accesso con token (solo se configurato) ---------------------------------------------

    def token_given() -> str:
        auth = request.headers.get("Authorization", "")
        return (request.args.get("token") or request.cookies.get(TOKEN_COOKIE)
                or (auth[7:].strip() if auth.lower().startswith("bearer ") else "") or "")

    def with_cookie(resp, value: str):
        resp.set_cookie(TOKEN_COOKIE, value, max_age=30 * 24 * 3600, httponly=True, samesite="Lax")
        return resp

    @app.before_request
    def require_token():
        expected = app.config["MARIO_TOKEN"]
        if not expected or request.endpoint in ("static", "page_login"):
            return None
        if hmac.compare_digest(token_given(), expected):
            if request.args.get("token") and request.method == "GET" and not request.path.startswith("/api/"):
                return with_cookie(redirect(request.path), expected)  # il token esce dall'URL e resta nel cookie
            return None
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "accesso negato: token mancante o errato"}), 401
        return redirect(url_for("page_login", next=request.path))

    @app.route("/login", methods=["GET", "POST"])
    def page_login():
        expected = app.config["MARIO_TOKEN"]
        error = ""
        if not expected:
            return redirect("/")
        if request.method == "POST":
            given = (request.form.get("token") or "").strip()
            if hmac.compare_digest(given, expected):
                target = request.form.get("next") or "/"
                return with_cookie(redirect(target if target.startswith("/") else "/"), expected)
            error = "token errato"
        return render_template("login.html", page="login", error=error, next=request.args.get("next", "/")), (200 if not error else 403)

    def body() -> dict:
        return request.get_json(silent=True) or request.form.to_dict() or {}

    def ok(**data: Any):
        return jsonify({"ok": True, **data})

    def api(fn: Callable) -> Callable:
        """Converte le eccezioni in risposte JSON {"ok": false, "error": ...}."""
        def wrapper(*args: Any, **kwargs: Any):
            try:
                return fn(*args, **kwargs)
            except (ValueError, KeyError, TypeError) as exc:
                return jsonify({"ok": False, "error": f"parametri non validi: {exc}"}), 400
            except LWPError as exc:
                return jsonify({"ok": False, "error": f"Mario risponde con un errore: {exc}"}), 409
            except (BleakError, concurrent.futures.TimeoutError, TimeoutError) as exc:
                return jsonify({"ok": False, "error": str(exc) or type(exc).__name__}), 503
            except Exception as exc:  # pragma: no cover - rete di sicurezza
                app.logger.exception("errore API")
                return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
        wrapper.__name__ = fn.__name__
        return wrapper

    # ----- pagine ---------------------------------------------------------------------

    @app.get("/")
    def page_index():
        return render_template("index.html", page="index", status=worker.status(),
                               devices=db.list_devices()[:5], acc_stats=db.accessory_stats(),
                               ev_stats=db.event_stats())

    @app.get("/scan")
    def page_scan():
        checks = syscheck.run_checks()
        return render_template("scan.html", page="scan", last_scan=worker.last_scan,
                               last_scan_time=worker.last_scan_time, checks=checks, worst=syscheck.worst(checks))

    @app.get("/devices")
    def page_devices():
        return render_template("devices.html", page="devices", devices=db.list_devices(),
                               system_names=lwp3.SYSTEM_TYPE_NAMES)

    @app.get("/devices/<int:device_id>")
    def page_device(device_id: int):
        device = db.get_device(device_id)
        if device is None:
            return render_template("message.html", page="devices", title="Dispositivo non trovato",
                                   text="Nessun dispositivo con questo identificativo."), 404
        caps = json.loads(device["capabilities"]) if device.get("capabilities") else {}
        return render_template("device.html", page="devices", device=device, caps=caps,
                               caps_text=capabilities_text(caps) if caps else "",
                               system_names=lwp3.SYSTEM_TYPE_NAMES)

    @app.get("/monitor")
    def page_monitor():
        return render_template("monitor.html", page="monitor", status=worker.status())

    @app.get("/events")
    def page_events():
        return render_template("events.html", page="events", devices=db.list_devices(), types=EVENT_TYPES,
                               stats=db.event_stats())

    @app.get("/console")
    def page_console():
        return render_template("console.html", page="console",
                               properties=[(p.name, int(p)) for p in HubProperty],
                               actions=[(a.name, int(a)) for a in HubAction if a < 0x30],
                               alerts=list(ALERT_NAMES))

    @app.get("/accessories")
    def page_accessories():
        items = db.list_accessories()
        grouped = {kind: [a for a in items if a["kind"] == kind] for kind in ACCESSORY_KINDS}
        verified = {"pants": codes.verified_codes("pants"), "tile": codes.verified_codes("barcodes"),
                    "color": codes.verified_codes("colors"), "other": {}}
        return render_template("accessories.html", page="accessories", grouped=grouped,
                               stats=db.accessory_stats(), kinds=ACCESSORY_KINDS, verified=verified)

    @app.get("/assistant")
    def page_assistant():
        return render_template("assistant.html", page="assistant", emotions=[e.to_dict() for e in EMOTIONS.values()],
                               models=ANTHROPIC_MODELS)

    @app.get("/adventures")
    def page_adventures():
        return render_template("adventures.html", page="adventures", adventures=adventures.list(),
                               trigger_kinds=TRIGGER_KINDS, action_kinds=ACTION_KINDS, simulations=list(SIMULATIONS))

    @app.get("/adventures/play")
    def page_adventure_play():
        return render_template("play.html", page="adventures", adventures=adventures.list(), simulations=list(SIMULATIONS),
                               selected=request.args.get("id", ""))

    @app.get("/report")
    def page_report():
        text, source = "", ""
        if worker.device is not None and worker.device.connected:
            text, source = worker.device.report(), "dispositivo connesso"
        else:
            devices = db.list_devices()
            if devices and devices[0].get("capabilities"):
                text = capabilities_text(json.loads(devices[0]["capabilities"]))
                source = f"database: {devices[0]['name'] or devices[0]['identity']} (ultima lettura {fmt_time(devices[0]['last_seen'])})"
        return render_template("report.html", page="report", text=text, source=source)

    @app.get("/docs")
    def page_docs():
        docs = []
        for name in ("README.md", "ASIS.md", "TODO.md", "HANDOFF.md"):
            path = PROJECT_DIR / name
            if path.exists():
                docs.append((name, path.read_text(encoding="utf-8")))
        return render_template("docs.html", page="docs", docs=docs)

    # ----- API: stato, scansione, connessione ----------------------------------------------

    @app.get("/api/status")
    @api
    def api_status():
        return ok(status=worker.status())

    @app.get("/api/system/checks")
    @api
    def api_system_checks():
        checks = syscheck.run_checks()
        return ok(checks=syscheck.as_dicts(checks), worst=syscheck.worst(checks))

    @app.post("/api/scan")
    @api
    def api_scan():
        duration = float(body().get("duration", 8))
        return ok(devices=worker.scan(max(1.0, min(duration, 60.0))))

    @app.post("/api/connect")
    @api
    def api_connect():
        data = body()
        address = (data.get("address") or "").strip() or None
        subscribe = str(data.get("subscribe", "1")).lower() not in ("0", "false", "no")
        return ok(status=worker.connect(address, timeout=float(data.get("timeout", 45)), subscribe=subscribe))

    @app.post("/api/disconnect")
    @api
    def api_disconnect():
        return ok(status=worker.disconnect())

    @app.post("/api/discover")
    @api
    def api_discover():
        full = str(body().get("full", "1")).lower() not in ("0", "false", "no")
        return ok(capabilities=worker.discover(full=full))

    @app.get("/api/report")
    @api
    def api_report():
        return ok(text=worker.report())

    @app.post("/api/unpair")
    @api
    def api_unpair():
        return ok(removed=worker.unpair((body().get("address") or "").strip() or None))

    # ----- API: eventi -----------------------------------------------------------------

    @app.get("/api/events")
    @api
    def api_events():
        since = int(request.args.get("since", 0))
        limit = int(request.args.get("limit", 200))
        port = request.args.get("port")
        all_types = request.args.get("all", "0") == "1"
        return ok(events=worker.events_since(since, limit, int(port) if port not in (None, "") else None,
                                             only_interesting=not all_types), seq=worker.seq)

    @app.get("/api/stream")
    def api_stream():
        q = worker.open_stream()

        def generate():
            try:
                yield "retry: 2000\n\n"
                last_ping = time.time()
                while True:
                    try:
                        ev = q.get(timeout=1.0)
                        yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                    except queue.Empty:
                        if time.time() - last_ping > 15:
                            yield ": ping\n\n"
                            last_ping = time.time()
            finally:
                worker.close_stream(q)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def history_filters() -> dict:
        a = request.args
        port, device_id = a.get("port"), a.get("device_id")
        return {"port": int(port) if port not in (None, "") else None, "type_name": a.get("type") or None,
                "device_id": int(device_id) if device_id not in (None, "") else None, "query": a.get("q", ""),
                "since_id": int(a.get("since_id", 0) or 0)}

    @app.get("/api/history")
    @api
    def api_history():
        rows = db.list_events(limit=int(request.args.get("limit", 200)), **history_filters())
        return ok(events=rows, stats=db.event_stats())

    def export_rows() -> list[dict]:
        a = request.args
        extra = {}
        for key, name in (("from", "since_ts"), ("to", "until_ts")):
            if a.get(key):
                try:
                    extra[name] = float(a[key])
                except ValueError:
                    extra[name] = time.mktime(time.strptime(a[key][:16], "%Y-%m-%dT%H:%M"))
        return db.iter_events(limit=int(a.get("limit", 50000)), **history_filters(), **extra)

    @app.get("/api/history.csv")
    @api
    def api_history_csv():
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["id", "data_ora", "ts", "dispositivo", "tipo", "porta", "modo", "grezzo", "testo", "decodifica"])
        for r in export_rows():
            writer.writerow([r["id"], fmt_time(r["ts"]), r["ts"], r["device_id"], r["type"], r["port"], r["mode"],
                             r["raw"], r["text"], r["decoded"]])
        name = f"mario-eventi-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.get("/api/history.json")
    @api
    def api_history_json():
        name = f"mario-eventi-{time.strftime('%Y%m%d-%H%M%S')}.json"
        return Response(json.dumps(export_rows(), ensure_ascii=False, indent=1), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.get("/api/charts")
    @api
    def api_charts():
        device_id = request.args.get("device_id")
        dev_id = int(device_id) if device_id not in (None, "") else None
        return ok(battery=db.battery_series(device_id=dev_id), gestures=db.gesture_histogram(dev_id),
                  tiles=db.tile_counts(dev_id), types=db.event_type_counts(dev_id), stats=db.event_stats())

    # ----- API: porte e comandi ------------------------------------------------------------

    @app.post("/api/subscribe")
    @api
    def api_subscribe():
        d = body()
        notify = str(d.get("notify", "1")).lower() not in ("0", "false", "no")
        return ok(reply=worker.subscribe(parse_int(str(d["port"])), parse_int(str(d["mode"])),
                                         parse_int(str(d.get("delta", 1))), notify))

    @app.post("/api/subscribe_defaults")
    @api
    def api_subscribe_defaults():
        dev = worker._require()
        worker.call(lambda: worker._subscribe_defaults(dev), timeout=60)
        return ok(subscriptions=worker.status()["subscriptions"])

    @app.post("/api/value")
    @api
    def api_value():
        return ok(raw=worker.request_value(parse_int(str(body()["port"]))))

    @app.get("/api/properties")
    @api
    def api_properties():
        return ok(properties=worker.get_all_properties())

    @app.post("/api/property")
    @api
    def api_property():
        d = body()
        prop = parse_hub_property(str(d["prop"]))
        action = d.get("action", "get")
        if action == "get":
            return ok(**worker.get_property(prop))
        if action in ("watch", "unwatch"):
            worker.property_updates(prop, action == "watch")
            return ok()
        if action == "set":
            value = str(d.get("value", ""))
            if prop == HubProperty.VOLUME:
                return ok(volume=worker.set_volume(parse_int(value)))
            if prop == HubProperty.ADVERTISING_NAME:
                return ok(name=worker.set_name(value))
            raw = bytes.fromhex(value.replace(" ", ""))
            worker.call(lambda: worker._require().set_property(prop, raw), timeout=20)
            return ok(**worker.get_property(prop))
        raise ValueError(f"azione sconosciuta {action!r}")

    @app.post("/api/volume")
    @api
    def api_volume():
        return ok(volume=worker.set_volume(parse_int(str(body()["level"]))))

    @app.post("/api/name")
    @api
    def api_name():
        return ok(name=worker.set_name(str(body()["name"])))

    @app.post("/api/raw")
    @api
    def api_raw():
        worker.send_hex(str(body()["hex"]))
        return ok()

    @app.post("/api/message")
    @api
    def api_message():
        d = body()
        worker.send_message(parse_int(str(d["type"])), bytes.fromhex(str(d.get("payload", "")).replace(" ", "")))
        return ok()

    @app.post("/api/port_output")
    @api
    def api_port_output():
        d = body()
        worker.port_output(parse_int(str(d["port"])), parse_int(str(d["subcommand"])),
                           bytes.fromhex(str(d.get("payload", "")).replace(" ", "")))
        return ok()

    @app.post("/api/hub_action")
    @api
    def api_hub_action():
        worker.hub_action(parse_enum(HubAction, str(body()["action"])))
        return ok()

    @app.post("/api/alert")
    @api
    def api_alert():
        d = body()
        key = str(d["alert"]).lower()
        alert = int(ALERT_NAMES[key]) if key in ALERT_NAMES else parse_int(key)
        op = {"on": HubAlertOperation.ENABLE_UPDATES, "off": HubAlertOperation.DISABLE_UPDATES,
              "req": HubAlertOperation.REQUEST_UPDATE}[str(d.get("operation", "req"))]
        return ok(reply=worker.alert(alert, op))

    # ----- API: dispositivi ------------------------------------------------------------

    @app.get("/api/devices")
    @api
    def api_devices():
        return ok(devices=db.list_devices())

    @app.get("/api/devices/<int:device_id>")
    @api
    def api_device(device_id: int):
        device = db.get_device(device_id)
        if device is None:
            return jsonify({"ok": False, "error": "dispositivo non trovato"}), 404
        device["capabilities"] = json.loads(device["capabilities"]) if device.get("capabilities") else None
        return ok(device=device)

    @app.post("/api/devices/<int:device_id>")
    @api
    def api_device_update(device_id: int):
        d = body()
        fields = {k: d[k] for k in ("notes", "name", "volume") if k in d}
        if "volume" in fields:
            fields["volume"] = int(fields["volume"])
        return ok(updated=db.update_device(device_id, **fields))

    @app.delete("/api/devices/<int:device_id>")
    @api
    def api_device_delete(device_id: int):
        return ok(deleted=db.delete_device(device_id))

    # ----- API: accessori --------------------------------------------------------------

    @app.get("/api/accessories")
    @api
    def api_accessories():
        a = request.args
        owned = a.get("owned")
        return ok(items=db.list_accessories(a.get("kind") or None,
                                            None if owned in (None, "") else owned == "1", a.get("q", "")),
                  stats=db.accessory_stats())

    @app.post("/api/accessories")
    @api
    def api_accessory_add():
        d = body()
        code = d.get("code")
        code_int = parse_int(str(code)) if code not in (None, "") else None
        new_id = db.add_accessory(str(d["kind"]), str(d["name"]).strip(), code_int,
                                  owned=str(d.get("owned", "0")).lower() in ("1", "true", "on"),
                                  quantity=int(d.get("quantity", 0) or 0), set_number=str(d.get("set_number", "")),
                                  notes=str(d.get("notes", "")))
        return ok(item=db.get_accessory(new_id))

    @app.post("/api/accessories/<int:accessory_id>")
    @api
    def api_accessory_update(accessory_id: int):
        d = dict(body())
        if "code" in d:
            d["code"] = parse_int(str(d["code"])) if d["code"] not in (None, "") else None
        if "quantity" in d:
            d["quantity"] = int(d["quantity"] or 0)
        if "owned" in d:
            d["owned"] = str(d["owned"]).lower() in ("1", "true", "on")
        db.update_accessory(accessory_id, **d)
        return ok(item=db.get_accessory(accessory_id))

    @app.delete("/api/accessories/<int:accessory_id>")
    @api
    def api_accessory_delete(accessory_id: int):
        return ok(deleted=db.delete_accessory(accessory_id))

    @app.get("/api/accessories/stats")
    @api
    def api_accessory_stats():
        return ok(stats=db.accessory_stats())

    # ----- API: assistente (Mario come I/O di un LLM) -------------------------------------

    @app.get("/api/assistant/status")
    @api
    def api_assistant_status():
        return ok(status=assistant.status())

    @app.post("/api/assistant/ask")
    @api
    def api_assistant_ask():
        d = body()
        try:
            turn = assistant.session.ask(str(d.get("text", "")), source=str(d.get("source") or "utente"))
        except AssistantBusy as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return ok(turn=turn)

    @app.post("/api/assistant/stop")
    @api
    def api_assistant_stop():
        assistant.session.stop()
        return ok()

    @app.post("/api/assistant/repeat")
    @api
    def api_assistant_repeat():
        return ok(repeated=assistant.session.repeat())

    @app.post("/api/assistant/reset")
    @api
    def api_assistant_reset():
        assistant.session.reset()
        if str(body().get("clear_history", "0")).lower() in ("1", "true", "on"):
            db.clear_assistant_messages()
        return ok()

    @app.post("/api/assistant/listen")
    @api
    def api_assistant_listen():
        on = str(body().get("on", "1")).lower() in ("1", "true", "on")
        return ok(listening=assistant.session.set_listening(on))

    @app.post("/api/assistant/say")
    @api
    def api_assistant_say():
        d = body()
        text = str(d.get("text", "")).strip()
        if not text:
            raise ValueError("testo vuoto")
        assistant.session.say(text, d.get("emotion") or None)
        return ok()

    @app.post("/api/assistant/emotion")
    @api
    def api_assistant_emotion():
        key = str(body().get("emotion", ""))
        if key not in EMOTIONS:
            raise ValueError(f"emozione sconosciuta {key!r}")
        assistant.session.say(SAMPLES[key], key)
        return ok(emotion=EMOTIONS[key].to_dict())

    @app.post("/api/assistant/simulate")
    @api
    def api_assistant_simulate():
        what = str(body().get("what", ""))
        spec = dict(SIMULATIONS[what])
        mapper = assistant.session.mapper
        intents = []
        if spec.get("type") == "PORT_VALUE_SINGLE":
            base = spec.pop("baseline", None)
            io_type = spec["io_type"]
            first = (io_type == 0x4A and mapper._pants_prev is None) or (io_type == 0x49 and mapper._color_prev is None)
            if base and first:  # il primo valore vale come stato iniziale: lo simuliamo prima
                assistant.session.handle_event({**spec, "ts": time.time(), "data": bytes.fromhex(base)})
            spec["data"] = bytes.fromhex(spec.pop("hex"))
            spec["settled"] = True  # simulazione: niente attesa di stabilità
        spec["ts"] = time.time()
        intents = assistant.session.handle_event(spec)
        return ok(intents=[i.to_dict() for i in intents])

    @app.get("/api/assistant/settings")
    @api
    def api_assistant_settings():
        return ok(settings=assistant.public_settings())

    @app.post("/api/assistant/settings")
    @api
    def api_assistant_settings_update():
        return ok(settings=assistant.update_settings(body()))

    voices_dir = Path(db_path or DEFAULT_DB).parent / "voices"

    @app.post("/api/assistant/voice-reference")
    @api
    def api_assistant_voice_reference():
        """Registrazione di riferimento della voce IA: WAV, oppure m4a/ogg/mp3 convertiti con ffmpeg.
        Salvata in data/voices/; il percorso va poi salvato nelle impostazioni (campo della pagina)."""
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValueError("nessun file ricevuto")
        data = upload.read(VOICE_MAX_BYTES + 1)
        if len(data) > VOICE_MAX_BYTES:
            raise ValueError(f"file troppo grande (massimo {VOICE_MAX_BYTES // 1_000_000} MB)")
        path, seconds = save_voice_reference(data, upload.filename, voices_dir)
        return ok(path=str(path), seconds=round(seconds, 1))

    def diary_range() -> dict:
        out = {}
        for key, name in (("from", "since_ts"), ("to", "until_ts")):
            value = request.args.get(key)
            if value:
                try:
                    out[name] = float(value)
                except ValueError:
                    out[name] = time.mktime(time.strptime(value[:16], "%Y-%m-%dT%H:%M"))
        return out

    @app.get("/api/assistant/history")
    @api
    def api_assistant_history():
        return ok(messages=db.list_assistant_messages(int(request.args.get("limit", 100)),
                                                      int(request.args.get("since_id", 0)), **diary_range()),
                  stats=db.assistant_stats())

    @app.get("/api/assistant/history.csv")
    @api
    def api_assistant_history_csv():
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["id", "data_ora", "turno", "chi", "origine", "emozione", "testo"])
        for m in db.list_assistant_messages(int(request.args.get("limit", 5000)), 0, **diary_range()):
            who = "Mario" if m["role"] == "assistant" else ("sensore" if m["source"] and m["source"] != "utente" else "bambino")
            writer.writerow([m["id"], fmt_time(m["ts"]), m["turn"], who, m["source"], m["emotion"], m["content"]])
        name = f"mario-diario-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.get("/api/assistant/memory")
    @api
    def api_assistant_memory():
        return ok(memory=assistant.session.memory.list(), enabled=assistant.session.memory_enabled)

    @app.post("/api/assistant/memory")
    @api
    def api_assistant_memory_add():
        text = str(body().get("text", "")).strip()
        if not text:
            raise ValueError("testo vuoto")
        mid = assistant.session.memory.add(text)
        assistant.session.emit("memory", text=text, id=mid, source="utente")
        return ok(id=mid, memory=assistant.session.memory.list())

    @app.delete("/api/assistant/memory")
    @api
    def api_assistant_memory_clear():
        return ok(deleted=assistant.session.forget())

    @app.delete("/api/assistant/memory/<int:memory_id>")
    @api
    def api_assistant_memory_delete(memory_id: int):
        return ok(deleted=assistant.session.forget(memory_id))

    # ----- API: avventure ----------------------------------------------------------------------

    @app.get("/api/adventures")
    @api
    def api_adventures():
        return ok(adventures=adventures.list())

    @app.post("/api/adventures")
    @api
    def api_adventure_create():
        d = body()
        data = d.get("data") if isinstance(d.get("data"), dict) else d
        new_id = adventures.save(data)
        return ok(id=new_id, adventure=adventures.get(new_id))

    @app.get("/api/adventures/library")
    @api
    def api_adventure_library():
        return ok(library=adventures.library(), trigger_kinds=TRIGGER_KINDS, action_kinds=ACTION_KINDS)

    @app.post("/api/adventures/validate")
    @api
    def api_adventure_validate():
        from ..adventures.model import Adventure, validate
        d = body()
        data = d.get("data") if isinstance(d.get("data"), dict) else d
        adv = Adventure.from_dict(data)
        return ok(problems=validate(adv), normalized=adv.to_dict())

    @app.post("/api/adventures/import")
    @api
    def api_adventure_import():
        d = body()
        text = d.get("json") if isinstance(d.get("json"), str) else json.dumps(d)
        new_id = adventures.save(json.loads(text))
        return ok(id=new_id, adventure=adventures.get(new_id))

    @app.get("/api/adventures/<int:adventure_id>")
    @api
    def api_adventure(adventure_id: int):
        row = adventures.get(adventure_id)
        if row is None:
            return jsonify({"ok": False, "error": "avventura non trovata"}), 404
        return ok(adventure=row)

    @app.get("/api/adventures/<int:adventure_id>/export")
    @api
    def api_adventure_export(adventure_id: int):
        row = adventures.get(adventure_id)
        if row is None:
            return jsonify({"ok": False, "error": "avventura non trovata"}), 404
        name = "".join(c if c.isalnum() else "-" for c in row["name"].lower()).strip("-") or "avventura"
        return Response(json.dumps(row["data"], ensure_ascii=False, indent=1), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=avventura-{name}.json"})

    @app.post("/api/adventures/<int:adventure_id>")
    @api
    def api_adventure_update(adventure_id: int):
        d = body()
        data = d.get("data") if isinstance(d.get("data"), dict) else d
        adventures.save(data, adventure_id)
        return ok(adventure=adventures.get(adventure_id))

    @app.delete("/api/adventures/<int:adventure_id>")
    @api
    def api_adventure_delete(adventure_id: int):
        return ok(deleted=adventures.delete(adventure_id))

    @app.get("/api/adventure/status")
    @api
    def api_adventure_status():
        return ok(status=adventures.status())

    @app.post("/api/adventure/start")
    @api
    def api_adventure_start():
        d = body()
        if isinstance(d.get("data"), dict):
            return ok(status=adventures.start(data=d["data"]))
        return ok(status=adventures.start(int(d["id"])))

    @app.post("/api/adventure/stop")
    @api
    def api_adventure_stop():
        return ok(status=adventures.stop(str(body().get("reason") or "abbandonata")))

    @app.post("/api/adventure/simulate")
    @api
    def api_adventure_simulate():
        what = str(body().get("what", ""))
        spec = dict(SIMULATIONS[what])
        spec.pop("baseline", None)
        if "hex" in spec:
            spec["data"] = bytes.fromhex(spec.pop("hex"))
        spec["ts"] = time.time()
        return ok(progressed=adventures.simulate(spec), status=adventures.status())

    @app.get("/api/adventure/runs")
    @api
    def api_adventure_runs():
        aid = request.args.get("adventure_id")
        return ok(runs=adventures.runs(int(aid) if aid not in (None, "") else None, int(request.args.get("limit", 50))))

    @app.get("/api/adventure/runs/<int:run_id>")
    @api
    def api_adventure_run(run_id: int):
        row = db.get_run(run_id)
        if row is None:
            return jsonify({"ok": False, "error": "partita non trovata"}), 404
        return ok(run=row)

    # ----- suoni: archivio locale, scelte per effetto, suoni propri --------------------------

    def pack():
        return assistant.speaker.pack

    @app.get("/sounds")
    def page_sounds():
        return render_template("sounds.html", page="sounds", status=pack().status(), styles=EDIT_STYLES)

    @app.get("/api/sounds/catalog")
    @api
    def api_sounds_catalog():
        a = request.args
        pack().reload()  # lo scaricamento può essere in corso: il catalogo cresce
        return ok(**pack().catalog(a.get("section", ""), a.get("game", ""), a.get("q", ""), int(a.get("limit", 300))))

    @app.get("/api/sounds/assignments")
    @api
    def api_sounds_assignments():
        p = pack()
        return ok(rows=p.table(), volume=p.volume, style=assistant.speaker.sound_style, status=p.status(),
                  styles=list(EDIT_STYLES))

    @app.post("/api/sounds/assign")
    @api
    def api_sounds_assign():
        d = body()
        files = d.get("files") or []
        files = [files] if isinstance(files, str) else [str(f) for f in files]
        style, name = str(d.get("style") or "giochi"), str(d.get("name") or "")
        if d.get("append"):
            files = list(pack().assignments.get(style, {}).get(name, [])) + files
        return ok(files=pack().assign(style, name, files), rows=pack().table())

    @app.post("/api/sounds/volume")
    @api
    def api_sounds_volume():
        return ok(volume=pack().set_volume(int(body().get("volume", 100))))

    @app.post("/api/sounds/play")
    @api
    def api_sounds_play():
        """Prova sul PC: un file dell'archivio ("file", con "seconds") o un effetto per nome ("name", "style")."""
        d = body()
        sp = assistant.speaker
        if not sp.player:
            return jsonify({"ok": False, "error": "nessun lettore audio sul PC (paplay, pw-play, aplay, ffplay)"}), 409
        if d.get("file"):
            path = sp.play_file(str(d["file"]), float(d["seconds"]) if d.get("seconds") else None)
        else:
            path = sp.preview(str(d.get("name") or ""), str(d.get("style") or "") or None)
        if path is None:
            return jsonify({"ok": False, "error": "suono non trovato o non riproducibile su questo PC"}), 404
        return ok(path=str(path))

    @app.get("/api/sounds/file/<path:rel>")
    def api_sounds_file(rel: str):
        """Il file per l'anteprima nel browser; i formati che il browser non legge (NSF, SPC, moduli, MIDI) e le
        richieste con ?wav=1 passano dalla conversione in WAV (?seconds=, ?track= per gli NSF)."""
        p = pack()
        path = p.resolve(rel)
        if path is None:
            return jsonify({"ok": False, "error": "file non trovato"}), 404
        item = p.by_rel.get(rel, {})
        if request.args.get("wav") or Path(item.get("play_rel") or rel).suffix.lower() not in BROWSER:
            track = request.args.get("track", "")
            seconds = float(request.args.get("seconds") or 0) or None
            path = p.playable(rel + (f"#{track}" if track.isdigit() else ""), seconds)
            if path is None:
                return jsonify({"ok": False, "error": "non riproducibile su questo PC: serve ffmpeg (per i MIDI "
                                                      "fluidsynth e fluid-soundfont-gm)"}), 415
        return send_file(path, conditional=True)

    @app.delete("/api/sounds/file/<path:rel>")
    @api
    def api_sounds_delete(rel: str):
        return ok(deleted=pack().delete_custom(rel))

    @app.post("/api/sounds/upload")
    @api
    def api_sounds_upload():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValueError("nessun file ricevuto")
        data = upload.read(VOICE_MAX_BYTES + 1)
        if len(data) > VOICE_MAX_BYTES:
            raise ValueError(f"file troppo grande (massimo {VOICE_MAX_BYTES // 1_000_000} MB)")
        item = pack().add_custom(data, upload.filename, request.form.get("description", ""))
        name, style = request.form.get("name", "").strip(), request.form.get("style", "giochi")
        if name:
            pack().assign(style, name, list(pack().assignments.get(style, {}).get(name, [])) + [item["rel"]])
        return ok(item=item)

    return app
