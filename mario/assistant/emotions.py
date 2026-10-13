"""
Emozioni: il modello marca ogni frase con un tag tra parentesi quadre (`[gioia] Yahoo!`).

Qui ci sono il catalogo (emoji, colore per la dashboard, parametri della voce, effetto sonoro),
il parser che separa i tag dal testo mentre la risposta arriva a pezzi (streaming) e un
accumulatore che spezza il testo in frasi complete da mandare alla sintesi vocale.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class Emotion:
    key: str
    label: str
    emoji: str
    color: str
    rate: int        # variazione velocità della voce (-100..100)
    pitch: int       # variazione tono della voce (-100..100)
    sound: Optional[str]
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


EMOTIONS: dict[str, Emotion] = {
    "gioia": Emotion("gioia", "Gioia", "😄", "#f5c400", 15, 20, "coin", "allegro, entusiasta"),
    "sorpresa": Emotion("sorpresa", "Sorpresa", "😮", "#ff8c42", 10, 40, "powerup", "stupito, meravigliato"),
    "pensieroso": Emotion("pensieroso", "Pensieroso", "🤔", "#7f8c8d", -20, -10, "think", "riflette, è curioso"),
    "allarme": Emotion("allarme", "Allarme", "😱", "#d7262c", 25, 30, "alarm", "spaventato, in pericolo"),
    "tristezza": Emotion("tristezza", "Tristezza", "😢", "#1f5fbf", -25, -30, "sad", "dispiaciuto, deluso"),
    "sonno": Emotion("sonno", "Sonno", "😴", "#5b4b8a", -40, -40, "yawn", "stanco, assonnato"),
    "calma": Emotion("calma", "Calma", "🙂", "#2e9e44", 0, 0, None, "tranquillo, sereno"),
    "attesa": Emotion("attesa", "Attesa", "⏳", "#9aa0a6", -10, 0, "tick", "sta pensando alla risposta"),
}
DEFAULT_EMOTION = "calma"
MODEL_EMOTIONS = [k for k in EMOTIONS if k != "attesa"]  # quelle che il modello può usare

ALIASES = {
    "felice": "gioia", "contento": "gioia", "allegro": "gioia", "entusiasta": "gioia", "felicità": "gioia",
    "sorpreso": "sorpresa", "stupore": "sorpresa", "stupito": "sorpresa", "meraviglia": "sorpresa",
    "pensoso": "pensieroso", "riflessivo": "pensieroso", "curioso": "pensieroso", "curiosità": "pensieroso",
    "paura": "allarme", "spaventato": "allarme", "preoccupato": "allarme", "allarmato": "allarme", "pericolo": "allarme",
    "triste": "tristezza", "dispiaciuto": "tristezza", "deluso": "tristezza",
    "stanco": "sonno", "assonnato": "sonno", "sonnolento": "sonno", "stanchezza": "sonno",
    "tranquillo": "calma", "sereno": "calma", "neutro": "calma", "normale": "calma",
}

TAG_MAX = 24
MEMORY_PREFIX = "ricorda:"   # [ricorda: il bambino si chiama Leo] -> memoria tra le sessioni, non viene letto
MEMORY_MAX = 200
_SENTENCE_END = re.compile(r"[.!?…]+[\"'»)]*(\s+)")
_MEMORY_TAG = re.compile(r"\[\s*ricorda:\s*([^\]]{1,%d})\]\s*" % MEMORY_MAX, re.IGNORECASE)


def resolve(tag: str) -> Optional[str]:
    key = tag.strip().lower().strip(" :.")
    if key in EMOTIONS and key != "attesa":
        return key
    return ALIASES.get(key)


def strip_tags(text: str) -> str:
    """Toglie i tag emozione e i tag [ricorda: ...] da un testo completo (per il log e la cronologia)."""
    def repl(m: re.Match) -> str:
        return "" if resolve(m.group(1)) else m.group(0)
    text = _MEMORY_TAG.sub("", text)
    return re.sub(r"\[([^\[\]]{1,%d})\]\s*" % TAG_MAX, repl, text).strip()


def memory_tags(text: str) -> list[str]:
    """Le memorie contenute nei tag [ricorda: ...] di un testo completo."""
    return [m.group(1).strip() for m in _MEMORY_TAG.finditer(text)]


class EmotionStream:
    """Riceve il testo a pezzi e restituisce una lista di ("emotion", chiave) / ("text", str).

    Un tag può arrivare spezzato in due pezzi ("[gio" + "ia] Ciao"): si aspetta la parentesi di
    chiusura prima di decidere. Le parentesi che non sono un'emozione restano nel testo."""

    def __init__(self) -> None:
        self.buffer = ""
        self.current = DEFAULT_EMOTION
        self.seen: list[str] = []
        self.memories: list[str] = []

    def _is_memory_prefix(self) -> bool:
        head = self.buffer[1:len(MEMORY_PREFIX) + 2].lstrip().lower()
        return head.startswith(MEMORY_PREFIX) or MEMORY_PREFIX.startswith(head) and bool(head)

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self.buffer += chunk
        out: list[tuple[str, str]] = []
        while self.buffer:
            start = self.buffer.find("[")
            if start < 0:
                out.append(("text", self.buffer))
                self.buffer = ""
                break
            if start > 0:
                out.append(("text", self.buffer[:start]))
                self.buffer = self.buffer[start:]
            end = self.buffer.find("]")
            if end < 0:
                limit = MEMORY_MAX + 12 if self._is_memory_prefix() else TAG_MAX + 2
                if len(self.buffer) > limit:  # troppo lungo per essere un tag: è testo
                    out.append(("text", self.buffer))
                    self.buffer = ""
                break  # aspettiamo il resto
            inner = self.buffer[1:end].strip()
            if inner.lower().startswith(MEMORY_PREFIX) and len(inner) <= MEMORY_MAX + len(MEMORY_PREFIX):
                memory = inner[len(MEMORY_PREFIX):].strip()
                if memory:
                    out.append(("memory", memory))
                    self.memories.append(memory)
                rest = self.buffer[end + 1:]
                self.buffer = rest.lstrip(" ") if rest else ""
                continue
            key = resolve(inner)
            if key:
                out.append(("emotion", key))
                self.current = key
                if not self.seen or self.seen[-1] != key:
                    self.seen.append(key)
                rest = self.buffer[end + 1:]
                self.buffer = rest.lstrip(" ") if rest else ""
            else:
                out.append(("text", self.buffer[:end + 1]))
                self.buffer = self.buffer[end + 1:]
        return _merge(out)

    def flush(self) -> list[tuple[str, str]]:
        out = [("text", self.buffer)] if self.buffer else []
        self.buffer = ""
        return out


def _merge(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    for kind, value in items:
        if kind == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + value)
        elif kind == "text" and not value:
            continue
        else:
            merged.append((kind, value))
    return merged


class SentenceBuffer:
    """Accumula testo e restituisce le frasi complete (per la voce, una alla volta).

    Una frase finisce con . ! ? … seguiti da spazio e da qualcosa che non è una lettera minuscola
    (così "bene... davvero." resta intera) oppure con un a capo."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        sentences: list[str] = []
        while True:
            nl = self.buffer.find("\n")
            cut = None
            for m in _SENTENCE_END.finditer(self.buffer):
                following = self.buffer[m.end():m.end() + 1]
                if following and not following.islower():
                    cut = (m.start(1), m.end())
                    break
            if nl != -1 and (cut is None or nl < cut[0]):
                sentence, self.buffer = self.buffer[:nl], self.buffer[nl + 1:]
            elif cut is not None:
                sentence, self.buffer = self.buffer[:cut[0]], self.buffer[cut[1]:]
            else:
                break
            if sentence.strip():
                sentences.append(sentence.strip())
        return sentences

    def flush(self) -> Optional[str]:
        rest, self.buffer = self.buffer.strip(), ""
        return rest or None


# Frasi di prova per ogni emozione (pulsanti della dashboard, comando `emotion` della CLI)
SAMPLES = {
    "gioia": "Yahoo! Che bella giornata!", "sorpresa": "Oh! Ma cosa è successo?",
    "pensieroso": "Mmm, fammi pensare un momento.", "allarme": "Mamma mia! Attenzione!",
    "tristezza": "Oh no, che peccato.", "sonno": "Che sonno, vado a fare un pisolino.",
    "calma": "Va tutto bene, sono qui.", "attesa": "Un attimo, ci penso.",
}
