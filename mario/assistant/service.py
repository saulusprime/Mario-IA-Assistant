"""
Collegamento tra la sessione dell'assistente, il worker Bluetooth e il database della dashboard.

- legge e salva le impostazioni nella tabella `settings` (chiavi `assistant.*`);
- consuma il flusso eventi del worker e lo passa alla sessione (sensori -> intenti);
- rimanda gli eventi dell'assistente nello stesso flusso SSE della dashboard;
- salva la conversazione nella tabella `assistant_messages`.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

from .behaviours import BehaviourEngine
from .intents import IntentMapper
from .llm import LLMConfig, make_backend
from .memory import DatabaseMemory
from .safety import parse_themes
from .session import AssistantSession
from .tts import Speaker

log = logging.getLogger("mario.assistant")

DEFAULTS: dict[str, str] = {
    "assistant.backend": "auto", "assistant.model": "", "assistant.sensor_model": "auto", "assistant.api_key": "",
    "assistant.base_url": "",
    "assistant.max_tokens": "350", "assistant.temperature": "0.8",
    "assistant.tts": "1", "assistant.voice": "", "assistant.rate": "0", "assistant.pitch": "0", "assistant.sounds": "1",
    "assistant.behaviours": "1", "assistant.volume_effects": "1", "assistant.busy_indication": "0",
    "assistant.port_writes": "0", "assistant.port_payloads": "{}",
    "assistant.listening": "1", "assistant.persona": "", "assistant.rules": "{}",
    "assistant.piper_model": "", "assistant.session_minutes": "0", "assistant.blocked_words": "", "assistant.memory": "1",
    "assistant.openvoice_reference": "", "assistant.openvoice_tau": "0.3", "assistant.openvoice_watermark": "0",
    "assistant.sound_style": "giochi", "assistant.voice_effect": "", "assistant.exclamations": "1",
    "assistant.challenges": "1", "assistant.wait_seconds": "20",
    "assistant.voice_input": "0", "assistant.voice_button": "1", "assistant.stt_model": "small",
    "assistant.safety_themes": "violenza,adulti,dati_personali", "assistant.session_turns": "0",
}
BOOL_KEYS = {"tts", "sounds", "behaviours", "volume_effects", "busy_indication", "port_writes", "listening", "memory",
             "openvoice_watermark", "exclamations", "challenges", "voice_input", "voice_button"}
SECRET_KEYS = {"api_key"}


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "on", "yes", "sì", "si")


def as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def split_words(text: str) -> list[str]:
    return [w.strip() for w in str(text or "").replace(";", ",").split(",") if w.strip()]


def minutes_to_seconds(text: Any) -> float:
    try:
        return max(0.0, float(text or 0)) * 60.0
    except (TypeError, ValueError):
        return 0.0


class WorkerActions:
    """Adatta `BleWorker` al protocollo `MarioActions` del motore dei comportamenti."""

    def __init__(self, worker):
        self.worker = worker

    def connected(self) -> bool:
        dev = self.worker.device
        return dev is not None and dev.connected

    def get_volume(self) -> Optional[int]:
        dev = self.worker.device
        if dev is None:
            return None
        return self.worker.call(lambda: dev.get_volume(), timeout=10)

    def set_volume(self, level: int) -> int:
        return self.worker.set_volume(level)

    def hub_action(self, action: int) -> None:
        self.worker.hub_action(action)

    def port_output(self, port: int, subcommand: int, payload: bytes) -> None:
        self.worker.port_output(port, subcommand, payload)


class AssistantService:
    def __init__(self, worker, db, quiet: bool = False, start: bool = True):
        self.worker = worker
        self.db = db
        self.quiet = quiet
        s = self.settings()
        self.speaker = Speaker(enabled=as_bool(s["tts"]) and not quiet, voice=s["voice"], rate=int(s["rate"] or 0),
                               pitch=int(s["pitch"] or 0), sounds=as_bool(s["sounds"]) and not quiet,
                               piper_model=self._piper_model(s), openvoice_reference=s["openvoice_reference"],
                               voice_effect=s["voice_effect"],
                               openvoice_tau=as_float(s["openvoice_tau"], 0.3),
                               openvoice_watermark=as_bool(s["openvoice_watermark"]), sound_style=s["sound_style"])
        self.behaviours = BehaviourEngine(WorkerActions(worker), emit=self._emit,
                                          enabled=as_bool(s["behaviours"]) and not quiet,
                                          volume_effects=as_bool(s["volume_effects"]),
                                          busy_indication=as_bool(s["busy_indication"]),
                                          port_writes=as_bool(s["port_writes"]),
                                          port_payloads=self._json(s["port_payloads"]))
        self.session = AssistantSession(self._make_backend, self.speaker, self.behaviours, emit=self._emit,
                                        mapper=IntentMapper(), persona=s["persona"], on_message=self._store,
                                        memory=DatabaseMemory(db), memory_enabled=as_bool(s["memory"]),
                                        session_limit=minutes_to_seconds(s["session_minutes"]),
                                        blocked_words=split_words(s["blocked_words"]),
                                        themes=parse_themes(s["safety_themes"]),
                                        session_turns=as_int(s["session_turns"]),
                                        exclamations=as_bool(s["exclamations"]),
                                        sensor_backend_factory=self._make_sensor_backend,
                                        challenges=as_bool(s["challenges"]),
                                        wait_seconds=as_float(s["wait_seconds"], 20.0))
        self.session.listening = as_bool(s["listening"])
        self._voice = None
        self._configure_voice(s)
        self._apply_rules(self._json(s["rules"]))
        self._thread = threading.Thread(target=self._consume, name="assistant-events", daemon=True)
        if start:
            self._thread.start()

    # ----- impostazioni -------------------------------------------------------------------

    @staticmethod
    def _json(text: str) -> dict:
        try:
            value = json.loads(text or "{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def settings(self) -> dict[str, str]:
        return {key.split(".", 1)[1]: (self.db.get_setting(key, default) or "") for key, default in DEFAULTS.items()}

    @staticmethod
    def _piper_model(s: dict) -> str:
        """Il modello piper scelto; con lo stile Mario e nessun modello, la voce maschile di base (riccardo)."""
        if s.get("piper_model") or s.get("voice_effect") != "mario":
            return s.get("piper_model") or ""
        from .openvoice import mario_base_voice
        return mario_base_voice()

    def public_settings(self) -> dict:
        s = self.settings()
        out: dict[str, Any] = {}
        for k, v in s.items():
            if k in SECRET_KEYS:
                out[k] = ""
                out[k + "_set"] = bool(v)
            elif k in BOOL_KEYS:
                out[k] = as_bool(v)
            else:
                out[k] = v
        out["env_anthropic_key"] = bool(__import__("os").environ.get("ANTHROPIC_API_KEY"))
        out["resolved_backend"] = self.config().resolved_backend()
        return out

    def update_settings(self, data: dict) -> dict:
        for key in DEFAULTS:
            short = key.split(".", 1)[1]
            if short not in data:
                continue
            value = data[short]
            if short in SECRET_KEYS and value in ("", None) and not data.get("clear_" + short):
                continue  # campo lasciato vuoto: la chiave salvata resta
            if short in BOOL_KEYS:
                value = "1" if as_bool(value) else "0"
            elif short in ("port_payloads", "rules"):
                value = json.dumps(value if isinstance(value, dict) else self._json(str(value)), ensure_ascii=False)
            self.db.set_setting(key, str(value if value is not None else ""))
        s = self.settings()
        self.speaker.configure(enabled=as_bool(s["tts"]) and not self.quiet, voice=s["voice"], rate=s["rate"] or 0,
                               pitch=s["pitch"] or 0, sounds=as_bool(s["sounds"]) and not self.quiet,
                               piper_model=self._piper_model(s), openvoice_reference=s["openvoice_reference"],
                               voice_effect=s["voice_effect"],
                               openvoice_tau=as_float(s["openvoice_tau"], 0.3),
                               openvoice_watermark=as_bool(s["openvoice_watermark"]), sound_style=s["sound_style"])
        self.session.configure_safety(session_limit=minutes_to_seconds(s["session_minutes"]),
                                      blocked_words=split_words(s["blocked_words"]), memory_enabled=as_bool(s["memory"]),
                                      themes=parse_themes(s["safety_themes"]), session_turns=as_int(s["session_turns"]))
        self.behaviours.configure(enabled=as_bool(s["behaviours"]) and not self.quiet,
                                  volume_effects=as_bool(s["volume_effects"]),
                                  busy_indication=as_bool(s["busy_indication"]), port_writes=as_bool(s["port_writes"]),
                                  port_payloads=self._json(s["port_payloads"]))
        self.session.persona = s["persona"]
        self.session.exclamations_enabled = as_bool(s["exclamations"])
        self.session.challenges_enabled = as_bool(s["challenges"])
        self._configure_voice(s)
        self.session.wait_seconds = min(120.0, max(5.0, as_float(s["wait_seconds"], 20.0)))
        if "listening" in data and as_bool(data["listening"]) != self.session.listening:
            self.session.set_listening(as_bool(data["listening"]))
        self._apply_rules(self._json(s["rules"]))
        return self.public_settings()

    def _configure_voice(self, s: dict) -> None:
        """Ingresso vocale (microfono -> faster-whisper): acceso solo se richiesto e mai in modalità silenziosa
        (test); il modello si carica in background appena si accende."""
        from .stt import MODELS, VoiceInput
        model = s["stt_model"] if s["stt_model"] in MODELS else MODELS[0]
        wanted = as_bool(s["voice_input"]) and not self.quiet
        if self._voice is not None and (not wanted or self._voice.model != model):
            self._voice.close()
            self._voice = None
        if wanted and self._voice is None:
            self._voice = VoiceInput(model)
            self._voice.warmup()
        self.session.voice_input = self._voice
        self.session.voice_button = as_bool(s["voice_button"])

    def _apply_rules(self, overrides: dict) -> None:
        for name, opts in overrides.items():
            if name in self.session.mapper.rules and isinstance(opts, dict):
                self.session.mapper.set_rule(name, enabled=opts.get("enabled"), text=opts.get("text"),
                                             cooldown=opts.get("cooldown"))

    def config(self) -> LLMConfig:
        s = self.settings()
        return LLMConfig(backend=s["backend"] or "auto", model=s["model"], sensor_model=s["sensor_model"],
                         api_key=s["api_key"], base_url=s["base_url"],
                         max_tokens=int(s["max_tokens"] or 350), temperature=float(s["temperature"] or 0.8))

    def _make_backend(self):
        return make_backend(self.config())

    def _make_sensor_backend(self):
        return make_backend(self.config().for_sensors())

    # ----- collegamenti --------------------------------------------------------------------

    def _emit(self, ev: dict) -> None:
        self.worker.push(ev)

    def _store(self, msg: dict) -> None:
        try:
            self.db.add_assistant_message(msg["role"], msg["content"], emotion=msg.get("emotion"),
                                          source=msg.get("source"), device_id=self.worker.device_row_id,
                                          turn=msg.get("turn"), ts=msg.get("ts"))
        except Exception as exc:
            log.debug("salvataggio messaggio fallito: %s", exc)

    def _consume(self) -> None:
        import queue as _queue
        q = self.worker.open_stream()
        while True:
            try:
                ev = q.get(timeout=0.5)
            except _queue.Empty:
                try:
                    self.session.tick()
                except Exception as exc:
                    log.debug("tick dell'assistente fallito: %s", exc)
                continue
            if ev.get("type") not in ("PORT_VALUE_SINGLE", "HUB_PROPERTIES"):
                continue
            try:
                self.session.handle_event(ev)
            except Exception as exc:
                log.debug("evento non gestito dall'assistente: %s", exc)

    def status(self) -> dict:
        st = self.session.status()
        st["settings"] = self.public_settings()
        st["llm"] = self.config().to_dict()
        return st
