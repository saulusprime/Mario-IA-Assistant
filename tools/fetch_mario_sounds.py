#!/usr/bin/env python3
"""
Scarica in locale l'archivio "Sounds & Music" di The Mushroom Kingdom (themushroomkingdom.net):
effetti e voci (.WAV), musiche MP3, MIDI, colonne sonore NES (NSF) e Super NES (SPC) e moduli
tracker (MOD/XM/IT), per l'uso privato dell'assistente e delle avventure.

I suoni sono © Nintendo e degli autori indicati; il sito è un archivio di appassionati. I file restano
in data/sounds/tmk/ (ignorata da git): il repository pubblico contiene solo questo script. Le richieste
sono distanziate (una al secondo di default) e i file già presenti non vengono riscaricati, quindi lo
scaricamento si può interrompere e riprendere.

Uso:
  ./run.sh fetch-sounds --list                     # giochi della sezione WAV (* = default)
  ./run.sh fetch-sounds --catalog --all            # solo il catalogo del sito, senza scaricare
  ./run.sh fetch-sounds                            # effetti e voci dei 16 giochi di default (49 MB)
  ./run.sh fetch-sounds --all                      # tutto l'archivio: tutte le sezioni e tutti i giochi
  ./run.sh fetch-sounds --sections mp3,nsf,spc     # solo alcune sezioni
  ./run.sh fetch-sounds --games smb,sm64           # solo alcuni giochi della sezione WAV

Risultato: data/sounds/tmk/catalogo.json (sezione, gioco, categoria, descrizione, URL, file) e i file:
  <gioco>/<file>.wav            effetti e voci (sezione wav)
  <sezione>/<gruppo>/<file>     mp3, mid, mod (con il modulo estratto da .mdr se c'è 7z), nsf e spc
                                (gli zip vengono estratti accanto)
`mario/assistant/soundpack.py` e la pagina Suoni della dashboard leggono il catalogo.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://themushroomkingdom.net"
PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "sounds" / "tmk"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) LEGO-Mario-home-assistant (uso privato)"
SECTIONS = ("wav", "mp3", "mid", "mod", "nsf", "spc")
AUDIO_IN_ZIP = (".nsf", ".nsfe", ".spc", ".mp3", ".wav", ".mid", ".it", ".xm", ".mod", ".s3m")

# Tutti i giochi della pagina wav.shtml (14/10/2026). Default: i platform (effetti, jingle e voci di Mario:
# 35 in Super Mario 64, 78 in Sunshine), Mario Kart 64 (voci di Mario, Luigi, Peach, Toad, Yoshi) e i
# giochi Mario & Luigi (versi dei due fratelli, utili anche per LEGO Luigi); circa 49 MB. Fuori: Mario
# Party, Smash, Golf e i puzzle (altri personaggi, annunciatori), scaricabili con --games all o --all.
GAMES = {
    "smb": "Super Mario Bros.", "smb2": "Super Mario Bros. 2", "smb3": "Super Mario Bros. 3",
    "smw": "Super Mario World", "smw2": "Super Mario World 2: Yoshi's Island", "sm64": "Super Mario 64",
    "smsunshine": "Super Mario Sunshine", "nsmb": "New Super Mario Bros.", "sm3dl": "Super Mario 3D Land",
    "smas": "Super Mario All-Stars", "sma4": "Super Mario Advance 4: Super Mario Bros. 3",
    "smrpg": "Super Mario RPG", "mlss": "Mario & Luigi: Superstar Saga", "mlpit": "Mario & Luigi: Partners in Time",
    "mlrpg3": "Mario & Luigi: Bowser's Inside Story", "lm": "Luigi's Mansion", "mk64": "Mario Kart 64",
    "mkdd": "Mario Kart: Double Dash!!", "mk7": "Mario Kart 7", "mparty8": "Mario Party 8",
    "mg-n64": "Mario Golf (N64)", "mb-nes": "Mario Bros. (NES)", "drm64": "Dr. Mario 64",
    "mtt": "Mario Teaches Typing", "npc": "Nintendo Puzzle Collection", "ssbm": "Super Smash Bros. Melee",
    "ta-snes": "Tetris Attack", "wwoods-snes": "Wario's Woods", "ystory": "Yoshi's Story",
    "dk-a2600": "Donkey Kong (Atari 2600)", "msatl2012og-3ds": "Mario & Sonic London 2012 (3DS)",
}
WAV_GAMES = list(GAMES)
DEFAULT_GAMES = ["smb", "smb2", "smb3", "smw", "smw2", "smas", "sma4", "sm64", "smsunshine", "nsmb", "sm3dl",
                 "mk64", "mlss", "mlpit", "mlrpg3", "lm"]

_SUBHEAD = re.compile(r'<h[2-4][^>]*>(.*?)</h[2-4]>', re.S)
_WAV_ROW = re.compile(r'<a href="(/sounds/wav/[^"]+)" title="Download">(.*?)</a>.*?<td class="sizebytes">([^<]*)</td>', re.S)
_MID_ROW = re.compile(r'<a href="(/?sounds/mid/[^"]+)">(.*?)</a></td><td class="num">([^<]*)</td><td>(.*?)</td>', re.S)
_MOD_ROW = re.compile(r'<a href="(/?sounds/mod/[^"]+)">(.*?)</a></td><td>([^<]*)</td><td class="num">([^<]*)</td>'
                      r'<td>(.*?)</td><td>(.*?)</td>', re.S)
_MP3_ROW = re.compile(r'<a href="(/?sounds/mp3/[^"]+)">(.*?)</a>\s*-\s*([\d.]+\s*[KM]B)([^<]*)', re.S)
_ZIP_ROW = re.compile(r'<a href="(/?sounds/(nsf|spc)/[^"]+\.zip)">(.*?)</a>([^<]*)', re.S)
_GAME_PAGE = re.compile(r'<a href="(?:/)?media/([^"/]+)/(mid|mod)">(.*?)</a>', re.S)


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment or ""))).strip()


def fetch(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def size_bytes(value: str) -> int:
    m = re.match(r"\s*([\d.]+)\s*([KMG]?)B", value or "", re.I)
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2).upper()])


def absolute(path: str) -> str:
    return BASE + urllib.parse.quote("/" + path.lstrip("/"), safe="/()_-.!~*'&")


def _category_at(heads: list[tuple[int, str]], pos: int) -> str:
    category = ""
    for start, name in heads:
        if start < pos:
            category = name
        else:
            break
    return category


def _heads(page: str) -> list[tuple[int, str]]:
    return [(m.start(), text(m.group(1))) for m in _SUBHEAD.finditer(page)]


def parse_page(game: str, page: str) -> list[dict]:
    """Pagina media/<gioco>/wav: categoria (sottotitolo), descrizione, URL, peso."""
    heads = [(m.start(), text(m.group(1))) for m in re.finditer(r'<h4 class="subhead">(.*?)</h4>', page, re.S)]
    items = []
    for m in _WAV_ROW.finditer(page):
        path = m.group(1)
        items.append({"section": "wav", "game": game, "category": _category_at(heads, m.start()),
                      "description": text(m.group(2)), "url": absolute(path),
                      "file": f"{game}/{Path(urllib.parse.unquote(path)).name}", "size": size_bytes(m.group(3))})
    return items


def parse_mid_page(game: str, page: str) -> list[dict]:
    heads = _heads(page)
    return [{"section": "mid", "game": game, "category": _category_at(heads, m.start()) or GAMES.get(game, game),
             "description": text(m.group(2)), "url": absolute(m.group(1)),
             "file": f"mid/{game}/{Path(urllib.parse.unquote(m.group(1))).name}", "size": size_bytes(m.group(3)),
             "extra": f"MIDI di {text(m.group(4))}"} for m in _MID_ROW.finditer(page)]


def parse_mod_page(game: str, page: str) -> list[dict]:
    heads = _heads(page)
    return [{"section": "mod", "game": game, "category": _category_at(heads, m.start()) or GAMES.get(game, game),
             "description": text(m.group(2)), "url": absolute(m.group(1)),
             "file": f"mod/{game}/{Path(urllib.parse.unquote(m.group(1))).name}", "size": 0,
             "extra": f"{text(m.group(3))}, {text(m.group(4))}, di {text(m.group(5))} (musica di {text(m.group(6))})"}
            for m in _MOD_ROW.finditer(page)]


def parse_mp3_page(page: str) -> list[dict]:
    heads = _heads(page)
    return [{"section": "mp3", "game": "mp3", "category": _category_at(heads, m.start()),
             "description": text(m.group(2)), "url": absolute(m.group(1)),
             "file": f"mp3/{Path(urllib.parse.unquote(m.group(1))).name}", "size": size_bytes(m.group(3)),
             "extra": text(m.group(4)).lstrip("- ")} for m in _MP3_ROW.finditer(page)]


def parse_zip_page(section: str, page: str) -> list[dict]:
    items = []
    for m in _ZIP_ROW.finditer(page):
        stem = Path(urllib.parse.unquote(m.group(1))).stem
        items.append({"section": section, "game": stem, "category": text(m.group(3)), "description": text(m.group(3)),
                      "url": absolute(m.group(1)), "file": f"{section}/{stem}.zip", "size": 0,
                      "extra": text(m.group(4)).lstrip("- ")})
    return items


def game_pages(index_page: str, kind: str) -> list[tuple[str, str]]:
    """(gioco, nome) delle pagine per gioco; il nome più lungo vince ("Super Mario 64" su "SM64" delle novità)."""
    names: dict[str, str] = {}
    for m in _GAME_PAGE.finditer(index_page):
        if m.group(2) == kind:
            name = text(m.group(3))
            if len(name) > len(names.get(m.group(1), "")):
                names[m.group(1)] = name
    return list(names.items())


# ----- catalogo del sito --------------------------------------------------------------------

class Catalog:
    def __init__(self, delay: float):
        self.delay = delay
        self.requests = 0

    def get(self, url: str) -> str:
        if self.requests:
            time.sleep(self.delay)
        self.requests += 1
        return fetch(url).decode("utf-8", "replace")

    def wav(self, games: list[str]) -> list[dict]:
        items = []
        for game in games:
            found = parse_page(game, self.get(f"{BASE}/media/{game}/wav"))
            report("wav", game, found)
            items += found
        return items

    def per_game(self, kind: str) -> list[dict]:
        index = self.get(f"{BASE}/{'midi' if kind == 'mid' else kind}.shtml")
        items = []
        for game, name in game_pages(index, kind):
            GAMES.setdefault(game, name)
            page = self.get(f"{BASE}/media/{game}/{kind}")
            found = parse_mid_page(game, page) if kind == "mid" else parse_mod_page(game, page)
            report(kind, game, found)
            items += found
        return items

    def section(self, section: str, games: list[str]) -> list[dict]:
        if section == "wav":
            return self.wav(games)
        if section in ("mid", "mod"):
            return self.per_game(section)
        page = self.get(f"{BASE}/{section}.shtml")
        found = parse_mp3_page(page) if section == "mp3" else parse_zip_page(section, page)
        report(section, "-", found)
        return found


def report(section: str, game: str, found: list[dict]) -> None:
    size = sum(x["size"] for x in found) / 1024 / 1024
    print(f"{section:<4} {game:<16} {len(found):>4} file" + (f"  {size:6.1f} MB" if size else "")
          + (f"  ({GAMES[game]})" if game in GAMES else ""), flush=True)


# ----- scaricamento ed estrazione ----------------------------------------------------------

def safe_target(rel: str) -> Path:
    target = (OUT / rel).resolve()
    if OUT.resolve() not in target.parents:
        raise ValueError(f"percorso fuori dall'archivio: {rel}")
    return target


def extract(item: dict) -> list[dict]:
    """Zip (NSF, SPC): estratto in <sezione>/<nome>/; .mdr (modulo compresso RAR): estratto con 7z se c'è."""
    archive = OUT / item["file"]
    found: list[dict] = []
    if archive.suffix.lower() == ".zip":
        folder = archive.with_suffix("")
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                name = Path(member.filename).name
                if member.is_dir() or not name.lower().endswith(AUDIO_IN_ZIP):
                    continue
                target = safe_target(str((folder / name).relative_to(OUT)))
                if not target.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(member))
                found.append({"section": item["section"], "game": item["game"], "category": item["category"],
                              "description": Path(name).stem.replace("_", " "), "url": item["url"],
                              "file": str(target.relative_to(OUT.resolve())), "size": target.stat().st_size,
                              "archive": item["file"], "extra": item.get("extra", "")})
    elif archive.suffix.lower() == ".mdr" and shutil.which("7z"):
        folder = archive.with_suffix("")
        if not folder.is_dir() or not any(folder.iterdir()):
            folder.mkdir(parents=True, exist_ok=True)
            subprocess.run(["7z", "x", "-y", f"-o{folder}", str(archive)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
        modules = [p for p in folder.iterdir() if p.suffix.lower() in (".it", ".xm", ".mod", ".s3m")] if folder.is_dir() else []
        if modules:
            item["play"] = str(modules[0].relative_to(OUT))
    return found


def download(items: list[dict], delay: float, checkpoint) -> tuple[int, int, list[str]]:
    new, skipped, errors = 0, 0, []
    for n, item in enumerate(list(items), 1):
        target = safe_target(item["file"])
        if not (target.is_file() and target.stat().st_size > 0):
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = fetch(item["url"])
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append(f"{item['url']}: {exc}")
                continue
            tmp = target.with_name(target.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(target)
            new += 1
            time.sleep(delay)
        else:
            skipped += 1
        if not item.get("size"):
            item["size"] = target.stat().st_size
        try:
            items += extract(item)
        except (zipfile.BadZipFile, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{item['file']}: estrazione fallita ({exc})")
        if new and new % 25 == 0 and not item.get("_checkpoint"):
            item["_checkpoint"] = True
            print(f"  {n} file controllati, {new} nuovi ...", flush=True)
            checkpoint()
    return new, skipped, errors


def write_index(items: list[dict]) -> Path:
    """catalogo.json: i file presenti su disco (anche di scaricamenti precedenti), con fonte e descrizione."""
    index_path = OUT / "catalogo.json"
    old = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"items": []}
    merged = {x["file"]: x for x in old.get("items", []) if (OUT / x["file"]).is_file()}
    merged.update({x["file"]: {k: v for k, v in x.items() if not k.startswith("_")}
                   for x in items if (OUT / x["file"]).is_file()})
    for x in merged.values():
        x.setdefault("section", "wav")
    tmp = index_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "_fonte": "The Mushroom Kingdom, https://themushroomkingdom.net/sounds.shtml (suoni © Nintendo e degli "
                  "autori indicati; copia locale per uso privato, non ripubblicare)",
        "scaricato": time.strftime("%Y-%m-%d"), "items": sorted(merged.values(), key=lambda x: x["file"])},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(index_path)
    return index_path


def main() -> int:
    ap = argparse.ArgumentParser(description="scarica l'archivio Sounds & Music di themushroomkingdom.net (uso privato)")
    ap.add_argument("--games", default=",".join(DEFAULT_GAMES), help="giochi della sezione WAV, oppure 'all'")
    ap.add_argument("--sections", default="wav", help=f"sezioni separate da virgola tra {', '.join(SECTIONS)}, o 'all'")
    ap.add_argument("--all", action="store_true", help="tutte le sezioni e tutti i giochi (l'archivio intero)")
    ap.add_argument("--list", action="store_true", help="elenca i giochi della sezione WAV")
    ap.add_argument("--catalog", action="store_true", help="legge solo il catalogo, senza scaricare i file")
    ap.add_argument("--delay", type=float, default=1.0, help="secondi tra una richiesta e l'altra (default 1)")
    args = ap.parse_args()
    if args.list:
        for slug in WAV_GAMES:
            print(f"{'*' if slug in DEFAULT_GAMES else ' '} {slug:<16} {GAMES[slug]}")
        print("* = scaricati di default")
        return 0
    sections = list(SECTIONS) if args.all or args.sections == "all" else [s.strip() for s in args.sections.split(",") if s.strip()]
    games = list(WAV_GAMES) if args.all or args.games == "all" else [g.strip() for g in args.games.split(",") if g.strip()]
    bad = [s for s in sections if s not in SECTIONS] + [g for g in games if g not in GAMES]
    if bad:
        print(f"sezioni o giochi sconosciuti: {bad} (vedi --list)", file=sys.stderr)
        return 2
    cat = Catalog(args.delay)
    items: list[dict] = []
    for section in sections:
        items += cat.section(section, games)
    known = sum(x["size"] for x in items)
    print(f"totale: {len(items)} file" + (f", {known / 1024 / 1024:.1f} MB noti" if known else ""))
    OUT.mkdir(parents=True, exist_ok=True)
    if args.catalog:  # catalogo di ciò che c'è sul sito, per scegliere senza scaricare
        remote = OUT / "catalogo-remoto.json"
        remote.write_text(json.dumps({"letto": time.strftime("%Y-%m-%d"), "items": items}, ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8")
        print(f"catalogo del sito salvato in {remote}")
        return 0
    new, skipped, errors = 0, 0, []
    try:
        new, skipped, errors = download(items, args.delay, lambda: write_index(items))
    finally:  # anche se interrotto (Ctrl+C): l'indice descrive i file già scaricati
        index_path = write_index(items)
    print(f"scaricati {new}, già presenti {skipped}, errori {len(errors)}; catalogo: {index_path}")
    for e in errors[:10]:
        print("  errore:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
