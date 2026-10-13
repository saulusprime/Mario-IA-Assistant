"""
Designer di avventure (TODO, sezione 6): missioni fatte di elementi del mondo di Mario che "girano"
con il Mario fisico.

  model    Adventure / Step / Trigger / Action, da e verso JSON (condivisibile)
  engine   AdventureRunner: macchina a stati che ascolta gli eventi del worker, valuta il passo
           corrente, applica le azioni (testo, voce, suono, punti, volume, webhook), gestisce i timeout
  library  elementi disponibili (basette, colori, pantaloni, gesti, eventi) con icona e descrizione
  service  collegamento con il worker Bluetooth, il database e la voce dell'assistente (dashboard)

Nessun modulo qui dipende da bleak o Flask: l'engine si prova con eventi simulati.
"""
from .engine import AdventureRunner
from .model import Action, Adventure, Step, Trigger, load_examples, validate

__all__ = ["Action", "Adventure", "AdventureRunner", "Step", "Trigger", "load_examples", "validate"]
