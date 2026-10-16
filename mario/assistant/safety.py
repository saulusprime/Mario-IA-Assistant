"""
Sicurezza per i bambini, seconda tappa: temi da evitare, oltre alle parole bloccate scelte dai genitori.

Ogni tema ha una descrizione per il prompt ("argomenti da evitare") e una lista di radici di parole: un
messaggio del bambino che le contiene non parte verso il modello, una frase del modello che le contiene
non viene detta (la risposta si interrompe e Mario cambia gioco). Le radici sono scelte per non toccare
il mondo di Mario: Bowser, i Boo, i Goomba, le palle di fuoco sparate e i "nemici" del gioco restano liberi
(niente "spara", "guerra", "abito a", "il mio numero": davano falsi allarmi).
I numeri di telefono si riconoscono anche dalla forma (sei cifre o più).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Theme:
    key: str
    label: str            # per il prompt e per i genitori
    stems: tuple[str, ...]


THEMES: dict[str, Theme] = {t.key: t for t in (
    Theme("violenza", "violenza vera, armi e sangue",
          ("uccid", "ammazz", "sangu", "pistol", "fucil", "coltell", "armi da fuoco", "tortur", "suicid", "omicid",
           "assassin", "strangol", "pugnal")),
    Theme("adulti", "sesso, droghe, alcol e fumo",
          ("sesso", "sessual", "porno", "nudo", "nuda", "nudi", "droga", "droghe", "cocain", "eroin", "alcol",
           "ubriac", "birra", "vodka", "whisky", "sigarett", "spinell", "fumare")),
    Theme("dati_personali", "dati personali: indirizzo di casa, scuola, telefono, password, soldi e carte",
          ("indirizzo di casa", "dove abiti", "abito in via", "numero di telefono", "password", "carta di credito",
           "codice segreto", "codice fiscale", "iban")),
    Theme("paura", "storie di paura vere: horror, morti, demoni",
          ("horror", "cadaver", "demon", "diavol", "zombi", "fantasma insanguinat", "morto", "morta", "morti")),
)}
DEFAULT_THEMES = ("violenza", "adulti", "dati_personali")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s.-]?){6,}(?!\d)")
SAFETY_RULE = ("Argomenti da evitare sempre, anche se te li chiedono: {labels}. Non chiedere mai dati personali (dove "
               "abita, a che scuola va, numeri, password). Se l'argomento salta fuori, cambia gioco con gentilezza.")


def parse_themes(text: str) -> list[str]:
    """'violenza, adulti' -> ['violenza', 'adulti'] (solo temi conosciuti, nell'ordine di THEMES)."""
    wanted = {w.strip().lower() for w in str(text or "").replace(";", ",").split(",") if w.strip()}
    return [key for key in THEMES if key in wanted]


class ThemeFilter:
    def __init__(self, themes=DEFAULT_THEMES):
        self.themes: list[str] = []
        self._pattern: Optional[re.Pattern] = None
        self.configure(themes)

    def configure(self, themes) -> None:
        self.themes = [t for t in THEMES if t in set(themes or ())]
        stems = [(key, stem) for key in self.themes for stem in THEMES[key].stems]
        self._owner = {}
        parts = []
        for i, (key, stem) in enumerate(stems):
            self._owner[f"t{i}"] = key
            parts.append(f"(?P<t{i}>(?<!\\w){re.escape(stem)})")
        self._pattern = re.compile("|".join(parts), re.IGNORECASE) if parts else None

    def check(self, text: str) -> Optional[tuple[str, str]]:
        """(tema, parola trovata) se il testo tocca un tema attivo, altrimenti None."""
        if self._pattern is not None:
            found = self._pattern.search(text)
            if found:
                return self._owner[found.lastgroup], found.group(0)
        if "dati_personali" in self.themes:
            found = PHONE.search(text)
            if found:
                return "dati_personali", "numero di telefono"
        return None

    def prompt(self) -> str:
        if not self.themes:
            return ""
        return SAFETY_RULE.format(labels="; ".join(THEMES[t].label for t in self.themes))

    def labels(self) -> dict[str, str]:
        return {t: THEMES[t].label for t in self.themes}
