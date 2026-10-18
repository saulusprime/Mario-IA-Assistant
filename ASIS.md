# ASIS — stato attuale: cosa è fatto e verificato

Fotografia del progetto LEGO Mario via Bluetooth LE al 18 ottobre 2026. Qui stanno le attività
**completate** (spostate da [TODO.md](TODO.md), che ora contiene solo il lavoro aperto); il
racconto di come ci si è arrivati è in [HANDOFF.md](HANDOFF.md), l'uso in [README.md](README.md).
Le prove dal vivo citate hanno i log in `docs/sessioni/`.

## 1. Verifiche dal vivo fatte (Mario, firmware 6.5.01; dal 18/10 6.5.03)

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
  `SWITCH_OFF` → `WILL_SWITCH_OFF` + `WILL_DISCONNECT`, scollegato in 6 s, Mario spento. (Nel log del 12/10
  i due avvisi comparivano due volte: era il difetto delle notifiche doppie dopo una riconnessione, corretto
  il 18/10; prima della riconnessione `WILL_DISCONNECT` era arrivato una volta sola.)
  `ACTIVATE_BUSY` / `RESET_BUSY` (0x05/0x06) → `INVALID_USE`: non supportate.
- **Batteria**: tensione raw 0..2705 = 0..3300 mV (porta 6) e percentuale come proprietà.
- **Standby**: 3 minuti di Mario connesso e fermo senza comandi: nessun messaggio LWP3 legato
  alle animazioni del display; il protocollo non vede ciò che fa il display.
- **Reverse engineering dal vivo (`./run.sh re-checks`, 14/10 18:57–19:03)**: la prova c'era stata ma i
  documenti non la registravano; il log è stato riletto il 18/10 (copia in `docs/sessioni/`).
  - `annuncio`: manufacturer data `00 43 03 ff ff 00` (tipo 0x43, capacità 0x03 central + peripheral).
  - `gatt`: quattro servizi; l'OAD ha tre caratteristiche, FFC1 Image Identify, FFC2 Image Block e FFC5
    Control, come nel profilo TI (solo elencate).
  - `sistema`: proprietà 0x0B = `0x40` contro `0x43` dell'advertisement, confermato.
  - `sonno`: confermati il conto alla rovescia `0x3861` (5→1, 8→1, 8→1) con `0x3862 = 2` dopo ogni giro,
    `0x3866 = 0` + `0x1802 = 2` dopo 32 s da fermo e `0x1802 = 1` + `0x3866 = 1` ripreso in mano.
  - `porta3`: letti per la prima volta CHAL e VERS; il modo DEBUG trasmette una traccia del firmware
    (sezione 2). Mario si è spento 12 s dopo l'attivazione di DEBUG: causa da capire.
  - `gioco`: livello di 60 s con Start, salti, Goomba, Clown Koopa, aereo e carrello; porte 4 e 7 mute.

## 2. Reverse engineering acquisito

- **Mappa delle funzioni** letta dal dispositivo (`mario/data/capabilities-fw-6.5.01.json`): porte 0
  (0x47), 1 (0x49), 2 (0x4A), 3 (0x46), 4 (0x55), 6 (0x14), 7 (0x60); la porta 7 "Telemetry"
  (16 byte, ingresso e uscita) è nuova dal firmware 6.x e non documentata altrove.
- **Eventi di gioco (porta 3 modo 2)**, `[codice LE16][valore LE16]`, ipotesi solide dalle sessioni
  con le basette reali e dalla rilettura dei log con `decode` (13/10): `0x0113` basetta letta
  (valore = barcode); `0x386B` totale monete del livello (0 alla lettura dello Start); `0x20C2`
  monete date dall'ultima basetta (6 o 8 con l'aereo, come una roulette); `0x2042` nemici sconfitti
  (riparte da 1 anche fuori dal livello); `0x3712 = 1` circa 1 s dopo ogni nemico; `0x2022` giri
  sulla piattaforma rotante; macchina a stati del livello `0x1801` (1 allo Start, 0 dopo la fine) e
  `0x1803` (1 allo Start, 4 negli ultimi 15 s, 3 a fine livello, 0 fuori); `0x3812` 2 a fine livello, 1
  fuori; sequenza di fine livello `0x3004=601, 0x3812=2, 0x3852=0, 0x1803=3, 0x386E=0, 0x3803=100`;
  `0x3861` conto alla rovescia a un passo al secondo (5→1, poi 8→1) con Mario fermo fuori dal livello,
  chiuso da `0x3866=0`/`0x1802=2` (sonno?), con `0x3866=1`/`0x1802=1` al risveglio (confermato dal vivo
  il 14/10); `0x3862` 0 a ogni movimento o basetta, 2 dopo ogni conto alla rovescia, 4 alla
  connessione; `0x0115` 0 con i pantaloni sfilati e 1 rimessi (un solo caso, 14/10). 23 codici in
  `codes.GAME_EVENTS`.
- **Durata del livello** (rilettura del 18/10 di tutti i log): in quattro livelli su quattro la fine
  (`0x1803 = 3` con `0x3004 = 601`) arriva esattamente 15 s dopo `0x1803 = 4` (12/10 17:33:14, 18:10:14,
  18:26:23; 14/10 19:01:50). I due livelli con lo Start registrato (12/10 18:09:14, 14/10 19:00:51) sono
  durati 60 s, con il 4 a 45 s; rileggere lo Start durante il livello non lo fa ripartire. Tutti finiti a
  tempo: `0x3004 = 601` potrebbe essere la durata in decimi (60,1 s), da verificare al traguardo.
- **Porta 3, modi CHAL, VERS e DEBUG** (14/10): CHAL vale `c0 00 00 00`; VERS `e2 00 01 b5` tre volte e
  `32 00 32 00`. DEBUG trasmette un flusso continuo a pezzi di 16 byte, cioè una traccia del firmware:
  record e testo attraversano i confini dei pacchetti. In 12 s, 1.264 byte. Contiene
  il testo `LEAF 0x65010000 May 27 2025 08:44:31` (0x65010000 è la versione 6.5.01; LEAF forse il nome
  in codice del firmware) e `Game identifier: LEAFGAMEFILE`, poi record periodici `0a 02 TT TT ff ff ff ff
  VV 01 00` (57, con `TT` che cresce di circa 200 a ogni record e `VV` tra 94 e 96) mescolati ad altri
  (`01 06 …`, `01 07 …`, `05 09 …`). `decode --summary` ricompone il flusso e ne estrae il testo.
- **Protocollo dell'app ufficiale (cattura HCI snoop del 18/10, `docs/sessioni/app-ufficiale-20261018.txt`)**:
  appena collegata, l'app attiva le notifiche delle porte 3 (modi 1 e 2), 4 e 7 e poi:
  - **porta 3**: scrive `01 f0` nel modo 2 (WriteDirectModeData; la porta dichiara solo ingressi ma accetta) e
    Mario risponde con un'istantanea di contatori come eventi della porta 3 (`0x4001`, `0x4002`, `0x3001`,
    `0x1901`, `0x1902` = 168, `0x1910` = 7, `0x1911` = 68, `0x0180`); alla seconda richiesta, dopo il comando
    0x0A della porta 4, gli stessi codici valgono 0;
  - **porta 4 = canale richiesta/risposta** `[comando LE16][valore LE16]`, risposte sulla porta 4 stessa:
    0x05 con `0x00FE` → risposta 0x01 con `0x00FE`; 0x09 → risposta 0x0B con `0xFFFF`; 0x0A con un valore
    scelto dall'app (`0xCAEC`) → risposta 0x0B con lo stesso valore (segno di sincronizzazione? ipotesi);
  - **porta 7 = archivio delle partite**: scrivendo un solo byte `01` Mario manda un record da 16 byte per
    notifica: partita (numero progressivo, 402–409 nella cattura; quanti record la compongono; due valori
    grezzi simili alla tensione della porta 6; pantaloni), basette lette con quante volte (quelle vere
    dell'utente), contatori `numero → valore` di due tipi (significato ignoto), record finale con i totali
    (61 record, 8 partite, coerenti). Poi l'app scrive `02` (probabile cancellazione dell'archivio).
    Le scritture del 12/10 non funzionavano perché mandavano 16 byte invece di uno;
  - durante il gioco l'app ha mandato solo il volume: nessun comando che faccia suonare o animare Mario.
  `decode` e il monitor descrivono queste scritture e risposte; `decode --summary` raggruppa l'archivio per
  partita e controlla i totali (`mario/history.py`).
- **Porta 4 (`Events`)**, prove a scatola nera del 12/10: con le notifiche attive non ha trasmesso nemmeno per tutto il livello del 14/10. Scritture (39 prove, fuori e dentro un livello):
  solo WriteDirectModeData (0x51) è accettato, con feedback "completato", ma il valore non viene
  conservato (rilettura `00 00 00 00`), nessun eco, contatore monete fermo, nessuna reazione.
  WriteDirect (0x50) → `INVALID_USE`; sottocomando 0x00 → `COMMAND_NOT_RECOGNIZED`.
- **Sottocomandi dei motori** (scansione del 18/10, firmware 6.5.03, `probe_port4.py --scan`, 36 prove):
  StartPower 0x01 e 0x02, SetAccTime 0x05, SetDecTime 0x06, StartSpeed 0x07 e 0x08, ciascuno senza
  payload, con un byte zero e con la forma dello standard, su porta 4 e porta 7 → sempre
  `COMMAND_NOT_RECOGNIZED`, nessun valore spontaneo delle porte 3, 4 e 7, nessuna reazione visibile. A differenza di WriteDirectModeData,
  la porta 7 risponde con un errore: le scritture 0x51 le ignora, i sottocomandi sconosciuti li rifiuta.
- **Porta 7 (`Telemetry`)**, prove a scatola nera del 12/10 (prima di conoscere la richiesta `01`): non ha trasmesso nemmeno nel livello del 14/10; 70 scritture (zeri, 255, un byte alla volta,
  rampe, ripetizioni) ignorate in silenzio: nessun feedback, nessun errore, rilettura zero. Un
  sospetto di reazione del display si è rivelato un falso positivo.
- **Conclusione sulla voce diretta**: con i comandi standard LWP3 non esiste una via per far
  suonare o animare Mario, e nemmeno l'app ufficiale ne usa una durante il gioco (cattura del 18/10); le
  piste rimaste sono nel TODO (carta stampata, e-ink, luce colorata, collegamento Mario–Luigi).
- **Ricognizione del firmware (18/10, sola lettura, `./run.sh firmware-recon`)**: dall'app ufficiale per
  Android (versione 2.11.8, copiata dal telefono dell'utente) risulta che "LEAF" è il nome in codice del
  prodotto (il firmware si chiama `LEAF_FW_…`, come la riga della traccia DEBUG) e che le definizioni delle
  porte di Mario salvate dall'app sono identiche fra 6.5.01 e 6.5.03. L'APK non contiene né il firmware né i
  pacchetti di suoni e animazioni: arrivano da Google Play come pacchetti separati, nella memoria privata
  dell'app, non leggibile senza root. Ricognizione chiusa al criterio di stop concordato; il log HCI snoop del
  telefono era spento durante l'aggiornamento alla 6.5.03.
- **Basette virtuali su schermo**: lo scanner non legge i barcode mostrati su un LCD (luce emessa,
  polarizzatori, probabile lettura nel vicino infrarosso). Pista chiusa per gli schermi emissivi.
- **System type**: la proprietà 0x0B risponde `0x40` mentre l'advertisement dichiara `0x43` (riletti
  entrambi il 14/10).
- **Servizi GATT**: quattro (GATT, GAP, LEGO 1623/1624, OAD Texas Instruments `f000ffc0-…`),
  nessuna caratteristica nascosta. `info` elenca la tabella GATT con i nomi dei servizi noti e l'avviso
  sull'OAD; letta dal vivo il 14/10: OAD con FFC1 Image Identify, FFC2 Image Block e FFC5 Control.

## 3. Sistema e Bluetooth del PC (configurazione in essere)

- Claude Code gira nel Flatpak di VS Code: BlueZ non è raggiungibile dal sandbox, `run.sh` salta
  sull'host con `flatpak-spawn`; venv dell'host in `.venv` (Python 3.13, bleak 3.0.2, dbus-fast 5.2,
  Flask 3.1, waitress 3.0).
- MacBookPro12,1 con Bluetooth e Wi-Fi Broadcom sullo stesso modulo: la connessione regge con il
  Wi-Fi sulla rete a 5 GHz (`doctor` avvisa se torna a 2,4 GHz; in alternativa si può usare una chiavetta
  Bluetooth USB con `./run.sh -i hci1 …`); autosospensione USB disattivata (`/sys/bus/usb/devices/1-3/power/control`
  = `on`). Confermato al primo riavvio, il 18/10 (kernel 6.12.113): `doctor` dice "disattivata (opzione
  del modulo btusb)". Il 15/10 si era verificato in anticipo che dopo il riavvio sarebbe rimasta
  disattivata (initramfs con `btusb.conf` e senza il modulo, nessuna regola di systemd, powertop e TLP
  assenti).
- Firmware: non serve. Per i controller Apple (05ac, interfaccia ff/01/01) il kernel usa
  `btbcm_setup_apple`, che non carica nessun `.hcd`; la patch la cerca solo `btbcm_setup_patchram`, usata per
  gli altri Broadcom (sorgenti del kernel 6.12, 15/10). Un file in `/lib/firmware/brcm/` non verrebbe letto, e
  caricare una patch a mano (comandi HCI del vendor a ogni avvio) richiederebbe un file che non esiste in
  pubblico. La ricerca del file dell'11 e del 13/10 partiva da una premessa sbagliata.
- **`./run.sh doctor`** (15/10) controlla senza sudo adattatore, rfkill, servizio, controller,
  autosospensione (subito e ai prossimi avvii), banda del Wi-Fi e hub LEGO associati, con il comando
  per ogni problema; lo stesso nella pagina Scansione e in `/api/system/checks`. Provato sull'host:
  tutto a posto, 124 ms.
- Agente di pairing ristretto al Mario in collegamento (15/10): prima approvava qualsiasi richiesta
  mentre l'app era aperta, e con il pannello Bluetooth di GNOME aperto un dispositivo vicino qualsiasi si
  sarebbe accoppiato senza conferma. Alla chiusura, anche brusca, BlueZ ripristina l'agente di GNOME (coda
  degli agenti `default_agents` in `src/agent.c`, sorgenti di BlueZ 5.82).
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
  `mario/data/mario_codes.json` con fonte e data di verifica (dal 18/10 anche le strisce colorate dei
  barcode, i 73 codici che pyLegoMario conosce senza nome, tenuti a parte perché non diventino basette
  "nemico", e la nota sulla discordanza `0x0A`/`0x0C` dei pantaloni fra legomario.py e le altre fonti);
  `MarioDevice` con scansione,
  connessione con tentativi, agente di pairing, richieste con attesa, scoperta, modalità combinata
  con delta per modo, report (con stato del bond) e JSON.
- **CLI** `./run.sh`: `scan`, `info` (salva `mario/data/capabilities-fw-<versione>.json`), `monitor`
  (`--combined`, `--json`, `--duration`, `--no-battery`, riconnessione automatica), `shell`
  (readline, `help <comando>`, `log`, riconnessione; dal 18/10 Tab completa anche nomi delle proprietà,
  porte annunciate, modi della porta e on/off, e c'è `history` con `!!` e `!N`), `decode` (catture btsnoop,
  bugreport Android con sezioni btsnooz, PacketLogger di Apple, gzip, log, byte; i formati dei telefoni
  provati su file sintetici), `assistant`, `adventure`, `bridge`, `web` (waitress, token, HTTPS), comandi
  rapidi, strumenti di `tools/` come sottocomandi; log di sessione in `logs/`.
- **`decode` sulle catture vere (18/10)**: le tre catture `btmon -w` dell'11/10 (tentativi di connessione
  falliti, nessun messaggio LWP3) hanno mostrato due difetti, corretti: una scrittura sul descrittore CCCD
  (`02 00`, la richiesta che faceva scattare il pairing) diventava un messaggio di Mario, e le risposte di
  lettura ATT (per esempio il nome del dispositivo) sarebbero state lette come LWP3. Ora la maniglia della
  caratteristica LEGO si impara dalla scoperta GATT nella cattura (`0x0012`, come in btmon) e, se la scoperta
  non c'è, passano solo dati con la forma di un messaggio LWP3. `--summary` ricostruisce anche i **livelli**
  (fasi, basette lette con le monete date, monete e nemici), pure quando l'inizio non è nel log: provato sui
  log di `docs/sessioni/`, per esempio il livello del 12/10 18:25 con otto basette, 8 monete e 2 nemici.
- **Ponte verso altri programmi (18/10)**: `./run.sh bridge` traduce gli eventi in fatti con un nome (gesto,
  basetta, colore, pantaloni, fase del livello, monete, pulsante, batteria, avventura) e li pubblica su MQTT
  3.1.1 e/o su un WebSocket, con la sola libreria standard; legge `monitor --json` o il flusso della
  dashboard, che tiene la connessione con Mario. Schema delle righe e dei fatti in `docs/monitor-json.md`.
  Provato con un broker finto, un client WebSocket e il flusso vero della dashboard; nessun broker reale.
- **Dashboard Flask** `mario/web/`: Panoramica, Scansione, Dispositivi (nome e volume dalla scheda),
  Monitor (SSE, dal 18/10 anche "Standard, modi combinati"), **Eventi** (filtri, esportazione CSV/JSON,
  grafici), Console, Accessori (con colonna "verificato"), **Tabelle dei codici**, Assistente,
  **Avventure** (editor, schermo di gioco, partite), Funzioni, Documentazione; database SQLite
  (`devices`, `accessories`, `events`, `settings`, `assistant_messages`, `assistant_memory`,
  `adventures`, `adventure_runs`, `web_sessions`); accesso con token oltre 127.0.0.1. Novità del 18/10:
  - **Eventi**: trascinando sul grafico della batteria si sceglie l'intervallo, e tabella, grafici ed
    esportazioni lo seguono (prima "Dal/Al" valeva solo per l'esportazione); gli eventi in modalità
    combinata conservano i valori per modo (colonna `modes`), con una colonna per modo nel CSV.
  - **Tabelle dei codici** (`/codes`, `/api/codes`): tutte le tabelle con fonte, verifica, ipotesi e note,
    anche gesti ed eventi di gioco; strisce colorate dei barcode secondo pyLegoMario.
  - **Accesso**: dopo il token il browser riceve una sessione (identificativo casuale nel cookie, impronta,
    browser e scadenza nel database, `web --session-days`, default 30), elencata nella Panoramica con la
    revoca; cambiare il token chiude tutte le sessioni; "Esci" nella barra; `/login` non rimanda verso altri
    siti. HTTPS con `--cert/--key` o `--self-signed` (openssl, impronta SHA-256 stampata all'avvio).
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
  comando `adventure` con simulazione da tastiera. Provato con eventi simulati, non ancora con Mario.
  Aggiunte del 18/10, anche queste solo simulate:
  - trigger `level` (inizio, ultimi 15 secondi, fine, uscita: l'evento `0x1803`), `any_of` ("A oppure B",
    anche "oppure dopo N secondi"), ripetizioni con `window` (tutte entro N secondi) e trigger `fail` del
    passo (se succede, il passo è perso come per il timeout);
  - azione `music`: un brano dell'archivio dei suoni in ciclo a volume basso (30 %), fermato a fine partita;
  - modalità "registra": si cammina con Mario sulle basette e il percorso diventa una bozza nell'editor,
    un passo per basetta, con il timeout suggerito dal tempo impiegato;
  - pagina della partita (`/adventures/runs/<id>`) con la cronaca e il replay a 1–10×; le basette lette
    entrano nella cronaca; schermo di gioco a tutto schermo con il percorso disegnato con le icone;
  - quarto esempio, "Sfida a tempo nel livello", che arriva anche nei database già in uso (gli esempi
    cancellati non tornano).
- **Strumenti dal vivo**: `tools/probe_port4.py`, `tools/catalog_tiles.py`, `tools/live_checks.py`
  (anche come `./run.sh live-checks` ecc.), `tools/99-mario-btusb.rules`.
- **Thread della voce IA (misura del 18/10, PC scarico)**: torch usa già 2 thread (i core fisici), e
  con 2 thread una frase richiede in media 1,7 s; con 4 thread 1,85 s, con uno solo 2,3 s. Il valore
  automatico resta. Riferimento sintetico (piper), script in `logs/ov-bench/` (locale).
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
  dei link con la barra rovesciata. Dal 15/10 si ascolta tutto: i 112 moduli `.mdr` (RAR 2.0) vengono
  estratti da `mario/rar20.py`, in Python e controllati con il CRC32; i MIDI vengono suonati da fluidsynth,
  fermato dopo i secondi richiesti e portato al volume delle altre musiche. Provati tutti i 904 MIDI e i
  112 moduli: nessun errore. Da ascoltare per volume e scelte.
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
- **Evoluzioni dell'assistente (16/10)**, provate senza Mario e senza microfono. Tutte le prove con
  Claude erano vere.
  - Modello rapido per i sensori (`sensor_model` "auto" = haiku con Claude): prima frase 1,2–1,5 s contro
    1,2–2,1 s di sonnet.
  - Ragionamento di Claude 5 spento: le risposte non sono più vuote e sonnet scrive la prima parola in
    1,0 s invece di 6–11 s.
  - Storia interattiva con `[aspetta: salto | giro | pantaloni X | basetta X | terreno X | scossa | pulsante]`:
    sfida riuscita, sbagliata o scaduta; provata con Claude e sensori simulati.
  - Ingresso vocale offline (faster-whisper, `./tools/setup_stt.sh`) con il pulsante di Mario come
    push-to-talk, il pulsante della pagina e `/parla`: small circa 3 s per frase, 4,2 s dal rilascio alla
    prima frase.
  - Temi da evitare nelle domande e nelle risposte, limite di messaggi, riepilogo del giorno con
    riassunto scritto dal modello su richiesta.
  - Ricordi per bambino, ricordi comuni, scadenza per quelli del modello, riassunto automatico oltre i 20.
- **Attivazione con «Ehi Mario» (18/10)**, provata senza microfono. Vosk (modello italiano piccolo, offline,
  circa il 3–4 % di un core) ascolta con una grammatica ristretta e registra la frase dall'inizio; Whisper la
  trascrive e la tiene solo se contiene la chiamata, altrimenti la scarta senza conservarne il testo. Ascolto in
  pausa mentre Mario parla. Su un flusso di 195 s con 64 frasi sintetiche (24 chiamate, 40 frasi scelte per
  ingannare, voci adulte e da bambino): 22 chiamate su 24, 1 frase su 40 accettata per sbaglio. "Ehi Mario!"
  da solo: Mario risponde "Eccomi!" e ascolta la frase dopo. Spenta di default (casella nella pagina
  Assistente, `assistant --wake`); `./run.sh listen --wake` per provarla.
  - Segnale "ti ascolto" appena Vosk sente la chiamata (anche per quelle poi scartate).
  - Catena intera provata in una sessione (processo di ascolto su un file a velocità reale, Whisper, modello
    demo): chiamata con domanda, "Ehi Mario!" con "Eccomi!" e ascolto successivo, stato senza testo.
  - Provato e scartato: Vosk con il vocabolario libero al posto di Whisper sbaglia troppo ("raccontano una
    storia", "poi magari facciamo un salto"); senza "Mario" nel suggerimento di vocabolario Whisper perde 4
    chiamate su 24.
- **Regole della partita (18/10)**: `tempo` (ultimi 15 secondi del livello, priorità 2) e `fine_livello`
  dagli eventi della porta 3, accese; la regola delle monete, spenta, legge il totale vero (`0x386B`) invece del
  conto alla rovescia (`0x3861`).
- **Notifiche doppie dopo una riconnessione (corretto il 18/10)**: quando era Mario a chiudere il link
  (spegnimento, sonno, azione DISCONNECT), il vecchio client di bleak restava con la sua connessione D-Bus
  aperta e BlueZ ne riattivava la sessione di notifica alla riconnessione, quindi ogni messaggio arrivava
  due volte (`monitor` e `shell` dopo la riconnessione, dashboard dopo "Connetti", `live-checks` e
  `re-checks`). Ora `MarioDevice` chiude sempre il client; lo provano un BleakClient finto che imita
  BlueZ e il worker della dashboard. La conferma con Mario è nel TODO.
- **Test**: 256 senza Bluetooth né rete esterna (BleakClient finto per dispositivo e worker, server
  SSE locale per i backend, broker MQTT finto, controlli statici del JavaScript), verdi nel venv
  dell'host; workflow GitHub Actions su Python 3.11 e 3.13. Dal 18/10 le pagine della dashboard si provano
  anche in un **Firefox headless vero** (`tests/test_browser.py`): client WebDriver BiDi con la sola libreria
  standard (`tests/browser.py`), errori JavaScript di ogni pagina, editor e registrazione di un percorso,
  partita simulata e replay, filtri delle tabelle, zoom con un trascinamento vero del mouse; saltati se
  Firefox non c'è. La CI era rossa dal 16/10 per un test che cercava la chiave API del `.env` locale: corretto,
  e la suite è stata riprovata da un clone pulito con Python 3.11 e quasi nessun programma di sistema.
- **Packaging**: `pyproject.toml` (`mario-ble` 0.2.0, entry point `mario`, extra `serve` per waitress).
  Dal 18/10 si installa anche come pacchetto normale (`pip install .`, provato in un venv pulito): esempi
  delle avventure e mappa delle funzioni (spostata in `mario/data/`) sono nel pacchetto; database, log e
  modelli vanno in `~/.local/share/mario-ble` invece che dentro site-packages (`mario/paths.py`,
  `MARIO_HOME` per un'altra cartella); dal sorgente tutto resta nella cartella del progetto.
- **Documentazione**: README (uso, protocollo, funzioni verificate, eventi decodificati, decode,
  avventure, problemi di connessione), HANDOFF (diario con orari), TODO (lavoro aperto), ASIS
  (questo file), `docs/sessioni/` (15 log delle prove, anonimizzati).
- **Git**: repository pubblicato su GitHub (`saulusprime/Mario-IA-Assistant`), documenti
  anonimizzati; esclusi `.venv/`, `/logs/`, `/data/`, `.env`, `third_party/` (cloni di riferimento).

## 5. Mario come I/O di un LLM: tappe fatte

- **Ingresso**: salto, caduta, rotazione (solo con Mario appoggiato), urto (spento), scossa = ripeti
  a riposo e stop mentre parla, pulsante = ascolto on/off, pantaloni con carattere, basette per
  famiglia (nemico, premio, start, traguardo, amico), terreno, batteria scarica, monete del livello (spenta),
  ultimi 15 secondi e fine del livello; a voce col pulsante o con «Ehi Mario» (18/10); pause minime e filtro di stabilità per pantaloni e colore; regole modificabili dalla
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

- **Mario**: firmware 6.5.03 (aggiornato dall'utente con l'app ufficiale dopo il 14/10; prima 6.5.01), hardware 0.4, LWP 3.0, associato al PC; pantaloni blu (0x21).
- **Set**: 71360 "Avventura di Mario" e 71425 "Corsa nella miniera di Diddy Kong".
- **8 basette**, tutte catalogate nel codice e nella pagina Accessori: tubo di partenza (0x00B8),
  Bowser Jr. nel Clown Koopa (0x00F5), Goomba ×2 (0x0002), aereo (0x0160), carrello minerario
  (0x015B), zattera di nuvole (0x002E), piattaforma rotante (0x0014).
- **Fonte esterna** consultata: wiki italiano di Super Mario, pagina LEGO Super Mario (set,
  regole: monete dai nemici, roulette del blocco ?, cuore con Yoshi/Toad, piattaforma rotante,
  asta della bandiera), leggibile tramite la sua API MediaWiki.
