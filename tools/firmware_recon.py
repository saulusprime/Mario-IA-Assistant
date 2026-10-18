#!/usr/bin/env python3
"""
Ricognizione del firmware di LEGO Mario (TODO, sezione 7): solo lettura, nessun accesso a Mario.

L'app ufficiale (pacchetto Android `com.lego.supermario`) porta con sé le immagini che installa via OAD:
`LEAF_FW_V<versione>_GE.bin` (firmware; "LEAF" è il nome che Mario stampa nella traccia DEBUG della
porta 3) e, per ogni personaggio, `<Nome>_AssetPack-…bin` e `<Nome>_OadFull-…bin` (suoni e animazioni?).
Lo strumento:

  phone    copia dal telefono (adb, Debug USB attivo) gli APK dell'app in data/firmware/apk/<versione>/
           e dice se il log HCI snoop di Android è attivo; poi estrae e analizza;
  extract  cerca quei file dentro APK, XAPK, APKM e zip (anche annidati) e li copia in data/firmware/;
  analyze  per ogni file: dimensione, SHA-256, entropia (cifrato o compresso?), intestazione OAD di
           Texas Instruments, tabella dei vettori ARM Cortex-M, stringhe note ("LEAF", "Events", ...).

Esito del 18/10 (app Android 2.11.8): l'APK non contiene né firmware né pacchetti di asset, che l'app riceve
da Google Play nella sua memoria privata; ricognizione chiusa (vedi HANDOFF). Lo strumento resta per le
versioni dell'app che li portano dentro l'APK.

data/ è fuori da git: le immagini sono di LEGO e restano private. Nessuna scrittura sul telefono né
su Mario; sul servizio OAD di Mario non si scrive mai.

Uso (sull'host):
  ./run.sh firmware-recon phone
  ./run.sh firmware-recon extract percorso/app.apk [altri.apk]
  ./run.sh firmware-recon analyze data/firmware/LEAF_FW_V65030000_GE.bin [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mario.paths import DATA_DIR  # noqa: E402

PACKAGE = "com.lego.supermario"
OUT_DIR = DATA_DIR / "firmware"
# nomi dei file dell'app (visti negli elenchi pubblici delle versioni 2020-2023)
WANTED = re.compile(r"(LEAF_FW_V[0-9A-Fa-f]{8}[^/]*\.bin|[^/]*AssetPack[^/]*\.(bin|zip)|[^/]*OadFull[^/]*\.bin)$")
CONTAINERS = (".apk", ".xapk", ".apkm", ".zip", ".obb", ".apks")
# intestazioni OAD di Texas Instruments (oad_image_header.h): CC26x2/CC13x2 e CC2640R2
OAD_IMAGE_IDS = {b"CC26x2R1": "CC26x2 (SimpleLink)", b"CC13x2R1": "CC13x2 (SimpleLink)", b"CC26x0R2": "CC2640R2",
                 b"OAD IMG ": "CC2640R2 (BLE-Stack 3)", b"CC23x0R2": "CC23x0", b"CC23x0R5": "CC23x0"}
OAD_HEADER = struct.Struct("<8sIBBHBBBBIII4sIHH")  # 44 byte, vedi oad_image_header.h
OAD_IMG_TYPES = {0: "persistente", 1: "applicazione", 2: "stack", 3: "app+stack", 4: "rete (processore)",
                 5: "BIM", 6: "app+stack fusi", 7: "solo stack", 8: "solo applicazione"}
OAD_SEGMENTS = {0: "boundary", 1: "contiguous", 2: "zero", 3: "security", 4: "fl_ext"}
KEYWORDS = ("LEAF", "Game identifier", "GAMEFILE", "Events", "Telemetry", "LEGO", "Mario", "Luigi", "Peach",
            "Lego", "version", "Version", "assert", ".c", "OAD", "Asset", "WAV", "RIFF", "Goomba", "Bowser")
BLOCK = 4096


def phone(out_dir: Path) -> list[Path]:
    """APK dell'app dal telefono collegato via adb; stampa anche lo stato del log HCI snoop."""
    def wait(seconds: float) -> None:
        try:
            subprocess.run(["adb", "wait-for-device"], capture_output=True, timeout=seconds)
        except subprocess.TimeoutExpired:
            pass

    def adb(*args: str, tries: int = 6) -> str:
        # con un cavo debole la connessione cade e torna in un secondo: si aspetta il telefono e si riprova
        for attempt in range(tries):
            if attempt:
                wait(60)
            result = subprocess.run(["adb", *args], capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                return result.stdout
        raise RuntimeError(f"adb {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")

    if not shutil.which("adb"):
        raise RuntimeError("adb non trovato (pacchetto Debian `adb`)")
    wait(30)
    devices = [line for line in adb("devices").splitlines()[1:] if line.strip()]
    if not devices:
        raise RuntimeError("nessun telefono: attivare il Debug USB, collegare il cavo e accettare la richiesta")
    if any("unauthorized" in d for d in devices):
        raise RuntimeError("il telefono non ha ancora autorizzato questo PC: accettare la richiesta sullo schermo")
    snoop = adb("shell", "getprop", "persist.bluetooth.btsnooplogmode").strip() or \
        adb("shell", "settings", "get", "secure", "bluetooth_hci_log").strip()
    print(f"log HCI snoop di Android: {snoop or 'sconosciuto'}")
    dump = adb("shell", "dumpsys", "package", PACKAGE)
    version = (re.search(r"versionName=(\S+)", dump) or [None, "sconosciuta"])[1]
    paths = [line.split(":", 1)[1].strip() for line in adb("shell", "pm", "path", PACKAGE).splitlines()
             if line.startswith("package:")]
    if not paths:
        raise RuntimeError(f"l'app {PACKAGE} non è installata sul telefono")
    target = out_dir / "apk" / version
    target.mkdir(parents=True, exist_ok=True)
    pulled = []
    for remote in paths:
        local = target / Path(remote).name
        adb("pull", remote, str(local))
        pulled.append(local)
        print(f"copiato {remote} -> {local} ({local.stat().st_size // 1024} kB)")
    print(f"app {PACKAGE} versione {version}: {len(pulled)} APK")
    return pulled


def _walk(data: bytes, label: str, depth: int = 0):
    """Voci (nome, byte) di uno zip, scendendo negli zip annidati (XAPK -> APK -> ...)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if WANTED.search(name):
            yield f"{label}!{name}", archive.read(info)
        elif depth < 3 and name.lower().endswith(CONTAINERS):
            yield from _walk(archive.read(info), f"{label}!{name}", depth + 1)


def extract(sources: list[Path], out_dir: Path) -> list[Path]:
    """Copia in out_dir i file del firmware e degli asset trovati nei contenitori; li analizza."""
    out_dir.mkdir(parents=True, exist_ok=True)
    found = []
    for source in sources:
        for where, blob in _walk(source.read_bytes(), source.name):
            name = Path(where.split("!")[-1]).name
            dest = out_dir / name
            if dest.exists() and dest.read_bytes() != blob:
                dest = out_dir / f"{dest.stem}-{hashlib.sha256(blob).hexdigest()[:8]}{dest.suffix}"
            dest.write_bytes(blob)
            found.append(dest)
            print(f"trovato {where} ({len(blob) // 1024} kB) -> {dest}")
    if not found:
        print("nessun file LEAF_FW/AssetPack/OadFull negli archivi: l'app potrebbe scaricarli a parte "
              "(cercare URL con `strings` nei file .so e in global-metadata.dat)")
    return found


def entropy(data: bytes) -> float:
    """Entropia di Shannon in bit per byte (8 = casuale: cifrato o compresso)."""
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def strings(data: bytes, minimum: int = 6) -> list[tuple[int, str]]:
    return [(m.start(), m.group().decode("ascii")) for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minimum, data)]


def oad_header(data: bytes) -> dict | None:
    """Intestazione OAD di TI all'inizio del file (e i suoi segmenti), oppure None."""
    if len(data) < OAD_HEADER.size or data[:8] not in OAD_IMAGE_IDS:
        return None
    (img_id, crc, bim_ver, meta_ver, tech, cp_stat, crc_stat, img_type, img_no, img_vld, length, entry, soft_ver,
     end_addr, hdr_len, _rfu) = OAD_HEADER.unpack_from(data)
    header = {"chip": OAD_IMAGE_IDS[img_id], "image_id": img_id.decode(), "crc32": f"0x{crc:08X}",
              "crc_ok": None, "bim_ver": bim_ver, "meta_ver": meta_ver, "tech_type": f"0x{tech:04X}",
              "image_type": OAD_IMG_TYPES.get(img_type, img_type), "image_no": img_no, "length": length,
              "entry": f"0x{entry:08X}", "soft_ver": soft_ver.decode("ascii", "replace"),
              "end_addr": f"0x{end_addr:08X}", "header_len": hdr_len, "segments": []}
    # il CRC di TI copre l'immagine dal byte 12 (dopo imgID e crc32) fino a len
    if 12 < length <= len(data):
        header["crc_ok"] = (zlib.crc32(data[12:length]) & 0xFFFFFFFF) == crc
    offset = OAD_HEADER.size
    while offset + 8 <= min(len(data), max(hdr_len, OAD_HEADER.size) + 64) and len(header["segments"]) < 8:
        seg_type, wireless, _r, payload_len = struct.unpack_from("<BHBI", data, offset)
        if seg_type not in OAD_SEGMENTS or payload_len < 8 or payload_len > len(data):
            break
        header["segments"].append({"type": OAD_SEGMENTS[seg_type], "offset": offset, "len": payload_len,
                                   "wireless": f"0x{wireless:04X}"})
        if seg_type == 1:  # contiguous: dopo i suoi 8 byte di testa c'è il codice, fine dei segmenti di testa
            break
        offset += payload_len
    return header


def vector_tables(data: bytes, limit: int = 0x2000) -> list[dict]:
    """Possibili tabelle dei vettori Cortex-M: stack in RAM (0x2000xxxx) e reset dispari in flash."""
    found = []
    for offset in range(0, min(len(data) - 8, limit), 4):
        sp, reset = struct.unpack_from("<II", data, offset)
        if 0x20000000 <= sp <= 0x20100000 and sp % 4 == 0 and reset & 1 and reset < 0x00200000:
            nmi, hard = struct.unpack_from("<II", data, offset + 8) if offset + 16 <= len(data) else (0, 0)
            if nmi & 1 and hard & 1:
                found.append({"offset": offset, "sp": f"0x{sp:08X}", "reset": f"0x{reset:08X}"})
    return found


def version_from_name(name: str) -> str | None:
    m = re.search(r"LEAF_FW_V([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})", name)
    return f"{int(m[1][0], 16)}.{int(m[1][1], 16)}.{m[2]}" if m else None


def analyze(path: Path) -> dict:
    data = path.read_bytes()
    blocks = [entropy(data[i:i + BLOCK]) for i in range(0, len(data), BLOCK)]
    found = strings(data)
    interesting = [(off, s) for off, s in found if any(k in s for k in KEYWORDS)]
    report = {
        "file": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "version_from_name": version_from_name(path.name),
        "entropy": round(entropy(data), 3),
        "blocks": {"total": len(blocks), "high (>7.5)": sum(e > 7.5 for e in blocks),
                   "medium": sum(4.0 <= e <= 7.5 for e in blocks), "low (<4)": sum(e < 4.0 for e in blocks)},
        "head": data[:64].hex(" "),
        "oad": oad_header(data),
        "vector_tables": vector_tables(data)[:4],
        "strings": len(found),
        "interesting": [{"offset": off, "text": s[:120]} for off, s in interesting[:80]],
        "leaf_strings": [s for _, s in found if "LEAF" in s][:10],
    }
    high = report["blocks"]["high (>7.5)"] / max(1, len(blocks))
    # anche i dati casuali contengono stringhe brevi per caso: contano entropia e stringhe note
    if high > 0.9 and not report["leaf_strings"] and not report["vector_tables"] and not report["oad"]:
        report["verdict"] = "quasi tutto ad alta entropia e quasi nessuna stringa: probabilmente cifrato o compresso"
    elif report["leaf_strings"] or report["vector_tables"]:
        report["verdict"] = "in chiaro: stringhe del firmware e/o tabella dei vettori leggibili"
    else:
        report["verdict"] = "in parte leggibile: guardare blocchi ed eventuali sezioni compresse"
    return report


def print_report(r: dict) -> None:
    print(f"\n=== {r['file']}  ({r['size']} byte, sha256 {r['sha256'][:16]}…)")
    if r["version_from_name"]:
        print(f"versione dal nome: {r['version_from_name']}")
    b = r["blocks"]
    print(f"entropia {r['entropy']} bit/byte; blocchi da 4 KiB: {b['high (>7.5)']} alti, {b['medium']} medi, "
          f"{b['low (<4)']} bassi su {b['total']}")
    print(f"primi 64 byte: {r['head']}")
    if r["oad"]:
        o = r["oad"]
        print(f"intestazione OAD {o['image_id']} ({o['chip']}): tipo {o['image_type']}, lunghezza {o['length']}, "
              f"entry {o['entry']}, versione '{o['soft_ver']}', CRC {o['crc32']} "
              f"({'giusto' if o['crc_ok'] else 'non verificato' if o['crc_ok'] is None else 'diverso'})")
        for s in o["segments"]:
            print(f"  segmento {s['type']} a {s['offset']}, {s['len']} byte")
    else:
        print("nessuna intestazione OAD di TI riconosciuta all'inizio")
    for v in r["vector_tables"]:
        print(f"tabella dei vettori? a 0x{v['offset']:X}: SP {v['sp']}, reset {v['reset']}")
    print(f"stringhe ASCII (≥ 6 caratteri): {r['strings']}; con 'LEAF': {r['leaf_strings'] or 'nessuna'}")
    for item in r["interesting"][:25]:
        print(f"  0x{item['offset']:06X}  {item['text']}")
    print(f"giudizio: {r['verdict']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ricognizione del firmware di LEGO Mario (sola lettura)")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help=f"cartella dei file estratti (default {OUT_DIR})")
    ap.add_argument("--json", action="store_true", help="rapporto in JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("phone", help="APK dell'app dal telefono via adb, poi estrazione e analisi")
    p = sub.add_parser("extract", help="estrae firmware e asset da APK/XAPK/zip, poi li analizza")
    p.add_argument("sources", nargs="+", type=Path)
    p = sub.add_parser("analyze", help="analizza file già estratti")
    p.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "phone":
            files = extract(phone(args.out), args.out)
        elif args.cmd == "extract":
            files = extract(args.sources, args.out)
        else:
            files = args.files
    except RuntimeError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    reports = [analyze(f) for f in files]
    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for r in reports:
            print_report(r)
    if reports:
        (args.out).mkdir(parents=True, exist_ok=True)
        (args.out / "ricognizione.json").write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
