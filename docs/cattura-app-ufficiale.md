# Cattura del traffico dell'app ufficiale LEGO Super Mario

Obiettivo: vedere cosa scrive l'app ufficiale sulle porte 4 (`Events`, 0x55) e 7 (`Telemetry`, 0x60),
che con i comandi standard LWP3 restano inerti, e dare un nome agli eventi della porta 3 incrociandoli
con quello che l'app mostra sullo schermo. Il PC non può ascoltare il traffico tra telefono e Mario:
la cattura si fa sul telefono e si decodifica qui con `./run.sh decode`.

Nessun file di cattura va committato: metterlo in `logs/` (ignorata da git). Un bugreport Android
contiene molti dati personali (account, reti, posizione): da tenere sul PC e cancellare dopo l'uso.

## Android

1. **Opzioni sviluppatore**: Impostazioni → Informazioni sul telefono → toccare sette volte "Numero
   build"; poi Impostazioni → Sistema → Opzioni sviluppatore.
2. **Attivare "Abilita log HCI snoop Bluetooth"** (su alcuni telefoni: "Attivato" oppure "Attivato,
   filtrato": serve *Attivato*, non filtrato). Poi **spegnere e riaccendere il Bluetooth** (su molti
   telefoni serve un riavvio perché l'impostazione abbia effetto).
3. **Giocare con l'app** seguendo il copione più sotto, annotando l'ora di ogni azione.
4. **Estrarre il log** con `adb` dal PC (pacchetto `adb` su Debian, Debug USB attivo sul telefono):

   ```sh
   adb bugreport logs/bugreport-mario.zip
   ./run.sh decode logs/bugreport-mario.zip --summary
   ```

   `decode` cerca da solo `FS/data/misc/bluetooth/logs/btsnoop_hci.log` (e `.last`, il file precedente),
   il percorso Samsung `/data/log/bt/btsnoop_hci.log` (anche `.cfa`), e in mancanza le sezioni
   `BTSNOOP_LOG_SUMMARY` del testo del bugreport.
5. Senza `adb`: Opzioni sviluppatore → "Segnalazione di bug" → "Report completo"; lo zip arriva nelle
   notifiche e si può condividere con il PC. Il risultato è lo stesso.

**Se `decode` dice "pacchetti ACL troncati"**: il log HCI snoop non era attivo (o era "filtrato") e il
bugreport contiene solo il riassunto *btsnooz*, che tiene i comandi HCI interi ma taglia i dati ACL,
cioè proprio i messaggi LWP3. Riattivare il log al punto 2, riavviare il telefono e rifare la cattura.

Al termine disattivare il log HCI snoop: altrimenti il telefono continua a registrare tutto il traffico
Bluetooth (anche delle cuffie).

## iPhone / iPad

1. Da un computer scaricare il profilo "Bluetooth Logging" per iOS dalla pagina Apple "Profiles and
   Logs" (serve un account sviluppatore Apple, anche gratuito) e installarlo sull'iPhone.
2. Giocare con l'app seguendo il copione.
3. Avviare un *sysdiagnose* (premere insieme i due tasti del volume e il tasto laterale per circa 1,5 s;
   il telefono vibra) e dopo qualche minuto esportare l'archivio da Impostazioni → Privacy e sicurezza →
   Analisi e miglioramenti → Dati di analisi (file `sysdiagnose_...tar.gz`).
4. Dentro l'archivio, in `logs/Bluetooth/`, ci sono i file PacketLogger (`.pklg`):

   ```sh
   ./run.sh decode logs/stack-mario.pklg --summary
   ```

   `decode` riconosce il formato PacketLogger (big o little endian) dal contenuto. In alternativa, con un
   Mac e Xcode, PacketLogger (Additional Tools) cattura in diretta dall'iPhone collegato via USB.

Il lettore PacketLogger e quello btsnooz sono stati provati solo su file sintetici (il btsnoop completo di
Android invece su una cattura vera, il 18/10/2026): la prima cattura
vera va confrontata con `--json` e, in caso di dubbi, con Wireshark.

## Copione di gioco (da annotare con gli orari)

Serve una sequenza breve e riconoscibile, così gli eventi si allineano a quello che fa l'app:

1. Accendere Mario, aprire l'app, collegare Mario (pairing dall'app). Annotare l'ora.
2. Restare fermi 30 secondi senza toccare nulla (traffico di fondo: che cosa legge o scrive l'app?).
3. Leggere la basetta **Start**, aspettare 5 secondi.
4. Un **Goomba**, poi una basetta che dà **monete** (per esempio l'aereo del set 71425), poi la
   **Rotazione** facendo tre giri.
5. **Cambiare i pantaloni** se se ne ha un altro paio.
6. Lasciare che il **tempo scada** senza muovere Mario (circa un minuto).
7. Nell'app, se c'è, cambiare il **volume** di Mario e il **nome**: sono scritture LWP3 note (proprietà
   0x12 e 0x01) e servono da riferimento per riconoscere la direzione dei messaggi.
8. Scollegare dall'app, spegnere Mario.

## Analisi

```sh
./run.sh decode logs/bugreport-mario.zip --summary                 # conteggi per tipo e porta, eventi porta 3
./run.sh decode logs/bugreport-mario.zip --no-default-modes        # i modi delle porte solo da ciò che l'app imposta
./run.sh decode logs/bugreport-mario.zip --type 41,42,81 --port 4  # cosa scrive l'app sulla porta 4
./run.sh decode logs/bugreport-mario.zip --port 7                  # porta 7 in entrambe le direzioni
./run.sh decode logs/bugreport-mario.zip --json > logs/app.jsonl   # per confronti con i log di monitor
```

Domande a cui la cattura deve rispondere (TODO, sezione 2): quali modi l'app attiva su ogni porta e
con quale delta; se e cosa scrive sulle porte 4 e 7 (sottocomando, payload, momento); quali eventi della
porta 3 arrivano in corrispondenza di monete, nemici, cambi di pantaloni e fine livello; se usa messaggi
LWP3 fuori standard. Il risultato va riportato in `mario/data/mario_codes.json` (eventi), in `ASIS.md` e,
se emergono scritture utili, in `tools/probe_port4.py`.

## La prima cattura (18/10/2026): cosa ha funzionato

- **Telefono Samsung con Android 12.** Il Debug USB cadeva una volta al secondo ("Protocol error" nel log di
  `adb`) anche dopo aver rifatto l'autorizzazione; il **Debug wireless** ha funzionato: Opzioni sviluppatore →
  Debug wireless → "Associa dispositivo con codice di associazione", poi dal PC
  `adb pair <ip>:<porta del riquadro> <codice>` e `adb connect <ip>:<porta della schermata principale>`.
- **Controllo che il log sia attivo** prima di giocare: `adb shell dumpsys bluetooth_manager | grep -i snoop` deve
  dire `mSnoopLogSettingAtEnable = full` (le proprietà `persist.bluetooth.*` su quel telefono restavano vuote).
- **Il file**: nel bugreport il log completo era in `FS/data/log/bt/btsnoop_hci.log` (percorso Samsung); `decode`
  lo trova da solo. Tenuto solo quel file, cancellato il resto del bugreport.
- **Cosa ha mostrato**: le richieste che l'app fa alla connessione (istantanea della porta 3, porta 4 come canale
  richiesta/risposta, archivio delle partite della porta 7) e nessun comando di suono durante il gioco. Dettagli
  in `ASIS.md` (sezione 2) e testo decodificato in `docs/sessioni/app-ufficiale-20261018.txt`.
- Alla fine vanno spenti sul telefono il log HCI snoop (altrimenti registra tutto il Bluetooth) e il Debug wireless.
