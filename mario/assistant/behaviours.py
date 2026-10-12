"""
Comportamenti: come un'emozione si manifesta sul Mario fisico.

Via Bluetooth oggi si controlla con certezza solo il volume (proprietà 0x12). L'indicazione
"occupato" dell'hub (azioni 0x05/0x06) su Mario fw 6.5.01 risponde INVALID_USE (provato il
12/10/2026): l'opzione resta per altri firmware ma non fa nulla. Le scritture sulle porte 4 e 7
(solo WriteDirectModeData 0x51 è accettato) hanno significato ignoto e restano un esperimento.

Regole di sicurezza: solo volume, busy on/off e, se abilitato, Port Output verso le porte 4 e 7
con payload corto. Mai spegnimento, disconnessione o cambio nome in automatico.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Protocol

from ..lwp3 import HubAction
from .emotions import EMOTIONS


class MarioActions(Protocol):
    """Ciò che il motore può fare sul dispositivo (implementato dal worker web e dalla CLI)."""

    def connected(self) -> bool: ...
    def get_volume(self) -> Optional[int]: ...
    def set_volume(self, level: int) -> int: ...
    def hub_action(self, action: int) -> None: ...
    def port_output(self, port: int, subcommand: int, payload: bytes) -> None: ...


class NullActions:
    """Nessun dispositivo: usato nei test e con `assistant --no-mario`."""

    def connected(self) -> bool:
        return False

    def get_volume(self) -> Optional[int]:
        return None

    def set_volume(self, level: int) -> int:
        return level

    def hub_action(self, action: int) -> None:
        pass

    def port_output(self, port: int, subcommand: int, payload: bytes) -> None:
        pass


# Script per emozione. Livelli di volume relativi al volume di partenza ("base", letto da Mario):
# "up" = base alzato di un terzo (massimo 100), un numero tra 0 e 1 = frazione della base.
# ("sleep", secondi) attende (interrompibile), ("busy", bool) = indicazione "occupato" dell'hub.
SCRIPTS: dict[str, list[tuple]] = {
    "gioia": [("volume", "up"), ("sleep", 0.6), ("volume", "base")],
    "sorpresa": [("volume", "up"), ("sleep", 0.3), ("volume", 0.2), ("sleep", 0.3), ("volume", "base")],
    "pensieroso": [("volume", 0.4), ("sleep", 1.0), ("volume", "base")],
    "allarme": [("volume", "up"), ("sleep", 0.25), ("volume", 0.1), ("sleep", 0.25), ("volume", "up"), ("sleep", 0.25),
                ("volume", "base")],
    "tristezza": [("volume", 0.25), ("sleep", 1.5), ("volume", "base")],
    "sonno": [("volume", 0.1), ("sleep", 2.0), ("volume", "base")],
    "calma": [("volume", "base")],
    "attesa": [("busy", True)],
}


def volume_level(spec, base: int) -> int:
    if spec == "base":
        return base
    if spec == "up":
        return min(100, round(base * 4 / 3) + 5)
    if isinstance(spec, float) and spec <= 1.0:
        return max(0, min(100, round(base * spec)))
    return max(0, min(100, int(spec)))


ALLOWED_HUB_ACTIONS = {int(HubAction.ACTIVATE_BUSY), int(HubAction.RESET_BUSY)}
ALLOWED_PORTS = {4, 7}


class BehaviourEngine:
    """Esegue gli script in un thread dedicato; una nuova emozione interrompe lo script in corso."""

    def __init__(self, actions: MarioActions, emit: Optional[Callable[[dict], None]] = None,
                 enabled: bool = True, volume_effects: bool = True, busy_indication: bool = False,
                 port_writes: bool = False, port_payloads: Optional[dict[str, str]] = None):
        self.actions = actions
        self.emit = emit or (lambda ev: None)
        self.enabled = enabled
        self.volume_effects = volume_effects
        self.busy_indication = busy_indication
        self.port_writes = port_writes
        # emozione -> "porta:hex" (esperimenti dell'utente), es. {"gioia": "4:01 00 00 00"}
        self.port_payloads: dict[str, str] = dict(port_payloads or {})
        self.base_volume: Optional[int] = None
        self.last_actions: list[str] = []
        self._busy_on = False
        self._active = False
        self._pending: Optional[str] = None
        self._serial = 0
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name="behaviours", daemon=True)
        self._thread.start()

    def configure(self, **opts) -> None:
        for key in ("enabled", "volume_effects", "busy_indication", "port_writes"):
            if key in opts and opts[key] is not None:
                setattr(self, key, bool(opts[key]))
        if opts.get("port_payloads") is not None:
            self.port_payloads = {k: str(v) for k, v in dict(opts["port_payloads"]).items() if str(v).strip()}
        if "base_volume" in opts and opts["base_volume"] not in (None, ""):
            self.base_volume = max(0, min(100, int(opts["base_volume"])))

    def status(self) -> dict:
        return {"enabled": self.enabled, "volume_effects": self.volume_effects, "busy_indication": self.busy_indication,
                "port_writes": self.port_writes, "port_payloads": self.port_payloads, "base_volume": self.base_volume,
                "connected": self._connected(), "last_actions": self.last_actions[-8:]}

    # ----- API ---------------------------------------------------------------------------

    def express(self, emotion: str) -> None:
        if emotion not in EMOTIONS:
            return
        self._serial += 1
        self._pending = emotion
        self._wake.set()

    def restore(self) -> None:
        """Riporta Mario allo stato di base (volume originale, busy spento)."""
        self.express("calma")

    def shutdown(self, timeout: float = 3.0) -> None:
        """Fine sessione: ripristina lo stato di base aspettando che finisca, poi si disattiva."""
        if self.enabled and self._connected():
            self.restore()
            deadline = time.time() + timeout
            time.sleep(0.05)
            while time.time() < deadline and (self._active or self._wake.is_set()):
                time.sleep(0.05)
        self.enabled = False

    # ----- esecuzione --------------------------------------------------------------------

    def _connected(self) -> bool:
        try:
            return bool(self.actions.connected())
        except Exception:
            return False

    def _log(self, text: str) -> None:
        self.last_actions.append(f"{time.strftime('%H:%M:%S')} {text}")
        del self.last_actions[:-50]
        try:
            self.emit({"ts": time.time(), "type": "ASSISTANT", "kind": "behaviour", "text": text, "raw": "",
                       "interesting": False})
        except Exception:  # chi ascolta può essere già chiuso (fine sessione)
            pass

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            emotion, serial = self._pending, self._serial
            if emotion is None:
                continue
            self._active = True
            try:
                self._perform(emotion, serial)
            except Exception as exc:  # un errore Bluetooth non deve fermare la sessione
                self._log(f"comportamento '{emotion}' fallito: {exc}")
            finally:
                self._active = False

    def _perform(self, emotion: str, serial: int) -> None:
        if not (self.enabled and self._connected()):
            return
        if self.base_volume is None:
            try:
                self.base_volume = self.actions.get_volume()
            except Exception:
                self.base_volume = None
        if self._busy_on and emotion != "attesa":
            self._hub_action(HubAction.RESET_BUSY, "busy off")
        payload = self.port_payloads.get(emotion)
        if self.port_writes and payload:
            self._port_write(emotion, payload)
        for step in SCRIPTS.get(emotion, []):
            if self._serial != serial:
                return  # è arrivata un'altra emozione
            kind = step[0]
            if kind == "volume" and self.volume_effects:
                if self.base_volume is None:
                    continue
                level = volume_level(step[1], int(self.base_volume))
                self.actions.set_volume(level)
                self._log(f"{emotion}: volume {level}")
            elif kind == "sleep":
                if self._wake.wait(float(step[1])):  # un'altra emozione interrompe l'attesa
                    return
            elif kind == "busy" and self.busy_indication:
                self._hub_action(HubAction.ACTIVATE_BUSY if step[1] else HubAction.RESET_BUSY,
                                 "busy on" if step[1] else "busy off")

    def _hub_action(self, action: int, label: str) -> None:
        if int(action) not in ALLOWED_HUB_ACTIONS:
            return
        self.actions.hub_action(int(action))
        self._busy_on = int(action) == int(HubAction.ACTIVATE_BUSY)
        self._log(f"azione hub {label} (0x{int(action):02X})")

    def _port_write(self, emotion: str, spec: str) -> None:
        try:
            port_text, hex_text = spec.split(":", 1)
            port = int(port_text.strip(), 0)
            payload = bytes.fromhex(hex_text.replace(" ", ""))
        except ValueError:
            self._log(f"{emotion}: payload porta non valido '{spec}' (atteso porta:hex)")
            return
        if port not in ALLOWED_PORTS or not 1 <= len(payload) <= 16:
            self._log(f"{emotion}: scrittura rifiutata (porte ammesse 4 e 7, 1..16 byte)")
            return
        self.actions.port_output(port, 0x00, payload)
        self._log(f"{emotion}: scritto porta {port} <- {payload.hex(' ')} (esperimento)")
