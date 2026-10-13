# LEGO Mario via Bluetooth LE

Applicazione Python 3 per parlare con LEGO Mario (e Luigi / Peach) dal PC: scoperta
automatica di tutte le funzioni esposte dal dispositivo, comunicazione bidirezionale grezza
o decodificata, dashboard web e un assistente che usa Mario come ingresso e uscita di una
sessione con un modello di linguaggio. Repository: https://github.com/saulusprime/Mario-IA-Assistant
(stato al 13 ottobre 2026: cosa è fatto e verificato in [ASIS.md](ASIS.md), lavoro aperto in
[TODO.md](TODO.md), diario in [HANDOFF.md](HANDOFF.md)).

Mario è un hub LEGO che usa il **LEGO Wireless Protocol 3.0** (LWP3), lo stesso dei
Powered Up / Boost / Technic. L'app implementa il protocollo generico (quindi scopre
porte e modalità interrogando il dispositivo, senza tabelle fisse) e aggiunge i
decodificatori specifici di Mario ricavati dal reverse engineering della community.

## Requisiti

- Linux con BlueZ (testato su Debian 13, bluetoothctl 5.82) e un adattatore Bluetooth LE.
- Python 3.10 o superiore e il pacchetto `bleak` (installato automaticamente nel venv).
- L'utente deve poter usare BlueZ (gruppo `bluetooth` o policy D-Bus standard).

## Avvio

```sh
./run.sh scan                 # cerca i Mario nelle vicinanze
./run.sh info                 # si connette e stampa tutte le funzioni scoperte
./run.sh info --json          # stessa cosa in JSON
./run.sh monitor              # eventi in tempo reale: gesti, barcode/colori, pantaloni, eventi, batteria
./run.sh shell                # console interattiva bidirezionale
./run.sh web                  # dashboard web su http://127.0.0.1:8765
./run.sh assistant            # Mario come ingresso/uscita di una sessione con un modello (voce dal PC)
./run.sh assistant --no-mario # idem senza Bluetooth: tastiera, modello e voce (chiave Claude in .env)
./run.sh adventure --list     # avventure del designer; --play quiz [--no-mario] per giocarne una
./run.sh decode logs/x.txt    # decodifica offline di catture btsnoop, log o byte esadecimali
./run.sh live-checks --steps gesti   # strumenti di tools/ come sottocomandi (catalog-tiles, probe-port4, diag-connect)
./run.sh volume 30            # imposta il volume (0-100)
./run.sh name "Mario Casa"    # cambia il nome Bluetooth
./run.sh prop battery         # legge una proprietà
./run.sh raw 05 00 01 12 05   # invia byte grezzi e mostra le risposte
./run.sh pair                 # associa Mario al PC (di norma avviene da solo)
./run.sh unpair               # dimentica l'associazione
./run.sh off                  # spegne Mario
```

Opzioni globali, prima del comando: `-a INDIRIZZO` sceglie un Mario preciso, `-t SECONDI`
il tempo di ricerca, `-i hci1` un altro adattatore, `--any-lego` accetta qualsiasi hub LEGO,
`--no-pair` disattiva il pairing automatico, `-d` mostra i byte grezzi TX/RX.

Opzioni utili di `monitor`: `--combined` (accelerometro + gesti e barcode + RGB insieme, in
modalità combinata), `--json` (una riga JSON per evento, per collegare Mario ad altri programmi),
`--duration 120`, `--no-battery`, `--rssi`, `--raw-accel`, `--sub 7:0`. Se Mario cade, `monitor`
e `shell` aspettano che torni visibile e lo riagganciano (`--no-reconnect` per uscire subito).
`info` salva la mappa delle funzioni in `docs/capabilities-fw-<versione>.json` (`--no-save` per
evitarlo; il nome Bluetooth non viene scritto).

**Pairing.** Mario pretende un pairing Bluetooth "Just Works" (lo stesso che fanno i
telefoni, senza PIN) prima di permettere la scrittura dei descrittori GATT: senza,
risponde `Insufficient Authentication` e BlueZ, non riuscendo a farsi confermare il
pairing dall'agente di sistema, perde la connessione. L'app registra per la durata della
sessione un proprio agente BlueZ NoInputNoOutput che accetta automaticamente, e alla prima
connessione associa Mario al PC (`Device1.Pair`, con bond salvato da BlueZ). Dalle volte
successive il bond viene riusato. Comandi utili: `./run.sh pair` (solo associazione),
`./run.sh unpair` (dimentica Mario), opzione `--no-pair` per disattivare l'automatismo.

`run.sh` crea il virtualenv `.venv` al primo avvio. Se viene lanciato dentro un
sandbox Flatpak (per esempio il terminale di VS Code installato da Flathub) si
riavvia da solo sull'host con `flatpak-spawn`, perché dal sandbox BlueZ non è
raggiungibile.

Prima di ogni comando: accendi Mario e **premi il tasto Bluetooth sulla schiena**.
L'icona Bluetooth sul petto lampeggia per circa 30 secondi: in quel tempo Mario è
visibile e accetta la connessione. Opzioni globali: `-a INDIRIZZO` per scegliere un
Mario preciso, `-t SECONDI` per il tempo di ricerca, `-i hci1` per usare un altro
adattatore (per esempio una chiavetta USB), `-d` per vedere i byte grezzi TX/RX.

## Dashboard web

```sh
./run.sh web            # http://127.0.0.1:8765  (--port, --host, --db, --open)
```

Interfaccia Flask con menù principale: **Panoramica** (stato, azioni rapide, ultimi
eventi), **Scansione**, **Dispositivi** (catalogo nel database locale: proprietà, mappa
delle funzioni, note), **Monitor** (sensori in tempo reale via Server-Sent Events, schede
per porta, storico), **Console** (proprietà, volume, nome, messaggi LWP3 e byte grezzi,
Port Output Command, azioni e allarmi, log di tutte le risposte), **Accessori**
(pantaloni, basette con barcode, colori terreno e altri accessori: posseduti, quantità,
set, note; "visti" si aggiorna da solo quando il sensore li riconosce), **Assistente**
(Mario come ingresso e uscita di una sessione con un modello: chat in streaming, voce dal
PC, emozioni, regole sensori → intenti, sezione dedicata più sotto), **Eventi** (archivio con
filtri per dispositivo, porta, tipo, testo e intervallo; esportazione CSV e JSON; grafici senza
librerie esterne: batteria nel tempo, istogramma dei gesti, basette lette, eventi per tipo),
**Avventure** (designer e schermo di gioco, sezione dedicata più sotto), **Funzioni** (report
completo) e **Documentazione** (README, ASIS, TODO, HANDOFF). Dalla scheda di un dispositivo si
cambiano nome e volume con Mario connesso.

Server: `waitress` se installato nel venv (`.venv/bin/pip install waitress`), altrimenti quello
di sviluppo di Flask (`--server flask|waitress`, `--threads`). Con `--host 0.0.0.0` la dashboard
è raggiungibile dalla rete locale e l'accesso richiede un token: generato all'avvio e stampato,
oppure scelto con `--token` o `MARIO_WEB_TOKEN` (`--no-token` per disattivarlo). Si entra con
`?token=…` nell'URL, dalla pagina `/login` o con `Authorization: Bearer` per le API; il token
resta in un cookie per trenta giorni.

Architettura: un thread dedicato possiede il loop asyncio e il `MarioDevice`; le rotte
Flask vi inoltrano le richieste con `run_coroutine_threadsafe`; gli eventi di Mario vanno
ai client SSE e nel database SQLite `data/mario.sqlite3` (tabelle `devices`,
`accessories`, `events`, `settings`, `assistant_messages`, senza dipendenze oltre a Flask). Il server gira
sull'host (serve BlueZ); il browser si collega in locale.

## Assistente: Mario come ingresso e uscita di un modello di linguaggio

Punto 7 del piano (tappe fatte in [ASIS.md](ASIS.md), prove aperte in [TODO.md](TODO.md)), prima tappa realizzata l'11 ottobre 2026. Mario diventa il
"corpo" di un assistente: i sensori producono messaggi per il modello, la risposta torna
come voce dagli altoparlanti del PC, con emozioni che si vedono nella dashboard e si
riflettono sul Mario fisico per quel poco che il protocollo permette oggi (volume,
indicazione "occupato").

```sh
./run.sh assistant                 # con Mario connesso: sensori -> modello -> voce
./run.sh assistant --no-mario      # solo tastiera, modello e voce (prova senza Bluetooth)
./run.sh assistant --backend demo  # risposte locali a regole, senza rete
./run.sh web                       # pagina "Assistente" della dashboard
```

**Ingresso.** `mario/assistant/intents.py` traduce gli eventi in intenti secondo regole
modificabili dalla dashboard: salto, caduta e rotazione diventano frasi come
"[sensore: gesto: salto] Ho appena fatto un salto!"; il cambio di pantaloni porta con sé il
carattere corrispondente (Mario Fuoco è energico, Mario Gatto giocoso, …); una basetta
letta dallo scanner dice se è un nemico, un premio, la partenza o il traguardo; il colore
del terreno aggiunge "scotta, è lava" e simili; il pulsante sulla schiena attiva o sospende
l'ascolto; la scossa ripete l'ultima risposta; la batteria sotto il 15 % fa dire a Mario che
è stanco. Lo stato del corpo (pantaloni, terreno, ultima basetta, gesto, batteria) va nel
prompt di sistema e, quando cambia, in testa al messaggio. Ogni regola ha una pausa minima
tra due attivazioni (i gesti arrivano a raffica); l'urto è spento di default.

**Modello.** `mario/assistant/llm.py` parla con l'API Messages di Claude (modello
predefinito `claude-sonnet-5-5`; chiave nella variabile `ANTHROPIC_API_KEY`, nel file `.env`
nella radice del progetto con la riga `ANTHROPIC_API_KEY=sk-ant-...`, caricato all'avvio da
`mario/env.py` e ignorato da git, oppure nelle impostazioni della pagina), con qualsiasi
endpoint compatibile OpenAI (Ollama, LM Studio, …) o con un backend "demo" a regole, senza
rete, che serve a provare tutta la catena. Solo libreria standard, risposta in streaming.
Ai modelli Claude 5 non va inviato `temperature` (HTTP 400 "deprecated for this model"):
il parametro resta solo per gli endpoint OpenAI-compatibili. Il modello è istruito a parlare come Mario, in italiano,
frasi brevi per bambini, e a iniziare ogni frase con un tag emozione tra `[gioia]`
`[sorpresa]` `[pensieroso]` `[allarme]` `[tristezza]` `[sonno]` `[calma]`.

**Uscita.** `emotions.py` separa i tag dal testo mentre arriva e spezza il testo in frasi;
`tts.py` le pronuncia una alla volta con `spd-say` (speech-dispatcher + espeak-ng, voci
italiane) o `espeak-ng`, con velocità e tono diversi per emozione, e suona piccoli effetti
sintetizzati (moneta, power-up, allarme, sbadiglio, …) con `paplay`. `behaviours.py` traduce
l'emozione in azioni sul Mario fisico, sincronizzate con l'inizio di ogni frase pronunciata:
oggi solo volume (su per la gioia, giù per sonno e tristezza, alternato per l'allarme, poi
ritorno al valore di base), indicazione "occupato" dell'hub (azioni 0x05/0x06, effetto da
verificare) e, se attivate a mano, scritture sperimentali sulle porte 4 e 7 (payload per
emozione, massimo 16 byte). Mai spegnimento, disconnessione o cambio nome in automatico.

**Ritmo.** Il modello risponde in 2–3 s ma una risposta parlata dura 8–10 s: per non
accumulare frasi vecchie, un intento dai sensori aspetta che Mario abbia finito di parlare.
Nel frattempo gli eventi si accumulano (uno per regola), quelli più vecchi di 8 secondi si
scartano e i restanti diventano un solo prompt ("Nel frattempo sono successe più cose: …").
Chi scrive da tastiera, o una caduta, interrompe la voce in corso. Le risposte ai sensori
sono chieste in una frase, massimo due. I livelli di volume dei comportamenti sono relativi
al volume di partenza di Mario (letto alla prima emozione e ripristinato alla chiusura).

**Dashboard.** La pagina Assistente ha la chat con la risposta in streaming, la faccia di
Mario che cambia emoji e colore, lo stato del corpo, gli ultimi intenti, il log dei
comportamenti, i pulsanti per simulare i sensori senza il Mario fisico, le impostazioni
(backend, modello, chiave, voce, comportamenti) e la tabella delle regole. Gli eventi
viaggiano sullo stesso flusso SSE del monitor (`type` = `ASSISTANT`, campo `kind`:
`user`, `start`, `emotion`, `delta`, `sentence`, `speaking`, `done`, `error`, `intent`,
`context`, `behaviour`, `listening`, `memory`, `memory_forgotten`, `blocked`, `time_up`). La
conversazione è salvata nella tabella `assistant_messages`; le impostazioni nella tabella
`settings` (chiavi `assistant.*`).

**Ricordi.** Il modello può chiudere una risposta con `[ricorda: il bambino si chiama Leo]`: il
tag non viene letto, finisce nella tabella `assistant_memory` e nelle sessioni successive entra
nel prompt di sistema ("Cose che ricordi dalle volte scorse…"). I ricordi si vedono, si
aggiungono e si cancellano dalla pagina (sezione Ricordi), dalle API `/api/assistant/memory` e
dalla CLI (`/ricordi`, `/ricorda TESTO`, `/dimentica [N|tutto]`); la CLI usa lo stesso database
della dashboard (`data/mario.sqlite3`, `--no-db` per evitarlo), dove salva anche il diario.

**Sicurezza per i bambini.** Dalle impostazioni: minuti di gioco per sessione (allo scadere Mario
saluta, smette di ascoltare i sensori e rifiuta nuovi messaggi finché non si preme "Nuova"), parole
bloccate (un messaggio che le contiene non va al modello: Mario cambia discorso; nelle risposte
vengono sostituite con "…"), memoria attivabile o no. La sezione "Diario per i genitori" mostra
tutto ciò che è stato detto, con filtro per data ed esportazione CSV (`/api/assistant/history.csv`).
Da CLI: `--minutes`, `--block`, `--no-memory`.

**Gesti di controllo.** La scossa a riposo ripete l'ultima risposta; la scossa mentre Mario parla
la interrompe. La rotazione vale solo se lo scanner vede qualcosa sotto i piedi (colore o
basetta): girato in mano, Mario non reagisce (prima la rotazione scattava di continuo).

**Latenza.** Misurata il 13/10/2026 con l'API (tre prompt per modello, rete di casa): tempo alla
prima frase pronunciabile 0,7–1,6 s con `claude-haiku-5-5` (risposta completa 0,9–1,9 s) e
1,1–1,5 s con `claude-sonnet-5-5` (una richiesta a freddo ha impiegato 8,8 s). Entrambi
rispettano i tag emozione; haiku è una buona scelta per i turni dei sensori. L'evento `done`
riporta `first_sentence` (la CLI lo stampa come "prima frase dopo N s").

**Voce.** Oltre a `spd-say` ed `espeak-ng`, se nel PC ci sono il binario `piper` e un modello
italiano `.onnx` (campo "Modello piper" nelle impostazioni, o `--piper-model`), le frasi vengono
sintetizzate con piper (voce neurale, offline; l'emozione agisce solo sulla velocità).

**Limiti noti.** Mario non ha un canale audio né comandi documentati per display e suoni:
la voce esce dal PC. L'indicazione "occupato" dell'hub (azioni 0x05/0x06) su Mario risponde
`INVALID_USE` (provato il 12/10/2026): l'opzione resta ma non fa nulla. La porta 4 accetta
scritture solo con WriteDirectModeData (0x51), con feedback "completato", ma non conserva il
valore e non reagisce; la porta 7 ignora le scritture in silenzio (`tools/probe_port4.py`,
prove del 12/10/2026, dettagli in HANDOFF e TODO sezione 7). Provato con il backend demo, con Claude (`claude-sonnet-5-5`: risposte
in italiano nel personaggio, con i tag emozione, 3–4 s a risposta completa) e con la voce
del PC e, alle 18:50, con Mario connesso da terminale: salto, rotazione, basette (Start,
Goomba) e colori del terreno hanno prodotto risposte coerenti nel personaggio; il volume
seguiva le emozioni. Da quella prova vengono il ritmo "aspetta la voce", le pause più lunghe
per rotazione e colore (8 s: girare Mario in mano e appoggiarlo li attiva di continuo) e
l'opzione `-v` del comando `assistant` per vedere i comportamenti riga per riga. Il resto del punto 7 (cattura del traffico
dell'app ufficiale, firmware) resta nel TODO.

## Designer di avventure

Un'avventura è una sequenza di passi: ogni passo ha una missione detta e mostrata ("Trova
qualcosa di verde"), un trigger che la completa e un'azione. I trigger sono gli elementi del mondo
di Mario che il PC riconosce dai sensori: basetta (codice preciso o famiglia: partenza, traguardo,
nemico, premio, amico, qualsiasi), colore del terreno, pantaloni, gesto, pulsante, evento di gioco
della porta 3 (per esempio monete totali ≥ 5 o tre giri sulla piattaforma rotante), timer,
sequenza di trigger entro N secondi, stesso trigger ripetuto N volte. Le azioni: punti, frase
detta dal PC, effetto sonoro, messaggio a schermo, volume di Mario, salto a un altro passo, fine,
webhook (POST JSON, solo se abilitati). Ogni passo può avere un timeout con frase e passo di
ripiego; l'avventura un limite di tempo complessivo.

```sh
./run.sh web                      # pagina "Avventure": catalogo, editor, libreria degli elementi, partite giocate
                                  # pagina "Gioca": missione grande, punti, barra del tempo, basette lette, cronaca
./run.sh adventure --list         # le avventure nel database (o gli esempi)
./run.sh adventure --play quiz    # con Mario connesso; --no-mario: eventi da tastiera (salto, basetta 0x02, colore 0x25…)
```

Il motore (`mario/adventures/engine.py`) è una macchina a stati che riceve gli stessi eventi del
worker della dashboard e di `monitor --json`; si prova senza Mario con i pulsanti di simulazione
della pagina o con la tastiera. Le avventure sono JSON (`mario/data/adventures/` contiene tre
esempi: caccia al tesoro dei colori, percorso a ostacoli con i gesti, quiz con le basette dei set
71360 e 71425), si importano ed esportano dalla pagina. Le partite finiscono nella tabella
`adventure_runs` con esito, punti e cronaca. L'editor è a moduli (passo per passo) più il JSON
per sequenze e azioni; la versione trascinabile e la modalità "registra" sono nel TODO.

## La shell interattiva

`./run.sh shell` si connette, fa una scoperta rapida e poi accetta comandi mentre
mostra ogni messaggio in arrivo (prefisso `<-`). Comandi principali:

| Comando | Effetto |
|---|---|
| `props`, `prop volume`, `watch button on` | legge le proprietà dell'hub, attiva gli aggiornamenti automatici |
| `name Nuovo`, `volume 50` | scrive proprietà |
| `ports`, `modes 1`, `discover`, `report` | esplora porte e modalità |
| `sub 2 0`, `unsub 2 0`, `value 2` | notifiche di una porta, lettura puntuale |
| `combo 1 0 0:0 0:1 1:0 1:1 1:2` | modalità combinata sulle porte combinabili |
| `alert low_voltage on`, `busy on`, `action 0x05` | allarmi e azioni dell'hub |
| `msg 0x01 12 05`, `raw 05 00 01 12 05`, `out 0 1 00` | messaggi arbitrari verso Mario |
| `log nome.txt`, `log off` | cambia o sospende il file di log della sessione |
| `quiet`, `debug`, `off`, `quit` | utilità |

Scrivi `help` per l'elenco completo e `help sub` (o `combo`, `out`, `alert`, …) per i dettagli di
un comando. Su un terminale la shell usa readline: frecce per la cronologia (salvata in
`~/.local/state/mario/`), Tab per completare i comandi. I valori combinati (0x46) vengono
ricomposti per modo e descritti come i valori singoli.

## Decodifica offline (`decode`)

```sh
./run.sh decode logs/monitor-20261012-180642.txt --summary     # log di monitor/shell/strumenti
./run.sh decode cattura.btsnoop --type 45,46 --port 3 --json   # btmon -w, HCI snoop di Android
./run.sh decode "05 00 45 02 21" "-> 06 00 01 12 01 32"        # byte, anche in uscita (->)
btmon -r cattura.btsnoop | ./run.sh decode -                    # testo di btmon da stdin
```

Legge catture btsnoop (formato "Linux monitor" di btmon, H4 dei telefoni Android, HCI puro) con
riassemblaggio L2CAP, i log di testo del progetto (byte tra parentesi quadre in coda alla riga,
righe `TX`/`RX` dell'opzione `-d`, uscita di `btmon -r`) e byte esadecimali. Lo stato delle porte
parte da `docs/capabilities-fw-6.5.01.json` (ogni porta nel modo che `monitor` attiva di default,
`--no-default-modes` per non presumerlo) ed evolve con le sottoscrizioni e le modalità combinate
viste nel file. `--summary` conta i messaggi per tipo e porta ed elenca gli eventi della porta 3
con i valori osservati: è lo strumento per la cattura del traffico dell'app ufficiale (TODO).

## Uso come libreria

```python
import asyncio
from mario import MarioDevice

async def main():
    dev = MarioDevice()                 # oppure MarioDevice("7C:5B:9C:67:AB:52")
    await dev.connect()
    await dev.discover()
    print(dev.report())
    dev.add_listener(lambda msg, text: print(text))
    await dev.subscribe(2, 0)           # pantaloni
    await dev.subscribe(1, 0)           # barcode e colori
    await dev.set_volume(20)
    await asyncio.sleep(60)
    await dev.disconnect()

asyncio.run(main())
```

Il modulo `mario.lwp3` (senza dipendenze) costruisce e decodifica qualsiasi messaggio
LWP3; `mario.codes` contiene le tabelle di Mario; `mario.device` gestisce la
connessione, le richieste con attesa della risposta e lo stato dell'hub.

## Protocollo

- Servizio GATT `00001623-1212-efde-1623-785feabcd123`, caratteristica
  `00001624-1212-efde-1623-785feabcd123` (write + notify): tutto passa da lì.
- Advertisement: manufacturer data con company ID `0x0397` (LEGO), per esempio
  `00 43 03 ff ff 00` dove `43` è il system type (0x43 Mario, 0x44 Luigi, 0x45 Peach).
- Frame: `[lunghezza] [hub id 0x00] [tipo] [payload]`.
- Tipi di messaggio usati: Hub Properties `0x01`, Hub Actions `0x02`, Hub Alerts
  `0x03`, Hub Attached I/O `0x04`, Generic Error `0x05`, Port Information Request
  `0x21`, Port Mode Information Request `0x22`, Port Input Format Setup `0x41`/`0x42`,
  Port Information `0x43`, Port Mode Information `0x44`, Port Value `0x45`/`0x46`,
  Port Input Format `0x47`/`0x48`, Virtual Port Setup `0x61`, Port Output Command
  `0x81`, feedback `0x82`.

### Funzioni di Mario (verificate dal vivo, firmware 6.5.01)

Report completo in [docs/capabilities-fw-6.5.01.json](docs/capabilities-fw-6.5.01.json),
prodotto da `./run.sh info --json` l'11 ottobre 2026.

| Porta | IO type | Modalità (formato) | Contenuto |
|---|---|---|---|
| 0 | 0x47 | 0 `RAW` (3 × int8), 1 `GEST` (2 × int16); combinabili (verificato) | accelerometro x/y/z (fermo in mano y ≈ 17..60, scossa fino a ±98); gesti (bitfield) |
| 1 | 0x49 | 0 `TAG` (2 × int16), 1 `RGB` (3 × int8); combinabili (verificato) | `[barcode LE16][colore LE16]`, `FFFF` = nulla; in modo RGB combinato si muove solo il primo canale (0..189) |
| 2 | 0x4A | 0 `PANT` (1 × int8, 0..63) | codice pantaloni a 6 bit; codici transitori durante il cambio |
| 3 | 0x46 | 0 `CHAL` (2 × int16), 1 `VERS` (4 × int32), 2 `EVENTS` (2 × int16), 3 `DEBUG` (4 × int32) | eventi di gioco: basetta letta, monete, nemici, giri, stato del livello (sotto) |
| 4 | 0x55 | 0 `Events` (2 × int16), ingresso **e uscita** | mai trasmesso; accetta solo WriteDirectModeData (0x51) con feedback "completato", ma non conserva il valore e non reagisce |
| 6 | 0x14 | 0 `VLT L`, 1 `VLT S` (1 × int16, raw 0..2705 = 0..3300 mV) | tensione batteria |
| 7 | 0x60 | 0 `Telemetry` (16 × int8), ingresso **e uscita** | nuova dal firmware 6.x; mai trasmesso; ignora le scritture in silenzio |

Modalità combinata: la maschera dei dataset nel messaggio 0x46 arriva con il byte alto per primo
(`00 1f` = dataset 0..4) e con multi-update Mario manda solo i dataset cambiati. Proprietà:
`RSSI` risponde sempre 0 (non implementata); `BUTTON` manda premuto/rilasciato; i quattro allarmi
dell'hub rispondono; le azioni `DISCONNECT` e `SWITCH_OFF` annunciano `WILL_DISCONNECT` /
`WILL_SWITCH_OFF`; le azioni busy 0x05/0x06 sono rifiutate (`INVALID_USE`).

Proprietà lette: nome `Mario 5wzZ`, firmware 6.5.01, hardware 0.4, LWP 3.0, batteria in
percentuale, volume 100, MAC identità `F4:60:77:5A:20:11`. Curiosità: la proprietà
"system type" risponde `0x40`, mentre l'advertisement dichiara `0x43` (Mario).

Gesti (porta 0 modo 1): `0x0001` urto, `0x0010` scossa, `0x0100` rotazione,
`0x0200` movimento rapido, `0x0400` traslazione, `0x0800` caduta/impatto,
`0x1000` cambio direzione, `0x2000` inversione, `0x8000` salto. Dal vivo: il salto scatta anche
sollevando, appoggiando o colpendo Mario; la scossa accende `0xA111`; girare Mario in piedi dà
`0x8102` (bit `0x0002` senza nome); inclinarlo `0x0100`; cadere da pochi cm `0x2100`; il bit
`0x0040` compare con l'urto quando Mario atterra su una basetta. Senza nome: `0x0008`, `0x0020`, `0x0080`.

Pantaloni (porta 2): `0x00` nessuno, `0x03` Ape, `0x05` Luigi, `0x06` Rana,
`0x0A` Tanuki, `0x0C` Elica, `0x11` Gatto, `0x12` Fuoco, `0x14` Pinguino,
`0x18` Peach, `0x21` Mario, `0x22` Costruttore, `0x23` Ghiaccio. Verificato dal vivo solo `0x21`
(pantaloni blu): sfilandoli arriva `0x00`, infilandoli i contatti danno per un attimo codici
transitori (`0x18`, `0x01`): chi reagisce ai pantaloni deve aspettare che il valore sia stabile.

Barcode (porta 1): la tabella `codes.BARCODES` ha 61 basette con nome (fonte principale il file
`ALL_RGB_CODES.json` di pyLegoMario, più tre lette dal vivo: `0xF5` Bowser Jr. nel Clown Koopa,
`0x160` aereo e `0x15B` carrello minerario del set 71425). Verificate dal vivo: `0xB8` Start Mario,
`0x02` Goomba, `0x2E` Nuvola, `0x14` Rotazione, oltre alle tre nuove. Altri esempi: `0x29`
Blocco ?, `0x63` Super Fungo, `0x7B` Super Stella, `0x88` Re Boo, `0x99` Bowser Jr. (community),
`0xB7` Traguardo, `0xAF` Start Luigi, `0x14D` Start Peach, `0x1D` Bowser, `0x15E` Bowser gigante.
I codici non in tabella vengono mostrati in esadecimale.
Colori: `0x13` bianco, `0x15` rosso, `0x17` blu, `0x18` giallo, `0x1A` nero,
`0x25` verde, `0x6A` marrone, `0x10C` viola, `0x138` nougat, `0x142` ciano (i primi sette e il
nougat verificati dal vivo).

Eventi di gioco (porta 3 modo 2, `[codice LE16][valore LE16]`), significato ricavato dal vivo il
12 ottobre con le basette reali (ipotesi solide, in `codes.GAME_EVENTS`):

| Codice | Significato |
|---|---|
| `0x0113` | basetta letta: il valore è il codice del barcode |
| `0x386B` | totale monete del livello (1 dopo un Goomba, +6 dopo l'aereo) |
| `0x20C2` | monete date dall'ultima basetta (6 o 8 con l'aereo, come una roulette) |
| `0x2042` | nemici sconfitti nel livello (1, 2, 3 a ogni Goomba) |
| `0x3712` | = 1 circa un secondo dopo ogni nemico sconfitto |
| `0x2022` | giri contati sulla piattaforma rotante |
| `0x1803` | 4 subito dopo lo Start, 3 a fine livello, 0 fuori dal livello |
| `0x3812` | 2 in livello / a fine livello, 1 fuori dal livello |
| `0x3004`, `0x3852`, `0x386E`, `0x3803=100` | sequenza del "tempo scaduto" (conto alla rovescia sul petto) |
| `0x3862` | 0 quasi a ogni basetta letta o sollevata; 4 alla connessione |

### Osservazioni dal vivo (sessione `monitor` dell'11 ottobre 2026, 3 minuti e 40 secondi)

- Gesti ricevuti: rotazione `0x0100` (108 volte), rotazione+inversione `0x2100`, salto `0x8000`,
  scossa `0x0010`, urto `0x0001`; quasi sempre l'urto arriva come `0x0041`, cioè con un bit
  `0x0040` non documentato. Visti anche `0x0020` e `0x0008` da soli: significato da scoprire
  (nei due int16 del modo GEST il secondo valore ripete il primo).
- Scanner: colori bianco `0x13`, nero `0x1A`, nougat `0x138`, giallo `0x18`, rosso `0x15`;
  `FFFF FFFF` quando Mario viene sollevato. Nessun barcode in quella sessione.
- Porta 3 modo EVENTS: coppie `[codice, valore]`; il codice si legge come classe/evento
  (`0x38/0x61` con valore che sale 1→8 come un contatore, `0x38/0x62`, `0x38/0x57`,
  `0x38/0x66`, `0x38/0x03` = 100, `0x20/0x0E` = 2, `0x18/0x02` = 1 e 2). Tabella provvisoria
  in `codes.GAME_EVENTS`.
- Batteria (porta 6): la tensione oscilla 2740–2760 mV; con delta 1 arrivava un valore ogni
  millivolt, per questo `monitor` ora usa delta 25.
- Allo spegnimento Mario annuncia `WILL_SWITCH_OFF` e `WILL_DISCONNECT`.

### Seconda sessione dal vivo (dashboard, 17:45–17:51, 1189 eventi nel database)

- Pantaloni (porta 2): alla sottoscrizione arriva il valore corrente (`Mario normale`,
  0x21) e poi solo ai cambi; il cambio dal vivo non è ancora stato provato.
- Basette: riconosciuta la basetta di partenza `0xB8` (Start Mario). Colori visti anche
  ciano `0x142` e blu `0x17`.
- Gesti: oltre a quelli già noti, `traslazione 0x0400` e `cambio direzione 0x1000`;
  nuovi bit senza nome `0x0002` e `0x0080` (quest'ultimo con l'urto, `0x0081`).
- Porta 3: undici codici nuovi; `0x01/0x13` ha valore 184 = `0xB8`, cioè "basetta letta
  con il suo codice"; `0x30/0x04` = 601 (tempo o punteggio?); `0x38/0x6B` conta 0,1,3,5.
  Tutti in `codes.GAME_EVENTS` come ipotesi.
- Il contatore "visti" degli Accessori nella dashboard si è aggiornato da solo
  (6 colori, 1 pantalone, 1 basetta).

Proprietà dell'hub: le 15 standard LWP3 (nome, pulsante, versioni, RSSI, batteria,
tipo, MAC...) più `0x12` **volume** (0-100), specifica di Mario:
`06 00 01 12 01 <volume>` per impostarlo, `05 00 01 12 05` per leggerlo.

Il comando `info` interroga davvero il dispositivo, quindi se il firmware espone
porte o modalità in più rispetto a questa tabella le vedrai nel report.

### Verifiche del 12 ottobre 2026 (`tools/live_checks.py`, `tools/catalog_tiles.py`)

- Pulsante: arrivano gli aggiornamenti premuto/rilasciato. RSSI: sempre 0, non implementata.
  Allarmi: i quattro tipi rispondono. `DISCONNECT` e `SWITCH_OFF` annunciano `WILL_DISCONNECT` /
  `WILL_SWITCH_OFF` e chiudono il link.
- Modalità combinata (porte 0 e 1): funziona, ma la maschera dei dataset arriva con il byte alto
  per primo e, con multi-update, solo i dataset cambiati vengono inviati.
- Pantaloni: con i soli pantaloni blu (0x21) il cambio dà 0x00 da sfilati e, infilandoli, codici
  transitori (0x18, 0x01) prima di tornare a 0x21: l'assistente aspetta che il valore sia stabile.
  Colori del modo TAG confermati per sette colori. Basette: otto basette catalogate (sette codici,
  tre nuovi: Clown Koopa, aereo, carrello minerario).
- Gesti: il salto scatta anche sollevando o appoggiando Mario; la scossa accende cinque bit insieme;
  bit nuovo 0x0002 girando Mario in piedi.

## Problemi di connessione (MacBook con Bluetooth Broadcom)

Sintomo: Mario viene trovato, compare `Connected: yes` per un istante e subito dopo
`le-connection-abort-by-local` oppure `failed to discover services, device disconnected`.
Succede identico anche con `bluetoothctl connect`, quindi non dipende dall'app: è il
lato PC che chiude il collegamento LE.

Su questo PC (MacBook, controller Broadcom `05ac:8290`, driver `btusb`, Debian 13,
verificato l'11 ottobre 2026) le cause trovate sono due:

1. **Firmware Broadcom assente.** In `/lib/firmware/brcm/` non c'è nessun file `.hcd`,
   quindi il chip gira con il firmware ROM, che ha bug noti sul Bluetooth LE. Il
   pacchetto Debian `bluez-firmware` non contiene il file per questo chip
   (`BCM20703A1-05ac-8290.hcd`): va preso dai driver Windows/Boot Camp, per esempio
   dal repository https://github.com/winterheart/broadcom-bt-firmware, e copiato in
   `/lib/firmware/brcm/`.
2. **Autosospensione USB del controller** (`btusb enable_autosuspend=Y`,
   `power/control = auto`, stato `suspended` a riposo).

Comandi, tutti con privilegi di root:

```sh
# 1) autosospensione: subito (vale fino al riavvio)...
echo on | sudo tee /sys/bus/usb/devices/1-3/power/control
#    ...e in modo permanente
echo "options btusb enable_autosuspend=0" | sudo tee /etc/modprobe.d/btusb.conf

# 2) firmware: copia il file BCM20703A1-05ac-8290.hcd in /lib/firmware/brcm/ e ricarica il driver
sudo modprobe -r btusb && sudo modprobe btusb

# verifica: deve comparire la riga di caricamento del patch, non "Patch ... not found"
sudo dmesg | grep -iE "bluetooth|brcm|patch"
```

**Cosa ha mostrato `btmon` (11 ottobre 2026, 14:02).** Il link LE si stabilisce
(intervallo 48,75 ms, supervision timeout 420 ms), lo scambio GATT parte, ma le
conferme dei pacchetti arrivano dopo 194, 335 e 378 ms e dopo 2,3 s il controller
chiude con `Reason: Connection Timeout (0x08)`, cioè supervision timeout. Nello stesso
istante BlueZ aveva ripreso la scansione LE (finestra 11,25 ms su 11,25 ms, il 100% del
tempo) perché lo scanner dell'app era ancora attivo: il chip Broadcom non regge scansione
continua e connessione insieme. Da quella versione l'app ferma la scansione prima di
connettersi. Se non bastasse, due altri rimedi:

```sh
# supervision timeout predefinito del kernel per le nuove connessioni LE: da 420 ms a 5 s
# (unità di 10 ms; non persiste al riavvio)
echo 500 | sudo tee /sys/kernel/debug/bluetooth/hci0/supervision_timeout
# intervallo di connessione più corto (7,5-15 ms): più occasioni di recupero del link
echo 6  | sudo tee /sys/kernel/debug/bluetooth/hci0/conn_min_interval
echo 12 | sudo tee /sys/kernel/debug/bluetooth/hci0/conn_max_interval
```

**Seconda cattura (14:08, scansione fermata prima della connessione).** Il link non si
forma nemmeno: sette tentativi di fila con `LE Connection Complete` seguito dopo circa
300 ms (6 intervalli di connessione) da `Reason: Connection Failed to be Established
(0x3E)`, cioè nessun pacchetto ricevuto da Mario dopo il pacchetto di connessione. I
parametri del comando `LE Create Connection` erano identici alla cattura precedente,
quindi la differenza sta nelle condizioni radio. Questo MacBookPro12,1 ha Wi-Fi BCM43602 e
Bluetooth sullo stesso modulo Broadcom con antenna condivisa, e il Wi-Fi era agganciato a
2,4 GHz (canale 11). Rimedio provato subito dopo: Wi-Fi spostato sulla rete a 5 GHz
(la rete a 5 GHz di casa) e nuova cattura, vedi sotto.

**Terza cattura (14:59, Wi-Fi spostato sui 5 GHz): causa trovata.** Con il Wi-Fi a
5 GHz la connessione riesce in 2 s e la scoperta GATT fila liscia (4 servizi: GATT, GAP,
LEGO 1623 con caratteristica 1624 e CCCD all'handle 0x0013, servizio OAD Texas
Instruments `f000ffc0-...`). Poi BlueZ scrive il CCCD di *Service Changed* (handle
0x0004) e Mario risponde `Insufficient Authentication (0x05)`. BlueZ avvia da solo il
pairing (IO `DisplayYesNo`, MITM); Mario risponde IO `NoInputNoOutput`, bonding, SC;
BlueZ gira la conferma all'agente di sistema, che la nega (`User Confirmation Negative
Reply`, `Pairing Failed: Numeric comparison failed`) e Mario chiude la connessione.
Nel frattempo il nostro `StartNotify` resta in coda: ecco il blocco "non fa nulla".
Rimedio: agente NoInputNoOutput dell'app e pairing automatico (vedi sopra). Nota: a 7,7 s
Mario chiede lui stesso nuovi parametri di connessione (intervallo 30 ms, supervision
timeout 3 s), quindi il timeout di 420 ms del kernel conta solo nei primi secondi.

Secondo sintomo possibile, con errore immediato `br-connection-canceled`: BlueZ ha
registrato Mario come dispositivo Bluetooth classico e prova a collegarsi via BR/EDR.
L'app lo riconosce da sola: stampa il tipo di indirizzo visto da BlueZ nella riga
"Trovato ...", rimuove il record del dispositivo (`Adapter1.RemoveDevice`) e riprova
via LE. Per una diagnosi dettagliata c'è `tools/diag_connect.py` (da lanciare sull'host
con `.venv/bin/python tools/diag_connect.py`): mostra le proprietà BlueZ del dispositivo
(AddressType, Paired, Bonded, AdvertisingFlags...) e tenta la connessione prima e dopo
la rimozione del record.

Per vedere il motivo esatto con cui il controller chiude il collegamento (es. 0x16
"terminated by local host", 0x13 "remote user terminated", 0x3E "failed to be
established") c'è `./tools/capture_btmon.sh`: avvia `btmon` con sudo, tenta la
connessione e stampa gli eventi HCI rilevanti; il log completo finisce in `logs/`.

Poi `./run.sh info`. Fattore secondario: il Wi-Fi è collegato a 2,4 GHz (canale 11)
sulla stessa scheda combo; se i problemi persistono prova una rete a 5 GHz.

**Autosospensione USB.** `/etc/modprobe.d/btusb.conf` (`options btusb enable_autosuspend=0`)
agisce al caricamento del driver; il 13/10/2026 il PC non era ancora stato riavviato dalla
creazione del file, quindi `/sys/module/btusb/parameters/enable_autosuspend` diceva ancora `Y`
mentre il dispositivo era già su `on` per via della scrittura manuale. Dopo il primo riavvio va
controllato `cat /sys/bus/usb/devices/1-3/power/control`; se torna `auto`, c'è la regola udev
pronta in `tools/99-mario-btusb.rules` (istruzioni nel file). Il firmware
`BCM20703A1-05ac-8290.hcd` è stato cercato di nuovo il 13/10: non è in linux-firmware né nei
pacchetti comunitari (altri proprietari di MacBookPro12,1 riportano lo stesso), resta la via
dell'estrazione dal driver macOS o Boot Camp.

## File di log

`monitor` e `shell` scrivono tutto quello che mostrano (con i byte grezzi) in
`logs/monitor-<data>.txt` e `logs/shell-<data>.txt`; `tools/capture_btmon.sh` salva lì le
catture HCI. La cartella è ignorata da git.

## Test

```sh
python3 -m unittest discover -s tests -v          # nel venv dell'host: .venv/bin/python -m unittest discover -s tests
```

Centoventi test senza Bluetooth né rete esterna, raggruppati per file:

| File | Cosa copre |
|---|---|
| `test_lwp3` (18) | codec LWP3, decodificatori e tabelle di Mario |
| `test_hubstate` (5) | stato dell'hub da `capabilities`, valori combinati con i dati reali del 12/10 |
| `test_decode` (4) | catture btsnoop sintetiche (monitor e H4, frammentazione L2CAP), log di testo, filtri |
| `test_device_fake` (5) | `MarioDevice` e `BleWorker` con un BleakClient finto: connessione con tentativi, richieste, errori, scoperta, notifiche, modalità combinata |
| `test_db` (6) | database della dashboard |
| `test_assistant` (30), `test_assistant_ext` (11) | emozioni, regole, sessione, ricordi, sicurezza, scossa/rotazione, piper, API |
| `test_llm_http` (4) | backend Claude e OpenAI contro un server SSE locale (intestazioni, errori HTTP, taglio, Stop) |
| `test_service` (3), `test_tts` (4) | servizio dell'assistente con worker finto; righe di comando dei motori vocali ed effetti WAV |
| `test_adventures` (6), `test_adventures_web` (3) | modello, validazione, motore con eventi simulati, servizio e API |
| `test_web_pages` (6), `test_web_static` (5) | pagina Eventi, esportazioni, grafici, token; sintassi del JavaScript e coerenza id/azioni con i template |
| `test_cli` (7), `test_smoke` (3) | parser, log di sessione, comandi dell'assistente e delle avventure, `decode`; importazione senza Bluetooth |

I test che importano Flask o bleak vengono saltati dove mancano (per esempio nel sandbox) e
girano nel venv dell'host e in GitHub Actions (`.github/workflows/tests.yml`, Python 3.11 e 3.13).

## Struttura del progetto

```
run.sh                  avvio (salta sull'host se in Flatpak, crea il venv)
pyproject.toml          pacchetto `mario-ble` con entry point `mario` (pip install -e .)
mario/lwp3.py           protocollo LWP3: messaggi, parser, descrizioni (anche in uscita), formati valore
mario/hubstate.py       stato dell'hub senza bleak: porte, modi, formati, valori combinati, eventi
mario/codes.py          tabelle Mario caricate da mario/data/mario_codes.json (fonte e verifica), modi di default
mario/device.py         MarioDevice (bleak), agente di pairing BlueZ, scoperta, report
mario/decode.py         decodifica offline: btsnoop, log di testo, byte esadecimali, riepilogo
mario/cli.py            comandi scan/info/monitor/shell/decode/assistant/adventure/web/volume/name/prop/pair/unpair/off/raw
                        e gli strumenti di tools/ come sottocomandi
mario/web/              dashboard: app.py (pagine e API), ble_worker.py, db.py, templates/, static/
mario/assistant/        Mario come I/O di un LLM: intents, emotions, tts, llm, behaviours, memory, session, service
mario/adventures/       designer di avventure: model, engine, library, service
mario/data/             mario_codes.json (tabelle) e adventures/ (tre avventure di esempio)
mario/env.py            caricamento di .env (chiave API) senza dipendenze
tools/                  capture_btmon.sh, diag_connect.py, gatt_notify_test.sh (prova con il solo bluetoothctl),
                        probe_port4.py, catalog_tiles.py, live_checks.py, 99-mario-btusb.rules (udev)
tests/                  test offline (120)
docs/                   capabilities-fw-6.5.01.json (mappa delle funzioni), sessioni/ (log delle prove dal vivo)
third_party/            cloni di riferimento (LEGO-Mario-Reveng, legomario.py, pyLegoMario), ignorati da git
data/                   database SQLite della dashboard (ignorato da git)
logs/                   catture btmon, log di monitor/shell/assistente/avventure/prove (ignorato da git)
.env                    ANTHROPIC_API_KEY=... (ignorato da git)
ASIS.md, TODO.md        stato attuale (fatto e verificato) e lavoro aperto
HANDOFF.md              diario di lavoro con orari
```

## Diario di lavoro e cose da fare

Cosa è fatto e verificato è in [ASIS.md](ASIS.md); i punti aperti e le idee sono in
[TODO.md](TODO.md); la cronologia completa, con i vicoli ciechi, è in [HANDOFF.md](HANDOFF.md).

## Fonti

- Wiki italiano di Super Mario, pagina LEGO Super Mario (set, elementi interattivi, regole del
  gioco: monete dai nemici, roulette del blocco ?, cuore con Yoshi/Toad, piattaforma rotante,
  asta della bandiera): https://mario.fandom.com/it/wiki/LEGO_Super_Mario. Il sito blocca curl e
  i lettori automatici, ma la sua API MediaWiki risponde:
  `https://mario.fandom.com/it/api.php?action=parse&page=LEGO_Super_Mario&prop=wikitext&format=json`.

- LEGO Wireless Protocol 3.0: https://lego.github.io/lego-ble-wireless-protocol-docs/
- bricklife/LEGO-Mario-Reveng: https://github.com/bricklife/LEGO-Mario-Reveng
- djipko/legomario.py: https://github.com/djipko/legomario.py
- node-poweredup: https://github.com/nathankellenicki/node-poweredup
- Firmware Broadcom del MacBook (05ac:8290), segnalazione su Launchpad con il file mancante:
  https://bugs.launchpad.net/ubuntu/+source/linux/+bug/1065400 ; pacchetto comunitario
  broadcom-bt-firmware: https://github.com/winterheart/broadcom-bt-firmware
