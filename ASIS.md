# ASIS — stato attuale: cosa è fatto e verificato

Fotografia del progetto LEGO Mario via Bluetooth LE al 13 ottobre 2026 (sera). Qui stanno le attività
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
  solo i dataset cambiati: il ricevitore deve tenere l'ultimo valore di ciascuno (lo fa
  `mario/hubstate.py` per dispositivo, dashboard, CLI e `decode`; verificato offline sui messaggi
  registrati, `monitor --combined` ancora da provare dal vivo). In RGB combinato si muove solo il primo dei
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
  con le basette reali e dalla rilettura dei log con `decode` (13/10): `0x0113` basetta letta
  (valore = barcode); `0x386B` totale monete del livello (0 alla lettura dello Start); `0x20C2`
  monete date dall'ultima basetta (6 o 8 con l'aereo, come una roulette); `0x2042` nemici sconfitti
  (riparte da 1 anche fuori dal livello); `0x3712 = 1` circa 1 s dopo ogni nemico; `0x2022` giri
  sulla piattaforma rotante; macchina a stati del livello `0x1801` (1 allo Start, 0 dopo la fine) e
  `0x1803` (1 allo Start, 4 in livello, 3 a fine livello, 0 fuori); `0x3812` 2 a fine livello, 1
  fuori; sequenza di fine livello `0x3004=601, 0x3812=2, 0x3852=0, 0x1803=3, 0x386E=0, 0x3803=100`
  (601 è costante: non è il tempo); `0x3861` conto alla rovescia a un passo al secondo (5→1, poi
  8→1) con Mario fermo fuori dal livello, chiuso da `0x3866=0`/`0x1802=2` e da 93 s di immobilità
  (sonno?), con `0x3866=1`/`0x1802=1` al risveglio; `0x3862` 0 a ogni movimento o basetta, 2 dopo
  il conto alla rovescia, 4 alla connessione. 22 codici in `codes.GAME_EVENTS`; la conferma dal vivo
  è il passo `sonno` di `./run.sh re-checks`.
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
  nessuna caratteristica nascosta. Dal 13/10 `info` elenca la tabella GATT con i nomi dei servizi
  noti e l'avviso sull'OAD (da rileggere dal vivo: passo `gatt` di `re-checks`).

## 3. Sistema e Bluetooth del PC (configurazione in essere)

- Claude Code gira nel Flatpak di VS Code: BlueZ non è raggiungibile dal sandbox, `run.sh` salta
  sull'host con `flatpak-spawn`; venv dell'host in `.venv` (Python 3.13, bleak 3.0.2, dbus-fast 5.2,
  Flask 3.1, waitress 3.0).
- MacBookPro12,1 con Bluetooth e Wi-Fi Broadcom sullo stesso modulo e senza firmware `.hcd`
  disponibile: la connessione regge con il Wi-Fi sulla rete a 5 GHz; autosospensione USB
  disattivata (`/sys/bus/usb/devices/1-3/power/control` = `on`). Il 13/10 il PC risultava acceso
  dall'11/10 alle 11:30, cioè mai riavviato dopo la creazione di `/etc/modprobe.d/btusb.conf`: la
  persistenza dopo il riavvio resta da controllare; regola udev pronta in `tools/99-mario-btusb.rules`.
- Firmware `BCM20703A1-05ac-8290.hcd` cercato di nuovo il 13/10: assente da linux-firmware e dai
  pacchetti comunitari (confermato da altri utenti dello stesso MacBook su Launchpad).
- Cause dei blocchi iniziali trovate con tre catture btmon: scansione al 100 % durante la
  connessione (supervision timeout), coesistenza radio a 2,4 GHz (0x3E), pairing negato
  dall'agente di GNOME (`Insufficient Authentication`). Tutte risolte nell'app.
- Diagnostica: `tools/capture_btmon.sh`, `tools/diag_connect.py`; ogni fase di connessione ha un
  timeout e stampa dove si trova.

## 4. Software realizzato

- **Libreria** `mario/`: protocollo LWP3 completo (costruttori, parser, descrizioni dei messaggi in
  arrivo e in uscita, formati valore, scaling SI); `mario/hubstate.py` con lo stato dell'hub senza
  bleak (porte, modi, formati, **valori combinati 0x46 ricomposti per modo**, evento unico per
  dashboard, CLI e assistente, stato iniziale dal JSON delle funzioni); tabelle in
  `mario/data/mario_codes.json` con fonte e data di verifica; `MarioDevice` con scansione,
  connessione con tentativi, agente di pairing, richieste con attesa, scoperta, modalità combinata
  con delta per modo, report (con stato del bond) e JSON.
- **CLI** `./run.sh`: `scan`, `info` (salva `docs/capabilities-fw-<versione>.json`), `monitor`
  (`--combined`, `--json`, `--duration`, `--no-battery`, riconnessione automatica), `shell`
  (readline, `help <comando>`, `log`, riconnessione), `decode` (catture btsnoop, bugreport Android con
  sezioni btsnooz, PacketLogger di Apple, gzip, log, byte; i formati dei telefoni provati su file sintetici),
  `assistant`, `adventure`, `web` (waitress, token), comandi rapidi, strumenti di `tools/` come
  sottocomandi; log di sessione in `logs/`.
- **Dashboard Flask** `mario/web/`: Panoramica, Scansione, Dispositivi (nome e volume dalla scheda),
  Monitor (SSE), **Eventi** (filtri, esportazione CSV/JSON, grafici), Console, Accessori (con colonna
  "verificato"), Assistente, **Avventure** (editor e schermo di gioco), Funzioni, Documentazione;
  database SQLite (`devices`, `accessories`, `events`, `settings`, `assistant_messages`,
  `assistant_memory`, `adventures`, `adventure_runs`); accesso con token oltre 127.0.0.1.
- **Assistente** `mario/assistant/`: regole sensori → intenti con pause e filtro di stabilità,
  parser delle emozioni in streaming, voce dal PC (speech-dispatcher / espeak-ng / piper) con
  effetti sonori, backend Claude (API Messages), compatibile OpenAI e demo, comportamenti su Mario
  (volume relativo, sincronizzati con la voce, allow-list), intenti in attesa finché Mario parla,
  ricordi tra le sessioni, limite di tempo e parole bloccate, diario per i genitori, chiave API da
  `.env`. Provato con Claude e con Mario connesso (prima versione, 11/10).
- **Designer di avventure** `mario/adventures/` (prima versione, 13/10): modello JSON con
  validazione, motore a stati con trigger (basetta, colore, pantaloni, gesto, pulsante, evento di
  gioco, timer, sequenza, ripetizione) e azioni (punti, voce, suono, messaggio, volume, goto, fine,
  webhook), timeout per passo e limite dell'avventura, stima della posizione dalle basette, log
  della partita; libreria degli elementi; servizio collegato al worker; pagine editor e gioco;
  comando `adventure` con simulazione da tastiera; tre avventure di esempio. Provato con eventi
  simulati, non ancora con Mario.
- **Strumenti dal vivo**: `tools/probe_port4.py`, `tools/catalog_tiles.py`, `tools/live_checks.py`
  (anche come `./run.sh live-checks` ecc.), `tools/99-mario-btusb.rules`.
- **Voce IA con OpenVoice V2 (14/10)**: `tools/setup_openvoice.sh` installa in `.venv-openvoice`
  torch per CPU, OpenVoice (convertitore V2 da Hugging Face) e piper con le voci italiane. piper legge
  la frase e OpenVoice le dà il timbro della registrazione di riferimento, in un processo separato
  che tiene i modelli caricati. Verificato sull'host con un riferimento sintetico: pronto in 8–10 s,
  prima frase udibile in 0,4–1,1 s, conversione a circa 0,7 s per secondo di voce, nessuna pausa tra
  le frasi grazie alla preparazione in anticipo; impronta della frase convertita simile al
  riferimento per 0,94 (0,51 con la voce di partenza). Voce di riserva e ripartenza dopo un crash
  provate con un processo finto. Lo stesso processo rende piper istantaneo (0,08–0,16 s a frase
  contro 2,8 s del comando). Non ancora ascoltata con una voce vera né in una sessione con Mario.
- **Suoni dei giochi (14/10)**: `./run.sh fetch-sounds --all` copia in locale l'archivio "Sounds &
  Music" di The Mushroom Kingdom (WAV, MP3, MIDI, moduli, NSF, SPC; `data/sounds/tmk/`, uso privato,
  fuori da git) con il catalogo. Emozioni, sessione e avventure suonano gli effetti veri o, nello
  stile "voce", le esclamazioni di Mario; tutte le 57 corrispondenze predefinite esistono nel
  catalogo. La pagina Suoni della dashboard permette di scegliere il file di ogni effetto, regolare
  il volume, ascoltare l'archivio e caricare suoni propri. Archivio completo il 14/10: 2.783 voci,
  170 MB (1.374 WAV, 904 MIDI, 114 moduli, 13 MP3, 10 NSF, 348 SPC), nessun errore dopo la correzione
  dei link con la barra rovesciata. Conversioni provate: SPC, NSF con traccia, moduli IT/XM/MOD (5 su
  112: gli altri sono RAR 2.0 e servono `unar`); i MIDI richiedono un sintetizzatore. Da ascoltare
  per volume e scelte.
- **Messaggio stile Mario (14/10)**: `./run.sh voice` compone esclamazioni originali (`{letsago}`,
  `{wahoo}`) e frasi di piper con l'effetto `mario` (rubberband: +6 semitoni, ×1,1) e salva il
  messaggio in WAV. Provato sull'host: frasi pronte in 0,2–0,35 s più 0,16–0,19 s di effetto, altezza
  misurata 207 Hz come previsto; messaggio di 8,5 s suonato dal PC. Lo stile c'è anche nelle
  impostazioni dell'assistente. Non ancora valutato a orecchio dall'utente.
- **Voce stile Mario nella sessione con il modello (14/10)**: `assistant --effect mario` e l'impostazione
  "Effetto: stile Mario"; il modello può far suonare le esclamazioni originali scrivendo `{nome}` (elenco
  delle disponibili nel prompt, massimo due per risposta, testo salvato senza graffe). Provato con
  `claude-sonnet-5-5`: risposta aperta da `{herewego}`, esclamazione suonata prima delle frasi, prima
  frase dopo 1,8 s, log pulito.
- **Test**: 155 senza Bluetooth né rete esterna (BleakClient finto per dispositivo e worker, server
  SSE locale per i backend, controlli statici del JavaScript), verdi nel venv dell'host; workflow
  GitHub Actions su Python 3.11 e 3.13.
- **Packaging**: `pyproject.toml` (`mario-ble` 0.2.0, entry point `mario`, extra `serve` per waitress).
- **Documentazione**: README (uso, protocollo, funzioni verificate, eventi decodificati, decode,
  avventure, problemi di connessione), HANDOFF (diario con orari), TODO (lavoro aperto), ASIS
  (questo file), `docs/sessioni/` (14 log delle prove, anonimizzati).
- **Git**: repository pubblicato su GitHub (`saulusprime/Mario-IA-Assistant`), documenti
  anonimizzati; esclusi `.venv/`, `/logs/`, `/data/`, `.env`, `third_party/` (cloni di riferimento).

## 5. Mario come I/O di un LLM: tappe fatte

- **Ingresso**: salto, caduta, rotazione (solo con Mario appoggiato), urto (spento), scossa = ripeti
  a riposo e stop mentre parla, pulsante = ascolto on/off, pantaloni con carattere, basette per
  famiglia (nemico, premio, start, traguardo, amico), terreno, batteria scarica, moneta (ipotesi,
  spenta); pause minime e filtro di stabilità per pantaloni e colore; regole modificabili dalla
  dashboard; valori combinati accettati come valori singoli.
- **Uscita indiretta**: voce dal PC con velocità e tono per emozione (piper se disponibile), effetti
  sonori, volume di Mario che segue l'emozione e torna alla base; il nome non viene toccato.
- **Memoria e sicurezza**: ricordi con il tag `[ricorda: …]` nel database, nel prompt delle sessioni
  successive, gestibili da pagina, API e CLI; minuti per sessione, parole bloccate, diario per i
  genitori con esportazione CSV.
- **Architettura**: sessione con streaming, frasi pronunciate una alla volta, loop eventi →
  prompt, intenti accumulati e uniti mentre Mario parla (scarto dopo 8 s), impostazioni, cronologia
  e ricordi nel database (anche dalla CLI), allow-list dei comandi verso Mario, tempo alla prima
  frase misurato a ogni risposta.
- **Prove fatte**: backend demo e voce (11/10), Claude `claude-sonnet-5-5` (risposte nel personaggio
  con i tag emozione in 2–4 s), sessione con Mario connesso da terminale (sensori veri → risposte
  coerenti), indicazione "occupato" (non supportata), scritture su porta 4 e 7 (inerti); latenza
  misurata il 13/10: prima frase in 0,7–1,6 s con `claude-haiku-5-5`, 1,1–1,5 s con sonnet (8,8 s a
  freddo una volta); backend HTTP provati contro un server SSE locale (errori, taglio, Stop).

## 6. Catalogo dell'utente

- **Mario**: firmware 6.5.01, hardware 0.4, LWP 3.0, associato al PC; pantaloni blu (0x21).
- **Set**: 71360 "Avventura di Mario" e 71425 "Corsa nella miniera di Diddy Kong".
- **8 basette**, tutte catalogate nel codice e nella pagina Accessori: tubo di partenza (0x00B8),
  Bowser Jr. nel Clown Koopa (0x00F5), Goomba ×2 (0x0002), aereo (0x0160), carrello minerario
  (0x015B), zattera di nuvole (0x002E), piattaforma rotante (0x0014).
- **Fonte esterna** consultata: wiki italiano di Super Mario, pagina LEGO Super Mario (set,
  regole: monete dai nemici, roulette del blocco ?, cuore con Yoshi/Toad, piattaforma rotante,
  asta della bandiera), leggibile tramite la sua API MediaWiki.
