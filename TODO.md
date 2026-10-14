# TODO — lavoro aperto e idee

Lista di lavoro del progetto LEGO Mario via Bluetooth LE. Stato al 13 ottobre 2026 (sera).
Qui c'è **solo ciò che resta da fare**: le attività completate e verificate sono in
[ASIS.md](ASIS.md), il diario in [HANDOFF.md](HANDOFF.md). Le voci sono raggruppate per tipo;
dentro ogni gruppo le prime sono le più utili.

## 1. Da verificare dal vivo (bastano Mario e `./run.sh monitor` o `tools/live_checks.py`)

- [ ] **Gesti senza nome**: dare un nome ai bit `0x0008`, `0x0020`, `0x0080` e confermare il
      `0x0002` (visto girando Mario in piedi): movimenti più isolati, uno alla volta, con
      `tools/live_checks.py --steps gesti` (accelerometro e gesti insieme); girare Mario senza
      sollevarlo.
- [ ] **Scanner in modo RGB da solo** (`monitor --sub 1:1`): in modalità combinata si muove solo
      il primo dei tre canali; capire se nel modo singolo i tre canali cambiano con il colore.
- [ ] **Pantaloni diversi dai blu**: quando ci saranno altri pantaloni, confrontare i codici con
      `codes.PANTS` (oggi verificato solo 0x21; 0x18 e 0x01 erano letture transitorie).
- [ ] **Bowser Jr. `0x0099`**: capire se il codice della community è un'altra basetta di Bowser Jr.
      (set 71396 "Clown Koopa di Bowser Junior"?) o un errore della tabella.
- [ ] **Allarme batteria spontaneo**: con batteria scarica, verificare che arrivi l'aggiornamento
      `LOW_VOLTAGE` senza richiesta (gli allarmi sono abilitati e rispondono alla richiesta).
- [ ] **Reset completo**: `./run.sh unpair` e nuovo pairing, per verificare che il flusso
      automatico funzioni da zero (voce anche in sezione 3).
- [ ] **`./run.sh re-checks`** (sezione 2 dal vivo, scritto il 13/10 senza Mario): passi `annuncio`
      (manufacturer data), `gatt` (tabella GATT nel report `info`, OAD solo elencato), `sistema`
      (0x0B contro advertisement), `sonno` (conto alla rovescia 0x3861 e 0x3866/0x1802 con Mario
      fermo 45 s, poi ripreso in mano), `porta3` (CHAL/VERS/DEBUG), `gioco` (porte 3, 4, 7 durante
      un mini livello). Confermare le ipotesi del 13/10 su 0x3861/0x3862/0x3866/0x1802 e sulla
      macchina a stati 0x1801/0x1803/0x3812; controllare che il log si rilegga con `decode`.
- [ ] **Voce IA (OpenVoice, 14/10)**: provata solo con una registrazione di riferimento sintetica
      (voce piper "riccardo"). Registrare 20–30 s di una voce vera (la propria o di un familiare
      d'accordo), caricarla dalla dashboard (anche m4a dal telefono) e ascoltare
      `./run.sh voice "..." --openvoice-ref data/voices/<file>.wav` con `--openvoice-tau` 0, 0,3 e 0,6;
      poi una sessione `assistant` con Mario e la voce IA: pause tra le frasi, primo avvio mentre
      carica (voce di riserva), consumo di CPU e memoria con dashboard e Bluetooth attivi.
- [ ] **Suoni dei giochi e pagina Suoni (14/10)**: dalla pagina `/sounds` ascoltare le scelte
      predefinite nei due stili e cambiare quelle che non piacciono; regolare il volume degli
      effetti rispetto alla voce; provare in una sessione dell'assistente se le esclamazioni si
      sovrappongono alla frase successiva; ascoltare nel browser un NSF, un SPC e un modulo
      convertiti; caricare un suono dal telefono. Per i MIDI installare un sintetizzatore
      (`sudo apt install fluidsynth fluid-soundfont-gm`).
- [ ] **Novità del 13/10 scritte senza Mario, da provare dal vivo**: `monitor --combined`
      (accelerometro + gesti e barcode + RGB ricomposti per modo), riconnessione automatica di
      `monitor` e `shell` (spegnere e riaccendere Mario a metà sessione), `info` che salva il JSON
      delle funzioni, nome e volume dalla scheda dispositivo della dashboard, pagina Avventure con
      Mario connesso (quiz con le basette dell'utente), rotazione ignorata quando Mario è in mano,
      scossa che interrompe la voce, ricordi e diario della CLI nel database.

## 2. Reverse engineering ancora da fare

Il 13/10, senza Mario, i log registrati sono stati riletti con `decode` (risultati nelle ipotesi di
`codes.GAME_EVENTS`, nel README e in ASIS) ed è nato lo strumento `./run.sh re-checks` che esegue dal
vivo i passi qui sotto; la cattura dal telefono ha la sua guida in `docs/cattura-app-ufficiale.md`.

- [ ] **Eventi porta 3 (modo EVENTS)**: confermare le ipotesi di `codes.GAME_EVENTS` giocando un
      livello intero (passo `gioco` di `re-checks`, o `monitor`) e incrociando i contatori (monete
      `0x386B`, nemici `0x2042`, giri `0x2022`, monete per basetta `0x20C2`) con il display di Mario.
      Dalla rilettura del 13/10: `0x3861` è un conto alla rovescia (5→1, 8→1, un passo al secondo)
      con Mario fermo, `0x3866`/`0x1802` sembrano sonno (0/2) e risveglio (1/1), `0x1801`/`0x1803`/
      `0x3812` una macchina a stati del livello: da confermare con il passo `sonno`. Restano senza
      nome `0x2009`, `0x200C`, `0x200E`, `0x3857` (rosso/lava?) e il valore 601 di `0x3004`.
- [ ] **Porta 3, modi CHAL / VERS / DEBUG**: mai letti. Passo `porta3` di `re-checks` (valore letto
      e 20 s di notifiche per modo, poi ritorno a EVENTS) oppure `sub 3 0` / `sub 3 1` / `sub 3 3`
      nella shell, uno alla volta.
- [ ] **Porta 4 (0x55, `Events`)**: le scritture standard sono accettate ma inerti; capire cosa
      vi scrive l'app ufficiale o Luigi (cattura dal telefono, sotto). Il passo `gioco` di `re-checks`
      la tiene sottoscritta durante un mini livello e conta quanto arriva.
- [ ] **Porta 7 (0x60, `Telemetry`)**: le scritture vengono ignorate; il passo `gioco` di
      `re-checks` la ascolta per circa due minuti di gioco (oppure `monitor --sub 7:0` a lungo).
- [ ] **Cattura del traffico dell'app ufficiale**: procedura in `docs/cattura-app-ufficiale.md`
      (Android con HCI snoop log e `adb bugreport`, iPhone con il profilo Bluetooth logging e
      PacketLogger, copione di gioco, comandi di analisi). `decode` legge direttamente lo zip del
      bugreport, le sezioni btsnooz del testo (segnalando i dati troncati) e i file `.pklg`: tutto
      provato solo su file sintetici, la prima cattura vera va controllata con `--json`. È il modo
      più rapido per capire le porte 4 e 7 e il significato degli eventi.
- [ ] **System type 0x40**: la proprietà 0x0B risponde `0x40` ("Boost Move Hub" nella tabella LWP3,
      cioè numero di dispositivo 0) mentre l'advertisement dice `0x43` (anche per bricklife). Passi
      `annuncio` e `sistema` di `re-checks` per rileggere entrambi; verificare se Luigi (0x44) e
      Peach (0x45) fanno lo stesso quando ci saranno.
- [ ] **Mario come "central"**: l'advertisement dichiara capacità 0x03 (central e
      peripheral). È il meccanismo con cui Mario si collega a Luigi/Peach. Osservarlo con
      btmon avendo un secondo personaggio.
- [ ] **Servizio OAD Texas Instruments** (`f000ffc0-…`): è l'aggiornamento firmware via radio.
      Da non toccare. Dal 13/10 `info` (e il JSON delle funzioni) elenca la tabella GATT con il
      servizio OAD e le sue caratteristiche, nominate secondo il profilo TI: da leggere dal vivo
      (passo `gatt` di `re-checks`) e confrontare con il profilo (FFC1 Image Identify, FFC2 Image
      Block, FFC5 Control).

## 3. Sistema e Bluetooth del PC

- [ ] **Firmware Broadcom** `BCM20703A1-05ac-8290.hcd`: ricercato di nuovo il 13/10, non è in
      linux-firmware né nei repository comunitari (altri utenti dello stesso MacBook lo
      confermano su Launchpad). Resta l'estrazione dal kext di macOS o dal driver Boot Camp con
      `hex2hcd`, copia in `/lib/firmware/brcm/`, ricarica di `btusb` e verifica con
      `sudo dmesg | grep -i brcm`. Dovrebbe migliorare la coesistenza con il Wi-Fi a 2,4 GHz.
- [ ] **Senza firmware**: tenere il Wi-Fi sui 5 GHz quando si usa Mario; in alternativa
      una chiavetta Bluetooth USB (`./run.sh -i hci1 …`).
- [ ] **Rendere persistente** l'autosospensione disattivata dopo un riavvio: il 13/10 il PC non
      era ancora stato riavviato dalla creazione di `/etc/modprobe.d/btusb.conf` (il parametro del
      modulo diceva ancora `Y`). Al primo riavvio controllare `cat /sys/bus/usb/devices/1-3/power/control`
      (atteso `on`); se torna `auto`, installare `tools/99-mario-btusb.rules` (istruzioni nel file).
- [ ] **Parametri LE del kernel** (solo se il link torna instabile): supervision timeout e
      intervallo via debugfs, comandi nel README.
- [ ] **Agente di pairing**: verificare che dopo l'uscita dell'app l'agente di GNOME torni
      predefinito (BlueZ dovrebbe farlo da solo); altrimenti riavviare `bluetooth.service`.
- [ ] **Reset completo**: `./run.sh unpair` (o `bluetoothctl remove <indirizzo di Mario>`)
      e nuovo pairing, per verificare che il flusso automatico funzioni da zero.

## 4. Miglioramenti software

- [ ] **`decode` su una cattura vera**: i formati btsnoop (monitor, H4 di Android, HCI) sono stati
      provati solo con file costruiti nei test; verificare con una cattura `btmon -w` durante una
      sessione reale e con un HCI snoop log di Android.
- [ ] **Luigi e Peach**: stesso protocollo; verificare che tabelle e nomi reggano
      (system type 0x44/0x45, pantaloni Luigi/Peach già in tabella).
- [ ] **`monitor --json` come sorgente per altri programmi**: documentare lo schema delle righe e
      aggiungere un esempio di consumatore (MQTT o WebSocket, vedi sezione 8).
- [ ] **Shell**: completamento anche dei nomi delle proprietà e dei numeri di porta; `history`.
- [ ] **Pacchetto**: provare `pip install -e .` in un venv pulito e pubblicare una release su GitHub
      (il workflow dei test è attivo; manca il badge nel README e il controllo che la CI sia verde).
- [ ] **Tabelle JSON**: aggiungere le voci della community ancora mancanti (altri barcode di
      pyLegoMario senza nome, pantaloni di Luigi/Peach) e una pagina della dashboard che mostri
      fonte e verifica anche per gesti ed eventi di gioco (oggi solo accessori).

## 5. Dashboard web

- [ ] **Console e "Aggiorna funzioni"** dalla scheda dispositivo: mai provate con Mario connesso.
- [ ] **Pagina "Assistente" nel browser con Mario connesso**: provata solo con backend demo e
      simulazione dei sensori; checklist nella sezione 7.
- [ ] **Test con un browser headless** (Playwright): oggi il JavaScript ha solo il controllo di
      sintassi con `node --check` e la coerenza id/azioni con i template (`tests/test_web_static.py`);
      le pagine vanno provate a mano dopo ogni modifica.
- [ ] Dispositivo attivo selezionabile quando ce n'è più d'uno (Mario, Luigi, Peach) e
      connessioni multiple in parallelo (un `MarioDevice` per personaggio).
- [ ] **Pagina Eventi**: grafici con zoom e scelta dell'intervallo direttamente sul grafico;
      esportazione anche dei valori combinati per modo.
- [ ] Foto o icona per dispositivo e per accessorio.
- [ ] **Token**: scadenza configurabile e revoca; HTTPS (oggi il token viaggia in chiaro sulla
      rete locale).

## 6. Designer di avventure (prima versione fatta il 13/10, cosa manca)

La prima versione è in `mario/adventures/` (vedi ASIS, sezione 4): modello JSON, motore a stati,
libreria degli elementi, servizio, pagine editor e gioco, comando `adventure`, tre esempi.
Provata solo con eventi simulati.

- [ ] **Prova dal vivo**: il "Quiz sulle basette" con le 8 basette dell'utente e il "Percorso a
      ostacoli" con i gesti; tarare le pause dei gesti ripetuti (0,8 s) e i timeout.
- [ ] **Editor visuale**: griglia o percorso trascinabile con le basette, proprietà per casella,
      anteprima; oggi l'editor è a moduli (tabella dei passi) più il JSON per sequenze e azioni.
- [ ] **Modalità "registra"**: costruire il percorso facendo camminare Mario sulle basette reali
      (il motore già tiene la sequenza delle basette lette).
- [ ] **Trigger mancanti**: "salto riuscito" e "colpito" dagli eventi della porta 3 (servono le
      ipotesi confermate della sezione 2), contatori con soglie sul tempo, combinazioni "A oppure B".
- [ ] **Azioni verso Mario**: oltre al volume non c'è nulla finché le porte 4 e 7 restano inerti
      (sezione 7); notifica esterna oggi solo via webhook HTTP (ponte MQTT/WebSocket da scrivere).
- [ ] **Schermo di gioco**: mappa del percorso disegnata, immagini per basette e colori (catalogo
      di icone libere), versione a tutto schermo senza menù.
- [ ] **Partite**: pagina di dettaglio con la cronaca completa e il replay degli eventi.
- [ ] **Multigiocatore**: Mario + Luigi + Peach connessi insieme, turni e gare.
- [ ] **Latenza del flusso** sotto i 200 ms: da misurare dal vivo (evento → voce del PC).

## 7. Mario come dispositivo di I/O per una sessione con un LLM

Idea: usare Mario come "corpo" di un assistente. In ingresso i sensori diventano comandi o
contesto per il modello; in uscita la risposta del modello si manifesta su Mario, oppure Mario
la "accompagna" con sensazioni e comportamenti. La prima tappa è fatta (vedi ASIS, sezione 5):
pacchetto `mario/assistant/`, pagina "Assistente", comando `assistant`, provati con Claude e con
Mario connesso. Via Bluetooth non esiste un canale audio né un comando per display e suoni.

### Prove dal vivo dell'assistente ancora da fare

Servono Mario acceso, `./run.sh assistant` (o `./run.sh web` → pagina Assistente) e la chiave in `.env`.

- [ ] **Ritmo della voce e filtro di stabilità**: ripetere la sessione con Mario in mano,
      basette e colori: gli eventi devono comparire come "in attesa: Mario sta ancora parlando",
      poi uniti in un solo prompt ("Nel frattempo sono successe più cose") e, se vecchi, "eventi
      vecchi scartati"; appoggiare e sollevare Mario non deve più far parlare del colore del
      tavolo; sfilare e rinfilare i pantaloni blu non deve produrre risposte.
- [ ] **Pagina Assistente nel browser con Mario connesso**: faccia che cambia, stato del corpo
      che si aggiorna, intenti in lista, log dei comportamenti, chat in streaming, pulsanti
      "Stop", "Ripeti", "Nuova"; confrontare con la CLI.
- [ ] **Pulsante sulla schiena**: una pressione deve sospendere l'ascolto (suono "listen_off",
      riga "ascolto dei sensori sospeso"), una seconda riattivarlo; con l'ascolto sospeso un
      salto non deve produrre risposte.
- [ ] **Cambio pantaloni**: con altri pantaloni, verificare che Mario si presenti nel nuovo
      carattere (dopo gli 0,8 s di stabilità) e che il carattere resti nel prompt di sistema.
- [ ] **Scossa = ripeti / stop**: scuotere Mario a riposo e sentire ripetuta l'ultima risposta;
      scuoterlo mentre parla deve interromperla (fatto nel codice il 13/10, da sentire dal vivo).
- [ ] **Rotazione in mano**: girare Mario sollevato non deve produrre risposte; sulla piattaforma
      rotante (o su un colore) sì (filtro "solo a terra" del 13/10).
- [ ] **Ricordi e sicurezza**: dire a Mario il proprio nome e verificare che compaia in Ricordi e
      che venga usato nella sessione successiva; provare il limite di 2 minuti e una parola bloccata
      dal vivo; controllare il diario e l'esportazione CSV.
- [ ] **Caduta interrompe**: far "cadere" Mario (bit 0x0800) mentre parla: deve tagliare la voce e
      rispondere subito alla caduta (priorità 2).
- [ ] **Batteria scarica**: con Mario sotto il 15 % verificare la frase "mi sento stanco" una
      sola volta per sessione (regola `batteria`).
- [ ] **Volume su Mario**: con il volume di base a 50 (`./run.sh volume 50` prima di partire)
      ascoltare se i suoni propri di Mario (salto, basetta) cambiano davvero con gioia (≈ 72),
      sonno (5), allarme (alternato); annotare se si sente e se è piacevole o fastidioso.
- [ ] **Scritture sperimentali porta 4 e 7**: dalle impostazioni, `port_payloads` per emozione,
      una alla volta, annotando ogni reazione di Mario (le prove a scatola nera sono state
      negative: aspettativa bassa).
- [ ] **Effetti sonori**: moneta, power-up, allarme, sbadiglio dal PC: volume adeguato rispetto
      alla voce? disattivabili dalle impostazioni (già previsto) se fastidiosi.
- [ ] **Voce configurata**: impostare voce (`Italian+Alex` o altra da `spd-say -L`), velocità e
      tono dalla pagina e verificare che cambino subito e che restino dopo il riavvio.
- [ ] **Voce piper**: `tools/setup_openvoice.sh` (14/10) installa piper e le voci italiane in
      `third_party/piper-voices/`; indicare `it_IT-paola-medium.onnx` nelle impostazioni e confrontare
      la qualità con espeak-ng (provata da terminale con `./run.sh voice`, mai ascoltata durante
      una sessione dell'assistente).
- [ ] **Chiave API dalle impostazioni** (senza `.env`): rinominare temporaneamente `.env`,
      inserire la chiave nel campo della pagina, verificare "chiave salvata nel database", che
      il backend risolto sia `anthropic` e che il campo resti mascherato.
- [ ] **Backend compatibile OpenAI**: installare Ollama con un modello italiano piccolo, impostare
      backend `openai`, base URL `http://localhost:11434/v1` e il nome del modello; confrontare
      qualità dei tag emozione e latenza con Claude.
- [ ] **Disconnessione durante la sessione**: spegnere Mario mentre l'assistente è attivo. CLI:
      deve uscire pulita ("Mario si è disconnesso", volume non ripristinabile). Dashboard: la
      chat deve continuare a funzionare da tastiera, i comportamenti devono tacere, e dopo
      "Connetti" riprendere senza riavviare il server.
- [ ] **Regole modificate dalla pagina**: spegnere `rotazione`, cambiare il testo di `salto`,
      salvare, riavviare il server e verificare che le modifiche siano rimaste (tabella `settings`).
- [ ] **Chiusura pulita della CLI**: `/esci` e Ctrl+C con Mario connesso: nessun traceback,
      volume riportato al valore iniziale prima della disconnessione (log `assistant-*.txt`).
- [ ] **Sessione lunga**: 20–30 minuti di gioco con l'assistente attivo: memoria del processo,
      coda SSE, cronologia che cresce (limite 20 messaggi verso il modello), costi API.

### Far parlare Mario direttamente, senza la voce del PC: piste rimaste

Le prove a scatola nera sulle porte 4 e 7, sulle azioni busy e sulle basette su schermo sono
state fatte con esito negativo (dettagli in ASIS, sezione 2). Restano, in ordine di promessa:

- [ ] **Basette stampate su carta**: le basette originali sono stampe, quindi una stampa opaca in
      scala 1:1 dovrebbe funzionare (la community lo riporta). Fonte dei disegni: foto o
      scansione ad alta risoluzione delle basette reali, stampa su carta opaca, prova con
      `./run.sh monitor` (porta 1). Se funziona: un "tastierino" di clip azionabile a mano e, per
      il controllo dal PC, una striscia o un disco di barcode mosso da un servomotore sotto Mario.
- [ ] **Colore con luce esterna**: illuminare un foglio bianco sotto Mario con una luce
      fortemente colorata (torcia del telefono con un filtro, lampadina RGB) e guardare in
      `monitor` se la porta 1 riporta un colore (lava, acqua, ghiaccio…). Se sì, una lampadina
      RGB comandata dal PC basta a far dire a Mario le sue frasi sui terreni.
- [ ] **Schermo e-ink** (riflessivo, opaco come la carta): se la carta funziona, un piccolo
      display e-paper comandato dal PC sotto i piedi di Mario è il modo pulito di scegliere la
      clip; da verificare che il contrasto regga anche all'infrarosso.
- [ ] **Scansione dei sottocomandi di Port Output** su porta 4 e 7: 0x01, 0x02, 0x05, 0x06,
      0x07, 0x08 con payload corti; registrare feedback ed errori e ogni reazione fisica
      (aspettativa bassa, costo un minuto con `probe_port4.py`).
- [ ] **Azioni e proprietà residue**: con prudenza 0x03/0x04 (VCC port on/off); scrittura del
      nome e `reset` (operazione 0x04) sulle proprietà scrivibili, per vedere se producono un
      feedback sonoro (improbabile ma gratis).
- [ ] **Porta virtuale** (0x61) fra 4 e 7: improbabile, a costo zero.
- [ ] **Cattura dell'app ufficiale** (vedi sezione 2): se nella cattura un Port Output verso la
      4 o la 7 è seguito da un suono di Mario, il comando si copia pari pari. Vale anche per il
      collegamento Mario–Luigi: lì Mario parla e suona a comando dell'altro personaggio.
- [ ] **Reverse engineering del firmware** (se i comandi non esistono): il chip è un
      Texas Instruments con servizio OAD (`f000ffc0-…`), quindi il firmware si aggiorna via
      radio con immagini firmate/cifrate dall'app. Passi: recuperare l'immagine firmware
      dall'app ufficiale (APK/IPA), identificarne il formato OAD, verificare se è firmata;
      analisi statica (Ghidra, ARM Cortex-M) per trovare le tabelle suoni/animazioni e il
      dispatcher LWP3; in alternativa accesso hardware via cJTAG sulla scheda. Rischi:
      brick del giocattolo, perdita di garanzia, aspetti legali del bypass della firma.
      Da fare solo su un Mario "da laboratorio".
- [ ] **Audio in streaming verso Mario**: richiederebbe un firmware modificato con un
      canale GATT per PCM/ADPCM e la pipeline verso il DAC/altoparlante; valutare banda
      BLE (≈ 50–100 kbit/s utili) e codec (ADPCM 4 bit, 8 kHz ≈ 32 kbit/s).
- [ ] **Senza firmware modificato**: la voce "diretta" resta quella delle clip: il PC dice le
      frasi del modello e Mario le "commenta" con le proprie clip scatenate dai comandi trovati
      o dalle basette (da integrare come comportamento in `behaviours.py`, emozione → clip).

### Evoluzioni dell'assistente

- [ ] **Ingresso vocale**: microfono del PC → riconoscimento (whisper.cpp / vosk, offline)
      così il bambino parla a Mario; il pulsante o la scossa avviano l'ascolto del microfono.
- [ ] **Storia interattiva**: il modello propone azioni fisiche ("salta!", "trova la basetta
      rossa") e la sessione le verifica con i sensori prima di continuare: il motore delle avventure
      (sezione 6) è il ponte; serve uno schema di uscita strutturato (JSON) oltre ai tag, o un tag
      `[aspetta: salto]` sul modello di `[ricorda: …]`.
- [ ] **Modello per i sensori**: usare `claude-haiku-5-5` per i turni dei sensori (prima frase in
      circa 1 s) e sonnet per la conversazione; oggi il modello è unico.
- [ ] **Sicurezza, seconda tappa**: filtro anche sui contenuti del modello oltre alle parole esatte
      (lista di temi), riepilogo giornaliero per i genitori, limite di messaggi oltre che di minuti.
- [ ] **Ricordi**: riassunto automatico quando superano i venti, data di scadenza, ricordi per
      bambino (più profili).
- [ ] **Clip di Mario come comportamento**: se una pista della sezione precedente funziona,
      collegare emozione → clip in `behaviours.py`.

## 8. Idee

- Controller: usare gesti e pantaloni di Mario come input per un gioco o un emulatore
  (precedenti pubblici: Mario come controller di Super Mario Bros).
- Registrare una partita intera e ricostruire il livello (sequenza di barcode e colori)
  come log leggibile.
- Far dialogare due personaggi tramite il PC (Mario e Luigi) emulando ciò che fanno via
  radio tra loro, una volta capito il formato della porta 4.
- Esporre Mario via MQTT o WebSocket per domotica e notifiche.
- Musica di sottofondo nelle avventure: un'azione "music" che avvia e ferma in ciclo un brano
  dell'archivio (NSF, SPC, MP3 convertiti) a volume basso, sotto voce ed effetti.
- Voce IA con le emozioni: OpenVoice cambia il timbro ma non il tono; una registrazione di riferimento
  per emozione (allegra, triste, spaventata) scelta in base al tag della frase darebbe più
  espressività (le impronte si calcolano una volta e restano in cache).
- Voce IA più veloce: il marchio d'acqua è già spento; resta da misurare l'effetto dell'opzione `--threads` del processo della voce
  (oggi torch usa i 4 thread logici dei 2 core) mentre Bluetooth e dashboard sono attivi.
