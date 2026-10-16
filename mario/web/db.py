"""
Database locale SQLite (solo libreria standard).

Tabelle:
  devices      dispositivi conosciuti (chiave: MAC identità), proprietà, capacità (JSON), note
  accessories  pantaloni, basette con barcode, colori terreno, altri accessori; posseduti/visti
  events       eventi dei sensori e dell'hub ricevuti dal vivo
  settings     impostazioni chiave/valore
  assistant_messages  conversazione con l'assistente (Mario come I/O di un LLM)
  assistant_memory    ricordi dell'assistente tra una sessione e l'altra
  adventures, adventure_runs  avventure del designer (JSON) e partite giocate (esito, punti, log)
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY,
    identity      TEXT UNIQUE NOT NULL,
    name          TEXT,
    system_type   INTEGER,
    fw_version    TEXT,
    hw_version    TEXT,
    lwp_version   TEXT,
    radio_fw      TEXT,
    manufacturer  TEXT,
    battery_type  INTEGER,
    volume        INTEGER,
    last_address  TEXT,
    bonded        INTEGER DEFAULT 0,
    last_battery  INTEGER,
    last_rssi     INTEGER,
    first_seen    REAL,
    last_seen     REAL,
    notes         TEXT DEFAULT '',
    capabilities  TEXT
);
CREATE TABLE IF NOT EXISTS accessories (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    code        INTEGER,
    name        TEXT NOT NULL,
    owned       INTEGER DEFAULT 0,
    quantity    INTEGER DEFAULT 0,
    set_number  TEXT DEFAULT '',
    notes       TEXT DEFAULT '',
    source      TEXT DEFAULT '',
    seen_count  INTEGER DEFAULT 0,
    last_seen   REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS accessories_kind_code ON accessories(kind, code) WHERE code IS NOT NULL;
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    device_id  INTEGER,
    type       TEXT,
    port       INTEGER,
    mode       INTEGER,
    raw        TEXT,
    text       TEXT,
    decoded    TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_port ON events(port);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS assistant_messages (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    device_id  INTEGER,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    emotion    TEXT,
    source     TEXT,
    turn       INTEGER
);
CREATE TABLE IF NOT EXISTS assistant_memory (
    id   INTEGER PRIMARY KEY,
    ts   REAL NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adventures (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    author      TEXT DEFAULT '',
    version     INTEGER DEFAULT 1,
    json        TEXT NOT NULL,
    created     REAL,
    updated     REAL
);
CREATE TABLE IF NOT EXISTS adventure_runs (
    id            INTEGER PRIMARY KEY,
    adventure_id  INTEGER,
    name          TEXT,
    started       REAL,
    ended         REAL,
    score         INTEGER DEFAULT 0,
    outcome       TEXT,
    log           TEXT
);
"""

DEVICE_FIELDS = ("name", "system_type", "fw_version", "hw_version", "lwp_version", "radio_fw",
                 "manufacturer", "battery_type", "volume", "last_address", "bonded", "last_battery",
                 "last_rssi", "capabilities", "notes")
ACCESSORY_KINDS = ("pants", "tile", "color", "other")
KIND_LABELS = {"pants": "Pantaloni", "tile": "Basette con barcode", "color": "Colori terreno", "other": "Altri accessori"}


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._inserts = 0
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _one(self, sql: str, params=()) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def _exec(self, sql: str, params=()) -> int:
        with self._conn() as c:
            return c.execute(sql, params).rowcount

    def _insert(self, sql: str, params=()) -> int:
        with self._conn() as c:
            return int(c.execute(sql, params).lastrowid or 0)

    # ----- dispositivi --------------------------------------------------------------

    def upsert_device(self, identity: str, **fields: Any) -> int:
        now = time.time()
        clean = {k: v for k, v in fields.items() if k in DEVICE_FIELDS and v is not None}
        row = self.get_device_by_identity(identity)
        if row is None:
            cols = ["identity", "first_seen", "last_seen", *clean]
            return self._insert(f"INSERT INTO devices ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                [identity, now, now, *clean.values()])
        sets = "".join(f", {k}=?" for k in clean)
        self._exec(f"UPDATE devices SET last_seen=?{sets} WHERE id=?", [now, *clean.values(), row["id"]])
        return int(row["id"])

    def list_devices(self) -> list[dict]:
        return self._all("SELECT * FROM devices ORDER BY last_seen DESC")

    def get_device(self, device_id: int) -> Optional[dict]:
        return self._one("SELECT * FROM devices WHERE id=?", (device_id,))

    def get_device_by_identity(self, identity: str) -> Optional[dict]:
        return self._one("SELECT * FROM devices WHERE identity=?", (identity,))

    def update_device(self, device_id: int, **fields: Any) -> int:
        clean = {k: v for k, v in fields.items() if k in DEVICE_FIELDS}
        if not clean:
            return 0
        sets = ", ".join(f"{k}=?" for k in clean)
        return self._exec(f"UPDATE devices SET {sets} WHERE id=?", [*clean.values(), device_id])

    def delete_device(self, device_id: int) -> int:
        self._exec("DELETE FROM events WHERE device_id=?", (device_id,))
        return self._exec("DELETE FROM devices WHERE id=?", (device_id,))

    # ----- accessori ----------------------------------------------------------------

    def seed_accessories(self, pants: dict, barcodes: dict, colors: dict) -> int:
        """Inserisce (una volta sola) gli accessori noti dalle tabelle; aggiorna i nomi se cambiano."""
        inserted = 0
        for kind, table in (("pants", pants), ("tile", barcodes), ("color", colors)):
            for code, name in table.items():
                if kind == "pants" and int(code) == 0:
                    continue
                inserted += self._exec(
                    "INSERT OR IGNORE INTO accessories (kind, code, name, source) VALUES (?, ?, ?, 'tabella')",
                    (kind, int(code), name))
                self._exec("UPDATE accessories SET name=? WHERE kind=? AND code=? AND source='tabella' AND name<>?",
                           (name, kind, int(code), name))
        return inserted

    def list_accessories(self, kind: Optional[str] = None, owned: Optional[bool] = None,
                         query: str = "") -> list[dict]:
        sql, params = "SELECT * FROM accessories WHERE 1=1", []
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if owned is not None:
            sql += " AND owned=?"
            params.append(1 if owned else 0)
        if query:
            sql += " AND (name LIKE ? OR notes LIKE ? OR set_number LIKE ?)"
            params += [f"%{query}%"] * 3
        sql += " ORDER BY kind, owned DESC, code"
        return self._all(sql, params)

    def get_accessory(self, accessory_id: int) -> Optional[dict]:
        return self._one("SELECT * FROM accessories WHERE id=?", (accessory_id,))

    def add_accessory(self, kind: str, name: str, code: Optional[int] = None, owned: bool = False,
                      quantity: int = 0, set_number: str = "", notes: str = "", source: str = "utente") -> int:
        if kind not in ACCESSORY_KINDS:
            raise ValueError(f"tipo accessorio non valido: {kind}")
        if code is not None:
            existing = self._one("SELECT id FROM accessories WHERE kind=? AND code=?", (kind, code))
            if existing:
                self.update_accessory(existing["id"], name=name, owned=owned, quantity=quantity,
                                      set_number=set_number, notes=notes)
                return int(existing["id"])
        return self._insert(
            "INSERT INTO accessories (kind, code, name, owned, quantity, set_number, notes, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, code, name, 1 if owned else 0, quantity, set_number, notes, source))

    def update_accessory(self, accessory_id: int, **fields: Any) -> int:
        allowed = {"name", "owned", "quantity", "set_number", "notes", "code", "kind"}
        clean = {k: (int(bool(v)) if k == "owned" else v) for k, v in fields.items() if k in allowed}
        if not clean:
            return 0
        sets = ", ".join(f"{k}=?" for k in clean)
        return self._exec(f"UPDATE accessories SET {sets} WHERE id=?", [*clean.values(), accessory_id])

    def delete_accessory(self, accessory_id: int) -> int:
        return self._exec("DELETE FROM accessories WHERE id=?", (accessory_id,))

    def mark_seen(self, kind: str, code: int) -> None:
        """Registra che il sensore ha visto l'accessorio; se sconosciuto lo crea come 'rilevato'."""
        now = time.time()
        if self._exec("UPDATE accessories SET seen_count=seen_count+1, last_seen=? WHERE kind=? AND code=?",
                      (now, kind, code)) == 0:
            self._exec("INSERT OR IGNORE INTO accessories (kind, code, name, source, seen_count, last_seen) "
                       "VALUES (?, ?, ?, 'rilevato', 1, ?)", (kind, code, f"sconosciuto 0x{code:04X}", now))

    def accessory_stats(self) -> dict:
        rows = self._all("SELECT kind, COUNT(*) AS total, SUM(owned) AS owned, "
                         "SUM(CASE WHEN seen_count>0 THEN 1 ELSE 0 END) AS seen FROM accessories GROUP BY kind")
        return {r["kind"]: {"total": r["total"], "owned": r["owned"] or 0, "seen": r["seen"] or 0} for r in rows}

    # ----- eventi -------------------------------------------------------------------

    def add_event(self, device_id: Optional[int], ev: dict) -> int:
        rowid = self._insert(
            "INSERT INTO events (ts, device_id, type, port, mode, raw, text, decoded) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ev.get("ts", time.time()), device_id, ev.get("type"), ev.get("port"), ev.get("mode"),
             ev.get("raw"), ev.get("text"), ev.get("decoded")))
        self._inserts += 1
        if self._inserts % 500 == 0:
            self.prune_events()
        return rowid

    def list_events(self, since_id: int = 0, limit: int = 200, port: Optional[int] = None,
                    type_name: Optional[str] = None, device_id: Optional[int] = None,
                    query: str = "") -> list[dict]:
        sql, params = "SELECT * FROM events WHERE id>?", [since_id]
        if port is not None:
            sql += " AND port=?"
            params.append(port)
        if type_name:
            sql += " AND type=?"
            params.append(type_name)
        if device_id is not None:
            sql += " AND device_id=?"
            params.append(device_id)
        if query:
            sql += " AND (text LIKE ? OR decoded LIKE ?)"
            params += [f"%{query}%"] * 2
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        return self._all(sql, params)

    def iter_events(self, limit: int = 50000, port: Optional[int] = None, type_name: Optional[str] = None,
                    device_id: Optional[int] = None, query: str = "", since_id: int = 0,
                    since_ts: Optional[float] = None, until_ts: Optional[float] = None) -> list[dict]:
        """Eventi in ordine cronologico per l'esportazione (limite alto, stessi filtri di list_events)."""
        sql, params = "SELECT * FROM events WHERE id>?", [since_id]
        if port is not None:
            sql += " AND port=?"
            params.append(port)
        if type_name:
            sql += " AND type=?"
            params.append(type_name)
        if device_id is not None:
            sql += " AND device_id=?"
            params.append(device_id)
        if query:
            sql += " AND (text LIKE ? OR decoded LIKE ?)"
            params += [f"%{query}%"] * 2
        if since_ts is not None:
            sql += " AND ts>=?"
            params.append(since_ts)
        if until_ts is not None:
            sql += " AND ts<=?"
            params.append(until_ts)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, min(int(limit), 200000)))
        return self._all(sql, params)

    _PCT = re.compile(r": (\d+) %")
    _MV = re.compile(r"circa (\d+) mV")
    _GESTURE_TAIL = re.compile(r"\s*\(0x[0-9A-Fa-f]{4}\)\s*$")

    def battery_series(self, limit: int = 2000, device_id: Optional[int] = None) -> dict:
        """Percentuale (proprietà BATTERY_VOLTAGE) e millivolt (porta 6) nel tempo, dal più vecchio."""
        sql = ("SELECT ts, type, text, decoded FROM events WHERE ((type='HUB_PROPERTIES' AND text LIKE 'Proprietà BATTERY_VOLTAGE%')"
               " OR (port=6 AND decoded LIKE 'batteria circa%'))")
        params: list = []
        if device_id is not None:
            sql += " AND device_id=?"
            params.append(device_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 20000)))
        percent, millivolt = [], []
        for r in reversed(self._all(sql, params)):
            if r["type"] == "HUB_PROPERTIES":
                m = self._PCT.search(r["text"] or "")
                if m:
                    percent.append({"ts": r["ts"], "value": int(m.group(1))})
            else:
                m = self._MV.search(r["decoded"] or "")
                if m:
                    millivolt.append({"ts": r["ts"], "value": int(m.group(1))})
        return {"percent": percent, "millivolt": millivolt}

    def gesture_histogram(self, device_id: Optional[int] = None) -> dict[str, int]:
        sql, params = "SELECT decoded FROM events WHERE port=0 AND decoded LIKE 'gesto:%' AND decoded NOT LIKE 'gesto: nessuno%'", []
        if device_id is not None:
            sql += " AND device_id=?"
            params.append(device_id)
        counts: dict[str, int] = {}
        for r in self._all(sql, params):
            body = self._GESTURE_TAIL.sub("", (r["decoded"] or "")[len("gesto:"):]).strip()
            for name in body.split(", "):
                name = name.strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def tile_counts(self, device_id: Optional[int] = None) -> dict[str, int]:
        sql, params = "SELECT decoded FROM events WHERE port=1 AND decoded LIKE 'barcode 0x%'", []
        if device_id is not None:
            sql += " AND device_id=?"
            params.append(device_id)
        counts: dict[str, int] = {}
        for r in self._all(sql, params):
            part = (r["decoded"] or "").split(";", 1)[0]
            name = part.split("= ", 1)[1] if "= " in part else part
            name = name.split(" (", 1)[0].strip()
            counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def event_type_counts(self, device_id: Optional[int] = None) -> dict[str, int]:
        sql, params = "SELECT type, COUNT(*) AS n FROM events", []
        if device_id is not None:
            sql += " WHERE device_id=?"
            params.append(device_id)
        sql += " GROUP BY type ORDER BY n DESC"
        return {r["type"]: r["n"] for r in self._all(sql, params)}

    def prune_events(self, max_rows: int = 50000) -> int:
        return self._exec("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
                          (max_rows,))

    def event_stats(self) -> dict:
        row = self._one("SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM events") or {}
        per_port = self._all("SELECT port, COUNT(*) AS n FROM events WHERE port IS NOT NULL GROUP BY port ORDER BY port")
        return {"total": row.get("n", 0), "first": row.get("first"), "last": row.get("last"),
                "per_port": {r["port"]: r["n"] for r in per_port}}

    # ----- impostazioni -------------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._exec("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))

    # ----- assistente ---------------------------------------------------------------

    def add_assistant_message(self, role: str, content: str, emotion: Optional[str] = None,
                              source: Optional[str] = None, device_id: Optional[int] = None,
                              turn: Optional[int] = None, ts: Optional[float] = None) -> int:
        return self._insert(
            "INSERT INTO assistant_messages (ts, device_id, role, content, emotion, source, turn) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts or time.time(), device_id, role, content, emotion, source, turn))

    def list_assistant_messages(self, limit: int = 100, since_id: int = 0, since_ts: Optional[float] = None,
                                until_ts: Optional[float] = None) -> list[dict]:
        sql, params = "SELECT * FROM assistant_messages WHERE id>?", [since_id]
        if since_ts is not None:
            sql += " AND ts>=?"
            params.append(since_ts)
        if until_ts is not None:
            sql += " AND ts<=?"
            params.append(until_ts)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 5000)))
        return list(reversed(self._all(sql, params)))

    # ----- memoria dell'assistente -----------------------------------------------------

    def add_memory(self, text: str) -> int:
        text = " ".join(str(text).split())
        if not text:
            return 0
        row = self._one("SELECT id FROM assistant_memory WHERE lower(text)=lower(?)", (text,))
        if row:
            return int(row["id"])
        return self._insert("INSERT INTO assistant_memory (ts, text) VALUES (?, ?)", (time.time(), text))

    def list_memories(self, limit: int = 50) -> list[dict]:
        rows = self._all("SELECT * FROM assistant_memory ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),))
        return list(reversed(rows))

    def delete_memory(self, memory_id: int) -> int:
        return self._exec("DELETE FROM assistant_memory WHERE id=?", (memory_id,))

    def clear_memories(self) -> int:
        return self._exec("DELETE FROM assistant_memory")

    def assistant_messages_between(self, since_ts: float, until_ts: float) -> list[dict]:
        return self._all("SELECT * FROM assistant_messages WHERE ts>=? AND ts<? ORDER BY ts, id", (since_ts, until_ts))

    def memories_between(self, since_ts: float, until_ts: float) -> list[dict]:
        return self._all("SELECT * FROM assistant_memory WHERE ts>=? AND ts<? ORDER BY ts", (since_ts, until_ts))

    def clear_assistant_messages(self) -> int:
        return self._exec("DELETE FROM assistant_messages")

    def assistant_stats(self) -> dict:
        row = self._one("SELECT COUNT(*) AS n, MAX(ts) AS last FROM assistant_messages") or {}
        return {"total": row.get("n", 0), "last": row.get("last")}

    # ----- avventure -------------------------------------------------------------------

    def save_adventure(self, data: dict, adventure_id: Optional[int] = None) -> int:
        import json as _json
        now = time.time()
        payload = _json.dumps(data, ensure_ascii=False)
        fields = (str(data.get("name") or "Senza nome"), str(data.get("description") or ""), str(data.get("author") or ""),
                  int(data.get("version") or 1), payload)
        if adventure_id is not None and self.get_adventure(adventure_id):
            self._exec("UPDATE adventures SET name=?, description=?, author=?, version=?, json=?, updated=? WHERE id=?",
                       (*fields, now, adventure_id))
            return int(adventure_id)
        return self._insert("INSERT INTO adventures (name, description, author, version, json, created, updated) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)", (*fields, now, now))

    def list_adventures(self) -> list[dict]:
        import json as _json
        rows = self._all("SELECT id, name, description, author, version, created, updated, json FROM adventures ORDER BY id")
        for r in rows:
            try:
                r["steps"] = len(_json.loads(r.pop("json")).get("steps") or [])
            except ValueError:
                r["steps"] = 0
        return rows

    def get_adventure(self, adventure_id: int) -> Optional[dict]:
        import json as _json
        row = self._one("SELECT * FROM adventures WHERE id=?", (adventure_id,))
        if row:
            row["data"] = _json.loads(row.pop("json"))
        return row

    def delete_adventure(self, adventure_id: int) -> int:
        return self._exec("DELETE FROM adventures WHERE id=?", (adventure_id,))

    def count_adventures(self) -> int:
        return int((self._one("SELECT COUNT(*) AS n FROM adventures") or {}).get("n", 0))

    def add_run(self, adventure_id: Optional[int], name: str, started: float, ended: float, score: int, outcome: str,
                log: list) -> int:
        import json as _json
        return self._insert("INSERT INTO adventure_runs (adventure_id, name, started, ended, score, outcome, log) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (adventure_id, name, started, ended, int(score), outcome, _json.dumps(log, ensure_ascii=False, default=str)))

    def list_runs(self, adventure_id: Optional[int] = None, limit: int = 50) -> list[dict]:
        sql, params = "SELECT id, adventure_id, name, started, ended, score, outcome FROM adventure_runs", []
        if adventure_id is not None:
            sql += " WHERE adventure_id=?"
            params.append(adventure_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        return self._all(sql, params)

    def get_run(self, run_id: int) -> Optional[dict]:
        import json as _json
        row = self._one("SELECT * FROM adventure_runs WHERE id=?", (run_id,))
        if row:
            try:
                row["log"] = _json.loads(row["log"] or "[]")
            except ValueError:
                row["log"] = []
        return row

