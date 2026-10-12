"""
Mario come dispositivo di ingresso/uscita per una sessione con un modello di linguaggio.

  intents      sensori (gesti, pantaloni, basette, colori, pulsante, batteria) -> intenti e stato del corpo
  emotions     tag [gioia] [sorpresa] ... nel testo del modello -> emozione corrente, frasi per la voce
  tts          voce dal PC (speech-dispatcher / espeak-ng) ed effetti sonori sintetizzati
  llm          backend del modello: Claude (API Messages), endpoint compatibile OpenAI, demo locale
  behaviours   l'emozione si manifesta su Mario: volume, indicazione "occupato", scritture sperimentali
  session      il ciclo completo, un turno alla volta, con eventi per dashboard e CLI
  service      collegamento con il worker Bluetooth e il database della dashboard

Nessun modulo qui dipende da Flask o bleak: i test girano anche senza Bluetooth.
"""
from .emotions import DEFAULT_EMOTION, EMOTIONS, EmotionStream, SentenceBuffer, strip_tags
from .intents import DEFAULT_RULES, Intent, IntentMapper, event_from_message
from .llm import DemoBackend, LLMConfig, LLMError, make_backend
from .session import AssistantBusy, AssistantSession
from .tts import Speaker

__all__ = ["DEFAULT_EMOTION", "EMOTIONS", "EmotionStream", "SentenceBuffer", "strip_tags", "DEFAULT_RULES",
           "Intent", "IntentMapper", "event_from_message", "DemoBackend", "LLMConfig", "LLMError", "make_backend",
           "AssistantBusy", "AssistantSession", "Speaker"]
