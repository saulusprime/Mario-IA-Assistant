"""
Suoni veri dei giochi di Mario al posto degli effetti sintetizzati, con le scelte dell'utente.

Cartelle (tutte in `data/sounds/`, ignorata da git: copia locale per uso privato):
  tmk/              archivio di The Mushroom Kingdom scaricato da `tools/fetch_mario_sounds.py`, con
                    `catalogo.json` (sezione, gioco, categoria, descrizione): effetti e voci WAV, MP3,
                    MIDI, NSF, SPC, moduli tracker
  custom/           suoni caricati dall'utente dalla pagina Suoni, con il proprio `catalogo.json`
  cache/            conversioni in WAV (NSF, SPC, MOD, MIDI, MP3) fatte con ffmpeg o un sintetizzatore MIDI
  assegnazioni.json le scelte della pagina Suoni: {"giochi": {nome: [file]}, "voce": {...}, "volume": 100}

Tre stili (`Speaker.sound_style`):
  - "sintetici": gli effetti generati al volo di `tts.py` (nessun file);
  - "giochi":    gli effetti dei giochi (moneta e power-up di Super Mario Bros., ...);
  - "voce":      dove c'è, un'esclamazione di Mario ("Yahoo!" per la gioia, "Mamma-mia" per
                 l'allarme, sbadiglio per il sonno), altrimenti l'effetto del gioco.
Per ogni nome vale la scelta dell'utente, poi la tabella qui sotto; se manca il file si torna allo
stile precedente, fino all'effetto sintetizzato.

I nomi sono quelli usati da emozioni, sessione e avventure (coin, powerup, alarm, sad, yawn, think,
tick, hello, listen_on, listen_off), alcuni nuovi per le avventure (oneup, jump, stomp, pipe, star,
clear, gameover, ...) e quelli creati dall'utente; un'avventura può anche indicare un file preciso
(`sm64/sm64_coin.wav` dell'archivio, `custom/risata.wav`). Un brano NSF con più tracce si indica con
`#numero` (`tmk/nsf/nsf_smb/smb.nsf#3`).
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parents[2]
SOUNDS_ROOT = PROJECT_DIR / "data" / "sounds"
PACK_DIR = SOUNDS_ROOT / "tmk"
STYLES = ("sintetici", "giochi", "voce")
EDIT_STYLES = ("giochi", "voce")
DEFAULT_STYLE = "giochi"
DIRECT = (".wav",)                                   # suonati così come sono dal lettore del PC
BROWSER = (".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac")  # ascoltabili direttamente nel browser
CONVERTIBLE = (".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".nsf", ".nsfe", ".spc", ".it", ".xm", ".mod",
               ".s3m", ".mid", ".midi")
GAME_MUSIC = (".nsf", ".nsfe", ".spc", ".it", ".xm", ".mod", ".s3m", ".mid", ".midi")
NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")

Rule = tuple[str, str]  # (gioco, descrizione nel catalogo, senza distinzione di maiuscole)

# effetti dei giochi; più regole = scelta a caso tra quelle scaricate
EFFECTS: dict[str, list[Rule]] = {
    "coin": [("smb", "Coin")],
    "powerup": [("smb", "Power-up")],
    "think": [("smw", "Message Block")],
    "alarm": [("nsmb", "Hurry up!"), ("smb", "Running out of time")],
    "sad": [("smb", "Mario dies")],
    "yawn": [("sm64", "Yawning")],
    "tick": [("smw", "Map screen: Stopping on a marker")],
    "hello": [("sm64", "\"It's-a me, Mario!\"")],
    "listen_on": [("smb", "Power-up appears")],
    "listen_off": [("smb", "Pipe travel / power-down")],
    # nuovi, per le avventure
    "oneup": [("smb", "1-Up")],
    "jump": [("smb", "Jump (small)")],
    "stomp": [("smb", "Stomp")],
    "kick": [("smb", "Kick")],
    "bump": [("smb", "Bump")],
    "brick": [("smb", "Brick smash")],
    "fireball": [("smb", "Fireball")],
    "pipe": [("smb", "Pipe travel / power-down")],
    "flagpole": [("smb", "Down the flagpole")],
    "fireworks": [("smb", "Fireworks")],
    "vine": [("smb", "Vine growing")],
    "pause": [("smb", "Pause")],
    "clear": [("smb", "Stage clear")],
    "world_clear": [("smb", "World clear")],
    "gameover": [("smb", "Game over")],
    "star": [("sm64", "Power Star appears")],
    "key": [("sm64", "Got a Key")],
    "red_coin": [("sm64", "Red Coin #1")],
    "blue_coin": [("sm64", "Blue Coin")],
    "dragon_coin": [("smw", "Dragon Coin")],
    "yoshi": [("smw", "Riding Yoshi")],
    "door": [("smw", "Door opens")],
    "midway": [("smw", "Midway Gate")],
    "thunder": [("smw", "Thunder")],
    "boo": [("nsmb", "Boo")],
}

# esclamazioni di Mario (stile "voce") per i nomi delle emozioni e alcune frasi pronte
VOICE: dict[str, list[Rule]] = {
    "coin": [("sm64", "\"Yahoo!\""), ("sm64", "\"Wa-ha!\""), ("sm64", "\"Yippee!\""),
             ("smsunshine", "Jumping - \"Yahoo!\""), ("smsunshine", "Jumping - \"Wahaa!\""),
             ("mk64", "\"Whoo-hoo!\"")],
    "powerup": [("sm64", "\"Whoa!\""), ("sm64", "\"Hoo!\""), ("mk64", "\"Whee-hee!\"")],
    "alarm": [("sm64", "\"Mamma-mia\""), ("smsunshine", "\"Mamma-mia\""), ("smsunshine", "\"Watch it!\""),
              ("mk64", "\"Mamma-mia!\"")],
    "sad": [("sm64", "\"D'oh!\""), ("sm64", "\"Ungh\""), ("smsunshine", "\"The horror\"")],
    "yawn": [("sm64", "\"I'm-a tired\""), ("sm64", "Yawning"), ("smsunshine", "Snore part 1")],
    "hello": [("sm64", "\"It's-a me, Mario!\""), ("sm64", "\"Hello!\""), ("sm3dl", "\"Let's-a go!\""),
              ("sm3dl", "\"Here we go!\"")],
    "calm": [("sm64", "\"Okey-dokey\""), ("smsunshine", "\"Okey-dokey\""), ("sm3dl", "\"Okey-dokey!\"")],
    # frasi pronte per le avventure
    "letsago": [("sm64", "\"Let's-a go!\""), ("sm3dl", "\"Let's-a go!\""), ("mk64", "\"Let's-a go!\"")],
    "herewego": [("sm64", "\"Here we go!\""), ("sm3dl", "\"Here we go!\""), ("smsunshine", "\"Here we go\"")],
    "wahoo": [("sm64", "\"Wa-ha!\""), ("sm64", "\"Yahoo!\"")],
    "mammamia": [("sm64", "\"Mamma-mia\""), ("mk64", "\"Mamma-mia!\"")],
    "okeydokey": [("sm64", "\"Okey-dokey\""), ("sm3dl", "\"Okey-dokey!\"")],
    "yippee": [("sm64", "\"Yippee!\""), ("mk64", "\"Yippeeee!\"")],
    "goodbye": [("smsunshine", "\"Arrivederci\""), ("sm64", "\"So long-a, Bowser!\"")],
    "thanks": [("sm64", "\"Thank you so much for to playing my game\"")],
    "snore": [("sm64", "Snore part 1"), ("smsunshine", "Snore part 1")],
}

# esclamazioni che il modello può far suonare tra le frasi scrivendo {nome}: (frase, quando usarla)
EXCLAMATIONS: dict[str, tuple[str, str]] = {
    "letsago": ("Let's-a go!", "quando si parte o si comincia un gioco"),
    "herewego": ("Here we go!", "quando inizia un'avventura"),
    "wahoo": ("Wa-ha!", "per un salto o una gioia improvvisa"),
    "yippee": ("Yippee!", "per festeggiare"),
    "mammamia": ("Mamma mia!", "per uno spavento o una brutta sorpresa"),
    "okeydokey": ("Okey-dokey!", "per dire d'accordo"),
    "hello": ("It's-a me, Mario!", "per presentarti"),
    "goodbye": ("Arrivederci!", "per salutare alla fine"),
    "snore": ("un russare", "quando hai tanto sonno"),
}

# a cosa servono i nomi (pagina Suoni)
USES: dict[str, str] = {
    "coin": "emozione gioia; punti nelle avventure", "powerup": "emozione sorpresa; avventura completata",
    "think": "emozione pensieroso", "alarm": "emozione allarme", "sad": "emozione tristezza; avventura fallita",
    "yawn": "emozione sonno", "tick": "attesa della risposta del modello", "hello": "inizio di un'avventura",
    "listen_on": "sensori collegati al modello", "listen_off": "sensori scollegati dal modello",
    "calm": "emozione calma (solo stile voce)",
}


def section_of(rel: str) -> str:
    """Sezione di un file dal percorso: wav, mp3, mid, mod, nsf, spc, custom."""
    parts = Path(rel.split("#")[0]).parts
    if parts and parts[0] == "custom":
        return "custom"
    if len(parts) > 1 and parts[0] == "tmk" and parts[1] in ("mp3", "mid", "mod", "nsf", "spc"):
        return parts[1]
    return "wav"


class SoundPack:
    """Indice dei suoni scaricati e caricati, scelte dell'utente, conversioni in WAV."""

    def __init__(self, root: Optional[Path] = None, rng: Optional[random.Random] = None):
        self.root = Path(root or SOUNDS_ROOT)
        self.folder = self.root / "tmk"
        self.custom_dir = self.root / "custom"
        self.cache_dir = self.root / "cache"
        self.assign_file = self.root / "assegnazioni.json"
        self.rng = rng or random.Random()
        self.index: dict[tuple[str, str], str] = {}   # (gioco, descrizione) -> percorso relativo alla radice
        self.items: list[dict] = []
        self.by_rel: dict[str, dict] = {}
        self.assignments: dict = {"giochi": {}, "voce": {}, "volume": 100}
        self._lock = threading.Lock()
        self.reload()

    # ----- caricamento ------------------------------------------------------------------

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def reload(self) -> None:
        items: list[dict] = []
        index: dict[tuple[str, str], str] = {}
        for base, prefix in ((self.folder, "tmk"), (self.custom_dir, "custom")):
            for item in self._read_json(base / "catalogo.json").get("items", []):
                rel = f"{prefix}/{item.get('file', '')}"
                if not (self.root / rel).is_file():
                    continue
                entry = dict(item, rel=rel, section="custom" if prefix == "custom" else item.get("section", "wav"))
                if item.get("play"):
                    entry["play_rel"] = f"{prefix}/{item['play']}"
                items.append(entry)
                if prefix == "tmk" and entry["section"] == "wav":
                    index[(item["game"], item.get("description", "").lower())] = rel
        data = self._read_json(self.assign_file)
        assignments = {"giochi": {}, "voce": {}, "volume": 100}
        for style in EDIT_STYLES:
            for name, files in (data.get(style) or {}).items():
                if NAME_RE.match(str(name)) and isinstance(files, list):
                    assignments[style][str(name)] = [str(f) for f in files if isinstance(f, str)]
        try:
            assignments["volume"] = max(0, min(150, int(data.get("volume", 100))))
        except (TypeError, ValueError):
            pass
        with self._lock:
            self.items, self.index, self.assignments = items, index, assignments
            self.by_rel = {x["rel"]: x for x in items}

    @property
    def available(self) -> bool:
        return bool(self.items)

    @property
    def volume(self) -> int:
        return int(self.assignments.get("volume", 100))

    # ----- percorsi ---------------------------------------------------------------------

    def resolve(self, rel: str) -> Optional[Path]:
        """Percorso sicuro dentro data/sounds (niente `..`, niente percorsi assoluti), se il file esiste."""
        rel = str(rel or "").split("#")[0].strip()
        if not rel or rel.startswith("/") or "\\" in rel:
            return None
        path = (self.root / rel).resolve()
        root = self.root.resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path

    def normalize(self, name: str) -> Optional[str]:
        """Un file indicato direttamente: `tmk/...`, `custom/...` o, per compatibilità, `gioco/file.wav`
        dell'archivio WAV. Restituisce il percorso relativo alla radice (con l'eventuale `#traccia`)."""
        if "/" not in name:
            return None
        base, _, track = name.partition("#")
        for rel in (base, f"tmk/{base}"):
            if self.resolve(rel):
                return rel + (f"#{track}" if track.isdigit() else "")
        return None

    # ----- scelta del suono -------------------------------------------------------------

    def _defaults(self, style: str, name: str) -> list[str]:
        table = VOICE if style == "voce" else EFFECTS
        return [self.index[(g, d.lower())] for g, d in table.get(name, []) if (g, d.lower()) in self.index]

    def _override(self, style: str, name: str) -> list[str]:
        files = self.assignments.get(style, {}).get(name) or []
        return [f for f in files if self.resolve(f)]

    def candidates(self, name: str, style: str = DEFAULT_STYLE) -> list[str]:
        """File tra cui scegliere per `name` nello stile (prima le scelte dell'utente, poi le tabelle)."""
        if style == "sintetici":
            return []
        direct = self.normalize(name)
        if direct:
            return [direct]
        order = ("voce", "giochi") if style == "voce" else ("giochi",)
        for st in order:
            for source in (self._override(st, name), self._defaults(st, name)):
                if source:
                    return source
        return []

    def path(self, name: str, style: str = DEFAULT_STYLE) -> Optional[Path]:
        """Il file WAV da suonare sul PC per `name`; None = usare l'effetto sintetizzato."""
        found = self.candidates(name, style)
        if not found:
            return None
        return self.playable(self.rng.choice(found))

    # ----- conversioni ------------------------------------------------------------------

    @staticmethod
    def midi_synth() -> Optional[str]:
        for cmd in ("fluidsynth", "timidity"):
            if shutil.which(cmd):
                return cmd
        return None

    def can_play(self, rel: str, tools: Optional[tuple[bool, Optional[str]]] = None) -> bool:
        """Vero se il PC può suonare il file (subito o dopo la conversione). `tools` = (ffmpeg c'è, synth MIDI)."""
        has_ffmpeg, midi = tools if tools is not None else (bool(shutil.which("ffmpeg")), self.midi_synth())
        item = self.by_rel.get(rel.split("#")[0], {})
        suffix = Path(item.get("play_rel") or rel.split("#")[0]).suffix.lower()
        if suffix in DIRECT:
            return True
        if suffix in (".mid", ".midi"):
            return midi is not None
        return suffix in CONVERTIBLE and has_ffmpeg

    def playable(self, rel: str, seconds: Optional[float] = None) -> Optional[Path]:
        """WAV pronto per il lettore: il file stesso o la sua conversione in cache (ffmpeg per MP3, NSF, SPC,
        moduli tracker; fluidsynth/timidity per i MIDI). `seconds` taglia i brani lunghi (le musiche NES e
        SNES girano all'infinito: 90 s se non indicato)."""
        base, _, track = rel.partition("#")
        item = self.by_rel.get(base, {})
        src = self.resolve(item.get("play_rel") or base)
        if src is None:
            return None
        suffix = src.suffix.lower()
        if suffix in DIRECT and seconds is None:
            return src
        if suffix not in CONVERTIBLE + DIRECT:
            return None
        if seconds is None and suffix in GAME_MUSIC:
            seconds = 90.0
        key = hashlib.sha1(f"{src}|{src.stat().st_mtime}|{track}|{seconds}".encode()).hexdigest()[:16]
        out = self.cache_dir / f"{src.stem[:40]}-{key}.wav"
        if out.is_file() and out.stat().st_size > 44:
            return out
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.wav")
        if suffix in (".mid", ".midi"):
            synth = self.midi_synth()
            if synth == "fluidsynth":
                fonts = sorted(Path("/usr/share/sounds/sf2").glob("*.sf2"))
                if not fonts:
                    return None
                cmd = ["fluidsynth", "-ni", "-F", str(tmp), "-r", "44100", str(fonts[0]), str(src)]
            elif synth == "timidity":
                cmd = ["timidity", "-Ow", "-o", str(tmp), str(src)]
            else:
                return None
        else:
            if not shutil.which("ffmpeg"):
                return None
            cmd = ["ffmpeg", "-v", "error", "-y"]
            if track.isdigit() and suffix in (".nsf", ".nsfe"):
                cmd += ["-track_index", track]
            cmd += ["-i", str(src)]
            if seconds:
                cmd += ["-t", f"{seconds:g}", "-af", f"afade=t=out:st={max(0.0, seconds - 2):g}:d=2"]
            cmd += ["-ac", "2", "-ar", "44100", str(tmp)]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            tmp.unlink(missing_ok=True)
            return None
        if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 44:
            tmp.unlink(missing_ok=True)
            return None
        tmp.replace(out)
        return out

    # ----- scelte dell'utente -----------------------------------------------------------

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.assign_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.assignments, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.assign_file)

    def assign(self, style: str, name: str, files: Optional[list[str]]) -> list[str]:
        """Imposta i file di `name` nello stile (lista vuota o None = torna alla scelta predefinita)."""
        if style not in EDIT_STYLES:
            raise ValueError(f"stile non modificabile: {style}")
        if not NAME_RE.match(name or ""):
            raise ValueError("nome non valido: lettere minuscole, cifre e _ (massimo 32)")
        clean = []
        for f in files or []:
            rel = self.normalize(f) if "/" in f else None
            if rel is None:
                raise ValueError(f"file non trovato nell'archivio: {f}")
            if rel not in clean:
                clean.append(rel)
        with self._lock:
            if clean:
                self.assignments[style][name] = clean
            else:
                self.assignments[style].pop(name, None)
            self._save()
        return clean

    def set_volume(self, volume: int) -> int:
        with self._lock:
            self.assignments["volume"] = max(0, min(150, int(volume)))
            self._save()
        return self.volume

    def forget_file(self, rel: str) -> int:
        """Toglie un file da tutte le assegnazioni (quando l'utente lo cancella)."""
        removed = 0
        with self._lock:
            for style in EDIT_STYLES:
                for name in list(self.assignments[style]):
                    files = self.assignments[style][name]
                    keep = [f for f in files if f.split("#")[0] != rel]
                    removed += len(files) - len(keep)
                    if keep:
                        self.assignments[style][name] = keep
                    else:
                        del self.assignments[style][name]
            self._save()
        return removed

    # ----- suoni propri -----------------------------------------------------------------

    def add_custom(self, data: bytes, filename: str, description: str = "") -> dict:
        """Salva un suono caricato in custom/ come WAV (ffmpeg converte gli altri formati)."""
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-")[:40] or "suono"
        target = self.custom_dir / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.wav"
        src = self.custom_dir / f".upload{Path(filename).suffix.lower()[:8] or '.bin'}"
        src.write_bytes(data)
        try:
            if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
                src.replace(target)
            else:
                if not shutil.which("ffmpeg"):
                    raise ValueError("serve un file WAV (per gli altri formati installare ffmpeg)")
                proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "2", "-ar", "44100",
                                       str(target)], capture_output=True, timeout=120)
                if proc.returncode != 0 or not target.is_file():
                    raise ValueError("file audio non leggibile: " + proc.stderr.decode(errors="replace").strip()[:160])
        finally:
            src.unlink(missing_ok=True)
        item = {"section": "custom", "game": "custom", "category": "I miei suoni",
                "description": description.strip()[:120] or Path(filename).stem, "file": target.name,
                "size": target.stat().st_size, "added": time.strftime("%Y-%m-%d %H:%M")}
        catalog = self._read_json(self.custom_dir / "catalogo.json")
        items = [x for x in catalog.get("items", []) if (self.custom_dir / x.get("file", "")).is_file()]
        items.append(item)
        (self.custom_dir / "catalogo.json").write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
        self.reload()
        return self.by_rel[f"custom/{target.name}"]

    def delete_custom(self, rel: str) -> bool:
        if not rel.startswith("custom/"):
            raise ValueError("si possono cancellare solo i suoni caricati")
        path = self.resolve(rel)
        if path is None:
            return False
        path.unlink()
        catalog = self._read_json(self.custom_dir / "catalogo.json")
        items = [x for x in catalog.get("items", []) if f"custom/{x.get('file', '')}" != rel]
        (self.custom_dir / "catalogo.json").write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
        self.forget_file(rel)
        self.reload()
        return True

    # ----- elenchi per la dashboard -----------------------------------------------------

    def names(self) -> dict[str, list[str]]:
        """Nomi utilizzabili con i suoni presenti, per stile (per le avventure e la dashboard)."""
        result = {}
        for style, table in (("giochi", EFFECTS), ("voce", VOICE)):
            result[style] = sorted({n for n in table if self._defaults(style, n)} | set(self.assignments[style]))
        return result

    def exclamations(self) -> dict[str, tuple[str, str]]:
        """Le esclamazioni per il modello che hanno davvero un file (scaricato o scelto dall'utente)."""
        return {name: info for name, info in EXCLAMATIONS.items() if self.candidates(name, "voce")}

    def all_names(self) -> list[str]:
        return sorted(set(EFFECTS) | set(VOICE) | set(self.assignments["giochi"]) | set(self.assignments["voce"]))

    def describe(self, rel: str) -> dict:
        base = rel.split("#")[0]
        item = self.by_rel.get(base, {})
        return {"rel": rel, "description": item.get("description") or Path(base).name, "game": item.get("game", ""),
                "section": item.get("section") or section_of(rel), "browser": Path(base).suffix.lower() in BROWSER}

    def table(self) -> list[dict]:
        """Una riga per nome: a cosa serve, file predefiniti e scelti per ogni stile."""
        rows = []
        for name in self.all_names():
            row = {"name": name, "uses": USES.get(name, "avventure (azione \"sound\" o campo sound dei passi)")}
            for style in EDIT_STYLES:
                override = self._override(style, name)
                default = self._defaults(style, name)
                row[style] = {"default": [self.describe(f) for f in default],
                              "override": [self.describe(f) for f in override] if style in self.assignments
                              and name in self.assignments[style] else None}
            rows.append(row)
        return rows

    def catalog(self, section: str = "", game: str = "", query: str = "", limit: int = 300) -> dict:
        q = query.strip().lower()
        tools = (bool(shutil.which("ffmpeg")), self.midi_synth())
        sections: dict[str, int] = {}
        games: dict[tuple[str, str], int] = {}
        found = []
        for item in self.items:
            sections[item["section"]] = sections.get(item["section"], 0) + 1
            if section and item["section"] != section:
                continue
            key = (item["section"], item.get("game", ""))
            games[key] = games.get(key, 0) + 1
            if game and item.get("game") != game:
                continue
            if q and q not in " ".join(str(item.get(k, "")) for k in ("description", "category", "game", "extra", "rel")).lower():
                continue
            found.append(item)
        return {"total": len(self.items), "sections": sections, "matches": len(found),
                "games": [{"section": s, "game": g, "count": n} for (s, g), n in sorted(games.items())],
                "items": [dict(x, browser=Path(x.get("play_rel") or x["rel"]).suffix.lower() in BROWSER,
                               playable=self.can_play(x["rel"], tools)) for x in found[:max(1, min(2000, limit))]]}

    def status(self) -> dict:
        games: dict[str, int] = {}
        for item in self.items:
            games[item.get("game", "?")] = games.get(item.get("game", "?"), 0) + 1
        missing = sorted(n for table in (EFFECTS, VOICE) for n, rules in table.items()
                         if self.index and not any((g, d.lower()) in self.index for g, d in rules))
        return {"folder": str(self.folder), "files": len(self.items), "games": games, "missing": missing,
                "volume": self.volume, "ffmpeg": bool(shutil.which("ffmpeg")), "midi": self.midi_synth()}
