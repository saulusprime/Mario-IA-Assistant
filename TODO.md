# TODO — punti aperti e idee

Lista di lavoro del progetto LEGO Mario via Bluetooth LE. Stato al 12 ottobre 2026.
Le voci sono raggruppate per tipo; dentro ogni gruppo le prime sono le più utili.
Il contesto di ogni punto è in [HANDOFF.md](HANDOFF.md). Legenda: `[ ]` da fare, `[~]` in parte
verificato, `[x]` fatto.

## 1. Da verificare dal vivo (bastano Mario e `./run.sh monitor`)

Sessione guidata del 12/10 alle 19:16 (`tools/live_checks.py`, log `logs/live-checks-20261012-191602.txt`).

- [x] **Pantaloni (porta 2, modo PANT)**: cambio dal vivo verificato il 12/10 con i soli pantaloni
      blu (0x21): sfilati → 0x00, infilati → di nuovo 0x21, ma durante l'inserimento i contatti
      danno **codici transitori** (visti 0x18 "Peach" e 0x01). Conseguenza: l'assistente ora
      considera un valore di pantaloni (e di colore) solo se resta stabile per 0,8 s. Da rifare con
      altri pantaloni veri quando ci saranno, per confrontare i codici con `codes.PANTS`.
- [x] **Barcode (porta 1, modo TAG)**: verificati `0xB8` Start Mario, `0x02` Goomba (due basette
      uguali), `0x2E` Nuvola, `0x14` Rotazione; aggiunti `0x00F5` Clown Koopa (71360), `0x0160`
      aereo e `0x015B` carrello minerario (71425). Le 8 basette dell'utente sono catalogate nella
      tabella e nella pagina Accessori. Resta da capire se `0x0099` della community è un altro
      Bowser Jr.
- [~] **Gesti**: osservati con accelerometro e gesti insieme (modalità combinata): salto `0x8000`
      scatta anche sollevando, appoggiando o colpendo Mario; la scossa produce `0xA111` (urto +
      scossa + rotazione + inversione + salto); girare Mario in piedi dà `0x0100` + salto + il **bit
      `0x0002`**; inclinarlo `0x0100`; cadere da pochi cm `0x2100`; fermo in mano nessun gesto.
      Il bit `0x0040` compare con l'urto quando Mario viene appoggiato su una basetta (atterraggio).
      Restano senza nome `0x0008`, `0x0020`, `0x0080`: ripetere con movimenti più "isolati"
      (`live_checks.py --steps gesti`) e, per il `0x0002`, girare Mario senza sollevarlo.
- [~] **Scanner in modo RGB (porta 1 modo 1)**: in modalità combinata TAG+RGB Mario aggiorna solo il
      primo dei tre canali (0..189, cresce sulle superfici chiare); gli altri due restano fissi (117,
      130). Da ripetere con il solo modo RGB (`monitor --sub 1:1`) per capire se i tre canali si
      muovono. Intanto i **codici colore del modo TAG sono verificati**: bianco, nero, giallo, nougat,
      verde, rosso, blu.
- [x] **Pulsante**: aggiornamenti `BUTTON` premuto/rilasciato arrivano (12/10).
- [x] **Allarmi hub**: tutti e quattro (`LOW_VOLTAGE`, `HIGH_CURRENT`, `LOW_SIGNAL_STRENGTH`,
      `OVER_POWER_CONDITION`) rispondono "nessun problema" alla richiesta e accettano l'abilitazione;
      resta da vedere se con batteria scarica arriva l'aggiornamento spontaneo.
- [x] **RSSI**: la proprietà risponde sempre 0 e gli aggiornamenti sono sempre 0 anche allontanando
      Mario: non implementata dal firmware. Usare l'RSSI dell'advertising in scansione.
- [x] **Modalità combinata**: funziona su porta 0 (RAW+GEST) e porta 1 (TAG+RGB). Attenzione: Mario
      manda la maschera dei dataset con il byte alto per primo (`00 1f`) e aggiorna solo i dataset
      cambiati (multi-update): il ricevitore deve tenere l'ultimo valore di ciascuno. Corretti
      `lwp3.describe` e `tools/live_checks.py`.
- [x] **Spegnimento e disconnessione da PC**: `DISCONNECT` → `WILL_DISCONNECT` e link chiuso in
      0,2 s, Mario resta acceso e si ricollega senza premere il tasto; `SWITCH_OFF` →
      `WILL_SWITCH_OFF` (×2) + `WILL_DISCONNECT` (×2), scollegato in 6 s, Mario spento.

## 2. Reverse engineering ancora da fare

- [~] **Eventi porta 3 (modo EVENTS)**: 22 codici in `codes.GAME_EVENTS` con ipotesi; il 12/10
      sono emersi contatori legati alle basette (`0x2042` Goomba, `0x2022` giri sulla Rotazione,
      `0x20C2` con la basetta 0x0160) e la sequenza di tempo scaduto. Da confermare giocando un
      livello intero e, per i contatori, incrociando con il display di Mario. Dare un significato ai codici in
      `codes.GAME_EVENTS` (`0x01/0x13` = basetta letta con il suo codice, `0x38/0x61`
      contatore 1→8, `0x30/0x04` = 601, `0x38/0x03` = 100, …). Metodo: giocare un livello completo con `monitor` attivo annotando
      gli eventi (moneta, nemico, caduta, traguardo) con l'orario.
- [ ] **Porta 3, modi CHAL / VERS / DEBUG**: mai letti. `sub 3 0`, `sub 3 1`, `sub 3 3`
      nella shell (uno alla volta: una porta ha un solo modo attivo per volta).
- [ ] **Porta 4 (0x55, modo Events, 2 × int16, anche in uscita)**: non ha mai trasmesso.
      Capire cosa scrive l'app ufficiale: idea sotto (cattura dal telefono).
- [ ] **Porta 7 (0x60, modo Telemetry, 16 byte, anche in uscita)**: nuova dal firmware 6.x,
      non documentata da nessuno. `monitor --sub 7:0` durante il gioco; poi provare scritture
      prudenti con `out 7 0 <hex>`.
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
- [ ] **Reset completo**: `./run.sh unpair` (o `bluetoothctl remove F4:60:77:5A:20:11`)
      e nuovo pairing, per verificare che il flusso automatico funzioni da zero.

## 4. Miglioramenti software

- [ ] **Strumento `decode`**: comando CLI che legge un file btsnoop (`btmon -r`), un log
      di `monitor`/`shell` o righe esadecimali e li decodifica con `lwp3.describe`,
      riusando lo stato delle porte da `docs/capabilities-fw-6.5.01.json`. Serve per il
      punto "cattura dal telefono".
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
- [~] **Repository di riferimento**: i cloni `LEGO-Mario-Reveng/`, `legomario.py/` e
      `pyLegoMario/` nella radice sono esclusi da git (`.gitignore`, 12/10); restano da spostare
      in `third_party/`; `logs/pylegomario-src/` è ridondante e si può cancellare.
- [x] **Git**: repository inizializzato il 12/10 sera (ramo `main`, identità locale), primo
      commit con codice, test e documentazione allineata. Esclusi: `.venv/`, `logs/`, `data/`,
      `.env`, i cloni di terze parti.
- [ ] **Pulizia**: spostare `logs/pylegomario-src/` in `docs/third_party/` o rimuoverlo;
      rinominare i log vecchi; aggiungere `tools/gatt_notify_test.sh` al README o toglierlo.
- [ ] **Tabelle**: portare `GESTURES`, `BARCODES`, `COLORS`, `PANTS`, `GAME_EVENTS` in un
      file dati (JSON) con campo "fonte" e "verificato dal vivo sì/no".

## 5. Dashboard web (appena nata)

- [x] Provare la dashboard con Mario connesso (fatto il 11/10 alle 17:45: connessione,
      monitor, accessori "visti" aggiornati da soli). Da provare ancora: console e
      "Aggiorna funzioni" dalla scheda dispositivo.
- [~] Pagina "Assistente": provata sull'host con backend demo, voce e simulazione dei
      sensori (18:20). Da provare nel browser con Mario connesso: checklist nella sezione 7.
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
reagisce con suoni, immagini, punteggi e missioni.

- [ ] **Modello dati** (SQLite): `adventures` (nome, descrizione, autore, versione),
      `adventure_steps` (ordine, tipo di trigger, condizione, azione, timeout, testo),
      `adventure_runs` (partita: inizio, fine, punteggio, esito) e `run_events`
      (collegamento agli eventi reali). Esportazione/importazione in JSON per condividere.
- [ ] **Libreria di elementi** a partire dagli Accessori: basette (barcode), colori
      terreno, pantaloni (poteri), nemici e power-up con icona, descrizione, "effetto"
      nel gioco originale (monete, danno, velocità). Unire le tabelle `codes.*` e il DB.
- [ ] **Trigger disponibili**: barcode letto, colore sotto i piedi, pantaloni indossati,
      gesto (salto, scossa, urto, rotazione), pulsante, evento di gioco della porta 3
      (quando decodificato), timer, sequenze (A poi B entro N secondi), contatori.
- [ ] **Azioni**: messaggio a schermo, suono sul PC, punteggio, avanzamento di missione,
      cambio volume di Mario, invio di comandi a Mario sulle porte scrivibili (4 e 7)
      appena se ne capisce il formato, notifica esterna (MQTT/WebSocket).
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
- [ ] **Prerequisiti tecnici**: decodifica affidabile di barcode e pantaloni (sezione 1),
      stima della posizione (sequenza di basette), riconoscimento del "salto riuscito" e
      del "colpito" dagli eventi della porta 3, latenza del flusso sotto i 200 ms.
- [ ] **Multigiocatore**: Mario + Luigi + Peach connessi insieme, turni e gare.

## 7. Mario come dispositivo di I/O per una sessione con un LLM

Idea: usare Mario come "corpo" di un assistente. In ingresso i sensori (gesti, pantaloni,
basette, pulsante) diventano comandi o contesto per il modello; in uscita la risposta del
modello si manifesta su Mario (voce, suoni, espressioni sul display, luci) oppure Mario
"accompagna" la risposta con sensazioni e comportamenti (gioia, sorpresa, attesa,
sonno).

Cosa Mario sa fare via Bluetooth oggi (protocollo LWP3): leggere i sensori, cambiare
volume e nome, ricevere scritture sulle porte 4 e 7 dal significato ignoto. **Non**
esiste un canale audio né un comando documentato per display, suoni o animazioni: il
firmware li gestisce internamente in base al gioco. Il percorso quindi è a tappe.

Stato all'11/10 sera: prima tappa fatta (pacchetto `mario/assistant/`, pagina "Assistente",
comando `assistant`; descrizione nel README). Provata con backend demo, con Claude e con Mario
connesso da terminale; la lista delle prove dal vivo ancora da fare è più sotto.

- [x] **Ingresso**: regole sensori → intenti (`intents.py`): salto, caduta, rotazione, urto
      (spento), scossa = ripeti, pulsante = ascolto on/off, pantaloni con carattere, basette
      per famiglia (nemico/premio/start/traguardo/amico), terreno, batteria scarica, moneta
      (ipotesi, spenta). Pausa minima per regola, modificabili dalla dashboard.
- [x] **Uscita indiretta**: voce dal PC (`spd-say`/`espeak-ng`, velocità e tono per emozione),
      effetti sonori sintetizzati, volume di Mario che segue l'emozione e torna alla base.
      Il nome non viene toccato (scrittura in flash, visibile solo in scansione).
- [~] **Sensazioni e comportamenti**: motore `behaviours.py` fatto (script per emozione,
      livelli relativi alla base, sincronizzati con la voce, interrompibili). Le verifiche su
      Mario (volume udibile, "occupato", porte 4 e 7) sono nella checklist qui sotto.
- [x] **Architettura software**: sessione con streaming, coda di frasi per la voce, loop
      eventi → prompt, intenti in attesa finché Mario parla, impostazioni e cronologia nel DB,
      allow-list dei comandi verso Mario, chiave in `.env`. Provata con Claude
      (`claude-sonnet-5-5`, 18:47) e con Mario connesso da terminale (18:50).

### Prove dal vivo dell'assistente ancora da fare (una per riga, da spuntare)

Servono Mario acceso, `./run.sh assistant` (o `./run.sh web` → pagina Assistente) e la chiave in `.env`.

- [ ] **Ritmo della voce dopo la correzione**: ripetere la sessione delle 18:50 (girare Mario,
      appoggiarlo su basette e colori) e verificare che la voce non resti più indietro: gli
      eventi devono comparire come "in attesa: Mario sta ancora parlando", poi uniti in un solo
      prompt ("Nel frattempo sono successe più cose") e, se vecchi, "eventi vecchi scartati".
- [ ] **Pagina Assistente nel browser con Mario connesso**: faccia che cambia, stato del corpo
      che si aggiorna, intenti in lista, log dei comportamenti, chat in streaming, pulsanti
      "Stop", "Ripeti", "Nuova"; confrontare con la CLI.
- [ ] **Pulsante sulla schiena**: una pressione deve sospendere l'ascolto (suono "listen_off",
      riga "ascolto dei sensori sospeso"), una seconda riattivarlo; con l'ascolto sospeso un
      salto non deve produrre risposte.
- [ ] **Cambio pantaloni**: mettere i pantaloni Fuoco/Gatto/… e verificare che Mario si presenti
      nel nuovo carattere e che il carattere resti nel prompt di sistema nelle risposte dopo.
- [ ] **Scossa = ripeti**: scuotere Mario a riposo e sentire ripetuta l'ultima risposta; scuoterlo
      mentre parla e decidere se deve invece fermarsi (voce "Interruzione fisica" sotto).
- [ ] **Caduta interrompe**: far "cadere" Mario (bit 0x0800) mentre parla: deve tagliare la voce e
      rispondere subito alla caduta (priorità 2).
- [ ] **Batteria scarica**: con Mario sotto il 15 % verificare la frase "mi sento stanco" una
      sola volta per sessione (regola `batteria`).
- [ ] **Volume su Mario**: con il volume di base a 50 (`./run.sh volume 50` prima di partire)
      ascoltare se i suoni propri di Mario (salto, basetta) cambiano davvero con gioia (≈ 72),
      sonno (5), allarme (alternato); annotare se si sente e se è piacevole o fastidioso.
- [x] **Indicazione "occupato"** (azioni hub 0x05/0x06): provata il 12/10, Mario risponde
      `INVALID_USE`: nessun effetto possibile; l'opzione nella pagina è segnata come inerte.
- [ ] **Scritture sperimentali porta 4 e 7**: dalle impostazioni, `port_payloads` per emozione
      (es. `{"gioia": "4:01 00 00 00"}`), una alla volta, annotando ogni reazione di Mario; prima
      leggere la porta con `monitor --sub 7:0` per vedere se trasmette qualcosa.
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

### Far parlare Mario direttamente, senza la voce del PC: prove a scatola nera

Premessa: la voce di Mario ("It's-a me!", "Yahoo!") è fatta di clip audio nel firmware,
scatenate dalla logica di gioco. LWP3 non ha un canale audio e nella mappa delle funzioni
letta dal dispositivo (`docs/capabilities-fw-6.5.01.json`) le sole porte scrivibili sono la 4
(`Events`, 2 × int16) e la 7 (`Telemetry`, 16 × int8); la porta 3 (EVENTS) è solo in ingresso
e i servizi GATT sono quattro (GATT, GAP, LEGO, OAD), senza caratteristiche nascoste. Quindi
"far parlare Mario" oggi vuol dire trovare un comando che faccia partire una clip già
presente. Prove in ordine di costo, con `./run.sh monitor` in un secondo terminale per vedere
cosa Mario ritrasmette, volume alto (`./run.sh volume 100`) e batteria carica. Rischio basso:
via LWP3 non si scrive la flash (tranne nome e proprietà); mai l'azione 0x2F né il servizio OAD.

- [x] **Eventi "riflessi" sulla porta 4**: fatto il 12/10 (`tools/probe_port4.py`, 19 codici
      fuori livello + 10 in livello con rilettura). Esito negativo: WriteDirectModeData accettato
      con feedback "completato", ma la porta 4 non conserva il valore (rilettura `00 00 00 00`),
      nessun eco, contatore monete fermo, nessuna reazione visibile o udibile (riferito
      dall'utente). 0x50 INVALID_USE, 0x00 non riconosciuto. Resta possibile che la porta 4 voglia
      un altro spazio di codici (quelli dell'app o del collegamento con Luigi): serve la cattura.
- [ ] **Scansione dei sottocomandi di Port Output** su porta 4 e 7: 0x00, 0x01, 0x02, 0x05,
      0x06, 0x07, 0x08, 0x50 (WriteDirect), 0x51 (WriteDirectModeData), con payload corti;
      registrare il feedback 0x82, gli errori 0x05 ("command not recognized" distingue i
      sottocomandi accettati) e ogni reazione fisica.
- [x] **Scansione dei valori sulla porta 7** (`Telemetry`, 16 byte): fatta il 12/10 alle 17:39
      (`probe_port4.py --port7`, 37 sequenze con rilettura) e ripetuta alle 17:55 per le prove
      15–25 tre volte ciascuna, dopo un sospetto di reazione (bocca illuminata) rivelatosi un
      falso positivo. Esito negativo: nessun feedback, nessun errore, rilettura sempre zero,
      nessuna reazione. Resta da leggerla a lungo durante il gioco (`monitor --sub 7:0`) per
      capire se trasmette mai qualcosa in ingresso.
- [x] **Osservazione di standby** (`probe_port4.py --baseline 180`): 3 minuti di Mario connesso
      e fermo senza comandi: nessun messaggio LWP3 legato alle animazioni del display (palpebre,
      sguardo); il protocollo non "vede" ciò che fa il display.
- [~] **Azioni e proprietà dell'hub**: 0x05/0x06 (busy) provati il 12/10: Mario risponde
      `INVALID_USE`, non li supporta. Restano, con prudenza, 0x03/0x04 (VCC port on/off) e le
      proprietà: scrittura del nome e `reset` (operazione 0x04) sulle proprietà scrivibili, per
      vedere se producono un feedback sonoro (improbabile ma gratis).
- [ ] **Porta virtuale** (0x61) fra 4 e 7 e modalità combinata: improbabile, a costo zero.
- [x] **Basette virtuali su schermo**: provato il 12/10, lo scanner **non legge** i barcode
      mostrati su uno schermo (luce emessa invece che riflessa, polarizzatori, probabile lettura
      nel vicino infrarosso dove un LCD è uniforme). Pista chiusa per gli schermi emissivi.
- [ ] **Basette stampate su carta**: le basette originali sono stampe, quindi una stampa opaca in
      scala 1:1 dovrebbe funzionare (la community lo riporta). Fonte dei disegni: foto o
      scansione ad alta risoluzione delle basette reali, stampa su carta opaca, prova con
      `./run.sh monitor` (porta 1). Se funziona: un "tastierino" di clip azionabile a mano e, per
      il controllo dal PC, una striscia o un disco di barcode mosso da un servomotore sotto Mario.
- [ ] **Schermo e-ink** (riflessivo, opaco come la carta): se la carta funziona, un piccolo
      display e-paper comandato dal PC sotto i piedi di Mario è il modo pulito di scegliere la
      clip; da verificare che il contrasto regga anche all'infrarosso.
- [ ] **Colore con luce esterna**: il sensore colore legge il terreno; provare a illuminare un
      foglio bianco sotto Mario con una luce fortemente colorata (torcia del telefono con un
      filtro colorato, lampadina RGB) e guardare in `monitor` se la porta 1 riporta un colore
      (lava, acqua, ghiaccio…). Se sì, una lampadina RGB comandata dal PC basta a far dire a
      Mario le sue frasi sui terreni senza toccare nulla.
- [ ] **Cattura dell'app ufficiale** (vedi sezione 2): se nella cattura un Port Output verso la
      4 o la 7 è seguito da un suono di Mario, il comando si copia pari pari. Vale anche per il
      collegamento Mario–Luigi: lì Mario parla e suona a comando dell'altro personaggio.
- [ ] **Cosa non si può fare senza firmware modificato**: audio PCM/ADPCM verso l'altoparlante e
      frasi libere. Se le prove sopra non trovano nulla, la voce "diretta" resta quella delle
      clip: il PC dice le frasi del modello e Mario le "commenta" con le proprie clip scatenate
      dai comandi trovati o dalle basette virtuali (da integrare come comportamento in
      `behaviours.py`, emozione → clip).

- [~] **Terreno e rotazione "nervosi"**: fatto per il colore (e i pantaloni): un valore conta
      solo se resta stabile per 0,8 s (`IntentMapper.tick`, chiamato ogni mezzo secondo dal
      servizio e dalla CLI). Da provare dal vivo. Per la rotazione restano le idee: ignorarla
      mentre Mario è sollevato, spegnerla di default o usarla solo come contesto.
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
- [ ] **Latenza**: con `claude-sonnet-5-5` la risposta completa arriva in 3–4 s; misurare il
      tempo alla prima frase pronunciata e provare `claude-haiku-5-5`; pronuncia già per
      frase, non a fine risposta.
- [ ] **Reverse engineering del protocollo lato app**: catturare il traffico tra l'app
      ufficiale e Mario (HCI snoop su Android) durante le animazioni (vittoria, sconfitta,
      collegamento con Luigi) per scoprire se esistono comandi che fanno suonare o animare
      Mario. È il primo passo prima di toccare il firmware.
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

## 8. Idee

- Controller: usare gesti e pantaloni di Mario come input per un gioco o un emulatore
  (precedenti pubblici: Mario come controller di Super Mario Bros).
- Dashboard web locale con gli eventi in tempo reale (riuso di `monitor --json`).
- Registrare una partita intera e ricostruire il livello (sequenza di barcode e colori)
  come log leggibile.
- Far dialogare due personaggi tramite il PC (Mario e Luigi) emulando ciò che fanno via
  radio tra loro, una volta capito il formato della porta 4.
- Esporre Mario via MQTT o WebSocket per domotica e notifiche.
