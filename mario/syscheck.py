"""
Controllo del PC per il Bluetooth di Mario (`./run.sh doctor`, pagina Scansione della dashboard).

Le cause note dei problemi di connessione, lette senza privilegi da sysfs, rfkill, BlueZ e
NetworkManager; per ognuna il comando che la risolve:
  - nessun adattatore, adattatore bloccato da rfkill, servizio bluetooth fermo o controller spento;
  - autosospensione USB del controller: con `power/control = auto` BlueZ interrompe il collegamento
    LE subito dopo averlo stabilito (11/10); serve `on`, reso stabile da `options btusb
    enable_autosuspend=0` in /etc/modprobe.d;
  - Wi-Fi a 2,4 GHz sullo stesso chip Broadcom del Bluetooth (MacBook): il collegamento cade, sui
    5 GHz no;
  - bond con Mario (servizio LEGO 00001623-... o codice costruttore 0x0397 nei dispositivi associati).
Sui controller Apple (05ac, interfaccia ff/01/01) il kernel usa `btbcm_setup_apple`, che non carica
nessun file `.hcd`: il chip gira con il firmware interno e non c'è nulla da installare (sorgenti
del kernel 6.12, verificato il 15/10).

Nessuna dipendenza: i test usano un sysfs finto (`root`) e un `run` finto per i comandi.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

SYS = Path("/sys")
MODPROBE_DIRS = (Path("/etc/modprobe.d"), Path("/run/modprobe.d"), Path("/usr/lib/modprobe.d"),
                 Path("/lib/modprobe.d"))
FLATPAK_INFO = Path("/.flatpak-info")  # c'è solo nel sandbox di Flatpak, dove /etc e i comandi non sono dell'host
LEGO_HUB_UUID = "00001623-1212-efde-1623-785feabcd123"
LEGO_COMPANY_KEY = "0x0397"
BROADCOM_VENDORS = ("05ac", "0a5c")  # Apple (moduli Broadcom dei Mac) e Broadcom

OK, INFO, WARN, ERROR = "ok", "info", "attenzione", "errore"
SYMBOLS = {OK: "✓", INFO: "·", WARN: "!", ERROR: "✗"}

Runner = Callable[[list[str]], Optional[str]]


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


@dataclass
class Adapter:
    name: str                # hci0
    usb: Optional[Path]      # cartella del dispositivo USB in sysfs (con idVendor e power/)
    vendor: str = ""
    product: str = ""
    driver: str = ""
    interface: str = ""      # classe/sottoclasse/protocollo dell'interfaccia, es. ff/01/01

    @property
    def apple(self) -> bool:
        return self.vendor == "05ac" and self.interface == "ff/01/01"


def run_command(cmd: list[str]) -> Optional[str]:
    """Uscita del comando, None se non c'è o non risponde (es. nel sandbox Flatpak)."""
    exe = shutil.which(cmd[0]) or next((f"{d}/{cmd[0]}" for d in ("/usr/sbin", "/sbin")
                                        if os.access(f"{d}/{cmd[0]}", os.X_OK)), None)
    if exe is None:
        return None
    try:
        proc = subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def adapters(root: Path = SYS) -> list[Adapter]:
    found = []
    folder = root / "class" / "bluetooth"
    for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not re.fullmatch(r"hci\d+", entry.name):
            continue
        iface = (entry / "device").resolve()
        usb = iface.parent if (iface.parent / "idVendor").is_file() else None
        driver = (iface / "driver").resolve().name if (iface / "driver").exists() else ""
        klass = "/".join(_read(iface / f"bInterface{k}").lower() for k in ("Class", "SubClass", "Protocol"))
        found.append(Adapter(entry.name, usb, _read(usb / "idVendor") if usb else "",
                             _read(usb / "idProduct") if usb else "", driver, klass if klass != "//" else ""))
    return found


def check_adapters(found: list[Adapter]) -> list[Check]:
    if not found:
        return [Check("adattatore", ERROR, "nessun adattatore Bluetooth nel sistema",
                      "controllare `rfkill list` o collegare una chiavetta Bluetooth USB")]
    checks = []
    for a in found:
        ident = f"{a.vendor}:{a.product}" if a.usb else "non USB"
        kind = " (Apple, chip Broadcom)" if a.apple else ""
        checks.append(Check("adattatore", OK, f"{a.name}: {ident}{kind}, driver {a.driver or '?'}"))
    if len(found) > 1:
        checks.append(Check("adattatore", INFO, f"{len(found)} adattatori: si sceglie con `-i hciN`"
                                                " (default quello predefinito di BlueZ)"))
    for a in found:
        if a.apple:
            checks.append(Check("firmware", INFO, f"{a.name}: controller Apple, il kernel non carica patch "
                                                  "(btbcm_setup_apple) e il chip usa il firmware interno: "
                                                  "nessun file .hcd da installare"))
    return checks


def check_rfkill(root: Path = SYS) -> list[Check]:
    checks = []
    folder = root / "class" / "rfkill"
    for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
        if _read(entry / "type") != "bluetooth":
            continue
        name = _read(entry / "name") or entry.name
        if _read(entry / "hard") == "1":
            checks.append(Check("rfkill", ERROR, f"{name}: bloccato dall'interruttore hardware o dal firmware",
                                "sbloccarlo dal tasto o dal BIOS"))
        elif _read(entry / "soft") == "1":
            checks.append(Check("rfkill", ERROR, f"{name}: Bluetooth disattivato (rfkill)", "rfkill unblock bluetooth"))
    return checks


def check_service(run: Runner) -> list[Check]:
    state = run(["systemctl", "is-active", "bluetooth"])
    if state is None:
        return [Check("servizio", INFO, "systemctl non disponibile: stato del servizio non controllato")]
    state = state.strip()
    if state == "active":
        return [Check("servizio", OK, "bluetooth.service attivo")]
    return [Check("servizio", ERROR, f"bluetooth.service {state or 'sconosciuto'}", "sudo systemctl start bluetooth")]


def check_powered(run: Runner) -> list[Check]:
    show = run(["bluetoothctl", "show"])
    if show is None:
        return [Check("controller", INFO, "bluetoothctl non disponibile (sandbox Flatpak?): usare ./run.sh doctor")]
    found = re.search(r"Powered:\s*(yes|no)", show)
    if found is None:
        return [Check("controller", WARN, "BlueZ non vede nessun controller", "sudo systemctl restart bluetooth")]
    if found.group(1) == "no":
        return [Check("controller", WARN, "controller spento", "bluetoothctl power on")]
    return [Check("controller", OK, "controller acceso")]


def modprobe_option(dirs=MODPROBE_DIRS) -> Optional[Path]:
    """Il file che disattiva l'autosospensione di btusb (`options btusb enable_autosuspend=0`), se c'è."""
    pattern = re.compile(r"^\s*options\s+btusb\b.*\benable_autosuspend\s*=\s*(0|n)\b", re.I | re.M)
    for folder in dirs:
        for conf in sorted(folder.glob("*.conf")) if folder.is_dir() else []:
            if pattern.search(_read(conf)):
                return conf
    return None


def check_autosuspend(found: list[Adapter], root: Path = SYS, dirs=MODPROBE_DIRS) -> list[Check]:
    checks = []
    param = _read(root / "module" / "btusb" / "parameters" / "enable_autosuspend").upper()
    conf = modprobe_option(dirs)
    persist = "echo 'options btusb enable_autosuspend=0' | sudo tee /etc/modprobe.d/btusb.conf"
    for a in found:
        if a.usb is None or a.driver != "btusb":
            continue
        control = _read(a.usb / "power" / "control")
        node = a.usb / "power" / "control"
        if control == "auto":
            fix = f"echo on | sudo tee {node}" + ("" if conf else f"; per i prossimi avvii: {persist}")
            checks.append(Check("autosospensione", WARN, f"{a.name}: autosospensione USB attiva: BlueZ interrompe "
                                                         "il collegamento con Mario subito dopo averlo stabilito", fix))
        elif control == "on" and param in ("N", "0"):
            checks.append(Check("autosospensione", OK, f"{a.name}: disattivata (opzione del modulo btusb)"))
        elif control == "on" and conf is not None:
            checks.append(Check("autosospensione", OK, f"{a.name}: disattivata a mano; dal prossimo avvio vale {conf}"
                                                       " (subito: sudo modprobe -r btusb && sudo modprobe btusb)"))
        elif control == "on":
            checks.append(Check("autosospensione", WARN, f"{a.name}: disattivata a mano, ma al prossimo avvio btusb "
                                                         "la riattiva", persist))
    return checks


def wifi_frequency(iface: str, run: Runner) -> Optional[int]:
    """MHz del collegamento Wi-Fi in corso (nmcli, poi iw); None se non collegato o non si sa."""
    out = run(["nmcli", "-t", "-f", "IN-USE,FREQ", "dev", "wifi", "list", "ifname", iface, "--rescan", "no"])
    for line in (out or "").splitlines():
        found = re.match(r"\*:(\d+)\s*MHz", line)
        if found:
            return int(found.group(1))
    found = re.search(r"freq:\s*(\d+)", run(["iw", "dev", iface, "link"]) or "")
    return int(found.group(1)) if found else None


def check_wifi(found: list[Adapter], run: Runner, root: Path = SYS) -> list[Check]:
    checks = []
    shared = any(a.vendor in BROADCOM_VENDORS for a in found)
    folder = root / "class" / "net"
    for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not (entry / "wireless").is_dir():
            continue
        driver = (entry / "device" / "driver").resolve().name if (entry / "device" / "driver").exists() else ""
        if _read(entry / "operstate") != "up":
            checks.append(Check("Wi-Fi", OK, f"{entry.name}: non collegato"))
            continue
        mhz = wifi_frequency(entry.name, run)
        if mhz is None:
            checks.append(Check("Wi-Fi", INFO, f"{entry.name}: banda sconosciuta (servono nmcli o iw)"))
        elif mhz >= 4900:
            checks.append(Check("Wi-Fi", OK, f"{entry.name}: 5 GHz ({mhz} MHz)"))
        elif shared and driver == "brcmfmac":
            checks.append(Check("Wi-Fi", WARN, f"{entry.name}: 2,4 GHz ({mhz} MHz) sullo stesso chip Broadcom del "
                                               "Bluetooth: il collegamento con Mario cade",
                                "collegarsi ai 5 GHz della stessa rete, oppure usare una chiavetta Bluetooth USB "
                                "(`-i hci1`)"))
        else:
            checks.append(Check("Wi-Fi", INFO, f"{entry.name}: 2,4 GHz ({mhz} MHz); se il collegamento con Mario è "
                                               "instabile, provare i 5 GHz"))
    return checks


def check_bond(run: Runner) -> list[Check]:
    listing = run(["bluetoothctl", "devices", "Paired"])
    if listing is None:
        return []
    hubs = []
    for address in re.findall(r"^Device\s+([0-9A-F:]{17})", listing, re.M):
        info = run(["bluetoothctl", "info", address]) or ""
        if LEGO_HUB_UUID in info.lower() or LEGO_COMPANY_KEY in info:
            hubs.append(address)
    if not hubs:
        return [Check("pairing", INFO, "nessun hub LEGO associato: al primo collegamento l'app fa il pairing "
                                       "Just Works da sola")]
    return [Check("pairing", OK, f"hub LEGO associati (bond): {', '.join(hubs)}")]


def check_versions(run: Runner) -> list[Check]:
    bluez = re.search(r"(\d+\.\d+)", run(["bluetoothctl", "--version"]) or "")
    return [Check("versioni", INFO, f"kernel {os.uname().release}, BlueZ {bluez.group(1) if bluez else '?'}")]


def run_checks(root: Path = SYS, run: Runner = run_command, dirs=MODPROBE_DIRS) -> list[Check]:
    found = adapters(root)
    if FLATPAK_INFO.exists() and root == SYS:
        return [Check("sandbox", WARN, "nel sandbox Flatpak /etc, BlueZ e NetworkManager non sono quelli del PC",
                      "./run.sh doctor (esegue il controllo sull'host)")] + check_adapters(found)
    return (check_adapters(found) + check_rfkill(root) + check_service(run) + check_powered(run)
            + check_autosuspend(found, root, dirs) + check_wifi(found, run, root) + check_bond(run)
            + check_versions(run))


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]


def worst(checks: list[Check]) -> str:
    for status in (ERROR, WARN):
        if any(c.status == status for c in checks):
            return status
    return OK


def format_checks(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f"{SYMBOLS.get(c.status, '?')} {c.name:<16} {c.detail}")
        if c.fix:
            lines.append(f"  {'':<16} → {c.fix}")
    summary = {OK: "nessun problema noto", WARN: "da sistemare (vedi le righe con !)",
               ERROR: "Bluetooth non utilizzabile (vedi le righe con ✗)"}[worst(checks)]
    lines.append(f"\nEsito: {summary}")
    return "\n".join(lines)
