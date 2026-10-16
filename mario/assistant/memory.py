"""
Memoria tra le sessioni: frasi brevi che Mario "ricorda" (nome di chi gioca, preferenze).

Il modello le produce con il tag `[ricorda: ...]` nella risposta (vedi `emotions.EmotionStream`),
oppure le scrive un genitore dalla pagina o dalla CLI. Finiscono nel prompt di sistema delle sessioni
successive. Due archivi: in memoria (test, `--no-mario` senza database) e nel database SQLite della
dashboard (tabella `assistant_memory`), condiviso da CLI e pagina Assistente.

Dal 16/10:
  - profili: ogni ricordo è di un bambino (`profile`) o comune a tutti (''); il profilo attivo vede i suoi e
    quelli comuni, e i ricordi nuovi del modello vanno al profilo attivo;
  - scadenza: con `days` > 0 i ricordi scritti dal modello scadono dopo tanti giorni; quelli dei genitori
    (`source` "genitore") restano finché non li si cancella;
  - riassunto: oltre SUMMARY_AT ricordi del modello per profilo, la sessione chiede al modello di riassumerli
    in al massimo SUMMARY_TARGET frasi (`summary_messages`, `parse_summary`) e li sostituisce.
"""
from __future__ import annotations

import re
import time
from typing import Optional, Protocol

SUMMARY_AT = 20
SUMMARY_TARGET = 10
MODEL, PARENT, SUMMARY = "modello", "genitore", "riassunto"
SUMMARY_SYSTEM = ("Riassumi i ricordi di Mario, un giocattolo che parla con un bambino, in al massimo {n} frasi brevi in "
                  "terza persona, una per riga, senza numeri né elenchi puntati. Tieni i fatti più utili per giocare "
                  "insieme (nomi, età, gusti, paure, cose importanti successe), unisci i doppioni e togli quelli "
                  "superati da ricordi più recenti. Non inventare nulla e non aggiungere commenti.")


class MemoryStore(Protocol):
    profile: str

    def list(self, limit: int = 50) -> list[dict]: ...
    def add(self, text: str, source: str = MODEL) -> int: ...
    def delete(self, memory_id: int) -> int: ...
    def clear(self) -> int: ...
    def replace(self, ids: list[int], texts: list[str]) -> int: ...


def clean(text: str) -> str:
    return " ".join(str(text).split()).strip(" .;")[:200]


def expiry(source: str, days: float, now: Optional[float] = None) -> Optional[float]:
    """Scadenza di un ricordo nuovo: solo quelli del modello (e i loro riassunti) scadono."""
    if days <= 0 or source == PARENT:
        return None
    return (now or time.time()) + days * 86400.0


class ListMemory:
    """Archivio in RAM (dura quanto il processo)."""

    def __init__(self, items: Optional[list[str]] = None, profile: str = "", days: float = 0.0):
        self._items: list[dict] = []
        self._next = 1
        self.profile = profile
        self.days = days
        for text in items or []:
            self.add(text)

    def _visible(self, item: dict, now: float) -> bool:
        return item["profile"] in ("", self.profile) and (item["expires"] is None or item["expires"] > now)

    def list(self, limit: int = 50) -> list[dict]:
        now = time.time()
        return [dict(i) for i in self._items if self._visible(i, now)][-limit:]

    def add(self, text: str, source: str = MODEL) -> int:
        text = clean(text)
        if not text:
            return 0
        for item in self._items:
            if item["text"].lower() == text.lower() and item["profile"] == self.profile:
                return item["id"]
        item = {"id": self._next, "ts": time.time(), "text": text, "profile": self.profile, "source": source,
                "expires": expiry(source, self.days)}
        self._items.append(item)
        self._next += 1
        return item["id"]

    def delete(self, memory_id: int) -> int:
        before = len(self._items)
        self._items = [i for i in self._items if i["id"] != memory_id]
        return before - len(self._items)

    def clear(self) -> int:
        """Cancella i ricordi del profilo attivo (tutti, se non c'è un profilo)."""
        before = len(self._items)
        self._items = [i for i in self._items if self.profile and i["profile"] != self.profile]
        return before - len(self._items)

    def replace(self, ids: list[int], texts: list[str]) -> int:
        self._items = [i for i in self._items if i["id"] not in set(ids)]
        for text in texts:
            self.add(text, SUMMARY)
        return len(texts)


class DatabaseMemory:
    """Archivio nella tabella `assistant_memory` del database della dashboard (mario.web.db.Database)."""

    def __init__(self, db, profile: str = "", days: float = 0.0):
        self.db = db
        self.profile = profile
        self.days = days

    def list(self, limit: int = 50) -> list[dict]:
        return self.db.list_memories(limit, profile=self.profile)

    def add(self, text: str, source: str = MODEL) -> int:
        text = clean(text)
        return self.db.add_memory(text, self.profile, source, expiry(source, self.days)) if text else 0

    def delete(self, memory_id: int) -> int:
        return self.db.delete_memory(memory_id)

    def clear(self) -> int:
        return self.db.clear_memories(self.profile or None)

    def replace(self, ids: list[int], texts: list[str]) -> int:
        return self.db.replace_memories(ids, texts, self.profile, SUMMARY, expiry(SUMMARY, self.days))


def memory_prompt(items: list[dict], limit: int = 20) -> str:
    """Blocco per il prompt di sistema; vuoto se non c'è nulla da ricordare."""
    texts = [i["text"] for i in items[-limit:] if i.get("text")]
    if not texts:
        return ""
    return "Cose che ricordi dalle volte scorse (usale con naturalezza, senza elencarle): " + "; ".join(texts) + "."


def summary_candidates(items: list[dict], profile: str = "") -> list[dict]:
    """I ricordi che il riassunto può toccare: del modello (o riassunti prima), del profilo attivo, non dei genitori."""
    return [i for i in items if i.get("source", MODEL) != PARENT and i.get("profile", "") == profile]


def summary_messages(items: list[dict]) -> list[dict]:
    lines = "\n".join(f"- {i['text']}" for i in items)
    return [{"role": "user", "content": f"Ricordi, dal più vecchio al più recente:\n{lines}"}]


def parse_summary(text: str, limit: int = SUMMARY_TARGET) -> list[str]:
    """Una frase per riga, senza puntini, numeri o tag; doppioni tolti; al massimo `limit`."""
    out: list[str] = []
    for line in str(text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line)
        line = re.sub(r"\[[^\]]*\]", "", line)
        line = clean(line)
        if len(line) >= 3 and line.lower() not in {o.lower() for o in out}:
            out.append(line)
    return out[:limit]
