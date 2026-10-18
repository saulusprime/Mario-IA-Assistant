# Log delle sessioni dal vivo

Copie dei log prodotti dagli strumenti del progetto (nome del dispositivo anonimizzato).
Servono come prova documentale delle osservazioni riportate in README, HANDOFF e nelle tabelle di `mario/codes.py`.

| File | Contenuto |
|---|---|
| `live-checks-20261012-191422.txt` |  (0 kB) |
| `live-checks-20261012-191602.txt` | verifiche della sezione 1: pulsante, pantaloni, gesti e RGB in modalità combinata (566 messaggi 0x46), RSSI, allarmi, disconnect, off; nel passo `off`, dopo la riconnessione, gli avvisi compaiono due volte (difetto corretto il 18/10) (103 kB) |
| `monitor-20261011-170650.txt` | prima sessione `monitor` (11/10, 3 min 40 s): gesti, Start, batteria, eventi porta 3 (220 kB) |
| `monitor-20261012-180149.txt` | sessione `monitor` dell'utente (12/10) (7 kB) |
| `monitor-20261012-180642.txt` | sessione `monitor` dell'utente (12/10) (28 kB) |
| `monitor-20261012-181612.txt` | sessione `monitor` dell'utente (12/10) (14 kB) |
| `probe-port4-20261012-073251.txt` | scritture sulla porta 4 fuori livello: 0x51 accettato, 0x50/0x00 rifiutati, busy INVALID_USE (13 kB) |
| `probe-port4-20261012-173210.txt` | scritture sulla porta 4 in livello con rilettura: valore non conservato; ciclo del livello sulla porta 3 (12 kB) |
| `probe-port4-20261012-173722.txt` | 37 scritture sulla porta 7: nessun feedback, rilettura zero (24 kB) |
| `probe-port4-20261012-175026.txt` | osservazione di 3 minuti senza comandi (standby) (7 kB) |
| `probe-port4-20261012-175535.txt` | ripetizione delle prove 15-25 sulla porta 7 (controprova negativa) (23 kB) |
| `probe-port4-20261018-162743.txt` | scansione dei sottocomandi dei motori (0x01, 0x02, 0x05–0x08) su porta 4 e 7, firmware 6.5.03: sempre `COMMAND_NOT_RECOGNIZED` (23 kB) |
| `app-ufficiale-20261018.txt` | traffico dell'app ufficiale (log HCI snoop di un telefono Android, decodificato con `decode --summary`): connessione, istantanea della porta 3, richieste della porta 4, archivio delle partite della porta 7, un livello, cambi di volume (117 kB) |
| `history-20261018-194402.txt` | `./run.sh history`: le richieste di lettura dell'app ripetute dal nostro programma; istantanea della porta 3, risposte della porta 4, archivio di 2 partite (16 kB) |
| `re-checks-20261014-185746.txt` | `./run.sh re-checks` (14/10, riletto il 18/10): annuncio, GATT, tipo di sistema, sonno, porta 3 CHAL/VERS/DEBUG (traccia del firmware, Mario spento a metà), livello di 60 s con porte 4 e 7 mute. Dopo la riconnessione delle 19:00:43 ogni messaggio compare due volte (difetto corretto il 18/10) (136 kB) |
| `shell-20261011-171124.txt` | prima sessione `shell` (11/10) (3 kB) |
| `tiles-20261012-180737.txt` | prima sessione con le 8 basette reali: codici e contatori della porta 3 (26 kB) |
| `tiles-20261012-182334.txt` | seconda sessione con le basette: totale monete 0x386B, nemici 0x2042 (11 kB) |
