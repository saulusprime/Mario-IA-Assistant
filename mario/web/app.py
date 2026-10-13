"""Applicazione Flask: pagine HTML (menu) e API JSON usate dalle pagine."""
from __future__ import annotations

import concurrent.futures
import json
import queue
import time
from pathlib import Path
from typing import Any, Callable, Optional

from bleak.exc import BleakError
from flask import Flask, Response, jsonify, render_template, request

from .. import codes, lwp3
from ..assistant import AssistantBusy
from ..assistant.emotions import EMOTIONS, SAMPLES
from ..assistant.llm import ANTHROPIC_MODELS
from ..assistant.service import AssistantService
from ..device import LWPError
from ..env import load_dotenv
from ..lwp3 import HubAction, HubAlertOperation, HubAlertType, HubProperty, parse_hub_property, parse_int
from .ble_worker import BleWorker
from .db import ACCESSORY_KINDS, KIND_LABELS, Database

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = PROJECT_DIR / "data" / "mario.sqlite3"

MENU = [
    ("index", "Panoramica", "/"),
    ("scan", "Scansione", "/scan"),
    ("devices", "Dispositivi", "/devices"),
    ("monitor", "Monitor", "/monitor"),
    ("console", "Console", "/console"),
    ("accessories", "Accessori", "/accessories"),
    ("assistant", "Assistente", "/assistant"),
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


def create_app(db_path: Optional[str | Path] = None, adapter: Optional[str] = None,
               pair: bool = True, start_worker: bool = True, quiet_assistant: bool = False) -> Flask:
    load_dotenv()
    app = Flask(__name__)
    app.json.ensure_ascii = False
    db = Database(db_path or DEFAULT_DB)
    db.seed_accessories(codes.PANTS, codes.BARCODES, codes.COLORS)
    worker = BleWorker(db, adapter=adapter, pair=pair)
    if start_worker:
        worker.start()
    assistant = AssistantService(worker, db, quiet=quiet_assistant)
    app.extensions["db"] = db
    app.extensions["worker"] = worker
    app.extensions["assistant"] = assistant

    @app.context_processor
    def inject_globals():
        return {"menu": MENU, "kind_labels": KIND_LABELS, "fmt_time": fmt_time}

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
        return render_template("scan.html", page="scan", last_scan=worker.last_scan,
                               last_scan_time=worker.last_scan_time)

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
        return render_template("accessories.html", page="accessories", grouped=grouped,
                               stats=db.accessory_stats(), kinds=ACCESSORY_KINDS)

    @app.get("/assistant")
    def page_assistant():
        return render_template("assistant.html", page="assistant", emotions=[e.to_dict() for e in EMOTIONS.values()],
                               models=ANTHROPIC_MODELS)

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
        for name in ("README.md", "HANDOFF.md", "TODO.md"):
            path = PROJECT_DIR / name
            if path.exists():
                docs.append((name, path.read_text(encoding="utf-8")))
        return render_template("docs.html", page="docs", docs=docs)

    # ----- API: stato, scansione, connessione ----------------------------------------------

    @app.get("/api/status")
    @api
    def api_status():
        return ok(status=worker.status())

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

    @app.get("/api/history")
    @api
    def api_history():
        a = request.args
        port = a.get("port")
        device_id = a.get("device_id")
        rows = db.list_events(since_id=int(a.get("since_id", 0)), limit=int(a.get("limit", 200)),
                              port=int(port) if port not in (None, "") else None,
                              type_name=a.get("type") or None,
                              device_id=int(device_id) if device_id not in (None, "") else None,
                              query=a.get("q", ""))
        return ok(events=rows, stats=db.event_stats())

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
        fields = {k: d[k] for k in ("notes", "name") if k in d}
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

    @app.get("/api/assistant/history")
    @api
    def api_assistant_history():
        return ok(messages=db.list_assistant_messages(int(request.args.get("limit", 100)),
                                                      int(request.args.get("since_id", 0))),
                  stats=db.assistant_stats())

    return app
