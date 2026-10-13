# TODO — lavoro aperto e idee

Lista di lavoro del progetto LEGO Mario via Bluetooth LE. Stato al 13 ottobre 2026.
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

## 2. Reverse engineering ancora da fare

- [ ] **Eventi porta 3 (modo EVENTS)**: confermare le ipotesi di `codes.GAME_EVENTS` giocando un
      livello intero con `monitor` attivo e incrociando i contatori (monete `0x386B`, nemici
      `0x2042`, giri `0x2022`, monete per basetta `0x20C2`) con il display di Mario; dare un nome
      ai codici ancora generici (`0x1801`, `0x1802`, `0x2009`, `0x200C`, `0x200E`, `0x3857`,
      `0x3862`, `0x3866`, `0x3861`).
- [ ] **Porta 3, modi CHAL / VERS / DEBUG**: mai letti. `sub 3 0`, `sub 3 1`, `sub 3 3`
      nella shell (uno alla volta: una porta ha un solo modo attivo per volta).
- [ ] **Porta 4 (0x55, `Events`)**: le scritture standard sono accettate ma inerti; capire cosa
      vi scrive l'app ufficiale o Luigi (cattura dal telefono, sotto).
- [ ] **Porta 7 (0x60, `Telemetry`)**: le scritture vengono ignorate; leggerla a lungo durante il
      gioco (`monitor --sub 7:0`) per vedere se trasmette mai qualcosa in ingresso.
- [ ] **Cattura del traffico dell'app ufficiale**: attivare l'HCI snoop log su un telefono
      Android, giocare con l'app LEGO Super Mario, estrarre il btsnoop e decodificarlo con
      `lwp3.parse_all` + `describe`. È il modo più rapido per capire le porte 4 e 7 e il
      significato degli eventi. Serve uno strumento (vedi punto software "decode").
- [ ] **System type 0x40**: la proprietà 0x0B risponde `0x40` mentre l'advertisement dice
      `0x43`. Verificare se Luigi (0x44) e Peach (0x45) fanno lo stesso.
- [ ] **Mario come "central"**: l'advertisement dichiara capacità 0x03 (central e
      peripheral). È il meccanismo con cui Mario si collega a Luigi/Peach. Osservarlo con
      btmon avendo un secondo personaggio.
- [ ] **Servizio OAD Texas Instruments** (`f000ffc0-…`): è l'aggiornamento firmware via
      radio. Da non toccare, ma documentarne le caratteristiche nel report `info`.

## 3. Sistema e Bluetooth del PC

- [ ] **Firmware Broadcom** `BCM20703A1-05ac-8290.hcd`: non è nei pacchetti Debian né nei
      repository comunitari. Estrarlo dal kext di macOS o dal driver Boot Camp con
      `hex2hcd`, copiarlo in `/lib/firmware/brcm/`, ricaricare `btusb` e verificare con
      `sudo dmesg | grep -i brcm`. Dovrebbe migliorare la coesistenza con il Wi-Fi a 2,4 GHz.
- [ ] **Senza firmware**: tenere il Wi-Fi sui 5 GHz quando si usa Mario; in alternativa
      una chiavetta Bluetooth USB (`./run.sh -i hci1 …`).
- [ ] **Rendere persistente** l'autosospensione disattivata dopo un riavvio: controllare
      `cat /sys/bus/usb/devices/1-3/power/control` (atteso `on`); se torna `auto`, serve
      una regola udev oltre al file in `/etc/modprobe.d/`.
- [ ] **Parametri LE del kernel** (solo se il link torna instabile): supervision timeout e
      intervallo via debugfs, comandi nel README.
- [ ] **Agente di pairing**: verificare che dopo l'uscita dell'app l'agente di GNOME torni
      predefinito (BlueZ dovrebbe farlo da solo); altrimenti riavviare `bluetooth.service`.
- [ ] **Reset completo**: `./run.sh unpair` (o `bluetoothctl remove <indirizzo di Mario>`)
      e nuovo pairing, per verificare che il flusso automatico funzioni da zero.

## 4. Miglioramenti software

- [ ] **Strumento `decode`**: comando CLI che legge un file btsnoop (`btmon -r`), un log
      di `monitor`/`shell` o righe esadecimali e li decodifica con `lwp3.describe`,
      riusando lo stato delle porte da `docs/capabilities-fw-6.5.01.json`. Serve per il
      punto "cattura dal telefono".
- [ ] **Valori combinati (0x46) decodificati nella libreria**: `MarioDevice` tiene lo stato per
      dataset (maschera con byte alto per primo, multi-update parziale) e li passa ai listener
      come valori normali; oggi lo fa solo `tools/live_checks.py`. Poi `monitor` può mostrare
      accelerometro e gesti insieme.
- [ ] **Strumenti dal vivo nella CLI**: `live_checks`, `catalog_tiles` e `probe_port4` come
      sottocomandi di `./run.sh` (oggi vanno lanciati con il python del venv).
- [ ] **Riconnessione automatica**: in `monitor` e `shell`, quando Mario si disconnette,
      riprendere la scansione e riagganciarlo senza riavviare il comando.
- [ ] **Test con un `BleakClient` finto**: coprire `request()` con i predicati di risposta,
      la gestione degli errori generici, la scoperta e il flusso di pairing.
- [ ] **Test dei backend HTTP dell'assistente**: `AnthropicBackend` e `OpenAICompatBackend` non
      hanno test (solo la demo): avviare un server HTTP locale finto che risponde con eventi SSE
      (`content_block_delta`, `[DONE]`, errori 400/401, connessione interrotta) e verificare il
      parser, gli errori `LLMError` e la chiusura dello stream quando si preme Stop.
- [ ] **Test di `assistant/service.py`**: thread che consuma il flusso del worker, salvataggio
      dei messaggi, `update_settings` con regole e payload (oggi coperto solo via API Flask).
- [ ] **Test di `tts.Speaker._command`**: riga di comando per `spd-say` ed `espeak-ng` con le
      variazioni di velocità/tono per emozione e il blocco ai limiti ±100; sintesi dei file WAV.
- [ ] **Test della CLI `assistant`**: `Printer` (righe laterali in coda durante lo streaming) e
      comandi `/stop`, `/emozione`, `/ascolto` con sessione e voce finte.
- [ ] **`monitor --json`**: una riga JSON per evento (porta, modo, valori grezzi,
      decodifica), per collegare Mario ad altri programmi.
- [ ] **`monitor --duration N`** e `--no-battery`.
- [ ] **Shell**: cronologia e completamento con `readline`, `help <comando>`, comando
      `log` per cambiare file di log al volo.
- [ ] **`info`**: salvare automaticamente il JSON in `docs/capabilities-fw-<versione>.json`
      e mostrare nel report se Mario è associato.
- [ ] **Luigi e Peach**: stesso protocollo; verificare che tabelle e nomi reggano
      (system type 0x44/0x45, pantaloni Luigi/Peach già in tabella).
- [ ] **Packaging**: `pyproject.toml` con entry point `mario`, versione, dipendenze
      (`bleak>=3`, `dbus-fast` esplicito), e un test di fumo in CI che non usi il Bluetooth.
- [ ] **Repository di riferimento**: i cloni `LEGO-Mario-Reveng/`, `legomario.py/` e
      `pyLegoMario/` nella radice sono esclusi da git; restano da spostare in `third_party/`;
      `logs/pylegomario-src/` è ridondante e si può cancellare.
- [ ] **Pulizia**: rinominare i log vecchi; aggiungere `tools/gatt_notify_test.sh` al README o
      toglierlo.
- [ ] **Tabelle**: portare `GESTURES`, `BARCODES`, `COLORS`, `PANTS`, `GAME_EVENTS` in un
      file dati (JSON) con campo "fonte" e "verificato dal vivo sì/no".

## 5. Dashboard web

- [ ] **Console e "Aggiorna funzioni"** dalla scheda dispositivo: mai provate con Mario connesso.
- [ ] **Pagina "Assistente" nel browser con Mario connesso**: provata solo con backend demo e
      simulazione dei sensori; checklist nella sezione 7.
- [ ] Nessun test automatico del JavaScript (`static/app.js`): solo `node --check` per la
      sintassi e il controllo che gli id usati esistano nei template. Provare le pagine a mano
      dopo ogni modifica, oppure aggiungere un test con un browser headless (Playwright).
- [ ] Dispositivo attivo selezionabile quando ce n'è più d'uno (Mario, Luigi, Peach) e
      connessioni multiple in parallelo (un `MarioDevice` per personaggio).
- [ ] Pagina "Eventi" dedicata con esportazione CSV/JSON e grafici (batteria nel tempo,
      istogramma gesti).
- [ ] Modifica di nome e volume direttamente dalla scheda dispositivo; foto o icona per
      dispositivo e per accessorio.
- [ ] Autenticazione minima se la dashboard viene esposta oltre `127.0.0.1`.
- [ ] Sostituire il server di sviluppo Flask con `waitress` o `gunicorn` (SSE con thread).

## 6. Designer di avventure (cosa serve per costruirlo)

Obiettivo: comporre nella dashboard un'avventura fatta di elementi del mondo di Mario
(basette con barcode, colori del terreno, pantaloni, nemici, power-up) e farla "girare"
con il Mario fisico: il PC riconosce dove si trova Mario, cosa indossa e cosa fa, e
reagisce con suoni, immagini, punteggi e missioni. I contatori della porta 3 (monete, nemici,
giri) e i codici delle basette dell'utente, già decodificati, sono la base di partenza.

- [ ] **Modello dati** (SQLite): `adventures` (nome, descrizione, autore, versione),
      `adventure_steps` (ordine, tipo di trigger, condizione, azione, timeout, testo),
      `adventure_runs` (partita: inizio, fine, punteggio, esito) e `run_events`
      (collegamento agli eventi reali). Esportazione/importazione in JSON per condividere.
- [ ] **Libreria di elementi** a partire dagli Accessori: basette (barcode), colori
      terreno, pantaloni (poteri), nemici e power-up con icona, descrizione, "effetto"
      nel gioco originale (monete, danno, velocità). Unire le tabelle `codes.*` e il DB.
- [ ] **Trigger disponibili**: barcode letto, colore sotto i piedi, pantaloni indossati,
      gesto (salto, scossa, urto, rotazione), pulsante, evento di gioco della porta 3
      (monete, nemici, giri, tempo scaduto), timer, sequenze (A poi B entro N secondi), contatori.
- [ ] **Azioni**: messaggio a schermo, suono sul PC, punteggio, avanzamento di missione,
      cambio volume di Mario, notifica esterna (MQTT/WebSocket). Le porte 4 e 7 sono inerti con
      i comandi standard: niente azioni verso Mario oltre al volume finché non si trova altro.
- [ ] **Editor visuale**: griglia o percorso trascinabile con le basette (start, blocchi,
      nemici, traguardo), proprietà per casella, anteprima del percorso; modalità
      "registra" che costruisce il percorso facendo camminare Mario sulle basette reali.
- [ ] **Motore di esecuzione**: macchina a stati che ascolta il flusso eventi del worker,
      valuta le condizioni del passo corrente, applica le azioni, gestisce timeout e
      ripetizioni; log della partita e replay.
- [ ] **Schermo di gioco**: pagina a tutto schermo con mappa, posizione stimata di Mario,
      missione corrente, punteggio, tempo, suoni; versione "bambino" con icone grandi.
- [ ] **Contenuti**: 3 avventure di esempio (caccia al tesoro con colori, percorso a
      ostacoli con gesti, quiz sui nemici), catalogo di immagini libere o icone generate.
- [ ] **Prerequisiti tecnici**: stima della posizione (sequenza di basette), riconoscimento del
      "salto riuscito" e del "colpito" dagli eventi della porta 3, latenza del flusso sotto i 200 ms.
- [ ] **Multigiocatore**: Mario + Luigi + Peach connessi insieme, turni e gare.

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
- [ ] **Scossa = ripeti**: scuotere Mario a riposo e sentire ripetuta l'ultima risposta; scuoterlo
      mentre parla e decidere se deve invece fermarsi (voce "Interruzione fisica" sotto).
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

- [ ] **Rotazione "nervosa"**: tenendo Mario in mano la rotazione scatta spesso; idee: ignorarla
      mentre Mario è sollevato, spegnerla di default o usarla solo come contesto (il colore e i
      pantaloni hanno già il filtro di stabilità, da provare dal vivo).
- [ ] **Voce migliore**: `piper` (voci neurali italiane, offline) al posto di espeak-ng;
      la classe `Speaker` va estesa con un motore in più. In alternativa un TTS in rete.
- [ ] **Ingresso vocale**: microfono del PC → riconoscimento (whisper.cpp / vosk, offline)
      così il bambino parla a Mario; il pulsante o la scossa avviano l'ascolto del microfono.
- [ ] **Interruzione fisica**: scossa o urto durante una risposta = stop della voce
      (oggi la scossa ripete e la caduta interrompe; valutare: scossa = ripeti a riposo,
      stop mentre parla).
- [ ] **Memoria tra sessioni**: nome del bambino, cose dette, preferenze (tabella nel DB,
      riassunto nel prompt di sistema); comando "dimentica".
- [ ] **Storia interattiva**: il modello propone azioni fisiche ("salta!", "trova la basetta
      rossa") e la sessione le verifica con i sensori prima di continuare: è il ponte con il
      designer di avventure (sezione 6). Serve uno schema di uscita strutturato (JSON) oltre ai tag.
- [ ] **Sicurezza per bambini**: istruzioni nel prompt già prudenti; aggiungere limiti di
      tempo per sessione, filtro delle parole, log consultabile dai genitori.
- [ ] **Latenza**: con `claude-sonnet-5-5` la risposta completa arriva in 2–4 s; misurare il
      tempo alla prima frase pronunciata e provare `claude-haiku-5-5`; pronuncia già per
      frase, non a fine risposta.
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
