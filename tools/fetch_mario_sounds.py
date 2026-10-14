#!/usr/bin/env python3
"""
Scarica effetti sonori e voci di Mario dall'archivio di The Mushroom Kingdom (themushroomkingdom.net,
sezione "Sound clips", file .WAV) per l'uso privato dell'assistente: le emozioni e le avventure suonano
i suoni veri dei giochi al posto di quelli sintetizzati.

I suoni sono © Nintendo; il sito è un archivio di appassionati che chiede di essere citato e di
chiedere il permesso prima di ripubblicare il materiale. Per questo i file finiscono in
data/sounds/tmk/ (ignorata da git) e non vanno mai committati né pubblicati: il repository contiene
solo questo script. Le richieste sono distanziate (una al secondo di default) e i file già presenti
non vengono riscaricati.

Uso:
  ./run.sh fetch-sounds --list                 # giochi disponibili e quelli scelti di default
  ./run.sh fetch-sounds --catalog              # solo il catalogo (categorie, numero e peso dei file)
  ./run.sh fetch-sounds                        # scarica i giochi di default
  ./run.sh fetch-sounds --games smb,sm64       # solo alcuni giochi
  ./run.sh fetch-sounds --games all            # tutti i 31 giochi dell'archivio

Risultato: data/sounds/tmk/<gioco>/<file>.wav e data/sounds/tmk/catalogo.json (gioco, categoria,
descrizione, URL, dimensione) che `mario/assistant/soundpack.py` usa per scegliere i suoni.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://themushroomkingdom.net"
PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "sounds" / "tmk"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) LEGO-Mario-home-assistant (uso privato)"

# Tutti i giochi della pagina wav.shtml (14/10/2026). Default: i platform (effetti, jingle e voci di Mario:
# 35 in Super Mario 64, 78 in Sunshine), Mario Kart 64 (voci di Mario, Luigi, Peach, Toad, Yoshi) e i
# giochi Mario & Luigi (versi dei due fratelli, utili anche per LEGO Luigi); circa 49 MB. Fuori: Mario
# Party, Smash, Golf e i puzzle (altri personaggi, annunciatori), scaricabili con --games all.
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
DEFAULT_GAMES = ["smb", "smb2", "smb3", "smw", "smw2", "smas", "sma4", "sm64", "smsunshine", "nsmb", "sm3dl",
                 "mk64", "mlss", "mlpit", "mlrpg3", "lm"]

_SUBHEAD = re.compile(r'<h4 class="subhead">(.*?)</h4>', re.S)
_ROW = re.compile(r'<a href="(/sounds/wav/[^"]+)" title="Download">(.*?)</a>.*?<td class="sizebytes">([^<]*)</td>', re.S)


def fetch(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def size_bytes(text: str) -> int:
    m = re.match(r"\s*([\d.]+)\s*([KMG]?)B", text or "", re.I)
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2).upper()])


def parse_page(game: str, page: str) -> list[dict]:
    """Righe del catalogo di una pagina media/<gioco>/wav: categoria (sottotitolo), descrizione, URL, peso."""
    items: list[dict] = []
    heads = [(m.start(), html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()) for m in _SUBHEAD.finditer(page)]
    for m in _ROW.finditer(page):
        category = ""
        for pos, name in heads:
            if pos < m.start():
                category = name
            else:
                break
        path = m.group(1)
        items.append({"game": game, "category": category,
                      "description": html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip(),
                      "url": BASE + urllib.parse.quote(path, safe="/()_-.!~*'"),
                      "file": f"{game}/{Path(urllib.parse.unquote(path)).name}",
                      "size": size_bytes(m.group(3))})
    return items


def catalog(games: list[str], delay: float) -> list[dict]:
    items: list[dict] = []
    for i, game in enumerate(games):
        if i:
            time.sleep(delay)
        page = fetch(f"{BASE}/media/{game}/wav").decode("utf-8", "replace")
        found = parse_page(game, page)
        print(f"{game:<16} {len(found):>4} file  {sum(x['size'] for x in found) / 1024 / 1024:6.1f} MB  "
              f"({GAMES.get(game, '?')})", flush=True)
        items += found
    return items


def download(items: list[dict], delay: float) -> tuple[int, int, list[str]]:
    new, skipped, errors = 0, 0, []
    for n, item in enumerate(items, 1):
        target = OUT / item["file"]
        if target.is_file() and target.stat().st_size > 0:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = fetch(item["url"])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{item['url']}: {exc}")
            continue
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        new += 1
        if new % 25 == 0:
            print(f"  {n}/{len(items)} ...", flush=True)
        time.sleep(delay)
    return new, skipped, errors


def write_index(items: list[dict]) -> Path:
    """catalogo.json: i file presenti su disco (anche di scaricamenti precedenti), con fonte e descrizione."""
    index_path = OUT / "catalogo.json"
    old = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"items": []}
    merged = {x["file"]: x for x in old.get("items", []) if (OUT / x["file"]).is_file()}
    merged.update({x["file"]: x for x in items if (OUT / x["file"]).is_file()})
    index_path.write_text(json.dumps({
        "_fonte": "The Mushroom Kingdom, https://themushroomkingdom.net/wav.shtml (suoni © Nintendo; "
                  "uso privato, non ripubblicare)",
        "scaricato": time.strftime("%Y-%m-%d"), "items": sorted(merged.values(), key=lambda x: x["file"])},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return index_path


def main() -> int:
    ap = argparse.ArgumentParser(description="scarica suoni e voci di Mario da themushroomkingdom.net (uso privato)")
    ap.add_argument("--games", default=",".join(DEFAULT_GAMES), help="giochi separati da virgola, oppure 'all'")
    ap.add_argument("--list", action="store_true", help="elenca i giochi dell'archivio")
    ap.add_argument("--catalog", action="store_true", help="legge solo il catalogo, senza scaricare i file")
    ap.add_argument("--delay", type=float, default=1.0, help="secondi tra una richiesta e l'altra (default 1)")
    args = ap.parse_args()
    if args.list:
        for slug, name in GAMES.items():
            print(f"{'*' if slug in DEFAULT_GAMES else ' '} {slug:<16} {name}")
        print("* = scaricati di default")
        return 0
    games = list(GAMES) if args.games == "all" else [g.strip() for g in args.games.split(",") if g.strip()]
    unknown = [g for g in games if g not in GAMES]
    if unknown:
        print(f"giochi sconosciuti: {unknown} (vedi --list)", file=sys.stderr)
        return 2
    items = catalog(games, args.delay)
    total = sum(x["size"] for x in items)
    print(f"totale: {len(items)} file, {total / 1024 / 1024:.1f} MB")
    if args.catalog:  # catalogo di ciò che c'è sul sito, per scegliere senza scaricare
        OUT.mkdir(parents=True, exist_ok=True)
        remote = OUT / "catalogo-remoto.json"
        remote.write_text(json.dumps({"letto": time.strftime("%Y-%m-%d"), "items": items}, ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8")
        print(f"catalogo del sito salvato in {remote}")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    new, skipped, errors = 0, 0, []
    try:
        new, skipped, errors = download(items, args.delay)
    finally:  # anche se interrotto (Ctrl+C): l'indice descrive i file già scaricati
        index_path = write_index(items)
    print(f"scaricati {new}, già presenti {skipped}, errori {len(errors)}; catalogo: {index_path}")
    for e in errors[:10]:
        print("  errore:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
