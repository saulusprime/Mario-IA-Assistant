# Le righe di `monitor --json` e il ponte verso altri programmi

`./run.sh monitor --json` scrive sullo standard output **una riga JSON per ogni messaggio
spontaneo di Mario** (valori dei sensori, proprietà, errori); i messaggi di avanzamento
("Connesso a ...", "Notifiche attive ...") vanno sullo standard error. Le stesse righe, con
tre campi in più, escono da `./run.sh decode FILE --json`, che rilegge un log salvato o una
cattura. Gli stessi dizionari girano nella dashboard (flusso `/api/stream`, database) e
nell'assistente.

Per usarle in un altro programma ci sono due strade:

- leggere le righe e interpretarle da sé (schema qui sotto);
- usare il ponte `./run.sh bridge`, che le traduce in **fatti con un nome** (gesto, basetta,
  colore, pantaloni, fase del livello, monete, pulsante, batteria, avventura) e li pubblica
  su MQTT e/o su un WebSocket (seconda parte).

## Campi comuni

| Campo | Tipo | Significato |
|---|---|---|
| `ts` | numero | istante di arrivo, secondi Unix con decimali (in `decode` c'è invece `when`, il testo dell'orario del log) |
| `type` | testo | tipo del messaggio LWP3: `PORT_VALUE_SINGLE`, `PORT_VALUE_COMBINED`, `HUB_PROPERTIES`, `HUB_ATTACHED_IO`, `HUB_ALERTS`, `GENERIC_ERROR`, ... |
| `text` | testo | descrizione leggibile, la stessa della riga di `monitor` senza `--json` |
| `raw` | testo | byte del messaggio in esadecimale separati da spazi, header compreso |

Solo in `decode --json`: `seq` (numero progressivo), `when` (orario del log, se c'è),
`direction` (`<-` da Mario, `->` verso Mario).

## Valore di una porta: `PORT_VALUE_SINGLE`

| Campo | Tipo | Significato |
|---|---|---|
| `port` | intero | porta di Mario (0 accelerometro, 1 scanner, 2 pantaloni, 3 eventi di gioco, 4 e 7 porte sconosciute, 6 tensione) |
| `mode` | intero o null | modo attivo della porta quando è arrivato il valore |
| `io_type` | intero o null | tipo del dispositivo: 71 = `0x47` accelerometro, 73 = `0x49` scanner, 74 = `0x4A` pantaloni, 70 = `0x46` eventi, 85 = `0x55`, 96 = `0x60`, 20 = `0x14` tensione |
| `io_name` | testo o null | nome del tipo (solo in `monitor`) |
| `data` | testo | byte del valore in esadecimale |
| `values` | lista di numeri | valori secondo il formato del modo (int8/int16/int32, con segno) |
| `decoded` | testo o null | interpretazione con le tabelle di `mario/data/mario_codes.json` |

Esempi (da `decode --json`, accorciati):

```json
{"type": "PORT_VALUE_SINGLE", "raw": "06 00 45 00 00 80", "port": 0, "mode": 1, "io_type": 71, "data": "00 80", "values": [-32768], "decoded": "gesto: salto (jump) (0x8000)"}
{"type": "PORT_VALUE_SINGLE", "raw": "08 00 45 01 02 00 25 00", "port": 1, "mode": 0, "io_type": 73, "data": "02 00 25 00", "values": [2, 37], "decoded": "barcode 0x0002 = Goomba (...); colore 0x0025 = verde (erba)"}
{"type": "PORT_VALUE_SINGLE", "raw": "05 00 45 02 12", "port": 2, "mode": 0, "io_type": 74, "data": "12", "values": [18], "decoded": "pantaloni: Fuoco (Fire) (0b010010)"}
{"type": "PORT_VALUE_SINGLE", "raw": "08 00 45 03 03 18 04 00", "port": 3, "mode": 2, "io_type": 70, "data": "03 18 04 00", "values": [6147, 4], "decoded": "evento ... (0x1803 = fase del livello ...) valore 4"}
```

Come leggere `data` per i modi di default di `monitor`:

- **gesti** (porta 0, modo 1): intero a 16 bit little endian, un bit per gesto (`0x8000` salto,
  `0x0800` caduta, `0x0100` rotazione, `0x0010` scossa, `0x0001` urto, tabella `gestures`);
  `values` lo mostra con il segno, quindi il salto vale -32768: meglio rileggere `data`.
- **scanner** (porta 1, modo 0): `[basetta LE16][colore LE16]`; `ffff` vuol dire "niente",
  `ff ff ff ff` è Mario sollevato.
- **pantaloni** (porta 2, modo 0): un byte, i 6 bit bassi sono il codice (tabella `pants`).
- **eventi di gioco** (porta 3, modo 2): `[codice LE16][valore LE16]`; il codice si scrive
  come `0x1803`, cioè classe `0x18` e numero `0x03` (tabella `game_events`, ASIS sezione 2).
- **tensione** (porta 6, modo 0): valore grezzo, 0..2705 corrisponde a 0..3300 mV.

## Modalità combinata: `PORT_VALUE_COMBINED`

Con `monitor --combined` (accelerometro + gesti, scanner + RGB) i valori arrivano insieme e
il programma li ricompone per modo:

| Campo | Tipo | Significato |
|---|---|---|
| `port`, `io_type`, `io_name` | | come sopra; `mode` è null |
| `modes` | oggetto | per ogni modo (chiave testuale `"0"`, `"1"`, ...): `name`, `data`, `values`, `decoded` |
| `decoded` | testo | i modi uniti con ` \| ` |

## Proprietà dell'hub: `HUB_PROPERTIES`

Solo gli aggiornamenti (operazione `UPDATE`) hanno i campi decodificati:

| Campo | Tipo | Significato |
|---|---|---|
| `prop` | testo | nome della proprietà: `BUTTON`, `BATTERY_VOLTAGE`, `RSSI`, `VOLUME`, ... |
| `value` | vario | valore decodificato: `true`/`false` per il pulsante, percentuale per la batteria, dBm per l'RSSI |

```json
{"type": "HUB_PROPERTIES", "raw": "06 00 01 02 06 01", "prop": "BUTTON", "value": true}
{"type": "HUB_PROPERTIES", "raw": "06 00 01 06 06 50", "prop": "BATTERY_VOLTAGE", "value": 80}
```

## Altri tipi

`HUB_ATTACHED_IO` ha solo `port`; `HUB_ALERTS`, `GENERIC_ERROR` e gli altri hanno i soli campi
comuni (il significato è in `text`, i byte in `raw`).

Nella dashboard il flusso `/api/stream` aggiunge `seq` e `interesting`, e porta anche eventi
che non vengono da Mario: `type` = `ADVENTURE` (partite del designer: `kind`, `adventure`,
`step`, `score`, `text`) e quelli dell'assistente.

---

# Il ponte: `./run.sh bridge`

Traduce gli eventi in fatti e li pubblica. Solo libreria standard (client MQTT 3.1.1 con QoS 0
e server WebSocket scritti in `mario/bridge.py`), nessuna dipendenza da installare.

```sh
# da monitor (il ponte legge lo standard input)
./run.sh monitor --json | ./run.sh bridge --mqtt localhost

# dalla dashboard: Mario accetta una sola connessione, quindi con la dashboard aperta si legge da lei
./run.sh bridge --dashboard http://127.0.0.1:8765 --ws 8766
./run.sh bridge --dashboard http://127.0.0.1:8765 --token SEGRETO --mqtt broker.casa:1883 --topic giochi/mario

# rileggere un log salvato
./run.sh decode logs/monitor-AAAAMMGG-HHMMSS.txt --json | ./run.sh bridge --print
```

Opzioni: `--mqtt HOST[:PORTA]`, `--topic` (prefisso, default `mario`), `--mqtt-user`,
`--mqtt-password`, `--client-id`; `--ws PORTA` e `--ws-host` (default `127.0.0.1`, cioè solo
questo PC: `0.0.0.0` lo apre alla rete di casa, senza autenticazione); `--print` stampa i fatti
(è il comportamento senza `--mqtt` e `--ws`); `--raw` pubblica anche ogni evento grezzo come
fatto di tipo `raw`.

## I fatti

Ogni fatto è un oggetto JSON con `kind` e `ts`. Su MQTT va sull'argomento `<topic>/<kind>`
(per esempio `mario/gesture`); `pants`, `battery`, `level` e `coins` sono pubblicati con
*retain*, così chi si collega dopo trova l'ultimo valore. Sul WebSocket ogni fatto è un
messaggio di testo.

| `kind` | Campi | Da dove viene |
|---|---|---|
| `gesture` | `bits` (`"0x8000"`), `names` (`["salto"]`), `unknown_bits` se ci sono bit senza nome | porta 0, modo 1 (anche combinato) |
| `tile` | `code` (`"0x0002"`), `name` (`"Goomba"`), `family` (`start`, `goal`, `enemy`, `prize`, `friend`, `unknown`) | porta 1, basetta letta |
| `color` | `code` (`"0x0025"`), `name` (`"verde"`) | porta 1, colore sotto i piedi |
| `lifted` | | porta 1, scanner vuoto (Mario in mano) |
| `pants` | `code` (`"0x12"`), `name` (`"Fuoco"`) | porta 2 |
| `level` | `phase`: `inizio`, `ultimi_secondi`, `fine`, `uscita` | porta 3, evento `0x1803` |
| `coins` | `total` | porta 3, evento `0x386B` (monete del livello) |
| `game_event` | `code` (`"0x1803"`), `value`, `description` (prima parte della voce della tabella) | porta 3, ogni evento |
| `button` | | pulsante premuto |
| `battery` | `percent` | proprietà `BATTERY_VOLTAGE` |
| `adventure` | `event` (`start`, `step`, `success`, `fail`, `timeout`, `finish`, ...), `adventure`, `step`, `score`, `text` | solo dalla dashboard: partite del designer di avventure |
| `raw` | `event` (l'evento grezzo intero) | solo con `--raw` |

Esempio di un salto sopra un Goomba durante un livello, con `--print`:

```json
{"kind": "gesture", "ts": 1760803210.41, "bits": "0x8000", "names": ["salto"]}
{"kind": "tile", "ts": 1760803210.93, "code": "0x0002", "family": "enemy", "name": "Goomba"}
{"kind": "color", "ts": 1760803210.93, "code": "0x0025", "name": "verde"}
{"kind": "coins", "ts": 1760803214.20, "total": 3}
{"kind": "game_event", "ts": 1760803214.20, "code": "0x386B", "value": 3, "description": "totale monete del livello"}
```

## Notifiche dalle avventure

L'azione `webhook` di un'avventura manda lo stato della partita con un POST HTTP. Per MQTT o
WebSocket basta il ponte collegato alla dashboard: ogni evento della partita (inizio, passo,
successo, passo perso, fine) arriva come fatto `adventure`, su `mario/adventure`.

## Consumatori di esempio

Con `mosquitto_sub` (pacchetto `mosquitto-clients`):

```sh
mosquitto_sub -h localhost -t 'mario/#' -v
```

Una pagina web che ascolta il WebSocket:

```html
<script>
  const ws = new WebSocket("ws://127.0.0.1:8766/");
  ws.onmessage = (m) => {
    const fact = JSON.parse(m.data);
    if (fact.kind === "gesture" && fact.names.includes("salto")) document.body.style.background = "gold";
  };
</script>
```

In Python, senza librerie, il client di prova dei test (`tests/browser.py`, classe `WebSocket`)
basta per leggere i messaggi.
