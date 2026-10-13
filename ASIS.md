# ASIS — stato attuale: cosa è fatto e verificato

Fotografia del progetto LEGO Mario via Bluetooth LE al 13 ottobre 2026. Qui stanno le attività
**completate** (spostate da [TODO.md](TODO.md), che ora contiene solo il lavoro aperto); il
racconto di come ci si è arrivati è in [HANDOFF.md](HANDOFF.md), l'uso in [README.md](README.md).
Le prove dal vivo citate hanno i log in `docs/sessioni/`.

## 1. Verifiche dal vivo fatte (Mario, firmware 6.5.01)

- **Connessione e pairing**: Mario richiede un pairing "Just Works" prima delle scritture GATT;
  l'app registra un agente BlueZ proprio e associa il dispositivo alla prima connessione
  (bond salvato). Connessione in 5–6 s, riconnessione senza premere il tasto dopo una
  disconnessione comandata dal PC.
- **Pulsante**: la proprietà `BUTTON` manda premuto e rilasciato (12/10).
- **Pantaloni (porta 2)**: cambio dal vivo verificato con i pantaloni blu (0x21): sfilati → 0x00,
  infilati → 0x21, con codici transitori dei contatti durante l'inserimento (visti 0x18 e 0x01).
  Per questo l'assistente accetta un valore di pantaloni solo se resta stabile per 0,8 s.
- **Barcode (porta 1, modo TAG)**: verificati `0xB8` Start Mario, `0x02` Goomba (due basette
  uguali), `0x2E` Nuvola, `0x14` Rotazione; letti e aggiunti alla tabella `0x00F5` Bowser Jr. nel
  Clown Koopa, `0x0160` aereo e `0x015B` carrello minerario. La tabella ha 61 nomi.
- **Colori (porta 1, modo TAG)**: verificati bianco, nero, giallo, nougat, verde, rosso, blu.
- **Gesti (porta 0)**: osservati con accelerometro e gesti insieme: salto `0x8000` anche
  sollevando, appoggiando o colpendo Mario; scossa `0xA111`; girare Mario in piedi `0x8102` (bit
  `0x0002`); inclinare `0x0100`; cadere da pochi cm `0x2100`; fermo in mano nessun gesto; bit
  `0x0040` con l'urto all'atterraggio su una basetta. Accelerometro int8: fermo in mano y ≈ 17..60,
  scossa fino a ±98.
- **Modalità combinata**: funziona su porta 0 (RAW+GEST) e porta 1 (TAG+RGB). Mario manda la
  maschera dei dataset con il byte alto per primo (`00 1f` = dataset 0..4) e, con multi-update,
  solo i dataset cambiati: il ricevitore deve tenere l'ultimo valore di ciascuno
  (`lwp3.describe` e `tools/live_checks.py` lo fanno). In RGB combinato si muove solo il primo dei
  tre canali (0..189, più alto sul chiaro); gli altri due restano 117 e 130.
- **RSSI**: la proprietà risponde sempre 0, anche con aggiornamenti attivi: non implementata.
- **Allarmi hub**: `LOW_VOLTAGE`, `HIGH_CURRENT`, `LOW_SIGNAL_STRENGTH`, `OVER_POWER_CONDITION`
  rispondono "nessun problema" alla richiesta e accettano l'abilitazione degli aggiornamenti.
- **Azioni hub**: `DISCONNECT` → `WILL_DISCONNECT`, link chiuso in 0,2 s, Mario resta acceso;
  `SWITCH_OFF` → `WILL_SWITCH_OFF` ×2 + `WILL_DISCONNECT` ×2, scollegato in 6 s, Mario spento.
  `ACTIVATE_BUSY` / `RESET_BUSY` (0x05/0x06) → `INVALID_USE`: non supportate.
- **Batteria**: tensione raw 0..2705 = 0..3300 mV (porta 6) e percentuale come proprietà.
- **Standby**: 3 minuti di Mario connesso e fermo senza comandi: nessun messaggio LWP3 legato
  alle animazioni del display; il protocollo non vede ciò che fa il display.

## 2. Reverse engineering acquisito

- **Mappa delle funzioni** letta dal dispositivo (`docs/capabilities-fw-6.5.01.json`): porte 0
  (0x47), 1 (0x49), 2 (0x4A), 3 (0x46), 4 (0x55), 6 (0x14), 7 (0x60); la porta 7 "Telemetry"
  (16 byte, ingresso e uscita) è nuova dal firmware 6.x e non documentata altrove.
- **Eventi di gioco (porta 3 modo 2)**, `[codice LE16][valore LE16]`, ipotesi solide dalle sessioni
  con le basette reali: `0x0113` basetta letta (valore = barcode); `0x386B` totale monete del
  livello; `0x20C2` monete date dall'ultima basetta (6 o 8 con l'aereo, come una roulette);
  `0x2042` nemici sconfitti; `0x3712 = 1` circa 1 s dopo ogni nemico; `0x2022` giri sulla
  piattaforma rotante; `0x1803` 4 dopo lo Start, 3 a fine livello, 0 fuori; `0x3812` 2 in livello,
  1 fuori; sequenza del tempo scaduto `0x3004=601, 0x3812=2, 0x3852=0, 0x1803=3, 0x386E=0,
  0x3803=100` (confermata dal conto alla rovescia sul petto); `0x3862 = 0` quasi a ogni basetta
  letta o sollevata, 4 alla connessione. 22 codici in `codes.GAME_EVENTS`.
- **Porta 4 (`Events`)**: non ha mai trasmesso. Scritture (39 prove, fuori e dentro un livello):
  solo WriteDirectModeData (0x51) è accettato, con feedback "completato", ma il valore non viene
  conservato (rilettura `00 00 00 00`), nessun eco, contatore monete fermo, nessuna reazione.
  WriteDirect (0x50) → `INVALID_USE`; sottocomando 0x00 → `COMMAND_NOT_RECOGNIZED`.
- **Porta 7 (`Telemetry`)**: non ha mai trasmesso; 70 scritture (zeri, 255, un byte alla volta,
  rampe, ripetizioni) ignorate in silenzio: nessun feedback, nessun errore, rilettura zero. Un
  sospetto di reazione del display si è rivelato un falso positivo.
- **Conclusione sulla voce diretta**: con i comandi standard LWP3 non esiste una via per far
  suonare o animare Mario; le piste rimaste sono nel TODO (carta stampata, e-ink, luce colorata,
  cattura dell'app ufficiale, firmware).
- **Basette virtuali su schermo**: lo scanner non legge i barcode mostrati su un LCD (luce emessa,
  polarizzatori, probabile lettura nel vicino infrarosso). Pista chiusa per gli schermi emissivi.
- **System type**: la proprietà 0x0B risponde `0x40` mentre l'advertisement dichiara `0x43`.
- **Servizi GATT**: quattro (GATT, GAP, LEGO 1623/1624, OAD Texas Instruments `f000ffc0-…`),
  nessuna caratteristica nascosta.

## 3. Sistema e Bluetooth del PC (configurazione in essere)

- Claude Code gira nel Flatpak di VS Code: BlueZ non è raggiungibile dal sandbox, `run.sh` salta
  sull'host con `flatpak-spawn`; venv dell'host in `.venv` (Python 3.13, bleak 3.0.2, dbus-fast 5.2,
  Flask 3.1).
- MacBookPro12,1 con Bluetooth e Wi-Fi Broadcom sullo stesso modulo e senza firmware `.hcd`
  disponibile: la connessione regge con il Wi-Fi sulla rete a 5 GHz; autosospensione USB
  disattivata.
- Cause dei blocchi iniziali trovate con tre catture btmon: scansione al 100 % durante la
  connessione (supervision timeout), coesistenza radio a 2,4 GHz (0x3E), pairing negato
  dall'agente di GNOME (`Insufficient Authentication`). Tutte risolte nell'app.
- Diagnostica: `tools/capture_btmon.sh`, `tools/diag_connect.py`; ogni fase di connessione ha un
  timeout e stampa dove si trova.

## 4. Software realizzato

- **Libreria** `mario/`: protocollo LWP3 completo (costruttori, parser, descrizioni, formati
  valore, scaling SI), tabelle di Mario, `MarioDevice` con scansione, connessione con tentativi,
  agente di pairing, richieste con attesa, scoperta delle funzioni, report testuale e JSON.
- **CLI** `./run.sh`: `scan`, `info [--json] [--quick]`, `monitor`, `shell` (25 comandi), `assistant`,
  `web`, `volume`, `name`, `prop`, `pair`, `unpair`, `off`, `raw`; log di sessione in `logs/`.
- **Dashboard Flask** `mario/web/`: Panoramica, Scansione, Dispositivi (catalogo nel database),
  Monitor (SSE), Console, Accessori (posseduti e "visti" automatici), Assistente, Funzioni,
  Documentazione; database SQLite (`devices`, `accessories`, `events`, `settings`,
  `assistant_messages`). Provata con Mario connesso.
- **Assistente** `mario/assistant/`: regole sensori → intenti con pause e filtro di stabilità,
  parser delle emozioni in streaming, voce dal PC (speech-dispatcher / espeak-ng) con effetti
  sonori, backend Claude (API Messages), compatibile OpenAI e demo, comportamenti su Mario
  (volume relativo alla base, sincronizzati con la voce, allow-list), intenti in attesa finché
  Mario parla, chiave API da `.env`. Provato con Claude e con Mario connesso.
- **Strumenti dal vivo**: `tools/probe_port4.py` (scritture a scatola nera, osservazione di
  standby, ripetizioni), `tools/catalog_tiles.py` (sessione con le basette: codici, eventi,
  database), `tools/live_checks.py` (verifiche guidate a voce: pulsante, pantaloni, gesti, RGB,
  RSSI, allarmi, disconnect, off).
- **Test**: 54 senza Bluetooth né rete (`test_lwp3` 18, `test_db` 6, `test_assistant` 30), verdi nel
  sandbox e nell'host.
- **Documentazione**: README (uso, protocollo, funzioni verificate, eventi decodificati, problemi
  di connessione), HANDOFF (diario con orari), TODO (lavoro aperto), ASIS (questo file),
  `docs/sessioni/` (14 log delle prove, anonimizzati).
- **Git**: repository inizializzato il 12/10 e pubblicato su GitHub
  (`saulusprime/Mario-IA-Assistant`, pubblico), documenti anonimizzati (nessun nome di rete,
  hostname, percorso di casa, email o soprannome del dispositivo); esclusi `.venv/`, `logs/`,
  `data/`, `.env`, i cloni di terze parti.

## 5. Mario come I/O di un LLM: tappe fatte

- **Ingresso**: salto, caduta, rotazione, urto (spento), scossa = ripeti, pulsante = ascolto
  on/off, pantaloni con carattere, basette per famiglia (nemico, premio, start, traguardo, amico),
  terreno, batteria scarica, moneta (ipotesi, spenta); pause minime e filtro di stabilità per
  pantaloni e colore; regole modificabili dalla dashboard.
- **Uscita indiretta**: voce dal PC con velocità e tono per emozione, effetti sonori, volume di
  Mario che segue l'emozione e torna alla base; il nome non viene toccato.
- **Architettura**: sessione con streaming, frasi pronunciate una alla volta, loop eventi →
  prompt, intenti accumulati e uniti mentre Mario parla (scarto dopo 8 s), impostazioni e
  cronologia nel database, allow-list dei comandi verso Mario.
- **Prove fatte**: backend demo e voce (11/10), Claude `claude-sonnet-5-5` (risposte nel
  personaggio con i tag emozione in 2–4 s), sessione con Mario connesso da terminale (sensori
  veri → risposte coerenti; da lì il ritmo "aspetta la voce" e le pause più lunghe per rotazione
  e colore), indicazione "occupato" (non supportata), scritture su porta 4 e 7 (inerti).

## 6. Catalogo dell'utente

- **Mario**: firmware 6.5.01, hardware 0.4, LWP 3.0, associato al PC; pantaloni blu (0x21).
- **Set**: 71360 "Avventura di Mario" e 71425 "Corsa nella miniera di Diddy Kong".
- **8 basette**, tutte catalogate nel codice e nella pagina Accessori: tubo di partenza (0x00B8),
  Bowser Jr. nel Clown Koopa (0x00F5), Goomba ×2 (0x0002), aereo (0x0160), carrello minerario
  (0x015B), zattera di nuvole (0x002E), piattaforma rotante (0x0014).
- **Fonte esterna** consultata: wiki italiano di Super Mario, pagina LEGO Super Mario (set,
  regole: monete dai nemici, roulette del blocco ?, cuore con Yoshi/Toad, piattaforma rotante,
  asta della bandiera), leggibile tramite la sua API MediaWiki.
