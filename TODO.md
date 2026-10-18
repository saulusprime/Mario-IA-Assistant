# TODO — lavoro aperto e idee

Lista di lavoro del progetto LEGO Mario via Bluetooth LE. Stato al 18 ottobre 2026.
Qui c'è **solo ciò che resta da fare**: le attività completate e verificate sono in
[ASIS.md](ASIS.md), il diario in [HANDOFF.md](HANDOFF.md). Le voci sono raggruppate per tipo;
dentro ogni gruppo le prime sono le più utili.

## 1. Da verificare dal vivo (bastano Mario e `./run.sh monitor` o `tools/live_checks.py`)

- [ ] **Firmware 6.5.03**: fra il 14 e il 18/10 l'utente ha aggiornato Mario con l'app ufficiale (prima 6.5.01);
      il nome è tornato quello di fabbrica (`Mario` + quattro caratteri), il bond con il PC è rimasto valido.
      `./run.sh info` per salvare `capabilities-fw-6.5.03.json` e confrontarlo con quello della 6.5.01 (porte,
      modi, proprietà); ripetere le prove principali (gesti, basette, eventi della porta 3) per vedere se è
      cambiato qualcosa.
- [ ] **Riconnessione senza messaggi doppi** (correzione del 18/10, vedi ASIS sezione 4): `./run.sh monitor`,
      spegnere e riaccendere Mario a metà sessione: dopo "riconnesso" ogni messaggio deve comparire una
      volta sola. Stessa prova dalla dashboard (Mario spento, poi "Connetti").
- [ ] **Gesti senza nome**: dare un nome ai bit `0x0004`, `0x0008`, `0x0020`, `0x0080` e confermare il
      `0x0002` (visto girando Mario in piedi): movimenti più isolati, uno alla volta, con
      `tools/live_checks.py --steps gesti` (accelerometro e gesti insieme); girare Mario senza
      sollevarlo. `0x0004` è comparso da solo il 14/10, durante i salti e mentre venivano sfilati i pantaloni.
- [ ] **Scanner in modo RGB da solo** (`monitor --sub 1:1`): in modalità combinata si muove solo
      il primo dei tre canali; capire se nel modo singolo i tre canali cambiano con il colore.
- [ ] **Pantaloni diversi dai blu**: quando ci saranno altri pantaloni, confrontare i codici con
      `codes.PANTS` (oggi verificato solo 0x21). Con Tanuki o Elica, chiarire `0x0A` e `0x0C`: legomario.py
      li dà al contrario di bricklife e pyLegoMario (nota nella pagina Tabelle dei codici).
- [ ] **Bowser Jr. `0x0099`**: capire se il codice della community è un'altra basetta di Bowser Jr.
      (set 71396 "Clown Koopa di Bowser Junior"?) o un errore della tabella.
- [ ] **Allarme batteria spontaneo**: con batteria scarica, verificare che arrivi l'aggiornamento
      `LOW_VOLTAGE` senza richiesta (gli allarmi sono abilitati e rispondono alla richiesta).
- [ ] **`./run.sh re-checks --steps porta3 --port3-modes DEBUG`**: il 14/10 Mario si è spento 12 s dopo
      l'attivazione del modo DEBUG, mentre lo strumento chiedeva di premere il pulsante (tenuto premuto, lo
      spegne). Ora lo strumento chiede una pressione breve e scrive nel log ogni pressione: capire se a
      spegnerlo è il modo DEBUG o il pulsante, e se la riga `LEAF ...` arriva da sola o dopo la pressione.
- [ ] **Voce IA (OpenVoice)**: registrare 20–30 s di una voce vera (la propria o di un familiare
      d'accordo), caricarla dalla dashboard (anche m4a dal telefono) e ascoltare
      `./run.sh voice "..." --openvoice-ref data/voices/<file>.wav` con `--openvoice-tau` 0, 0,3 e 0,6;
      poi una sessione `assistant` con Mario e la voce IA: pause tra le frasi, primo avvio mentre
      carica (voce di riserva), consumo di CPU e memoria con dashboard e Bluetooth attivi.
- [ ] **Esclamazioni scelte dal modello**: in una sessione lunga con Mario controllare frequenza (la regola
      dice "non in tutte"), scelte adatte al momento, sovrapposizione con l'effetto dell'emozione nello
      stile "voce di Mario" (due esclamazioni di fila?).
- [ ] **Messaggio stile Mario**: ascoltare `logs/messaggio-mario-cartone.wav` e
      `logs/messaggio-mario-naturale.wav`, scegliere tra formanti spostate e conservate e regolare
      `--semitones` (6) e `--tempo` (1,1); poi provare "Effetto: stile Mario" in una sessione
      dell'assistente. Per un timbro più vicino: registrare la propria imitazione di Mario (20–30 s)
      e usarla come riferimento della voce IA, insieme all'effetto.
- [ ] **Suoni dei giochi e pagina Suoni**: dalla pagina `/sounds` ascoltare le scelte
      predefinite nei due stili e cambiare quelle che non piacciono; regolare il volume degli
      effetti rispetto alla voce; provare in una sessione dell'assistente se le esclamazioni si
      sovrappongono alla frase successiva; ascoltare nel browser un NSF, un SPC e un modulo
      convertiti; caricare un suono dal telefono. Ascoltare qualche MIDI per il volume rispetto agli
      altri brani e qualcuno degli 11 moduli estratti in Python (`super_mario_rpg-culex-5`,
      `super_mario_land-muda-1`, ...).
- [ ] **Novità del 18/10 scritte senza Mario, da provare dal vivo**:
  - pulsante "Standard, modi combinati" del Monitor e, nella pagina Eventi, le colonne per modo
    dell'esportazione CSV con dati veri;
  - avventura "Sfida a tempo nel livello" con il livello vero (fasi `0x1803`, passo perso negli ultimi 15 s),
    modalità "registra" con le basette reali, musica di sottofondo durante una partita (volume rispetto alla
    voce e agli effetti), replay di una partita vera;
  - `./run.sh bridge --dashboard ...` con Mario connesso alla dashboard e un broker MQTT vero
    (`mosquitto_sub -t 'mario/#' -v`);
  - `decode --summary` sul log di un livello intero registrato dall'inizio: fasi, basette e monete;
  - dashboard aperta alla rete con `--self-signed` dal telefono: avviso del certificato con l'impronta
    stampata, sessione nella Panoramica, revoca dal PC.
- [ ] **Novità del 13/10 scritte senza Mario, da provare dal vivo**: `monitor --combined`
      (accelerometro + gesti e barcode + RGB ricomposti per modo), riconnessione automatica di
      `monitor` e `shell` (spegnere e riaccendere Mario a metà sessione), `info` che salva il JSON
      delle funzioni, nome e volume dalla scheda dispositivo della dashboard, pagina Avventure con
      Mario connesso (quiz con le basette dell'utente), rotazione ignorata quando Mario è in mano,
      scossa che interrompe la voce, ricordi e diario della CLI nel database.

## 2. Reverse engineering ancora da fare

Quanto è già acquisito (eventi della porta 3, durata del livello, modi CHAL/VERS/DEBUG, porte 4 e 7,
GATT) è in ASIS, sezione 2; la cattura dal telefono ha la sua guida in `docs/cattura-app-ufficiale.md`.

- [ ] **Eventi porta 3 (modo EVENTS)**:
  - incrociare i contatori (monete `0x386B`, nemici `0x2042`, giri `0x2022`, monete per basetta `0x20C2`)
    con il display di Mario durante un livello;
  - `0x3004 = 601`: chiudere un livello prima della fine del tempo (basetta Traguardo `0xB7`, che
    l'utente oggi non ha) e vedere se il valore cambia (durata in decimi di secondo?);
  - `0x0115` (0 pantaloni sfilati, 1 rimessi?): un solo caso, il 14/10. Con `monitor`, sfilare e
    rimettere i pantaloni dentro e fuori da un livello;
  - senza nome: `0x2009`, `0x200C`, `0x200E`, `0x3857` (rosso/lava?).
- [ ] **Traccia DEBUG della porta 3**: capire il formato dei record (quelli visti sono descritti in ASIS):
      il record periodico `0a 02 TT TT ff ff ff ff VV 01 00` (`TT` in millisecondi? `VV` un sensore, la
      batteria, la temperatura?) e gli altri tipi (`01 06`, `01 07`, `05 09`, `05 02`, `03 02`), forse gesti,
      basette e stati. Serve una registrazione più lunga in modo DEBUG con azioni annotate (salto, basetta,
      pantaloni), senza tenere premuto il pulsante; `decode --summary` ricompone già il flusso.
- [ ] **Porta 4 (0x55, `Events`)**: capire cosa vi scrive l'app ufficiale o Luigi (cattura dal telefono,
      sotto): con i comandi standard è inerte e non trasmette.
- [ ] **Porta 7 (0x60, `Telemetry`)**: stessa situazione; resta la cattura dell'app ufficiale.
- [ ] **Cattura del traffico dell'app ufficiale**: procedura in `docs/cattura-app-ufficiale.md`
      (Android con HCI snoop log e `adb bugreport`, iPhone con il profilo Bluetooth logging e
      PacketLogger, copione di gioco, comandi di analisi). I lettori di `decode` per i formati dei telefoni
      sono stati provati solo su file sintetici: la prima cattura vera va controllata con `--json`. È il modo
      più rapido per capire le porte 4 e 7 e il significato degli eventi.
- [ ] **Immagine del firmware da una cattura dell'aggiornamento**: l'utente ha aggiornato Mario alla 6.5.03 con
      l'app ufficiale; se sul telefono era attivo il log HCI snoop (Opzioni sviluppatore, di serie è spento), un
      `adb bugreport` potrebbe contenere ancora il trasferimento via OAD (`f000ffc0-…`), cioè l'immagine stessa.
      In quel caso servirebbe a `decode` un'opzione che ricompone i blocchi scritti sulla caratteristica Image
      Block (oggi le scritture non LWP3 vengono scartate). Per i prossimi aggiornamenti (anche di Luigi o
      Peach): attivare prima il log completo. È il punto di partenza della ricognizione del firmware (sezione 7).
- [ ] **System type con Luigi e Peach**: Mario risponde `0x40` alla proprietà 0x0B e dichiara `0x43`
      nell'advertisement; verificare se Luigi (0x44) e Peach (0x45) fanno lo stesso quando ci saranno.
- [ ] **Mario come "central"**: l'advertisement dichiara capacità 0x03 (central e
      peripheral). È il meccanismo con cui Mario si collega a Luigi/Peach. Osservarlo con
      btmon avendo un secondo personaggio.

## 3. Sistema e Bluetooth del PC

Controllo automatico: `./run.sh doctor` (anche nella pagina Scansione). Firmware, Wi-Fi, autosospensione e
agente di pairing sono chiusi (ASIS, sezione 3).

- [ ] **Reset completo** (serve Mario acceso): `./run.sh unpair` (o `bluetoothctl remove
      <indirizzo di Mario>`) e nuovo collegamento, per verificare che il pairing automatico funzioni da
      zero, ora con l'agente ristretto a Mario. `doctor` mostra prima e dopo gli hub associati.
- [ ] **Parametri LE del kernel** (solo se il link torna instabile): supervision timeout e
      intervallo via debugfs, comandi nel README.

## 4. Miglioramenti software

- [ ] **`decode` su una cattura vera con messaggi LWP3**: le tre catture `btmon -w` dell'11/10 (ASIS, sezione
      4) contengono solo tentativi di connessione falliti; serve una cattura durante una sessione riuscita
      (`./tools/capture_btmon.sh`) e un HCI snoop log di Android (H4).
- [ ] **Luigi e Peach**: stesso protocollo; verificare che tabelle e nomi reggano
      (system type 0x44/0x45, pantaloni Luigi/Peach già in tabella).
- [ ] **Release su GitHub**: il pacchetto si installa (anche non modificabile) e la CI è di nuovo verde;
      resta da decidere la versione (oggi 0.2.0) e pubblicare la release (`gh release create`, `gh` non è
      installato sul PC).
- [ ] **Tabelle**: le tute di Luigi e Peach oltre a quelle base non sono in nessuna fonte consultata;
      aggiungerle quando ci saranno, con `catalog-tiles` o `monitor`.

## 5. Dashboard web

- [ ] **Console e "Aggiorna funzioni"** dalla scheda dispositivo: mai provate con Mario connesso.
- [ ] **Pagina "Assistente" nel browser con Mario connesso**: provata solo con backend demo e
      simulazione dei sensori; checklist nella sezione 7.
- [ ] **Più giri nel browser headless**: `tests/test_browser.py` prova oggi caricamento ed errori di ogni
      pagina, avventure, tabelle e zoom degli eventi; mancano Assistente (chat in streaming con il backend
      demo), Suoni, Console e la pagina protetta da token.
- [ ] Dispositivo attivo selezionabile quando ce n'è più d'uno (Mario, Luigi, Peach) e
      connessioni multiple in parallelo (un `MarioDevice` per personaggio).
- [ ] Foto o icona per dispositivo e per accessorio.
- [ ] **HTTPS con waitress**: oggi con `--cert` o `--self-signed` serve il server di Flask (waitress non fa
      TLS); per un uso stabile in rete, un proxy (Caddy) davanti a waitress.

## 6. Designer di avventure

La prima versione (ASIS, sezione 4) è stata provata solo con eventi simulati.

- [ ] **Prova dal vivo**: il "Quiz sulle basette" con le 8 basette dell'utente, il "Percorso a
      ostacoli" con i gesti e la "Sfida a tempo nel livello"; tarare le pause dei gesti ripetuti (0,8 s)
      e i timeout (vedi anche le novità del 18/10 nella sezione 1).
- [ ] **Editor visuale**: griglia o percorso trascinabile con le basette, proprietà per casella,
      anteprima; oggi l'editor è a moduli (tabella dei passi) più il JSON per sequenze e azioni.
- [ ] **Trigger mancanti**: "salto riuscito" e "colpito" dagli eventi della porta 3 (servono le ipotesi
      confermate della sezione 2); un `any_of` che sappia quale ramo è scattato (oggi tutti i rami portano
      allo stesso esito; per "A prima di B" c'è il trigger `fail`).
- [ ] **Azioni verso Mario**: oltre al volume non c'è nulla finché le porte 4 e 7 restano inerti
      (sezione 7). Le notifiche esterne passano dal webhook o dal ponte (`./run.sh bridge`).
- [ ] **Schermo di gioco**: una mappa vera del percorso (oggi una fila di basette con le icone di famiglia
      e le frecce) e immagini per basette e colori (catalogo di icone libere).
- [ ] **Multigiocatore**: Mario + Luigi + Peach connessi insieme, turni e gare.
- [ ] **Latenza del flusso** sotto i 200 ms: da misurare dal vivo (evento → voce del PC).

## 7. Mario come dispositivo di I/O per una sessione con un LLM

Idea: usare Mario come "corpo" di un assistente. In ingresso i sensori diventano comandi o
contesto per il modello; in uscita la risposta del modello si manifesta su Mario, oppure Mario
la "accompagna" con sensazioni e comportamenti. Le tappe fatte sono in ASIS, sezioni 4 e 5. Via
Bluetooth non esiste un canale audio né un comando per display e suoni.

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
      scuoterlo mentre parla deve interromperla.
- [ ] **Rotazione in mano**: girare Mario sollevato non deve produrre risposte; sulla piattaforma
      rotante (o su un colore) sì.
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
- [ ] **Voce piper in una sessione**: indicare `it_IT-paola-medium.onnx` (in `third_party/piper-voices/`)
      nelle impostazioni e confrontare la qualità con espeak-ng durante una sessione dell'assistente
      (finora ascoltata solo da terminale con `./run.sh voice`).
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

### Evoluzioni dell'assistente: prove e scelte aperte

Le evoluzioni del 16 e del 18/10 sono fatte (ASIS, sezione 4; uso nel README, sezione Assistente).
Restano le prove dal vivo e alcune scelte.

- [ ] **«Ehi Mario» con il microfono vero**, con chi gioca d'accordo: `./run.sh listen --wake` (mostra ogni
      chiamata e il testo di Whisper, nulla viene salvato), poi `assistant --wake` con Mario e la voce del PC.
      Da guardare: chiamate perse con la voce di un bambino e a qualche metro dal MacBook; falsi allarmi con
      la TV o una conversazione accanto (quante frasi scartate all'ora: costano tempo di CPU a Whisper); che
      Mario non si attivi da solo con la propria voce (ascolto in pausa mentre parla e per 0,8 s dopo); il
      secondo di silenzio che chiude la frase (troppo corto per un bambino che si ferma a pensare?).
- [ ] **Tempi del solo "Ehi Mario!"**: tra la chiamata e "Eccomi!" passano un secondo di silenzio e la
      trascrizione (3–4 s, di più con il browser aperto); il segnale "ti ascolto" suona subito ma anche per
      le chiamate poi scartate. Con il microfono vero decidere se basta, se il segnale disturba, oppure se
      verificare le chiamate brevi con Whisper `base` (circa 1 s).
- [ ] **Nomi vicini a Mario**: "Ehi Maria, dove sei?" a volte passa come "Ehi Mario, dove sei?" (una volta su
      due nelle prove), e Mario risponde a una domanda non sua. Se in casa c'è una Maria: frase di attivazione
      diversa (`--wake-phrase "ciao mario"`, casella "frasi" della pagina) e prova con `listen --wake`.
- [ ] **Ascoltare anche mentre Mario parla** (interromperlo con "Ehi Mario, basta"), se servisse:
      cancellazione dell'eco con il modulo `echo-cancel` di PipeWire, oppure una soglia di confidenza più
      alta durante la voce.
- [ ] **Ingresso vocale con il microfono vero e un bambino vero**: precisione di `small` con la voce del
      bambino e il rumore di casa, distanza dal microfono del MacBook, pressioni lunghe (20 s al
      massimo). `./run.sh listen` per una prima prova, poi `assistant --voice` con Mario. Se 3 s sono troppi:
      `base` (1 s, sbaglia di più) oppure un PC più veloce (Vosk a vocabolario libero è stato provato e
      sbaglia troppo).
- [ ] **Regole della partita con Mario**: un livello intero con l'assistente; la frase sugli ultimi 15
      secondi deve arrivare in tempo (il modello dei sensori risponde in 1–2 s) e non accavallarsi con la fine
      del livello.
- [ ] **Storia interattiva con Mario**: salti veri durante una storia, basette giuste e sbagliate, tempo
      scaduto, cadute durante l'attesa.
- [ ] **Sicurezza**: rivedere con i genitori la lista delle parole (`safety.py`) e i falsi allarmi nel diario
      dopo qualche giornata di gioco.
- [ ] **Ricordi**: scegliere i profili e i giorni di scadenza.
- [ ] **Clip di Mario come comportamento**: resta bloccato finché una pista della sezione seguente
      non fa suonare Mario via Bluetooth.

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
- [ ] **Mappa completa dei sottocomandi** (`probe_port4.py --sweep`, 5–8 minuti): i sei sottocomandi dei
      motori sono `COMMAND_NOT_RECOGNIZED` su entrambe le porte (scansione del 18/10, vedi ASIS); resta da
      vedere se fra 0x00 e 0xFF ce n'è uno, oltre a 0x50 e 0x51, che il firmware riconosce. Tocca anche codici
      non documentati: da fare con l'utente presente.
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

## 8. Idee

- Controller: usare gesti e pantaloni di Mario come input per un gioco o un emulatore
  (precedenti pubblici: Mario come controller di Super Mario Bros).
- Nella ricostruzione dei livelli di `decode --summary` (oggi fasi, basette, monete e nemici) aggiungere i
  colori calpestati e i gesti, filtrando i colori del tavolo.
- Far dialogare due personaggi tramite il PC (Mario e Luigi) emulando ciò che fanno via
  radio tra loro, una volta capito il formato della porta 4.
- Ponte (`./run.sh bridge`): un esempio di automazione domestica (Home Assistant via MQTT: una luce che
  cambia colore con i pantaloni) e i comandi in ingresso (volume da MQTT).
- Voce IA con le emozioni: OpenVoice cambia il timbro ma non il tono; una registrazione di riferimento
  per emozione (allegra, triste, spaventata) scelta in base al tag della frase darebbe più
  espressività (le impronte si calcolano una volta e restano in cache).
