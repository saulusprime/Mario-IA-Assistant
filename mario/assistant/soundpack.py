"""
Suoni veri dei giochi di Mario al posto degli effetti sintetizzati.

I file vengono dall'archivio di appassionati The Mushroom Kingdom (themushroomkingdom.net, suoni
© Nintendo) e li scarica `tools/fetch_mario_sounds.py` in `data/sounds/tmk/` con `catalogo.json`
(gioco, categoria, descrizione). Restano solo sul PC: uso privato, mai nel repository.

Tre stili (`Speaker.sound_style`):
  - "sintetici": gli effetti generati al volo di `tts.py` (come prima, nessun file);
  - "giochi":    gli effetti dei giochi (moneta e power-up di Super Mario Bros., ...);
  - "voce":      dove c'è, un'esclamazione di Mario ("Yahoo!" per la gioia, "Mamma-mia" per
                 l'allarme, sbadiglio per il sonno), altrimenti l'effetto del gioco.
Se un suono manca si torna allo stile precedente, fino a quello sintetizzato.

I nomi sono quelli già usati da emozioni, sessione e avventure (coin, powerup, alarm, sad, yawn,
think, tick, hello, listen_on, listen_off) più alcuni nuovi per le avventure (oneup, jump, stomp,
pipe, star, clear, gameover, ...); un'avventura può anche indicare un file preciso, `sm64/sm64_coin.wav`.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parents[2]
PACK_DIR = PROJECT_DIR / "data" / "sounds" / "tmk"
STYLES = ("sintetici", "giochi", "voce")
DEFAULT_STYLE = "giochi"

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


class SoundPack:
    """Indice dei suoni scaricati: nome logico -> file, secondo lo stile."""

    def __init__(self, folder: Optional[Path] = None, rng: Optional[random.Random] = None):
        self.folder = Path(folder or PACK_DIR)
        self.rng = rng or random.Random()
        self.index: dict[tuple[str, str], Path] = {}
        self.items: list[dict] = []
        self.reload()

    def reload(self) -> None:
        self.index.clear()
        self.items = []
        catalog = self.folder / "catalogo.json"
        try:
            data = json.loads(catalog.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in data.get("items", []):
            path = self.folder / item.get("file", "")
            if path.is_file():
                self.items.append(item)
                self.index[(item["game"], item.get("description", "").lower())] = path

    @property
    def available(self) -> bool:
        return bool(self.index)

    def _pick(self, rules: list[Rule]) -> Optional[Path]:
        found = [self.index[(g, d.lower())] for g, d in rules if (g, d.lower()) in self.index]
        return self.rng.choice(found) if found else None

    def file(self, name: str) -> Optional[Path]:
        """Un file preciso dell'archivio indicato come `gioco/file.wav` (dalle avventure)."""
        if "/" not in name or ".." in name:
            return None
        path = self.folder / name
        return path if path.is_file() and path.suffix.lower() == ".wav" else None

    def path(self, name: str, style: str = DEFAULT_STYLE) -> Optional[Path]:
        """Il file da suonare per `name` nello stile scelto; None = usare l'effetto sintetizzato."""
        if style == "sintetici" or not self.index:
            return None
        direct = self.file(name)
        if direct:
            return direct
        if style == "voce":
            found = self._pick(VOICE.get(name, []))
            if found:
                return found
        return self._pick(EFFECTS.get(name, []))

    def names(self) -> dict[str, list[str]]:
        """Nomi utilizzabili con i suoni scaricati, per stile (per le avventure e la dashboard)."""
        have = lambda rules: any((g, d.lower()) in self.index for g, d in rules)
        return {"giochi": sorted(n for n, r in EFFECTS.items() if have(r)),
                "voce": sorted(n for n, r in VOICE.items() if have(r))}

    def status(self) -> dict:
        games: dict[str, int] = {}
        for item in self.items:
            games[item["game"]] = games.get(item["game"], 0) + 1
        missing = sorted(n for table in (EFFECTS, VOICE) for n, rules in table.items()
                         if self.index and not any((g, d.lower()) in self.index for g, d in rules))
        return {"folder": str(self.folder), "files": len(self.items), "games": games, "missing": missing}
