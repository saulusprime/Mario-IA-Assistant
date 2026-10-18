# TODO — lavoro aperto e idee

Lista di lavoro del progetto LEGO Mario via Bluetooth LE. Stato al 18 ottobre 2026.
Qui c'è **solo ciò che resta da fare**: le attività completate e verificate sono in
[ASIS.md](ASIS.md), il diario in [HANDOFF.md](HANDOFF.md). Le voci sono raggruppate per tipo;
dentro ogni gruppo le prime sono le più utili.

## 1. Da verificare dal vivo (bastano Mario e `./run.sh monitor` o `tools/live_checks.py`)

- [ ] **Notifiche doppie dopo una riconnessione (corretto il 18/10 senza Mario)**: `./run.sh monitor`,
      spegnere e riaccendere Mario a metà sessione: dopo "riconnesso" ogni messaggio deve comparire una
      volta sola (prima arrivava due volte). Stessa prova dalla dashboard (Mario spento, poi "Connetti").
- [ ] **Gesti senza nome**: dare un nome ai bit `0x0008`, `0x0020`, `0x0080` e confermare il
      `0x0002` (visto girando Mario in piedi): movimenti più isolati, uno alla volta, con
      `tools/live_checks.py --steps gesti` (accelerometro e gesti insieme); girare Mario senza
      sollevarlo. Il 14/10 è comparso anche `0x0004`, da solo, due volte (durante i salti e mentre
      venivano sfilati i pantaloni).
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
- [ ] **`./run.sh re-checks --steps porta3`**: tutti i passi sono stati eseguiti il 14/10 (risultati in
      ASIS, log in `docs/sessioni/`). Il passo `porta3` si è interrotto perché Mario si è spento 12 s dopo
      l'attivazione del modo DEBUG, mentre lo strumento chiedeva di premere il pulsante: una pressione
      lunga spegne Mario. Ripeterlo con `--port3-modes DEBUG`: ora lo strumento chiede una pressione
      breve e scrive nel log ogni pressione. Capire se a spegnerlo è il modo DEBUG o il pulsante, e se la
      riga `LEAF ...` arriva da sola o dopo la pressione.
- [ ] **Voce IA (OpenVoice, 14/10)**: provata solo con una registrazione di riferimento sintetica
      (voce piper "riccardo"). Registrare 20–30 s di una voce vera (la propria o di un familiare
      d'accordo), caricarla dalla dashboard (anche m4a dal telefono) e ascoltare
      `./run.sh voice "..." --openvoice-ref data/voices/<file>.wav` con `--openvoice-tau` 0, 0,3 e 0,6;
      poi una sessione `assistant` con Mario e la voce IA: pause tra le frasi, primo avvio mentre
      carica (voce di riserva), consumo di CPU e memoria con dashboard e Bluetooth attivi.
- [ ] **Esclamazioni scelte dal modello (14/10)**: provate da terminale con Claude (una risposta,
      `{herewego}` al posto giusto). Da verificare in una sessione lunga con Mario: frequenza (la regola
      dice "non in tutte"), scelte adatte al momento, sovrapposizione con l'effetto dell'emozione nello
      stile "voce di Mario" (due esclamazioni di fila?).
- [ ] **Messaggio stile Mario (14/10)**: ascoltare `logs/messaggio-mario-cartone.wav` e
      `logs/messaggio-mario-naturale.wav`, scegliere tra formanti spostate e conservate e regolare
      `--semitones` (6) e `--tempo` (1,1); poi provare "Effetto: stile Mario" in una sessione
      dell'assistente. Per un timbro più vicino: registrare la propria imitazione di Mario (20–30 s)
      e usarla come riferimento della voce IA, insieme all'effetto.
- [ ] **Suoni dei giochi e pagina Suoni (14/10)**: dalla pagina `/sounds` ascoltare le scelte
      predefinite nei due stili e cambiare quelle che non piacciono; regolare il volume degli
      effetti rispetto alla voce; provare in una sessione dell'assistente se le esclamazioni si
      sovrappongono alla frase successiva; ascoltare nel browser un NSF, un SPC e un modulo
      convertiti; caricare un suono dal telefono. Ascoltare qualche MIDI (fluidsynth, 15/10) per
      il volume rispetto agli altri brani e qualcuno degli 11 moduli estratti in Python
      (`super_mario_rpg-culex-5`, `super_mario_land-muda-1`, ...).
- [ ] **Novità del 13/10 scritte senza Mario, da provare dal vivo**: `monitor --combined`
      (accelerometro + gesti e barcode + RGB ricomposti per modo), riconnessione automatica di
      `monitor` e `shell` (spegnere e riaccendere Mario a metà sessione), `info` che salva il JSON
      delle funzioni, nome e volume dalla scheda dispositivo della dashboard, pagina Avventure con
      Mario connesso (quiz con le basette dell'utente), rotazione ignorata quando Mario è in mano,
      scossa che interrompe la voce, ricordi e diario della CLI nel database.

## 2. Reverse engineering ancora da fare

Il 13/10, senza Mario, i log registrati sono stati riletti con `decode` ed è nato lo strumento
`./run.sh re-checks`, eseguito con Mario il 14/10 (risultati riletti il 18/10, in ASIS sezioni 1 e 2);
la cattura dal telefono ha la sua guida in `docs/cattura-app-ufficiale.md`.

- [ ] **Eventi porta 3 (modo EVENTS)**: confermati dal vivo il conto alla rovescia `0x3861`, sonno e
      risveglio (`0x3866`/`0x1802`), la macchina a stati del livello; dalla rilettura del 18/10 `0x1803 = 4`
      segna gli ultimi 15 s del livello. Da fare:
  - incrociare i contatori (monete `0x386B`, nemici `0x2042`, giri `0x2022`, monete per basetta `0x20C2`)
    con il display di Mario durante un livello;
  - `0x3004 = 601`: chiudere un livello prima della fine del tempo (basetta Traguardo `0xB7`, che
    l'utente oggi non ha) e vedere se il valore cambia (durata in decimi di secondo?);
  - `0x0115` (0 pantaloni sfilati, 1 rimessi?): un solo caso, il 14/10. Con `monitor`, sfilare e
    rimettere i pantaloni dentro e fuori da un livello;
  - senza nome: `0x2009`, `0x200C`, `0x200E`, `0x3857` (rosso/lava?).
- [ ] **Traccia DEBUG della porta 3** (14/10, 1.264 byte in 12 s): capire il formato dei record. Il più
      frequente è `0a 02 TT TT ff ff ff ff VV 01 00`, con `TT` che cresce di circa 200 a ogni record
      (millisecondi?) e `VV` tra 94 e 96 (un sensore? batteria? temperatura?). Gli altri tipi (`01 06`,
      `01 07`, `05 09`, `05 02`, `03 02`) potrebbero corrispondere a gesti, basette e stati. Serve una
      registrazione più lunga in modo DEBUG con azioni annotate (salto, basetta, pantaloni), senza
      tenere premuto il pulsante. `decode --summary` già ricompone il flusso e ne estrae il testo.
- [ ] **Porta 4 (0x55, `Events`)**: le scritture standard sono accettate ma inerti e il 14/10, sottoscritta
      per tutto un livello, non ha trasmesso nulla; capire cosa vi scrive l'app ufficiale o Luigi
      (cattura dal telefono, sotto).
- [ ] **Porta 7 (0x60, `Telemetry`)**: le scritture vengono ignorate e il 14/10 non ha trasmesso nulla
      per tutto un livello; resta la cattura dell'app ufficiale.
- [ ] **Cattura del traffico dell'app ufficiale**: procedura in `docs/cattura-app-ufficiale.md`
      (Android con HCI snoop log e `adb bugreport`, iPhone con il profilo Bluetooth logging e
      PacketLogger, copione di gioco, comandi di analisi). `decode` legge direttamente lo zip del
      bugreport, le sezioni btsnooz del testo (segnalando i dati troncati) e i file `.pklg`: tutto
      provato solo su file sintetici, la prima cattura vera va controllata con `--json`. È il modo
      più rapido per capire le porte 4 e 7 e il significato degli eventi.
- [ ] **System type 0x40**: la proprietà 0x0B risponde `0x40` ("Boost Move Hub" nella tabella LWP3,
      cioè numero di dispositivo 0) mentre l'advertisement dice `0x43` (anche per bricklife; riletti
      entrambi il 14/10). Verificare se Luigi (0x44) e Peach (0x45) fanno lo stesso quando ci saranno.
- [ ] **Mario come "central"**: l'advertisement dichiara capacità 0x03 (central e
      peripheral). È il meccanismo con cui Mario si collega a Luigi/Peach. Osservarlo con
      btmon avendo un secondo personaggio.

## 3. Sistema e Bluetooth del PC

Controllo automatico di tutti i punti qui sotto: `./run.sh doctor` (anche nella pagina Scansione).

- [x] **Firmware Broadcom** (chiuso il 15/10: non serve). Per i controller Apple (05ac,
      interfaccia ff/01/01) `btusb` usa `btbcm_setup_apple`, che non carica nessun `.hcd`;
      la patch la cerca solo `btbcm_setup_patchram`, usata per gli altri Broadcom. Verificato sui
      sorgenti del kernel 6.12: un file in `/lib/firmware/brcm/` non verrebbe letto. Caricare una
      patch a mano (comandi HCI del vendor a ogni avvio) richiederebbe un file che non esiste in
      pubblico: non conviene.
- [x] **Wi-Fi sui 5 GHz quando si usa Mario** (15/10: era già sui 5 GHz; `doctor` avvisa se il
      Wi-Fi torna a 2,4 GHz sullo stesso chip Broadcom). In alternativa una chiavetta Bluetooth
      USB (`./run.sh -i hci1 …`).
- [x] **Autosospensione dopo un riavvio** (chiuso il 18/10): al primo riavvio dall'11/10 (kernel
      6.12.113) `./run.sh doctor` dice "disattivata (opzione del modulo btusb)", `enable_autosuspend` = `N`
      e il dispositivo è su `on`, come previsto il 15/10.
- [ ] **Parametri LE del kernel** (solo se il link torna instabile): supervision timeout e
      intervallo via debugfs, comandi nel README.
- [x] **Agente di pairing** (15/10): BlueZ tiene una coda degli agenti predefiniti
      (`default_agents` in `src/agent.c`, sorgenti 5.82). Quando l'agente dell'app si scollega,
      anche per una chiusura brusca, torna predefinito quello di GNOME. Trovato e corretto un
      problema: l'agente dell'app approvava qualsiasi richiesta, e con il pannello Bluetooth di GNOME
      aperto un dispositivo vicino qualsiasi si sarebbe accoppiato senza conferma. Ora accetta solo
      il Mario in collegamento.
- [ ] **Reset completo** (serve Mario acceso): `./run.sh unpair` (o `bluetoothctl remove
      <indirizzo di Mario>`) e nuovo collegamento, per verificare che il pairing automatico funzioni da
      zero, ora con l'agente ristretto a Mario. `doctor` mostra prima e dopo gli hub associati.

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
      Pronti da usare dopo la rilettura del 18/10: "tempo quasi scaduto" (`0x1803 = 4`, ultimi 15 s del
      livello) e "livello finito" (`0x1803 = 3`). L'assistente li ha come regole dal 18/10 (`tempo`,
      `fine_livello`); nelle avventure oggi si ottengono solo con il trigger generico "evento di gioco".
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

Fatte il 16 e il 18/10 (dettagli nel README, sezione Assistente); da provare dal vivo:

- [x] **Attivazione con «Ehi Mario»** (18/10): Vosk ascolta il microfono di continuo con una grammatica ristretta,
      Whisper controlla la frase; "Ehi Mario!" da solo fa rispondere "Eccomi!" e ascolta la frase dopo. Spenta di
      default (casella nella pagina Assistente, `assistant --wake`). Provata solo con frasi sintetiche in un flusso
      di 195 s: 22 chiamate su 24, 1 frase su 40 accettata per sbaglio.
  - [ ] **Prova con il microfono vero**, con chi gioca d'accordo: `./run.sh listen --wake` (mostra ogni
        chiamata e il testo di Whisper, nulla viene salvato), poi `assistant --wake` con Mario e la voce del PC.
        Da guardare: chiamate perse con la voce di un bambino e a qualche metro dal MacBook; falsi allarmi con
        la TV o una conversazione accanto (quante frasi scartate all'ora: costano tempo di CPU a Whisper); che
        Mario non si attivi da solo con la propria voce (ascolto in pausa mentre parla e per 0,8 s dopo); il
        secondo di silenzio che chiude la frase (troppo corto per un bambino che si ferma a pensare?).
  - [ ] **Tempi del solo "Ehi Mario!"**: tra la chiamata e "Eccomi!" passano un secondo di silenzio e la
        trascrizione (3–4 s, di più con il browser aperto); intanto suona subito il segnale "ti ascolto", che
        però suona anche per le chiamate poi scartate. Con il microfono vero decidere se basta, se il segnale
        disturba, oppure se verificare le chiamate brevi con Whisper `base` (circa 1 s).
  - [ ] **Nomi vicini a Mario**: "Ehi Maria, dove sei?" a volte passa come "Ehi Mario, dove sei?" (una volta su
        due nelle prove), e Mario risponde a una domanda non sua. Se in casa c'è una Maria: frase di attivazione
        diversa (`--wake-phrase "ciao mario"`, casella "frasi" della pagina) e prova con `listen --wake`.
  - [ ] Se servisse ascoltare anche mentre Mario parla (interromperlo con "Ehi Mario, basta"): cancellazione
        dell'eco con il modulo `echo-cancel` di PipeWire, oppure una soglia di confidenza più alta durante la voce.
  - [ ] Vosk con il vocabolario libero come riconoscimento rapido al posto di Whisper: provato il 18/10 sulle
        stesse frasi e sbaglia troppo ("raccontano una storia", "poi magari facciamo un salto"). Non conviene,
        a meno di un modello Vosk più grande.
- [x] **Regole della partita** (18/10): `tempo` (ultimi 15 s del livello, priorità 2) e `fine_livello`, accese;
      `moneta` ora legge il totale `0x386B` (prima `0x3861`, il conto alla rovescia), sempre spenta.
  - [ ] **Prova con Mario**: un livello intero con l'assistente; la frase sugli ultimi 15 secondi deve arrivare in
        tempo (il modello dei sensori risponde in 1–2 s) e non accavallarsi con la fine del livello.

- [x] **Ingresso vocale** (faster-whisper offline, `./tools/setup_stt.sh`; pulsante di Mario come
      "tieni premuto e parla", pulsante della pagina, `/parla`). Provato con frasi sintetiche: small
      circa 3 s per frase, 4,2 s dal rilascio alla prima frase di Mario.
  - [ ] **Prova con il microfono vero e un bambino vero**: precisione di `small` con la voce del
        bambino e il rumore di casa, distanza dal microfono del MacBook, pressioni lunghe (20 s al
        massimo). `./run.sh listen` per una prima prova, poi `assistant --voice` con Mario.
  - [ ] Se 3 s sono troppi: `base` (1 s, sbaglia di più), oppure un PC più veloce. Una trascrizione a
        pezzi durante la registrazione richiederebbe un altro motore.
- [x] **Storia interattiva** con `[aspetta: salto | giro | pantaloni X | basetta X | terreno X | scossa |
      pulsante]`. Provata con Claude e sensori simulati.
  - [ ] **Prova con Mario**: salti veri durante una storia, basette giuste e sbagliate, tempo scaduto,
        cadute durante l'attesa.
- [x] **Modello per i sensori**: `sensor_model` "auto" usa haiku con Claude. Prima frase 1,2–1,5 s
      contro 1,2–2,1 s di sonnet.
  - [x] Trovato e corretto: il ragionamento automatico di Claude 5 svuotava le risposte lunghe.
- [x] **Sicurezza, seconda tappa**: temi da evitare (nel testo del bambino e nelle risposte), limite di
      messaggi, riepilogo del giorno con riassunto scritto dal modello su richiesta.
  - [ ] Rivedere con i genitori la lista delle parole (`safety.py`) e i falsi allarmi nel diario dopo
        qualche giornata di gioco.
- [x] **Ricordi**: profili per bambino, ricordi comuni, scadenza per quelli del modello, riassunto
      automatico oltre i 20.
  - [ ] Scegliere i profili e i giorni di scadenza.
- [ ] **Clip di Mario come comportamento**: resta bloccato finché una pista della sezione precedente
      non fa suonare Mario via Bluetooth.

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
