# HANDOFF — LEGO Mario via Bluetooth LE

Diario dettagliato del lavoro svolto dall'11 al 15 ottobre 2026 (prima sessione l'11/10 dalle
12:40 alle 19:00, prove dal vivo il 12/10, documentazione e sviluppo senza Mario il 13/10, voce IA
con OpenVoice il 14/10, moduli e MIDI dell'archivio dei suoni e sistema Bluetooth del PC il 15/10)
e stato
del progetto per chi lo riprende. Il README spiega *come si usa*; questo file spiega *cosa è stato
fatto, perché, cosa ha funzionato e cosa no*.

**Stato al 15/10 sera, in breve.** Connessione e pairing stabili; sensori decodificati (gesti,
scanner, pantaloni con filtro di stabilità, eventi di gioco con contatori); dashboard con pagine
Monitor, Eventi, Console, Accessori, Assistente, Avventure; assistente con Claude provato con Mario
connesso (11/10), con ricordi, sicurezza per i bambini e diario; designer di avventure in prima
versione (solo simulato); strumento `decode` per log, catture e bugreport dei telefoni; voce IA
con OpenVoice (piper + timbro di una registrazione, 14/10); effetti, voci e musiche dei giochi in locale con la pagina Suoni (uso privato; tutti i formati riproducibili dal 15/10); messaggi
"stile Mario" (esclamazioni originali + piper più acuto), anche nella sessione con il modello; `./run.sh doctor` per il PC e agente di pairing ristretto a Mario; 166 test offline; repository pubblico su GitHub allineato. Le porte 4 e 7 restano inerti: nessuna via per far
suonare Mario via Bluetooth con i comandi standard. Da fare con Mario acceso: sezione 1 del TODO
(compreso `./run.sh re-checks`, che esegue dal vivo le prove di reverse engineering della sezione 2).

## 1. Obiettivo

Creare un'applicazione Python 3 che comunichi con il LEGO Mario (set 71360) dal PC via
Bluetooth LE, scopra tutte le funzioni esposte dal dispositivo e permetta la comunicazione
bidirezionale, sia decodificata sia a byte grezzi.

Risultato: obiettivo raggiunto. Scoperta completa eseguita dal vivo (7 porte, 13
modalità), lettura e scrittura delle proprietà verificate (volume, nome), sensori letti in
tempo reale per 3 minuti e 40 secondi, shell interattiva funzionante.

## 2. Ambiente (fatti verificati)

| Elemento | Valore |
|---|---|
| PC | MacBookPro12,1 (13", inizio 2015) |
| Sistema | Debian 13, kernel 6.12.111, BlueZ 5.82, Python 3.13.5 sull'host |
| Bluetooth | Broadcom BCM20703A1 integrato, USB `05ac:8290`, driver `btusb`, HCI 4.2 |
| Wi-Fi | Broadcom BCM43602 (`brcmfmac`), stesso modulo e stessa antenna del Bluetooth |
| Firmware BT | nessun file `.hcd`, e non serve: per i controller Apple `btusb` usa `btbcm_setup_apple`, che non carica patch (verificato il 15/10 sui sorgenti del kernel 6.12) |
| Ambiente di lavoro | VS Code da Flatpak: nel sandbox non esistono `bluetoothctl` né `org.bluez` sul bus di sistema |
| Utente | utente normale nei gruppi `bluetooth` e `sudo` (password richiesta, niente sudo automatico) |
| Mario | nome iniziale `Mario 5wzZ` (rinominato durante i test con un nome di famiglia, qui `MarioCasa`), firmware 6.5.01, hardware 0.4, LWP 3.0 |
| Indirizzo di Mario | random privato risolvibile, cambia a ogni accensione (visti 7C:5B:9C…, 61:15:65…, 6C:4E:A9…, 42:6B:D2…, 58:65:93…, 70:52:52…, F4:60:77:5A:20:11 = indirizzo identità dopo il bonding) |

Conseguenze pratiche:

- Tutto ciò che tocca il Bluetooth va eseguito sull'host con `flatpak-spawn --host`.
  `run.sh` lo fa da solo; il venv `.venv` è stato creato con il Python dell'host.
- Lo scratchpad del sandbox (`/tmp/claude-…`) non è visibile dall'host: i file di lavoro
  stanno nel progetto (`logs/`, `docs/`).
- Le chiamate Bash parallele condividono la stessa shell: un `cd` in una chiamata cambia
  la directory anche all'altra. Usare sempre percorsi assoluti.

## 3. Cronologia

### 12:40–12:50 — Ricerca del dispositivo

- `bluetoothctl` assente nel sandbox; raggiunto sull'host via `flatpak-spawn`.
- Prime scansioni: solo dispositivi Samsung (manufacturer 0x0075) e Google Fast Pair.
- Mario compare solo quando si preme il tasto Bluetooth sulla schiena (icona lampeggiante,
  circa 30 s): `Mario 5wzZ`, manufacturer `0x0397` LEGO con dati `00 43 03 ff ff 00`
  (0x43 = system type Mario), servizio `00001623-1212-efde-1623-785feabcd123`, RSSI −38…−61.

### 12:50–13:00 — Creazione del progetto

- Verificato: dal sandbox `org.bluez` non è raggiungibile (`ServiceUnknown`); sull'host
  Python 3.13.5 senza bleak; creato `.venv` sull'host e installato bleak 3.0.2 (API
  controllata per firme: `services` è una proprietà, `write_gatt_char(..., response=)`,
  `BleakScanner(detection_callback, service_uuids, ...)`).
- Ricerca fonti: documentazione ufficiale LWP3, bricklife/LEGO-Mario-Reveng (porte, modalità,
  pantaloni, colori), djipko/legomario.py (gesti, barcode, volume `06 00 01 12 01 <v>`),
  node-poweredup (IO type). Dettagli in README, sezione Fonti.
- Scritti: `mario/lwp3.py` (codec LWP3 completo, senza dipendenze), `mario/codes.py`
  (tabelle Mario), `mario/device.py` (bleak, stato hub, richieste con attesa risposta,
  scoperta automatica), `mario/cli.py` (scan, info, monitor, shell, comandi rapidi),
  `tests/test_lwp3.py`, `run.sh`, `README.md`.
- Test offline verdi sia nel sandbox sia nel venv dell'host. `mario/__init__.py` importa
  `device` in modo pigro così i test girano anche senza bleak.

### 13:00–13:50 — Prima serie di fallimenti di connessione

1. **12:53** Mario trovato, connessione fallita: primo tentativo con errore vuoto, poi
   `device … not found` perché BlueZ aveva scartato il record. Correzione: ri-scansione a
   ogni tentativo invece di riusare un `BLEDevice` stale.
2. **12:55** con debug: `le-connection-abort-by-local` ripetuto (retry interni di bleak),
   poi `failed to discover services, device disconnected`.
3. Prova con il solo `bluetoothctl connect`: stesso errore → problema di sistema, non
   dell'app.
4. Diagnosi dell'adattatore: Broadcom Apple, `power/control = auto`, `runtime_status =
   suspended` (autosospensione USB a 2 s), nessun firmware `.hcd`; `bluez-firmware` Debian
   non contiene il file per questo chip (verificato sul filelist); nemmeno il repository
   winterheart/broadcom-bt-firmware.
5. **L'utente ha disattivato l'autosospensione** (`echo on > …/power/control` e
   `options btusb enable_autosuspend=0` in `/etc/modprobe.d/btusb.conf`).
6. **13:35** nuovo errore, immediato: `br-connection-canceled` (BlueZ sceglieva il bearer
   BR/EDR). Il record era già scaduto quando l'ho ispezionato; dal sorgente di BlueZ 5.82
   un indirizzo random va sempre via LE. Aggiunto all'app: stampa del tipo di indirizzo
   visto da BlueZ e, su errore `br-connection`, `Adapter1.RemoveDevice` + nuovo tentativo.
   Non si è più ripresentato.
7. **13:48** con indirizzo `random`: link stabilito, poi caduto durante la scoperta GATT.

### 14:00–14:10 — Catture btmon (script `tools/capture_btmon.sh`, serve sudo)

- **Cattura 1 (14:02)**: `LE Connection Complete` (intervallo 48,75 ms, supervision
  420 ms), MTU scambiato, primi servizi letti, ma conferme ACL a 194/335/378 ms e dopo
  2,3 s `Disconnect Complete, Reason: Connection Timeout (0x08)` (supervision timeout).
  Subito dopo la connessione BlueZ aveva ripreso la scansione LE al 100% del tempo perché
  il mio scanner era ancora attivo. Correzione: fermare lo scanner prima di connettersi.
- **Cattura 2 (14:08)**, scanner fermo: sette volte `Connection Failed to be Established
  (0x3E)` dopo circa 300 ms (6 intervalli), con `LE Create Connection` identico alla
  cattura 1. Quindi condizioni radio: Wi-Fi a 2,4 GHz (canale 11, 95% di segnale) sulla
  stessa antenna, con il firmware ROM che gestisce male la coesistenza.
- **L'utente ha spostato il Wi-Fi sulla rete a 5 GHz la rete a 5 GHz di casa.** Da lì in poi la
  connessione riesce in circa 2 secondi.

### 14:40–15:00 — Il blocco silenzioso e la causa vera

- **14:44** `info --json` restava appeso per 8 minuti dopo "mi connetto": nessuna
  operazione aveva timeout. Aggiunti timeout a connessione (45 s), attivazione notifiche
  (15 s, poi ripiego `AcquireNotify` 10 s), scritture GATT (5 s), disconnessione (10 s);
  messaggi di avanzamento spostati su stderr (prima sparivano con `>` nel file).
- **14:55** con i timeout: "collegato ma non conferma l'attivazione delle notifiche".
- **Cattura 3 (14:59)**: scoperta GATT completa (servizi GATT, GAP, LEGO con CCCD
  all'handle 0x0013, servizio OAD Texas Instruments `f000ffc0-…`: il chip BLE di Mario è
  TI). Poi BlueZ scrive il CCCD di *Service Changed* (handle 0x0004) e Mario risponde
  `Insufficient Authentication (0x05)`. BlueZ avvia da solo un pairing SMP con IO
  `DisplayYesNo` e MITM; Mario risponde IO `NoInputNoOutput`, bonding, Secure Connections;
  la conferma va all'agente di sistema (GNOME) che la nega: `User Confirmation Negative
  Reply` → `Pairing Failed: Numeric comparison failed` → Mario chiude. Il nostro
  `StartNotify` restava in coda dietro al pairing. Nota utile: a 7,7 s Mario chiede lui
  i parametri di connessione (intervallo 30 ms, supervision 3 s).

### 15:00–15:15 — Rimedio: agente di pairing nell'app

- Implementato `BlueZAgent` in `device.py`: oggetto `org.bluez.Agent1` esportato con
  dbus-fast (già dipendenza di bleak), capacità `NoInputNoOutput`, accetta ogni
  richiesta; registrato e impostato come agente predefinito per la durata della sessione,
  rimosso alla disconnessione.
- Prima della connessione, se Mario non risulta `Bonded`/`Paired`, l'app chiama
  `Device1.Pair()` (Just Works con bonding); il bond viene salvato da BlueZ.
- Bug trovato subito: in `device.py` il nome `Message` è la classe LWP3, che nascondeva
  `dbus_fast.Message`; ora il tipo D-Bus è importato come `DBusMessage`.
- Aggiunti i comandi `pair`, `unpair` e l'opzione `--no-pair`; opzione `-i/--adapter`
  per usare un'eventuale chiavetta USB (`hci1`).

### 17:00 — Successo

- `./run.sh info --json` completa la scoperta. Mario risulta bonded in BlueZ con
  indirizzo identità `F4:60:77:5A:20:11`. Il JSON è in `docs/capabilities-fw-6.5.01.json`.
- Risultati rispetto alle note della community: firmware 6.5.01; porta 4 (0x55) ha **un
  solo** modo `Events` (2 × int16) in ingresso e uscita; esiste una **porta 7, IO type
  0x60, modo `Telemetry` (16 × int8), ingresso e uscita**, mai documentata; tensione
  batteria raw 0…2705 = 0…3300 mV; la proprietà "system type" risponde `0x40` mentre
  l'advertisement dice `0x43`.

### 17:06–17:12 — Sessioni dal vivo dell'utente

- `monitor` (log `logs/monitor-20261011-170650.txt`, 2003 righe): gesti decodificati
  (rotazione, rotazione+inversione, salto, scossa, urto), con bit non documentati
  `0x0040` (quasi sempre insieme all'urto), `0x0020` e `0x0008`; colori bianco, nero,
  nougat, giallo, rosso; eventi porta 3 come coppie `[codice, valore]` (es. `0x3861` con
  valore 1→8, un contatore); batteria che inondava il log a delta 1; allo spegnimento
  `WILL_SWITCH_OFF`/`WILL_DISCONNECT`. **Nessun valore dalla porta 2 (pantaloni)** e
  nessun barcode: da verificare in una sessione mirata.
- `shell`: `props` ok, `name MarioCasa` ha rinominato Mario, `modes 1` ha fatto crollare la
  shell: `port_report` era stato inserito per errore nella classe `ModeInfo`.

### 17:15–17:40 — Correzioni e completamenti

- `port_report` spostato in `MarioDevice`; la shell cattura ogni eccezione senza chiudersi.
- `monitor`: scelta dei modi per nome (robusta ai cambi di numerazione tra firmware),
  delta 25 per la batteria, esito delle sottoscrizioni scritto nel log.
- `monitor` e `shell` scrivono log in `logs/` con i byte grezzi.
- Decodifica strutturata degli eventi porta 3 (`codes.GAME_EVENTS`, significati ipotetici).
- Conversione raw→SI generica nel `describe` (es. batteria in mV) usando gli intervalli
  letti dal dispositivo.
- Tabella barcode portata da 13 a 58 tile con nome: fonte `ALL_RGB_CODES.json` del
  pacchetto pyLegoMario (scaricato con `pip download`, copia in
  `logs/pylegomario-src/`), codici letti come little endian dei primi due byte del messaggio.
  La tabella gesti di pyLegoMario è dichiarata inaffidabile dagli autori stessi.
- 18 test offline, tutti verdi.

### 17:25–17:45 — Dashboard web

- Su richiesta: dashboard Flask con menù di tutte le funzionalità, catalogo dei dispositivi
  in un database SQLite locale e gestione degli accessori (pantaloni, basette, colori).
- Architettura: `mario/web/ble_worker.py` (thread con loop asyncio che possiede il
  `MarioDevice`; `call()` inoltra le coroutine; eventi su code SSE e nel DB),
  `mario/web/db.py` (SQLite, libreria standard), `mario/web/app.py` (pagine + API JSON),
  template Jinja e `static/app.js` (barra di stato, SSE, logica per pagina).
- Verificato con il test client Flask (tutte le pagine e le API) e con il server reale
  sull'host (stato, home, scansione via API, flusso SSE). Test del database in
  `tests/test_db.py`.
- Comando: `./run.sh web` (porta 8765). Il DB è `data/mario.sqlite3` (ignorato da git).

### 17:45–17:51 — Prima sessione dal vivo con la dashboard (dall'utente)

- Connessione dalla Panoramica riuscita; 1189 eventi nel database, dispositivo salvato
  come `MarioCasa` (identità F4:60:77:5A:20:11).
- Porta 2: valore iniziale `Mario normale` ricevuto alla sottoscrizione (quindi la porta
  funziona e trasmette solo ai cambi). Basetta `0xB8` Start Mario riconosciuta; colori
  bianco, nero, giallo, nougat, ciano, blu. Il contatore "visti" degli Accessori si è
  aggiornato da solo.
- Gesti: nuovi bit `0x0002` e `0x0080`. Porta 3: undici codici nuovi, tra cui `0x0113`
  con valore 184 = 0xB8 (basetta letta) e `0x3004` = 601. Aggiunti a `codes.GAME_EVENTS`.

### 17:55 — Chiusura della sessione e stato finale

- Documentazione allineata (README, HANDOFF, TODO) prima della compressione del contesto
  della sessione di lavoro assistita: da qui in poi questi tre file sono la fonte di verità.
- Test offline: 24 verdi (`tests.test_lwp3` 18, `tests.test_db` 6), sia nel sandbox sia nel venv dell'host.
- Il database `data/mario.sqlite3` contiene già 1 dispositivo/i (MarioCasa), 1189 eventi e 8 accessori avvistati (ultimo evento alle 17:50): la dashboard è stata usata con Mario connesso.
- Nella cartella del progetto sono comparsi tre cloni git di repository di riferimento
  (`LEGO-Mario-Reveng/`, `legomario.py/`, `pyLegoMario/`), fatti dall'utente: utili per
  consultare le fonti offline, da spostare in `third_party/` (voce nel TODO; fatto il 13/10). In `logs/`
  c'è anche `pylegomario-src/` (pacchetto scaricato con `pip download`), ridondante con il clone
  (rimosso il 13/10).
- Memoria persistente dell'assistente: `~/.claude/projects/<cartella-del-progetto>/memory/mario-ble-environment.md`
  (ambiente, cause dei problemi, decisioni). Non fa parte del progetto.
- Repository git inizializzato il 12/10 sera (vedi la voce in cronologia); fino ad allora il progetto era senza versionamento.

### 18:00–18:25 — Punto 7 del TODO: Mario come I/O di una sessione con un LLM

- Verifiche sull'host: nessuna chiave API nell'ambiente, nessun pacchetto LLM nel venv;
  `spd-say` (speech-dispatcher 0.12 con espeak-ng 1.52 e voci italiane) e l'audio
  PipeWire/PulseAudio funzionano (prova vocale riuscita). Non ci sono `node` nel sandbox
  (c'è sull'host), né `ollama`, né `piper`.
- Nuovo pacchetto `mario/assistant/` (solo libreria standard, niente Flask né bleak):
  `intents.py` (11 regole sensori → intenti con pausa minima, stato del corpo, caratteri per
  pantaloni, famiglie di basette), `emotions.py` (8 emozioni, parser dei tag in streaming,
  spezzatura in frasi), `tts.py` (spd-say/espeak-ng, effetti sonori sintetizzati con `wave`),
  `llm.py` (Claude via API Messages, endpoint compatibile OpenAI, demo a regole), `behaviours.py`
  (volume, busy 0x05/0x06, porte 4/7 sperimentali, allow-list), `session.py` (turno in
  streaming, cronologia, eventi `ASSISTANT`), `service.py` (impostazioni nel DB, flusso del worker).
- Dashboard: pagina "Assistente" (chat, faccia, stato del corpo, simulazione sensori,
  impostazioni, tabella regole), 12 API `/api/assistant/*`, tabella `assistant_messages`,
  `BleWorker.push()`. CLI: `./run.sh assistant [--no-mario] [--backend] [--model] [--base-url]
  [--persona] [--no-tts] [--no-behaviours]`, log in `logs/assistant-*.txt`.
- Scelta di progetto: i comportamenti su Mario seguono la voce (callback all'inizio di ogni
  frase pronunciata), non il testo che arriva più veloce; senza voce seguono il testo.
  Un'emozione nuova interrompe lo script precedente (attese interrompibili).
- Test: 25 nuovi in `tests/test_assistant.py` (3 richiedono Flask+bleak e girano sull'host).
  Totale 49 verdi nel sandbox (3 saltati) e sull'host (nessun `ResourceWarning`).
- Prova dal vivo del server sull'host con DB temporaneo: pagine 200, risposta demo in
  streaming, voce pronunciata dal PC, eventi SSE (`delta`, `speaking`, `emotion`, `done`,
  `intent`, `context`), simulazione "basetta Goomba" → intento → risposta, cronologia salvata.
  Non ancora provato con Mario connesso né con una chiave Claude vera.

### 18:44–18:48 — Chiave API di Claude in `.env`

- L'utente ha creato `.env` nella radice del progetto con `ANTHROPIC_API_KEY`. Aggiunti
  `mario/env.py` (parser e caricatore senza dipendenze: `export`, apici, commenti; non
  sovrascrive l'ambiente), il caricamento in `cli.main()` e in `create_app()`, `.env` in
  `.gitignore`, permessi del file portati a 600.
- Prima richiesta vera: HTTP 400 "`temperature` is deprecated for this model": i modelli
  Claude 5 rifiutano il parametro; tolto dal corpo della richiesta Anthropic (resta per gli
  endpoint OpenAI-compatibili).
- Prova riuscita da CLI (`assistant --no-mario --no-tts`): `claude-sonnet-5-5` risponde in
  italiano, nel personaggio, con i tag emozione (gioia, sorpresa, pensieroso), 2,8–3,8 s a
  risposta completa. Test: 50 verdi. La dashboard risolve il backend `anthropic` dall'ambiente
  senza esporre la chiave (campo mascherato vuoto, `env_anthropic_key` = vero).

### 18:50–18:58 — Prima sessione dal vivo dell'assistente con Mario connesso (dall'utente)

- `./run.sh assistant`: connessione in 6 s, Claude risponde ai sensori veri: terreno
  bianco/nero/nougat, rotazione, salto, basette Start Mario, Goomba, "Rotazione" (0x14) e una
  sconosciuta 0x0160; risposte in 1,8–2,9 s, nel personaggio, con i tag; volume cambiato a
  ogni emozione (il volume di base di Mario era 100).
- Problemi visti e corretti subito:
  1. la voce restava indietro di decine di secondi (ogni risposta 8–10 s di parlato, nuovi
     turni ogni 2–3 s, intenti scartati con "sto già rispondendo"): ora gli intenti dai sensori
     aspettano che la voce sia libera (`Speaker.idle`), si accumulano uno per regola, i più
     vecchi di 8 s si scartano e i restanti diventano un solo prompt; chi scrive da tastiera
     taglia la voce; ai sensori si chiede "una frase, massimo due";
  2. volume "100 → 100": livelli ora relativi alla base (`up` = base × 4/3 + 5, frazioni per
     sonno/tristezza/pensieroso);
  3. terminale illeggibile (righe dei comportamenti sopra il testo in streaming, `\r`): le righe
     laterali aspettano la fine della risposta; comportamenti visibili solo con `-v`/`-d`;
  4. all'uscita (Ctrl+C) il thread dei comportamenti scriveva sul log già chiuso (traceback):
     `SessionLog` tollerante, `_log` del motore non propaga, `session.shutdown()` ripristina il
     volume aspettando la fine, poi disconnessione, poi chiusura del log.
- Rotazione e colore del terreno si attivano di continuo tenendo Mario in mano: pause portate
  a 8 s. Pulsante e pantaloni non provati in questa sessione. Nessuna osservazione ancora su
  cosa si sente o si vede su Mario quando cambia il volume.
- Test: 53 verdi (29 dell'assistente).

### 12/10 07:33–07:37 — Prova a scatola nera: eventi "riflessi" sulla porta 4 (`tools/probe_port4.py`)

- Domanda: si può far partire una clip sonora di Mario via Bluetooth? Primo tentativo: rimandare
  sulla porta 4 (`Events`, 2 × int16, scrivibile) i codici che la porta 3 emette nel gioco.
- Metodo: script che annuncia a voce dal PC "Prova N" (chi guarda Mario sa cosa succede), invia
  un Port Output Command e registra 3 s di risposte; log `logs/probe-port4-20261012-073251.txt`.
- Risultati sul protocollo (fw 6.5.01): **WriteDirectModeData (0x51)** accettato per tutti i 19
  codici con feedback `0x82` "comando completato, inattivo" (`05 00 82 04 0a`); **WriteDirect
  (0x50)** → `INVALID_USE`; **sottocomando 0x00** → `COMMAND_NOT_RECOGNIZED`; **azioni hub 0x05 e
  0x06** (busy) → `INVALID_USE`: Mario non ha l'indicazione "occupato" (opzione della dashboard
  lasciata ma segnata come senza effetto). Nessun eco sulle porte 3, 4 o 7 dopo le scritture.
- Reazione fisica (suoni, display): da riferire dall'utente, prova per prova (il PC annunciava i
  numeri). Prossima prova pronta: `--in-level` (Mario sulla basetta Start, poi monete crescenti e
  Goomba) e `--readback` (rilettura del valore della porta 4 dopo ogni scrittura, per capire se è
  una "casella" che il gioco legge).

### 12/10 17:32–17:34 — Prova "in livello" sulla porta 4 (`probe_port4.py --in-level`)

- Esito della prova del mattino, riferito dall'utente: nessuna reazione visibile di Mario alle
  19 scritture (nessun suono, nessuna animazione).
- Prova in livello: Mario appoggiato sulla basetta Start (letta alle 17:32:25), poi 10 scritture
  WriteDirectModeData sulla porta 4 (monete 1-2-3-10, Goomba, 0x3004=601, 0x3803=100, 0x386B=5,
  0x1803=4, traguardo), ciascuna seguita dalla rilettura del valore della porta 4.
- Risultato: la porta 4 **non conserva** il valore scritto (rilettura sempre `00 00 00 00`),
  nessun eco, il contatore monete della porta 3 (0x3861) non si muove. Le scritture vengono
  accettate e ignorate: l'ipotesi "porta 4 = eventi riflessi del gioco" è da scartare, almeno con
  questi codici e questo sottocomando.
- Ricavato collaterale: il ciclo del livello sulla porta 3. Subito dopo Start `0x1803=4`; dopo
  circa 45 s con Mario sollevato la sequenza di fine livello `0x3004=601, 0x1803=3, 0x3812=2,
  0x3852=0, 0x386E=0, 0x3803=100`, poi l'uscita dal livello `0x1801=0, 0x1803=0, 0x3812=1,
  0x3862=0` (ipotesi aggiornate in `codes.GAME_EVENTS`). Log `logs/probe-port4-20261012-173210.txt`.
- Conferma dell'utente: la sequenza vista è il "tempo scaduto" di Mario (conto alla rovescia
  accelerato sul display); le monete sono rimaste a zero per tutta la prova.
- Prossima prova pronta: `--port7` (37 sequenze sulla porta 7 Telemetry con rilettura).

### 12/10 17:39–17:43 — Prova sulla porta 7 Telemetry (`probe_port4.py --port7`)

- 37 scritture WriteDirectModeData (0x51) sulla porta 7: tutti zero, tutti 255, tutti 1, un byte
  alla volta a 1 e a 255 nelle 16 posizioni, rampe 0..15 e 15..0; rilettura dopo ciascuna.
- Risultato: **nessun feedback 0x82, nessun errore, rilettura sempre 16 zeri**. A differenza della
  porta 4 (che conferma "completato" ma non conserva), la porta 7 ignora le scritture in silenzio.
  Log `logs/probe-port4-20261012-173722.txt`.
- Alla connessione la porta 3 ha emesso `0x3862 = 4` (valore nuovo; prima visti 0 e 2).
- Bilancio delle prove a scatola nera: via LWP3, con i sottocomandi standard, non c'è una via
  per far suonare o animare Mario. Restano la scansione dei sottocomandi non standard (costo
  basso, aspettativa bassa), le basette virtuali su schermo e la cattura del traffico dell'app.

### 12/10 17:50–17:59 — Controprova sul "sorriso" visto durante le prove della porta 7

- L'utente aveva notato, tra le prove 15 e 25 della porta 7 (17:40:23–17:41:31), la bocca di
  Mario illuminarsi (di norma si vede solo la lingua).
- Controprova 1, `--baseline 180`: Mario connesso e fermo per 3 minuti senza alcun comando, con
  il PC che scandiva il tempo a voce ogni 30 s. Nessuna animazione anomala; via Bluetooth solo il
  valore iniziale del gesto e una lettura dello scanner "sollevato". Le animazioni di standby
  (palpebre, sguardo) non producono alcun messaggio LWP3.
- Controprova 2, `--port7 --only 15-25 --times 3`: 33 scritture identiche a quelle sospette.
  Nessuna reazione fisica, protocollo identico (nessun feedback, rilettura zero).
- Conclusione dell'utente: falso positivo, probabilmente una svista. Nessuna delle scritture
  provate oggi (porta 4: 29 + 10, porta 7: 37 + 33) produce effetti osservabili su Mario.
- Script arricchito con `--baseline`, `--only DA-A`, `--times N`, `--pause S`.

### 12/10 sera — Basette virtuali su schermo: negativo

- L'utente ha mostrato un barcode su uno schermo sotto lo scanner: nessuna lettura. Spiegazione
  probabile: il sensore illumina e legge la luce riflessa (verosimilmente nel vicino infrarosso),
  uno schermo emissivo con polarizzatori non offre contrasto. Nel TODO restano carta stampata,
  e-ink e il trucco della luce colorata sul foglio bianco per il sensore colore.

### 12/10 18:09–18:13 — Sessione guidata con le 8 basette reali (`tools/catalog_tiles.py`)

- Nuovo script: annuncia a voce il nome di ogni basetta letta, registra gli eventi della porta 3
  nei 6 s successivi, a fine sessione stampa il riepilogo e segna le basette come possedute nel
  database (7 aggiornate). Log `logs/tiles-20261012-180737.txt`.
- Codici letti (7 distinti): `0x00B8` Start Mario, `0x0002` Goomba, `0x002E` Nuvola, `0x0014`
  Rotazione: nomi di tabella confermati. **Tre codici non in tabella**: `0x00F5`, `0x0160`,
  `0x015B` (da identificare con l'utente: cosa c'è stampato sulle basette).
- Porta 3, nuovi codici con ipotesi: `0x2042` conta 1, 2, 3 a ogni Goomba letto e `0x3712 = 1`
  circa 1 s dopo (nemico sconfitto?); `0x20C2` 6 poi 8 circa 3 s dopo la basetta `0x0160`
  (monete?); `0x2022` 1, 2, 3 mentre Mario veniva fatto girare sulla basetta Rotazione (giri
  contati); `0x3862 = 0` quasi a ogni basetta letta o sollevata; `0x3803 = 0` subito dopo alcune
  basette e `100` alla fine del livello. Dopo la basetta `0x00F5` (8 s dopo) è arrivata la stessa
  sequenza del "tempo scaduto" (`0x3004=601, 0x3812=2, 0x3852=0, 0x1803=3, 0x386E=0, 0x3803=100`)
  a soli 20 s dallo Start. L'utente precisa che la basetta è il **Clown Koopa** (Bowser Jr.) e che
  il livello **non** si è chiuso: quella sequenza quindi non è per forza "fine livello"; forse
  è legata all'incontro con un boss o a un'animazione lunga. Aggiunta a `codes.BARCODES`
  (`0x00F5`, con la nota che pyLegoMario dà Bowser Jr. = `0x0099`).
- Gesti: a ogni appoggio su una basetta arriva `urto + bit 0x0040` (atterraggio?), poi spesso
  `salto`; sulla basetta Rotazione decine di `rotazione` consecutive.
- Colori visti: bianco (0x13), nero (0x1A), giallo sabbia (0x18), verde (0x25).
- Difetto corretto nello script: il numero della basetta nelle riletture.

### 12/10 18:25–18:27 — Seconda sessione con le basette (`catalog_tiles.py`), 8 appoggi

- Stessi 7 codici: l'ottava basetta condivide `0x0002` con il Goomba (appoggiata subito dopo la
  `0x015B`). Quindi le 8 basette dell'utente sono: Start, Clown Koopa (`0x00F5`), due nemici con
  codice Goomba, `0x0160`, `0x015B`, Nuvola, Rotazione.
- Contatori della porta 3 ora leggibili: `0x386B` = **totale monete** (1 dopo il Goomba, 7 dopo
  la `0x0160` che ne dà 6, 8 dopo il secondo nemico); `0x20C2` = monete date dalla basetta
  appena letta (6); `0x2042` = nemici sconfitti (1, 2); `0x3712 = 1` circa 1 s dopo ogni nemico.
  La basetta `0x0160` dà monete: probabilmente un blocco o un oggetto bonus.
- La sequenza `0x3004=601, 0x3812=2, 0x3852=0, 0x1803=3, 0x386E=0, 0x3803=100` è arrivata 44 s
  dopo lo Start senza basette sotto (alle 17:33 erano 45 s): è compatibile con la fine del livello
  a tempo (il conto alla rovescia visto dall'utente), anche se nella sessione delle 18:09 era
  arrivata dopo 21 s. Da osservare il display insieme al log la prossima volta.
- L'utente ha indicato il wiki italiano https://mario.fandom.com/it/wiki/LEGO_Super_Mario come
  fonte per i nomi di set e personaggi.

### 12/10 18:30 — Wiki LEGO Super Mario (fonte indicata dall'utente)

- `https://mario.fandom.com/it/wiki/LEGO_Super_Mario`: la pagina HTML è protetta (403 "Just a
  moment", 402 dal lettore), ma l'API MediaWiki funziona (`api.php?action=parse&...&prop=wikitext`).
- Set iniziale 71360 "Avventura di Mario": tubo, asta della bandiera, blocco ?, blocco mattone,
  fortezza, piattaforma mobile, zattera di nuvole, Bowser Jr., Goomba = 8 elementi, come le 8
  basette dell'utente. Regole: saltando sui nemici si ottengono monete; sul blocco ? parte una
  roulette che decide il premio (coerente con 0x20C2 = 6 o 8 della basetta `0x0160`); Yoshi, Toad
  e Toadette danno un cuore e 5 monete; la piattaforma rotante dà monete (coerente con `0x2022`
  che conta i giri); la lava toglie energia; all'asta della bandiera parte la musica di fine
  livello e il riepilogo monete sul petto.
- L'utente identifica `0x015B` = **carrello minerario**: le basette non vengono tutte dal set
  iniziale. Aggiunto a `codes.BARCODES` (60 nomi). Prima attribuito al 71365 in base al wiki,
  poi corretto dall'utente: il set è il **71425 "Corsa nella miniera di Diddy Kong"** (2023,
  ondata Donkey Kong, non presente nel wiki che si ferma al 2022).
  L'utente identifica poi `0x0160` = **aereo** del 71425 (aggiunto: 61 nomi); resta la seconda
  basetta con codice Goomba.
- Set di provenienza confermati dall'utente: **71360** (Avventura di Mario) e **71425** (Corsa
  nella miniera di Diddy Kong). Quindi la sesta basetta (codice Goomba 0x0002) e forse la quarta
  (`0x0160`, 6 o 8 monete) vengono dal 71425: nemici e oggetti dell'ondata Donkey Kong che il
  firmware tratta con codici già noti o nuovi. Esiste anche il set 71396 "Clown Koopa di Bowser
  Junior": il codice `0x00F5` potrebbe venire da lì, e `0x0099` della community dal Bowser Jr. del
  71360 (o viceversa).

### 12/10 sera — Repository git

- `git init` nella cartella del progetto (ramo `main`), identità impostata solo per questo
  repository (nome dell'utente ed email di lavoro, modificabili con `git config user.name` /
  `user.email`). Primo commit con codice, test, strumenti e documentazione.
- Esclusi da git: `.venv/`, `logs/` (catture btmon, log di monitor, shell, assistente e prove),
  `data/` (database della dashboard), `.env` (chiave API), i tre cloni di terze parti nella radice.
  I log delle prove del 12/10 restano quindi solo in locale: se servono come prova documentale,
  copiarne gli estratti in `docs/`.

### 12/10 19:16–19:21 — Verifiche dal vivo della sezione 1 (`tools/live_checks.py`)

- Nuovo script guidato a voce: pulsante, pantaloni, gesti con RAW+GEST combinati, scanner con
  TAG+RGB combinati, RSSI, allarmi, disconnessione comandata (con riconnessione), spegnimento.
- Esiti: pulsante OK; pantaloni cambiati dal vivo (0x21 Mario, 0x18 Peach confermati, **0x01
  nuovo**, 0x00 tra un paio e l'altro); RSSI sempre 0 (non implementata); allarmi: i quattro tipi
  rispondono "nessun problema"; `DISCONNECT` → `WILL_DISCONNECT`, link chiuso in 0,2 s e
  riconnessione senza tasto; `SWITCH_OFF` → `WILL_SWITCH_OFF` ×2 + `WILL_DISCONNECT` ×2, spento.
- Modalità combinata: accettata su porte 0 e 1 (404 + 162 messaggi 0x46), ma lo script leggeva la
  maschera dei dataset come little endian e scartava tutto; dal log è emerso che Mario la manda con
  il **byte alto per primo** (`00 1f`) e che con multi-update manda solo i dataset cambiati.
  Corretti `lwp3.describe` e lo script; i dati sono stati recuperati rianalizzando il log.
- Gesti recuperati dal log: salto `0x8000` anche nel sollevare/appoggiare/colpire; scossa `0xA111`;
  girare in piedi `0x8102` (bit `0x0002` nuovo); inclinare `0x0100`; cadere `0x2100`; fermo nulla.
  Accelerometro: int8, fermo in mano y ≈ 17..60 (gravità sull'asse y), scossa fino a ±98 su z.
- Scanner TAG+RGB: i codici colore TAG sono tutti corretti (bianco, nero, giallo, nougat, verde,
  rosso, blu: l'utente ha superfici rosse e blu). Del modo RGB si aggiorna solo il primo canale
  (0..189, più alto sul chiaro); gli altri due restano 117 e 130: da riprovare in modo singolo.

### 13/10 — Risposte dell'utente e filtro di stabilità

- Pantaloni: l'utente ha solo i pantaloni blu di Mario. Quindi 0x18 "Peach" e 0x01 visti il 12/10
  erano **letture transitorie dei contatti** durante il cambio, non pantaloni veri: corrette tabella
  e documenti. Mario si è spento regolarmente alla fine della sessione. La seconda basetta con
  codice Goomba è un secondo Goomba (quantità 2 nel database): le 8 basette sono catalogate.
- Conseguenza nel codice: `IntentMapper` accetta un nuovo valore di pantaloni o di colore solo se
  resta stabile per 0,8 s (`tick()` chiamato ogni mezzo secondo dal servizio della dashboard e dalla
  CLI; la simulazione passa `settled`). Nuovi test (54 in tutto).

### 13/10 — Documentazione allineata e stato del progetto

- `ASIS.md`: nuovo file con le attività completate e verificate (sensori, protocollo, software,
  assistente, catalogo dell'utente), spostate dal TODO che ora contiene solo il lavoro aperto.
  La pagina Documentazione della dashboard mostra anche ASIS.
- README riscritto nelle parti "Funzioni di Mario" (porte 4 e 7, modalità combinata, RSSI,
  allarmi, azioni), gesti, pantaloni, barcode (61), colori, nuova tabella degli eventi di gioco
  decodificati; test 54; struttura con i nuovi strumenti e `docs/sessioni/`.
- `docs/sessioni/`: copie anonimizzate dei log delle prove dal vivo (monitor, shell, porte 4/7,
  basette, verifiche), con indice: sono la prova documentale delle tabelle.
- Stato: connessione e pairing stabili; sensori decodificati (gesti con bit ancora senza nome,
  scanner con colori verificati, pantaloni con filtro di stabilità, eventi di gioco con contatori
  di monete/nemici/giri); dashboard con pagina Assistente; assistente provato con Claude e con Mario
  connesso; porte 4 e 7 scrivibili ma inerti; nessuna via per far suonare Mario via Bluetooth
  con i comandi standard. Repository pubblico su GitHub, 4 commit (10 alla sera del 13/10).
- Prossimi passi consigliati (dal TODO): modo RGB da solo e gesti isolati (sezione 1), prova del
  filtro di stabilità con l'assistente e la pagina Assistente con Mario connesso (sezione 7),
  cattura del traffico dell'app ufficiale (sezione 2).

### 13/10 pomeriggio e sera — Attività del TODO senza Mario (sessione autonoma)

Richiesta dell'utente: "porta avanti le attività in TODO per cui non si rende necessario
utilizzare Mario". Fatto, in cinque commit:

- **Libreria**: nuovo `mario/hubstate.py` (stato dell'hub senza bleak) con la decodifica dei
  valori combinati 0x46 ricomposti per modo, verificata offline sui messaggi reali della sessione
  del 12/10 (`00 1f` = dataset 0..4, aggiornamenti parziali `00 06`); `MarioDevice`, il worker della
  dashboard e l'assistente usano lo stesso evento; `lwp3.describe_downstream` per i messaggi in
  uscita; `set_combined_mode` con delta per modo.
- **`decode`** (`mario/decode.py`): btsnoop (formato monitor di btmon, H4 di Android, HCI puro) con
  riassemblaggio L2CAP, log di testo del progetto, uscita di `btmon -r`, byte esadecimali; stato
  iniziale da `docs/capabilities-fw-6.5.01.json` con i modi di default di `monitor`; riepilogo degli
  eventi della porta 3. Riletti i log di `docs/sessioni/`: le tabelle tornano.
- **CLI**: `monitor --combined/--json/--duration/--no-battery` e riconnessione automatica; shell con
  readline, `help <comando>`, `log`; `info` salva il JSON delle funzioni (nome Bluetooth omesso);
  strumenti di `tools/` come sottocomandi; `web --server waitress --token`; `adventure`.
- **Dashboard**: pagina Eventi (filtri, CSV/JSON, grafici SVG senza librerie), accesso con token
  oltre 127.0.0.1 (cookie, `?token=`, Bearer), nome e volume dalla scheda dispositivo, colonna
  "verificato" negli Accessori, pagina Avventure e schermo di gioco.
- **Tabelle** in `mario/data/mario_codes.json` con fonte e data di verifica (nomi invariati).
  Scoperto che `.gitignore` con `data/` escludeva anche `mario/data/`: ora `/data/` e `/logs/`.
- **Assistente**: ricordi `[ricorda: …]` nel database e nel prompt, limite di minuti, parole
  bloccate, diario per i genitori con CSV, scossa = stop mentre parla, rotazione solo a terra,
  tempo alla prima frase, motore piper; la CLI usa il database per ricordi e diario.
- **Latenza misurata con l'API** (tre prompt per modello): haiku prima frase 0,7–1,6 s, sonnet
  1,1–1,5 s con un caso a freddo da 8,8 s; tag emozione corretti in entrambi.
- **Designer di avventure, prima versione**: modello JSON, motore a stati (trigger basetta,
  colore, pantaloni, gesto, pulsante, evento di gioco, timer, sequenza, ripetizione; azioni punti,
  voce, suono, messaggio, volume, goto, fine, webhook), libreria degli elementi, servizio, editor a
  moduli + JSON, schermo di gioco, comando `adventure` con simulazione da tastiera, tre esempi
  (colori, gesti, quiz con le basette dell'utente). Partita simulata del quiz: 80 punti, 7 passi.
- **Test**: da 54 a 121, poi 126 in serata (BleakClient finto per `MarioDevice` e `BleWorker`, server SSE locale per i
  backend HTTP, servizio, voce, CLI, pagine, token, avventure, controlli statici del JavaScript,
  fumo); `pyproject.toml`, workflow GitHub Actions; cloni di terze parti in `third_party/`;
  `logs/pylegomario-src` rimosso; regola udev in `tools/`.
- **Sistema**: autosospensione ancora su `on` ma il PC non è mai stato riavviato dall'11/10
  (parametro del modulo `Y`): la persistenza resta da verificare al primo riavvio; firmware
  Broadcom cercato di nuovo, non disponibile pubblicamente.
- Non toccato perché serve Mario: sezione 1 del TODO, porte 4/7, cattura dell'app, prove dal vivo
  di assistente e avventure. Tutte le novità hanno una voce "da provare dal vivo" nel TODO.

### 13/10 sera — Sezione 2 del TODO (reverse engineering) senza Mario

Richiesta dell'utente: "procediamo con il punto 2. Reverse engineering ancora da fare". Mario non
era acceso (scansione vuota), quindi si è fatto tutto ciò che non richiede il dispositivo:

- **Rilettura dei log con `decode`** (sessioni dell'11 e del 12/10, cronologia compatta per porta):
  `0x3861` non è un contatore di monete ma un **conto alla rovescia** a un passo al secondo (5→1
  alle 17:07:08 dell'11/10; poi 5→1, 8→1, 8→1 dalle 17:08:16) con Mario fermo, interrotto dal
  movimento (`0x3862 = 0`) e chiuso da `0x3866 = 0` + `0x1802 = 2`, seguiti da 93 s senza alcun
  gesto (sonno?); alle 17:10:10, ripreso in mano, `0x3866 = 1` + `0x1802 = 1` (risveglio?).
  `0x1801`/`0x1803`/`0x3812` si comportano come una macchina a stati del livello (Start: `0x386B = 0`,
  `0x1801 = 1`, `0x1803 = 1`; poi `0x1803 = 4`; fine: `0x1803 = 3`, `0x3812 = 2`; ~9 s dopo
  `0x1801 = 0`, `0x1803 = 0`, `0x3812 = 1`). `0x3004 = 601` è costante anche a 21 s e 44 s dallo
  Start: non è il tempo. `0x3857` oscilla 1/0 attorno al rosso (lava) con urti e scosse. Ipotesi
  aggiornate in `mario/data/mario_codes.json`, README e ASIS.
- **`decode` per i telefoni**: legge lo zip di `adb bugreport` (cerca `btsnoop_hci.log`, anche `.last`,
  `.cfa`, `.gz`), le sezioni btsnooz base64+zlib del `bugreport-*.txt` (formato AOSP v1 e v2,
  ricostruzione dei timestamp dall'ultimo pacchetto) e i file PacketLogger di Apple (`.pklg`, big o
  little endian, euristica di Wireshark); riconosce il formato dal contenuto; conta e segnala i
  pacchetti ACL troncati (riassunto btsnooz senza HCI snoop log attivo). 5 test nuovi (126 in tutto).
- **Guida alla cattura** `docs/cattura-app-ufficiale.md`: Android (opzioni sviluppatore, snoop log,
  `adb bugreport`), iPhone (profilo Bluetooth logging, sysdiagnose, `.pklg`), copione di gioco con
  orari, comandi di analisi, avvertenze sulla privacy del bugreport.
- **Tabella GATT nel report `info`** e nel JSON delle funzioni: servizi, caratteristiche (proprietà) e
  descrittori con i nomi noti (GAP, GATT, LEGO, OAD Texas Instruments e le sue caratteristiche secondo
  il profilo TI) e l'avviso "da non toccare" sull'OAD; solo lettura della tabella, nessun accesso.
- **Nuovo strumento `./run.sh re-checks`** (`tools/re_checks.py`): esegue dal vivo, guidati a voce, i
  passi della sezione 2: `annuncio` (manufacturer data prima della connessione), `gatt`, `sistema`
  (0x0B contro advertisement), `sonno` (45 s fermo, poi ripreso in mano), `porta3` (CHAL/VERS/DEBUG
  con lettura e 20 s di notifiche, poi ritorno a EVENTS), `gioco` (porte 3, 4, 7 con scanner, gesti e
  pantaloni durante un mini livello di circa due minuti). Log in `logs/re-checks-<data>.txt`
  rileggibile con `decode`. Mai provato con Mario: voce nella sezione 1 del TODO.
- Fonti: formato btsnooz da `tools/scripts/btsnooz.py` di AOSP (mirror), formato PacketLogger da
  `wiretap/packetlogger.c` di Wireshark; Mario non acceso, nessuna prova dal vivo.

### 14/10 sera — Voce IA con OpenVoice

Richiesta dell'utente: "sarebbe interessante aggiungere anche un sintetizzatore vocale IA
(OpenVoice)". Fatto e provato sull'host, senza Mario:

- **Cosa è OpenVoice V2**: convertitore di timbro (MyShell e MIT, licenza MIT); le voci di base
  native sono inglese, spagnolo, francese, cinese, giapponese e coreano, niente italiano. Scelta:
  piper legge la frase con la voce italiana `it_IT-paola-medium`, OpenVoice ne cambia il timbro con
  quello della registrazione di riferimento (impronta della voce di base calcolata una volta da tre
  frasi di prova, in cache).
- **Installazione** `tools/setup_openvoice.sh`: `.venv-openvoice` con Python 3.11 da `uv`, torch
  2.14 per CPU, OpenVoice senza le sue dipendenze fissate (gradio, faster-whisper… non servono),
  piper-tts 1.8, voci piper paola e riccardo, convertitore V2 da Hugging Face a revisione fissata.
  Lo zip dei checkpoint su S3 indicato dalla documentazione di OpenVoice risponde 404. Spazio:
  1,4 GB l'ambiente, 133 MB OpenVoice, 88 MB le voci.
- **Due difetti aggirati**: lanciato come script, il processo vedeva `mario/assistant/openvoice.py`
  al posto del pacchetto `openvoice` (risolto con `python -I` e pulizia di `sys.path`); il
  costruttore `ToneColorConverter` di OpenVoice passa `enable_watermark` alla classe base, che lo
  rifiuta (replicate le sue tre righe in `make_converter`).
- **Architettura**: processo persistente `openvoice_worker.py` (una riga JSON per richiesta),
  client `openvoice.py` con avvio in background, tempi massimi, ripartenza dopo un blocco o un
  crash; `Speaker` con due thread (preparazione e riproduzione, due frasi in anticipo), contatore
  delle frasi non finite per lo stato "sta parlando", voce di riserva (piper o spd-say) quando la
  voce IA non è pronta. Lo stesso processo fa anche piper da solo, con il modello sempre caricato.
- **Misure** (i7-5557U, solo CPU): caricamento 8–10 s (24 s la prima volta); prima frase udibile
  0,4–1,1 s dopo una frase di riscaldamento all'avvio; conversione circa 0,7 s per secondo di voce
  senza marchio d'acqua, circa 1 s con il marchio (spento di default); piper nel processo 0,08–0,16 s
  a frase contro 2,8 s del comando `piper`. Impronta della frase convertita simile al riferimento
  sintetico per 0,94, alla voce di partenza per 0,51.
- **Interfacce**: `./run.sh voice` (prova con i tempi), `assistant --openvoice-ref/--openvoice-tau/
  --openvoice-watermark`, impostazioni della dashboard (sezione "Voce IA", caricamento della
  registrazione anche da m4a/ogg/mp3 con ffmpeg in `data/voices/`, stato del processo).
- **Test**: da 126 a 140 (`test_openvoice` con un processo finto che parla lo stesso protocollo,
  caricamento dalla dashboard); due test della sessione adattati al contatore delle frasi.
- Da fare: ascolto con una voce vera e sessione con Mario (TODO, sezione 1).

### 14/10 sera — Suoni e voci dei giochi (The Mushroom Kingdom)

Richiesta dell'utente: scaricare dall'archivio themushroomkingdom.net "tutto quello che ci può
servire" (musiche, effetti, voci).

- **Il sito**: archivio di appassionati dal 1997; la sezione Sound clips ha 31 giochi, 1.415 WAV,
  82 MB; `robots.txt` vuoto. La pagina About dice che i personaggi sono © Nintendo e chiede di
  chiedere il permesso per usare il materiale su un altro sito. Quindi: file solo in locale
  (`data/sounds/tmk/`, ignorata da git), nel repository solo lo script.
- **Scelta**: 16 giochi, 833 file, 49 MB: i platform per effetti, jingle e voci di Mario (35 voci in
  Super Mario 64, 78 in Sunshine, "Let's-a go!" e "Okey-dokey!" in 3D Land), Mario Kart 64 (voci di
  Mario, Luigi, Peach, Toad, Yoshi), i Mario & Luigi e Luigi's Mansion (utili anche per LEGO Luigi).
  Esclusi Mario Party, Smash, Golf, i puzzle; `--games all` li aggiunge. Musiche: solo i jingle
  brevi della sezione WAV (fine livello, game over, tempo che scade); le colonne sonore complete
  (MP3, MIDI, NSF, SPC) non servono agli effetti e non sono state scaricate.
- **Strumento** `tools/fetch_mario_sounds.py` (`./run.sh fetch-sounds`): legge le pagine dei giochi,
  scarica con una richiesta al secondo, non riscarica ciò che c'è, scrive `catalogo.json`;
  `--catalog` salva l'elenco del sito in `catalogo-remoto.json` senza scaricare.
- **Uso**: `mario/assistant/soundpack.py` con tre stili (dei giochi, voce di Mario, sintetizzati) e
  ricadute; `Speaker.play` lo usa per emozioni, ascolto e avventure; impostazione nella pagina
  Assistente, `--sound-style` in `assistant` e `voice`, `voice --play NOME` per ascoltare; nomi
  nuovi per le avventure (oneup, stomp, pipe, star, clear, letsago, …) e file precisi
  (`gioco/file.wav`); elenco nella libreria delle avventure.
- **Voci di Mario come riferimento della voce IA**: non usate. Sono esclamazioni brevi con musica ed
  effetti, quindi un cattivo riferimento, e sono la voce di una persona reale senza il suo consenso.
- Test: da 140 a 143.

### 14/10 sera — Archivio completo e pagina Suoni

Richiesta dell'utente: una pagina del portale per personalizzare i file multimediali e tenere in
locale tutto l'archivio originale ("per uso privato non c'è problema").

- **Archivio intero**: lo strumento ora copre tutte le sezioni di "Sounds & Music": WAV dei 31 giochi
  (1.415), MP3 (13), MIDI (906 in 47 giochi), moduli tracker (114 `.mdr`, cioè moduli compressi RAR,
  estratti con 7z), NSF (8 zip) e SPC (14 zip) estratti accanto. Il catalogo viene scritto ogni 25
  file e anche se lo scaricamento viene interrotto. Il sito risponde in circa 3 s a file, quindi
  l'archivio intero richiede circa due ore e mezza in background.
- **Riproduzione**: il ffmpeg di Debian ha libgme (NSF, SPC) e libopenmpt (moduli); provata la
  conversione di `SMB1.NSF` con `-track_index` (tracce diverse, audio diverso). Mancano fluidsynth e
  timidity, quindi i MIDI restano non riproducibili finché non si installa un sintetizzatore.
- **`soundpack.py` riscritto**: radice `data/sounds/` con `tmk/`, `custom/`, `cache/` e
  `assegnazioni.json`; scelte dell'utente per stile prima delle tabelle; nomi nuovi; file precisi
  con `#traccia`; conversioni in cache; percorsi confinati nella cartella; volume degli effetti.
- **Pagina Suoni** (`/sounds`, voce di menù "Suoni"): tabella dei nomi con file scelti e predefiniti,
  ascolto nel browser e sul PC, archivio filtrabile con "+" per assegnare, caricamento di suoni propri.
  API `/api/sounds/*`.
- Test: da 143 a 149 (`test_sounds_web`, nuovi casi in `test_openvoice`).

### 14/10 sera — Un messaggio "con la voce di Mario"

Domanda dell'utente: "riesci a sintetizzare già un messaggio con la voce di Mario?". Risposta: la voce
dei giochi non viene clonata, perché è quella di un doppiatore reale senza il suo consenso; inoltre
le registrazioni sono esclamazioni brevi con musica. Fatto invece un messaggio "alla Mario":

- **Misure** (pyin di librosa): esclamazioni di Mario tra 250 e 570 Hz (urlate, alcune disturbate
  dalla musica); piper riccardo 147 Hz, paola 197 Hz. Scelta: riccardo +6 semitoni (×1,41), cioè
  circa 207 Hz, misurati poi sul messaggio prodotto.
- **Effetto `mario`** in `Speaker`: ffmpeg `rubberband` (pitch, tempo, formanti spostate o
  conservate), con ripiego `asetrate`+`atempo`; per spd-say alza tono e velocità del motore.
- **Effetti tra le frasi**: `Speaker.speak_sound` mette in coda un'esclamazione o un file
  dell'archivio, che suona al suo turno; `voice` riconosce `{nome}` nel testo.
- **Salvataggio**: `voice --save` registra ogni pezzo suonato e li unisce con ffmpeg; `--no-play`.
- **Risultato**: `logs/messaggio-mario-cartone.wav` (8,5 s, suonato dal PC) e
  `logs/messaggio-mario-naturale.wav`; impostazione "Effetto: stile Mario" nella pagina Assistente.
- Test: da 149 a 152.

### 14/10 sera — Voce stile Mario collegata alla sessione con il modello

Richiesta dell'utente: collegare il modulo della voce alla sessione LLM ("aggiungi tutto").

- La sessione usava già `Speaker` per frasi ed effetti; mancavano l'effetto da CLI e le esclamazioni
  scelte dal modello.
- **CLI**: `assistant --effect mario --semitones --tempo --formant` (opzioni condivise con `voice`;
  senza `--piper-model` usa riccardo), `--no-exclamations`.
- **Esclamazioni**: `EmotionStream` riconosce `{nome}` (anche spezzato tra due pezzi); `SoundPack.
  exclamations()` dà le disponibili con frase e momento d'uso; `AssistantSession` aggiunge `SOUND_RULE` al
  prompt (solo con voce attiva e file presenti), suona l'esclamazione con `speak_sound` dopo la frase in
  corso, massimo due per risposta, ignora i nomi sconosciuti; `strip_tags` toglie le graffe da diario e
  cronologia; evento `sound` e campo `display` del `done` per la chat ("🔊 Let's-a go!").
- **Impostazione** `assistant.exclamations` (casella nella pagina Assistente).
- **Prova con Claude** (`claude-sonnet-5-5`, `--effect mario --sound-style voce`): "Partiamo per una nuova
  avventura?" → `{herewego}` all'inizio, poi tre frasi con l'effetto; prima frase dopo 1,78 s; nel log
  "🔊 Here we go!" e nessuna graffa.
- Test: da 152 a 155.

### 14/10 notte — Archivio completo scaricato

- Primo passaggio (sandbox, 2 ore e mezza): 1.921 file nuovi, 73 errori 403, tutti link del sito con
  la barra rovesciata (`mlrpg3\file.wav`, che i browser trattano come `/`). Corretto lo strumento e
  rifatto il passaggio sull'host: 73 file, nessun errore.
- Totale: 2.783 voci, 170 MB: 1.374 WAV, 904 MIDI, 114 moduli, 13 MP3, 10 NSF (da 8 zip), 348 SPC
  (da 14 zip); le tracce degli zip hanno la descrizione con il numero di brani.
- Moduli `.mdr`: sono RAR 2.0 del 2001. Il `7zip` di Debian (dfsg) non ha il decompressore RAR e crea
  file vuoti: lo strumento ora prova `unar`, poi `7z`, scarta le estrazioni vuote e riconosce i moduli
  non compressi dal contenuto (5 su 112). Nuova opzione `--reindex` per rifare estrazioni e catalogo
  senza contattare il sito, da lanciare dopo `sudo apt install unar`.
- Conversioni provate sull'host con il codice della pagina Suoni: SPC 8 s in 0,24 s, NSF traccia 5
  in 0,19 s, modulo IT 6 s in 0,21 s.

### 15/10 sera — Moduli tracker e MIDI sbloccati

- L'utente ha installato `unar`, `fluidsynth` e `fluid-soundfont-gm`. `fetch-sounds --reindex` con
  `unar`: 101 moduli su 112; gli altri 11 falliscono con "Attempted to read more data than was
  available", con l'archivio integro (62 byte di intestazione + dati = dimensione del file). Tutti i
  107 RAR sono 2.0, metodo "Best", file singolo: `unar` 1.10.8 sbaglia su alcuni flussi (probabilmente i
  blocchi audio). libarchive (provata via ctypes, senza installare bsdtar) non conosce il 2.0: fallisce
  su 108 archivi su 112. `unrar` e `7zip-rar` sono nel ramo non-free, non attivo.
- **`mario/rar20.py`**: decompressore RAR 2.0 in Python (Unpack20 dei sorgenti pubblici di unrar:
  tabelle di Huffman canoniche con tabella rapida a 10 bit, copie corte e lunghe, distanze ricordate,
  modalità audio con il predittore a 5 coefficienti per canale), controllato con il CRC32 dell'archivio.
  Al primo tentativo 107 archivi su 107 con il CRC giusto, 17,9 MB in 25 s. Ogni troncamento dà
  `RarError`, mai un `IndexError`. `fetch-sounds` lo usa per primo, poi `unar` e `7z`; 112 moduli su 112.
- **MIDI**: il codice c'era ma non era mai stato provato con un sintetizzatore vero. Due difetti: il
  taglio a `seconds` era ignorato (un'anteprima di 30 s produceva il brano intero) e il volume era
  10–20 dB sotto le altre musiche (media da −29 a −41 dB contro −16/−24 di SPC, NSF e moduli). Prima
  correzione con un file intermedio e la misura del picco con ffmpeg; la prova su tutto l'archivio ha
  mostrato che fluidsynth sintetizza comunque tutto il brano (oltre 90 s per "Life in the Mines" con sei
  conversioni in parallelo). Versione finale: fluidsynth scrive PCM sullo standard output (`-F - -T raw`),
  `read_stream` legge solo i byte necessari e lo ferma, il picco si misura in Python (`pcm_peak_db`) e
  ffmpeg applica guadagno (picco a −1 dB, al massimo +30 dB) e dissolvenza. Banco scelto per nome
  (`default-GM.sf2`, poi `FluidR3_GM.sf2`). Tolto timidity, non installato e mai provato.
- **Prova completa** (anteprime da 30 s, sei in parallelo): 904 MIDI e 112 moduli, 1.016 su 1.016
  convertiti in 517 s; da soli i casi peggiori richiedono 2,6–2,9 s. Volume dei MIDI dopo la
  correzione: media da −16 a −21 dB, picco −1,0 dB.
- Test: da 155 a 159 (`test_rar20` con un archivio costruito bit per bit, MIDI più forte e tagliato in
  `test_openvoice`).

### 15/10 notte — Sistema e Bluetooth del PC (sezione 3 del TODO)

- **Stato rilevato**: PC acceso dall'11/10 (mai riavviato), `enable_autosuspend` di btusb ancora
  `Y`, dispositivo `1-3` su `control=on`, Wi-Fi sui 5 GHz (5200 MHz), `hex2hcd` installato,
  `dmesg` e journal del kernel non leggibili senza sudo.
- **Firmware: premessa sbagliata.** Sui sorgenti del kernel 6.12, la regola Apple di `btusb.c`
  (`USB_VENDOR_AND_INTERFACE_INFO(0x05ac, 0xff, 0x01, 0x01)`, flag `BTUSB_BCM_APPLE`) imposta
  `hdev->setup = btbcm_setup_apple`. Questa funzione fa reset, legge versione, prodotto, funzioni
  e nome e imposta un quirk, senza caricare file. `.hcd` viene cercato solo da
  `btbcm_initialize`, chiamata da `btbcm_setup_patchram`. L'interfaccia del nostro adattatore è
  proprio `255/1/1`. Nei log salvati non c'è mai stato un "Patch not found": l'11/10 era una
  deduzione dalla cartella vuota. Punto chiuso.
- **Autosospensione persistente**, verificata senza riavvio:
  - `modprobe -c` mostra l'opzione;
  - l'initramfs del 14/10 contiene `etc/modprobe.d/btusb.conf` e non contiene `btusb`;
  - `60-autosuspend.rules` agisce solo con `ID_AUTOSUSPEND=1`, che per 05ac:8290 non c'è;
  - powertop è disabilitato, TLP non è installato;
  - quindi al riavvio il dispositivo resta su `on`.
- **Agente di pairing**: in `src/agent.c` di BlueZ 5.82 c'è una coda `default_agents`.
  `RequestDefaultAgent` porta l'agente in testa e `remove_default_agent` promuove il successivo,
  quindi alla chiusura dell'app torna quello di GNOME. Problema trovato: il nostro `_Agent1`
  approvava ogni richiesta (passkey 0, conferme, autorizzazioni dei servizi) per tutta la durata
  della connessione. Con il PC accoppiabile (pannello Bluetooth di GNOME aperto) un dispositivo
  qualsiasi si sarebbe accoppiato senza conferma. Ora `BlueZAgent.allow()` riceve l'indirizzo
  del Mario trovato e le altre richieste ricevono `org.bluez.Error.Rejected` con un avviso nel log.
- **`./run.sh doctor`** (`mario/syscheck.py`): controlli in sola lettura con comando di rimedio.
  - Fonti: sysfs, `/sys/class/rfkill`, `systemctl is-active`, `bluetoothctl show/devices/info`,
    nmcli o `iw` per la frequenza, file in `/etc/modprobe.d`.
  - Nel sandbox Flatpak avvisa che i risultati sono parziali, perché `/etc` non è quello dell'host.
  - Gli stessi controlli compaiono nella pagina Scansione ("Controllo del PC") e in
    `/api/system/checks`.
  - Il suggerimento sugli errori di connessione ora rimanda a `doctor` e non cita più il firmware.
  - Sull'host: tutto a posto in 124 ms.
- Restano: verifica al primo riavvio (basta `doctor`) e reset completo del pairing con Mario acceso.
- Test: da 159 a 166 (`test_syscheck` su un sysfs finto, agente ristretto in `test_device_fake`,
  sezione della pagina Scansione in `test_web_pages`).

## 4. Stato del codice

| File | Ruolo |
|---|---|
| `run.sh` | avvio; salta sull'host se in Flatpak; crea il venv se manca |
| `mario/lwp3.py` | protocollo LWP3: enumerazioni, costruttori di messaggi, parser, `describe` e `describe_downstream`, formati valore, versioni, scaling SI |
| `mario/hubstate.py` | stato dell'hub senza bleak: porte, modi, formati, valori combinati ricomposti per modo, evento unico (`event`), stato iniziale da JSON |
| `mario/codes.py`, `mario/data/mario_codes.json` | tabelle Mario (pantaloni, gesti, barcode 61, colori, eventi 22) con fonte e verifica; IO type; modi di default per nome; delta |
| `mario/device.py` | `MarioDevice` (scansione, connessione con retry, agente di pairing, bonding, notifiche, richieste con attesa, scoperta, modalità combinata con delta per modo, report, JSON), `BlueZAgent`, `remove_bluez_device` |
| `mario/decode.py` | decodifica offline: btsnoop (monitor/H4/HCI, riassemblaggio L2CAP), bugreport Android (zip, sezioni btsnooz), PacketLogger di Apple, gzip, log di testo, btmon -r, hex; note sui pacchetti troncati; riepilogo |
| `mario/cli.py` | comandi `scan`, `info [--json] [--quick] [--no-save]`, `monitor [--sub] [--combined] [--json] [--duration] [--no-battery] [--rssi] [--raw-accel] [--no-reconnect]`, `shell [--no-reconnect]`, `decode`, `doctor [--json]`, `assistant [--minutes] [--block] [--no-memory] [--no-db] [--piper-model]`, `adventure [--list] [--play] [--no-mario]`, `web [--server] [--token] [--threads]`, comandi rapidi, strumenti di tools/ come sottocomandi; opzioni globali `-a`, `-t`, `-i`, `--any-lego`, `--no-pair`, `-d` |
| `mario/env.py` | caricamento di `.env` (chiave API) senza dipendenze |
| `mario/syscheck.py` | controllo del PC per il Bluetooth (`doctor`, pagina Scansione, `/api/system/checks`): adattatore, rfkill, servizio, autosospensione, banda del Wi-Fi, hub LEGO associati |
| `mario/rar20.py` | estrazione RAR 2.0 in Python (Unpack20 con modalità audio, CRC32) per i moduli `.mdr` dell'archivio dei suoni |
| `mario/assistant/` | Mario come I/O di un LLM: `intents.py`, `emotions.py`, `tts.py`, `llm.py`, `behaviours.py`, `memory.py`, `session.py`, `service.py`, `openvoice.py` (client della voce IA) e `openvoice_worker.py` (processo piper + OpenVoice, gira con `.venv-openvoice`) (vedi README, sezione Assistente) |
| `mario/adventures/` | designer di avventure: `model.py`, `engine.py`, `library.py`, `service.py`; esempi in `mario/data/adventures/` |
| `tests/` | 166 test (vedi tabella nel README); quelli che importano Flask o bleak girano sull'host e in CI |
| `pyproject.toml`, `.github/workflows/tests.yml` | pacchetto `mario-ble` 0.2.0 con entry point `mario`; test su GitHub Actions |
| `mario/web/app.py` | Flask: pagine (menù) e API JSON |
| `mario/web/ble_worker.py` | thread Bluetooth della dashboard (loop asyncio, SSE, salvataggio eventi) |
| `mario/web/db.py` | SQLite: dispositivi, accessori, eventi, impostazioni, messaggi dell'assistente |
| `mario/web/templates/`, `static/` | pagine HTML, CSS e JavaScript |
| `tools/capture_btmon.sh` | cattura HCI con btmon durante un tentativo di connessione (sudo) |
| `tools/diag_connect.py` | proprietà BlueZ del dispositivo + tentativo di connessione prima/dopo la rimozione del record |
| `tools/live_checks.py` | verifiche dal vivo guidate a voce della sezione 1 del TODO (pulsante, pantaloni, gesti, RGB, RSSI, allarmi, disconnect, off) |
| `tools/catalog_tiles.py` | sessione guidata con le basette reali: annunci vocali, eventi porta 3 per basetta, aggiornamento del database |
| `tools/probe_port4.py` | prove a scatola nera: scritture sulla porta 4 con annunci vocali, `--in-level`, `--readback`, `--port7` |
| `tools/fetch_mario_sounds.py` | copia in locale l'archivio "Sounds & Music" di themushroomkingdom.net (WAV, MP3, MIDI, moduli, NSF, SPC) in `data/sounds/tmk/` con il catalogo (uso privato) |
| `tools/setup_openvoice.sh` | installa la voce IA: `.venv-openvoice` (torch CPU, OpenVoice, piper), convertitore V2 da Hugging Face, voci piper italiane (circa 1,5 GB) |
| `tools/re_checks.py` | reverse engineering dal vivo guidato a voce (sezione 2 del TODO): annuncio, GATT, tipo di sistema, sonno, porta 3 CHAL/VERS/DEBUG, porte 4 e 7 nel gioco; mai provato con Mario |
| `tools/gatt_notify_test.sh` | test notifiche con il solo bluetoothctl (mai servito, la cattura 3 ha chiarito prima) |
| `tools/99-mario-btusb.rules` | regola udev contro l'autosospensione dell'adattatore Broadcom (da installare con sudo, istruzioni nel file) |
| `docs/capabilities-fw-6.5.01.json` | mappa completa delle funzioni letta dal dispositivo (dal 13/10 `info` vi aggiunge la tabella GATT) |
| `docs/cattura-app-ufficiale.md` | guida alla cattura del traffico dell'app ufficiale dal telefono (Android, iPhone), copione di gioco, analisi con `decode` |
| `docs/sessioni/` | copie anonimizzate dei log delle prove dal vivo (11 e 12/10) con indice |
| `third_party/` | cloni di riferimento (LEGO-Mario-Reveng, legomario.py, pyLegoMario), ignorati da git |
| `logs/` | catture btmon, log di monitor/shell/assistente/avventure (ignorata da git) |

Nel README: uso, comandi della shell, protocollo, tabella porte verificata, osservazioni
dal vivo, sezione "Problemi di connessione" con tutte e tre le catture.

## 5. Punti aperti e stato

Il lavoro aperto è in [TODO.md](TODO.md); le attività completate e verificate sono raccolte in
[ASIS.md](ASIS.md) (dal 13/10: le voci spuntate del TODO sono state spostate lì).

## 6. Come riprendere

```sh
cd ~/Sviluppo/Mario
.venv/bin/python -m unittest discover -s tests   # test offline (166); nel sandbox: python3 -m unittest discover -s tests
./run.sh -t 60 info                          # premere il tasto Bluetooth di Mario; salva docs/capabilities-fw-*.json
./run.sh monitor --combined                  # eventi in tempo reale (accelerometro + gesti insieme), log in logs/
./run.sh shell                               # console bidirezionale con readline, log in logs/
./run.sh decode docs/sessioni/tiles-20261012-182334.txt --summary   # rilettura offline di un log
./run.sh web                                 # dashboard su http://127.0.0.1:8765 (Assistente, Eventi, Avventure)
./run.sh assistant --no-mario                # assistente senza Bluetooth: tastiera, modello (demo), voce
./run.sh fetch-sounds --all                  # archivio di suoni, voci e musiche (uso privato); pagina Suoni
./tools/setup_openvoice.sh                   # voce IA (una volta, circa 1,5 GB); poi:
./run.sh voice "Ciao!" --openvoice-ref data/voices/<file>.wav   # prova della voce IA con i tempi
./run.sh assistant                           # con Claude: la chiave è in .env (o nelle impostazioni della pagina)
./run.sh adventure --play quiz --no-mario    # avventura con eventi da tastiera; senza --no-mario con Mario
./run.sh live-checks --steps gesti           # verifiche guidate a voce (sezione 1 del TODO)
./run.sh re-checks                           # reverse engineering dal vivo (sezione 2): annuncio, GATT, sonno, porta 3, porte 4 e 7
./run.sh decode logs/bugreport-mario.zip --summary   # cattura dell'app ufficiale (guida: docs/cattura-app-ufficiale.md)
./run.sh catalog-tiles                       # sessione con le basette: codici, eventi, database
./run.sh probe-port4 --help                  # prove a scatola nera su porte 4 e 7
./tools/capture_btmon.sh                     # se la connessione torna a fallire
git pull && git push                         # remote: github.com/saulusprime/Mario-IA-Assistant (SSH)
```

Checklist prima di cercare bug nell'app: Wi-Fi su 5 GHz (`nmcli -t -f ACTIVE,SSID,FREQ dev
wifi | grep '^sì'`), autosospensione disattivata (`cat /sys/bus/usb/devices/1-3/power/control`
deve dire `on`), Mario con l'icona Bluetooth che lampeggia, bond presente
(`bluetoothctl devices Bonded`). Se qualcosa resta appeso, ogni fase dell'app ha un
timeout e stampa dove si trova; i log di `monitor`, `shell` e `btmon` sono in `logs/`.

Per i prossimi sviluppi partire da [TODO.md](TODO.md): la sezione 1 raccoglie le verifiche dal
vivo (comprese le novità del 13/10 scritte senza Mario), la 6 e la 7 le prove di avventure e
assistente con il Mario fisico, la 2 la cattura del traffico dell'app ufficiale con `decode`
(guida in `docs/cattura-app-ufficiale.md`) e le prove dal vivo che `./run.sh re-checks` esegue in un colpo.

## 7. Fonti consultate

- LEGO Wireless Protocol 3.0: https://lego.github.io/lego-ble-wireless-protocol-docs/
- bricklife/LEGO-Mario-Reveng: https://github.com/bricklife/LEGO-Mario-Reveng
- djipko/legomario.py: https://github.com/djipko/legomario.py
- Jackomatrus/pyLegoMario (file `ALL_RGB_CODES.json`): https://github.com/Jackomatrus/pyLegoMario
- node-poweredup: https://github.com/nathankellenicki/node-poweredup
- Sorgente BlueZ 5.82 (`src/device.c`, scelta del bearer): https://github.com/bluez/bluez
- Filelist Debian `bluez-firmware`: https://packages.debian.org/trixie/all/bluez-firmware/filelist
- winterheart/broadcom-bt-firmware: https://github.com/winterheart/broadcom-bt-firmware
- Thread Arch Linux su BCM20703A1 e BLE: https://bbs.archlinux.org/viewtopic.php?id=243616
- Thread Eurobricks sul reverse engineering di Mario: https://www.eurobricks.com/forum/forums/topic/179464-reverse-engineering-lego-super-mario
