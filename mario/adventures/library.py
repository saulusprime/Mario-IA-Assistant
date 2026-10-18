"""Libreria degli elementi usabili in un'avventura, a partire dalle tabelle di `mario.codes`."""
from __future__ import annotations

from .. import codes
from ..assistant.intents import COLOR_HINTS, TILE_FRIEND, TILE_GOAL, TILE_PRIZE, TILE_START, short_name, tile_family
from .model import GESTURE_NAMES, LEVEL_PHASE_NAMES, Trigger

FAMILY_LABELS = {"start": "partenza", "goal": "traguardo", "enemy": "nemico", "prize": "premio", "friend": "amico",
                 "any": "qualsiasi basetta", "unknown": "sconosciuta"}
FAMILY_ICONS = {"start": "🟢", "goal": "🏁", "enemy": "👾", "prize": "🎁", "friend": "🙂", "any": "🔲", "unknown": "❔"}
COLOR_ICONS = {0x0013: "⬜", 0x0015: "🟥", 0x0017: "🟦", 0x0018: "🟨", 0x001A: "⬛", 0x0025: "🟩", 0x006A: "🟫",
               0x010C: "🟪", 0x0138: "🧱", 0x0142: "🧊"}
LEVEL_LABELS = {"inizio": "inizio del livello", "ultimi_secondi": "ultimi 15 secondi del livello",
                "fine": "fine del livello", "uscita": "uscita dal livello"}
LEVEL_ICONS = {"inizio": "🚩", "ultimi_secondi": "⏳", "fine": "🏁", "uscita": "🚪"}
GESTURE_ICONS = {"salto": "🦘", "caduta": "💥", "rotazione": "🔄", "urto": "👊", "scossa": "🫨", "inversione": "🙃"}
PANTS_ICONS = {0x12: "🔥", 0x11: "🐱", 0x0C: "🚁", 0x03: "🐝", 0x06: "🐸", 0x0A: "🦝", 0x14: "🐧", 0x22: "🔧", 0x23: "🧊",
               0x21: "👖", 0x05: "💚", 0x18: "👑"}
# Effetto nel gioco originale (dal wiki LEGO Super Mario), per la descrizione dell'elemento
GAME_EFFECTS = {"enemy": "saltarci sopra dà monete", "prize": "dà monete o un potere", "start": "fa partire il livello (60 s)",
                "goal": "chiude il livello e conta le monete", "friend": "saluta e regala monete"}


def tiles(owned_codes: set[int] | None = None) -> list[dict]:
    out = []
    for code, name in sorted(codes.BARCODES.items()):
        fam = tile_family(code)
        out.append({"kind": "barcode", "code": code, "hex": f"0x{code:04X}", "name": short_name(name), "family": fam,
                    "family_label": FAMILY_LABELS[fam], "icon": FAMILY_ICONS[fam], "effect": GAME_EFFECTS.get(fam, ""),
                    "owned": code in (owned_codes or set()), "verified": codes.verified_codes("barcodes").get(code)})
    return out


def colors() -> list[dict]:
    return [{"kind": "color", "code": c, "hex": f"0x{c:04X}", "name": short_name(n), "icon": COLOR_ICONS.get(c, "🎨"),
             "effect": COLOR_HINTS.get(c, ""), "verified": codes.verified_codes("colors").get(c)}
            for c, n in sorted(codes.COLORS.items())]


def pants() -> list[dict]:
    return [{"kind": "pants", "code": c, "hex": f"0x{c:02X}", "name": short_name(n), "icon": PANTS_ICONS.get(c, "👖"),
             "verified": codes.verified_codes("pants").get(c)} for c, n in sorted(codes.PANTS.items()) if c]


def gestures() -> list[dict]:
    return [{"kind": "gesture", "name": name, "bits": f"0x{bits:04X}", "icon": GESTURE_ICONS.get(name, "✨"),
             "label": short_name(codes.GESTURES.get(bits, name))} for name, bits in GESTURE_NAMES.items()]


def levels() -> list[dict]:
    return [{"kind": "level", "name": name, "value": value, "icon": LEVEL_ICONS[name], "label": LEVEL_LABELS[name]}
            for name, value in LEVEL_PHASE_NAMES.items()]


def game_events() -> list[dict]:
    return [{"kind": "game_event", "code": c, "hex": f"0x{c:04X}", "name": n} for c, n in sorted(codes.GAME_EVENTS.items())]


def sounds() -> dict:
    """Nomi degli effetti per l'azione "sound" e il campo "sound" dei passi: sintetizzati (sempre) e, se
    l'archivio è stato scaricato con ./run.sh fetch-sounds, quelli dei giochi e le esclamazioni di Mario."""
    from ..assistant.soundpack import SoundPack
    from ..assistant.tts import SOUND_RECIPES
    pack = SoundPack()
    return {"sintetici": sorted(SOUND_RECIPES), **pack.names(), "file": len(pack.items)}


def library(owned_codes: set[int] | None = None) -> dict:
    return {"tiles": tiles(owned_codes), "colors": colors(), "pants": pants(), "gestures": gestures(),
            "levels": levels(), "game_events": game_events(), "families": FAMILY_LABELS, "sounds": sounds()}


def describe_trigger(t: Trigger) -> str:
    if t.kind == "barcode":
        if t.code is not None:
            return f"basetta {short_name(codes.BARCODES.get(t.code, f'0x{t.code:04X}'))}"
        return f"basetta {FAMILY_LABELS.get(t.family or 'any', t.family)}"
    if t.kind == "color":
        return f"terreno {short_name(codes.COLORS.get(t.code, f'0x{t.code:04X}'))}"
    if t.kind == "pants":
        return f"pantaloni {short_name(codes.PANTS.get(t.code, f'0x{t.code:02X}'))}"
    if t.kind == "gesture":
        return f"gesto {t.name}"
    if t.kind == "button":
        return "pulsante"
    if t.kind == "game_event":
        name = codes.GAME_EVENTS.get(t.code or -1, f"0x{(t.code or 0):04X}").split(" (")[0]
        extra = f" = {t.value}" if t.value is not None else (f" ≥ {t.min_value}" if t.min_value is not None else "")
        return f"evento {name}{extra}"
    if t.kind == "level":
        return LEVEL_LABELS.get((t.name or "").lower(), f"livello {t.name}")
    if t.kind == "timer":
        return f"dopo {t.seconds:g} s"
    if t.kind == "sequence":
        return " poi ".join(describe_trigger(s) for s in t.steps) + (f" entro {t.seconds:g} s" if t.seconds else "")
    if t.kind == "count":
        text = f"{describe_trigger(t.of)} × {t.times}" if t.of else f"× {t.times}"
        return text + (f" entro {t.window:g} s" if t.window else "")
    if t.kind == "any_of":
        return " oppure ".join(describe_trigger(s) for s in t.steps)
    return t.kind


__all__ = ["library", "tiles", "colors", "pants", "gestures", "levels", "game_events", "describe_trigger",
           "TILE_START", "TILE_GOAL", "TILE_PRIZE", "TILE_FRIEND"]
