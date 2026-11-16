## 2026-11-15 — Output strutturato e diagnosi Ollama

- **Diagnosi:** il vecchio messaggio identifica il fallimento di `json.loads(message.content)`; il testo grezzo non era persistito, quindi non è possibile dimostrare se la risposta fosse troncata, avvolta o sintatticamente errata.
- **Decisione:** schema atomico per scena e controllo `/api/show`; disabilitare thinking solo quando `false` è dichiarato supportato (altrimenti `low` se disponibile o default). Normalizzazione limitata a wrapper, validazione rigida, un solo retry; niente fallback silenzioso.
- **Diagnostica/privacy:** log locali atomici (64 KiB/risposta, 20 file), metadati, risposta, errori HTTP/parser; prompt e percorsi esclusi, testo thinking escluso, redazione percorsi. La risposta può ripetere il brief e l’interfaccia lo segnala.
- **Evidenza:** Ollama 0.40.16/Qwen 3.5 4B attivi il 15/11; JSON valido con `think:false` nel test diretto semplice (~5,2 s); prova end-to-end completa 4 scene valide in 119,71 s. Nessun download. Renderer e Timeline JSON v1 invariati. Backup `backups/ai_director_ollama_json_diag_2026-11-15`.

## 2026-11-14 — AI Video Director Fase 2: Ollama solo locale e opt-in

- **Decisione:** integrare un adapter Ollama via HTTP loopback usando libreria standard Python; niente SDK, cloud o provider remoto. L’utente sceglie esplicitamente il modello locale già installato.
- **Modelli:** `/api/tags` serve solo a elencarli. La generazione rifiuta modelli assenti; non chiama pull, non installa runtime e non scarica modelli.
- **Sicurezza e integrità:** il modello riceve brief e catalogo con ID/filename/type/duration senza percorsi. Solo gli ID catalogati sono ammessi; la risposta deve seguire schema JSON e viene validata completamente prima di produrre un progetto. Errore/timeout non ripiega silenziosamente sul template.
- **Interfaccia:** controllo e generazione asincroni; bozza deterministica rimane selezionabile. Timeline JSON v1, renderer e decisione finale export restano invariati.
- **Ambiente verificato:** comando, percorsi standard e registro app Ollama assenti; localhost:11434 rifiuta connessione. Adapter verificato con test HTTP locale simulato; generazione con modello e prestazioni hardware restano da verificare dopo installazione/modello scelto dall’utente.

## 2026-11-14 — AI Video Director Fase 1: bozza locale, provider non configurato

- **Decisione:** avviare il flusso AI Director con orchestrazione e contratto `DirectorProvider`, ma non fingere che il generatore deterministico incluso sia un modello AI.
- **Operativo:** acquisizione brief, validazione media e durata, interpretazione, struttura/script/storyboard template, abbinamento ai media e creazione di un progetto apribile nell’editor con Timeline JSON v1.
- **Salvataggio/revisione:** brief e fasi restano in `project.settings.ai_director`; l’utente rivede il draft e il progetto nell’editor prima di avviare manualmente l’export.
- **Non disponibile:** provider AI, modello locale, download o generazione immagini/video/voce. Nessuna chiamata esterna e nessun costo introdotti.
- **Gate successivo:** scelta esplicita del runtime/provider, consenso per eventuali servizi/costi, verifica privacy/licenze e adapter validato con output strutturato e revisione umana.

# Decisioni e questioni aperte

## 2026-11-14 — backend: mantenere FFmpeg, nessun secondo motore per ora

- **Decisione:** conservare FFmpeg come unico renderer d'export; non introdurre MLT/GES finché il modello non necessita di clip liberamente sovrapposte e compositing multitraccia. Nessuna migrazione in formato nativo MLT/GES.
- **Stato della valutazione:** confronto documentale basato su fonti ufficiali e verifica della build locale; nessun benchmark runtime MLT/GES, perché non installati e non si installano dipendenze in questa attività.
- **Motivo:** renderer FFmpeg già integrato con timeline JSON v1, coda Job Manager, cancellazione, QC; MLT/GES aggiungerebbero un secondo stack codec/seek/packaging senza un requisito coperto male dall'attuale timeline sequenziale. Per un futuro pilot NLE, provare GES per primo e confrontarlo con MLT su fixture identiche.
- **Distribuzione:** la build locale FFmpeg è statica, GPLv3 (`--enable-gpl --enable-version3`) e include libx264. Non va considerata automaticamente distribuibile con un prodotto proprietario. Prima release commerciale: inventario SBOM/licenze/codec, decisione compatibilità licenza, analisi brevetti nei mercati, e build/encoder alternativi testati se si richiede un profilo non GPL.
- **Hardware:** export attuale software `libx264`; NVENC/D3D11VA presenti nella build ma non usati. GTX 1660 SUPER non è requisito runtime. Mantenere job seriale e proxy; misure comparative CPU/RAM restano da fare nel pilot.
- **Fonti ufficiali e confronto tecnico dettagliati:** vedere la sezione “Confronto backend video e decisione” in `ARCHITECTURE.md`.
- **Completamento 2026-11-14:** menu **Transizione** nel GUI, compositing preview Qt e propagazione al renderer via Job Manager condividono lo stile. La preferenza si conserva in `settings.editor.crossfade_style`; vecchi progetti usano `fade`; schema timeline v1 invariato. Test per tutti gli stili e round-trip.

## 2026-11-14 — raccolta esplicita delle risorse di progetto

- **Decisione:** i media restano riferimenti esterni fino a quando l'utente sceglie **Raccogli media**; l'operazione copia gli asset referenziati nella cartella progetto e salva riferimenti relativi.
- **Motivo:** rendere trasferibile un progetto senza imporre copie durante l'importazione né modificare gli originali.
- **Comportamento:** preflight completo dei riferimenti, copia in `media/` o `fonts/` con nomi content-addressed, sostituzione atomica del file di progetto e rollback delle copie create in caso di errore. Riferimenti già interni non vengono ricopiati.
- **Compatibilità:** schema progetto 1, Timeline JSON v1, renderer ed export invariati; nessuna dipendenza esterna.
- **Verifica:** test di copia audio/video-immagini/font, riuso, file mancante senza copie parziali e rollback al fallimento del salvataggio; suite completa.

## 2026-11-12 — audio continuo tra scene e durata finale comune a editor/export

- **Decisione:** l'audio viene sincronizzato quando parte la riproduzione o in seguito a un seek manuale; non viene riposizionato né ricaricato ai confini tra scene. La fine timeline è il massimo tra durata visiva e fine della musica. Una coda senza scena resta nera nell'export e vuota nel preview.
- **Motivo:** l'editor configurava i player a ogni cambio scena e terminava al termine dell'ultima immagine; il renderer limitava il mix alla durata video e `-shortest` poteva troncare il render se la voce era più breve.
- **Durata musica:** `background_audio.duration` se esplicita; altrimenti durata sorgente FFprobe meno `source_in`. Il valore è arrotondato al fotogramma successivo. Se le immagini durano più dell'audio, si mantiene il video e l'audio viene completato con silenzio fino alla fine.
- **Compatibilità:** timeline JSON v1 e renderer/Job Manager restano gli stessi servizi; non si aggiungono campi obbligatori o dipendenze. Testo/sottotitoli restano validati entro la durata visiva.
- **Verifica:** Il test Qt registra una sola richiesta al punto di sincronizzazione all'avvio e nessuna ai confini di due fotografie; il playhead continua nel tratto vuoto. Render FFmpeg e FFprobe verificano entrambi gli ordini di durata e l'audio udibile fino alla coda. La prova manuale con output audio sulla macchina dell'utente resta da fare.

## 2026-11-12 — importazione persistente, pagine preview esclusive e miniature immagini

- **Decisione:** una selezione multipla immagine/video viene validata come gruppo e salvata nel progetto prima di essere confermata nell'editor; il preview mostra una sola pagina media alla volta e le fotografie ricevono miniature nella traccia VIDEO.
- **Motivo:** prima dell'intervento l'importazione modificava solo la timeline in memoria e richiedeva un successivo salvataggio manuale. Il layout `StackAll` teneva invece simultaneamente visibili i widget immagine e video, causando una pagina nera o coperta in alcuni host Qt Windows. Le barre uniformi rendevano le foto non riconoscibili.
- **Implementazione:** il salvataggio resta atomico via core; fallimenti ripristinano il modello precedente. Cache LRU in memoria limitata a 96 miniature, con decodifica Qt ridotta alla risoluzione utile e invalidazione tramite metadati file.
- **Compatibilità:** invariati JSON v1, CLI, renderer, Job Manager e dipendenze. Nessun media viene copiato o modificato; audio e tracce diverse dalla VIDEO non cambiano.
- **Verifica:** test Qt automatizzati, inclusi due asset persistiti/riaperti, pagine preview immagine/video/vuoto e cache. La prova con `TEST_PRIMO_REEL_2` e i file originali resta da eseguire manualmente sul PC dell'utente: il progetto di prova osservato è vuoto e viene preservato.

## 2026-11-12 — intestazioni timeline non cancellano la selezione

- **Decisione:** i click nella colonna VIDEO/AUDIO/TESTI/SOTTOTITOLI sono consumati da `TimelineWidget` come click d'intestazione; i click nell'area temporale e sui clip mantengono i rispettivi comportamenti.
- **Motivo:** inoltrare il click di un'intestazione a `QGraphicsView` può cancellare la selezione visuale. La prova isolata riproduceva la perdita dell'evidenziazione, senza rimuovere oggetti o nascondere pannelli.
- **Compatibilità:** correzione del routing GUI; nessuna dipendenza, modifica schema JSON, renderer o Job Manager.
- **Verifica:** test Qt per quattro intestazioni, selezione persistente, visibilità di Inspector/preview, seek e selezione clip. La visibilità reale sull'installazione Windows dell'utente non è stata provata manualmente.

## 2026-11-11 — feedback visivo immediato per le scene della timeline

- **Decisione:** selezione clip, spostamento della testina e playback devono riflettere la scena corrente nel widget preview; la clip corrente è marcata con un contorno ad alto contrasto. Le immagini sono decodificate via `QImageReader` così gli errori riportano la causa e il percorso.
- **Motivo:** nella prova editor le scene potevano essere presenti nel modello ma il rapporto tra selezione, testina e immagine non era abbastanza evidente. Errori di decodifica vanno distinti dal messaggio generico d'importazione.
- **Compatibilità:** nessun nuovo campo o migrazione: il progetto continua a salvare la timeline JSON v1. Il progetto di prova dell'utente non viene toccato.
- **Verifica:** test Qt per due immagini, selezione/indicatore, seek, playback, salvataggio/riapertura e file non leggibile; suite completa e prova pratica isolate.

Questo registro separa decisioni approvate, fatti verificati, proposte e scelte ancora da prendere. Una proposta non diventa decisione senza conferma dell'utente.

## Decisioni approvate

| ID | Decisione | Conseguenza |
|---|---|---|
| D-001 | Evoluzione incrementale del CLI verso un'app desktop Windows audiovisiva. | Il codice funzionante viene preservato e ampliato, non sostituito senza migrazione verificata. |
| D-002 | Stack iniziale: Python per servizi/applicazione, PySide6 per GUI, FFmpeg/ffprobe come primo backend. | UI e modello non devono dipendere dalle opzioni specifiche del renderer. |
| D-003 | Mantenere compatibilità con il comando legacy durante l'evoluzione. | `scripts/reel_engine.py` resta entry point finché una migrazione esplicita non venga approvata. |
| D-004 | Le funzioni AI locali/cloud sono opzionali e devono lasciare controllo all'utente. | Niente consumo automatico di crediti né invio automatico di media a servizi remoti. |
| D-005 | Progetto, codice, test e documentazione permanente sono la fonte di verità; non la memoria di una singola AI. | Ogni collaborazione deve leggere i documenti prima di modificare. |
| D-006 | Un solo operatore autorizzato modifica i file alla volta. | Gli altri collaboratori restano in sola lettura fino al passaggio esplicito del controllo. |

## Fatti verificati, non decisioni

- La timeline corrente è schema v1 con canvas, audio principale e sottofondo opzionale, scene sequenziali immagine/video, testi/sottotitoli temporizzati e transizioni cut/crossfade/fade nero; il rendering usa FFmpeg.
- Il core salva progetti in `project.json` formato `reel-engine-project` versione 1, con timeline v1 incorporata; il salvataggio è atomico e conserva impostazioni/campi estesi.
- I riferimenti media interni sono persistiti relativi alla cartella progetto e risolti all'apertura; i percorsi esterni restano assoluti. I file mancanti vengono diagnosticati senza impedire l'apertura.
- Il core esegue render da progetti salvati tramite coda FIFO seriale; ogni lavoro ha workspace/log propri e pubblica l'export solo dopo controllo FFprobe e decodifica FFmpeg.
- Il Job Manager conserva un registro JSON atomico in `temp/jobs`; le code in attesa sopravvivono al riavvio, mentre i processi attivi non vengono ripresi e sono marcati interrotti.
- La GUI PySide6 resta un package separato e opzionale; il core non dipende da Qt. Il launcher `scripts/reel_engine_gui.py` collega Home, progetti recenti, diagnostica e l'editor visuale `TimelineEditorPage`, che usa il progetto JSON v1 e il Job Manager già esistente.
- L'editor visuale mostra immagini/video e overlay temporizzati, dispone le clip su righe video/audio/testi/sottotitoli, consente di modificare proprietà, salva nel progetto e invia export al Job Manager. L'importazione mantiene riferimenti ai file originali; non ne crea copie. Il preview riproduce scene in sequenza ma non compone crossfade in tempo reale.
- La suite unittest verifica validazione timeline, compatibilità CLI, render immagine/video, mix audio, testi/sottotitoli temporizzati e transizioni con fixture sintetiche e FFmpeg.
- Ambiente verificato: Python 3.14.3, FFmpeg/ffprobe 9.0.2; macchina con 16 GB RAM e GPU NVIDIA GTX 1660 SUPER da 6 GB.
- FFmpeg locale include `libx264`, `aac` e supporto dichiarato CUDA/NVENC; la distribuzione della build attuale richiede una revisione delle licenze.
- Tipo/velocità fisica dei dischi non sono stati rilevati.

## Proposte non ancora approvate

- Evolvere e migrare il formato progetto oltre la versione 1, mantenendo la compatibilità.
- Migliorare la raccolta media con progresso/cancellazione per asset molto grandi e prova manuale di trasferimento su un secondo percorso Windows.
- Aggiungere proxy/thumbnail e workspace dedicati per il preview.
- Aggiungere preview composito delle transizioni; riordino, trim e waveform audio sono ora disponibili.
- Valutare OpenTimelineIO per interscambio e MLT/GStreamer per editing multitraccia.
- Avviare AI con trascrizione/sottotitoli e aggiungere altri provider in fasi successive.

## Questioni aperte

1. Il software sarà personale, distribuito gratuitamente o commercializzato/proprietario?
2. È accettabile distribuire una build FFmpeg GPL con x264 oppure serve un diverso profilo di licenza/codec?
3. Quali formati e funzioni multitraccia sono indispensabili nella prima GUI?
4. FFmpeg rimarrà renderer finale anche con multitraccia, o MLT/GES dovrà diventare backend principale?
5. Quali sono frame rate, risoluzioni e durata massima target per testare performance e QC?
6. Quale hardware minimo deve supportare l'AI locale? È accettabile che alcuni provider richiedano GPU più capienti?
7. Quale strategia di raccolta, collegamento e trasferimento dei media è desiderata?
8. Quale percorso di distribuzione Windows (portable/installer) e aggiornamento è richiesto?

Quando una questione viene risolta, registrare la scelta, la motivazione, la data e gli effetti sui documenti collegati.

## Verifica dell'editor

- **Fatto verificato (2026-11-11):** un progetto sintetico con immagini, video e audio è stato importato, modificato, salvato, chiuso, riaperto e renderizzato con Job Manager/QC. Gli asset sorgente non sono cambiati.
- **Limite della verifica:** import e apertura sono stati pilotati dal runtime Qt e dalle API GUI; i file picker non sono stati azionati manualmente perché la finestra era minimizzata e l'automazione UI non poteva attivarla. Nessun problema funzionale è stato osservato nel flusso provato.


## 2026-11-12 — Audio principale facoltativo

La timeline v1 può omettere o impostare a `null` `audio`. Se presente `background_audio`, questa è usata da sola; se entrambe le tracce sono presenti si conserva il mix attuale; senza entrambe si esporta video-only. Il Job Manager verifica uno stream audio soltanto quando la timeline ne dichiara almeno uno. La scelta evita di alterare la GUI e la sincronizzazione già implementata e mantiene compatibilità con JSON v1 e CLI.


## 2026-11-12 — Riordino clip video

Il primo spostamento clip si limita al riordino della sequenza VIDEO. In timeline v1 le scene sono seriali e la posizione è derivata: introdurre posizioni arbitrarie richiederebbe modificare schema, validazione e renderer e non è necessario per questo incremento. L'audio, i testi e i sottotitoli sono temporizzati in secondi assoluti e restano fermi quando cambia l'immagine mostrata in quei secondi. Le transizioni tra scene seguono la scena identificata da `after`; si rifiuta il riordino che lascia il riferimento senza successore o viola i limiti di durata. Nessuna dipendenza aggiunta; il renderer e il formato v1 restano invariati.


## 2026-11-13 — Split clip sulla griglia frame

Lo split usa il frame più vicino al playhead, coerente con la quantizzazione del renderer. Richiede un frame pieno per ciascun segmento. La seconda metà di un video avanza `source_in`; per immagini viene riusato lo stesso asset. La transizione in uscita segue la seconda metà per preservare l'effetto alla fine della scena sorgente. Le transizioni incompatibili annullano l'operazione prima di alterare il progetto. Non si aggiunge una transizione automatica al nuovo confine e non si modificano renderer o schema timeline v1.


## 2026-11-13 — Seek preview differito al caricamento sorgente

Confermati split e renderer corretti: la seconda scena contiene il `source_in` frame-aligned, il progetto lo persiste e FFmpeg lo usa. La correzione resta nella GUI: non ricaricare la stessa sorgente tra segmenti e differire seek/play finché Qt non ha caricato un diverso video. Questa scelta risolve la ripartenza del preview senza modificare schema, renderer, mix audio o tempi overlay.


## Trim senza cambiare timeline JSON v1 (2026-11-13)

Il modello v1 dispone le scene in sequenza e non codifica posizioni indipendenti o gap. Il trim mantiene quindi la timeline sequenziale: dopo ogni variazione di durata, le scene successive si riallineano. Il trim sinistro avanza o arretra il punto sorgente del video e cambia la durata inversamente; per immagini cambia solo la durata. I tempi di audio, testi e sottotitoli non vengono spostati. Tagli che invalidano transizioni o lasciano testi/sottotitoli oltre il video sono respinti senza applicare modifiche.

È stato riusato il concetto d'interazione con maniglie ai bordi e aggancio ai frame, non codice dei progetti Kdenlive o Shotcut. Kdenlive è GPL-3.0-only oppure GPL accettata da KDE; Shotcut è GPL-3.0. Il riuso di sorgenti richiederebbe obblighi GPL e introdurrebbe dipendenze architetturali non necessarie per il core Python/PySide6. Riferimenti: [licenza Kdenlive](https://github.com/KDE/kdenlive/blob/master/COPYING), [licenza Shotcut](https://github.com/mltframework/shotcut/blob/master/COPYING), [manuale Kdenlive: editing](https://docs.kdenlive.org/en/cutting_and_assembling/editing.html), [guida Shotcut](https://www.shotcut.org/stockmedia/Shotcut%20User%20Guide.pdf). Il percorso di editing resta locale, gratuito e senza servizi AI.


## Undo/Redo tramite snapshot QUndoStack (2026-11-13)

Usare `QUndoStack` e `QUndoCommand` già inclusi in PySide6, senza dipendenze aggiuntive. Un comando contiene snapshot profondi della timeline prima/dopo, selezione e playhead; così un lotto importato, un drag completato o l'applicazione Inspector sono unità atomiche. Questo approccio è più semplice e meno fragile di comandi inversi specifici mentre il modello JSON v1 resta piccolo e sequenziale. Il limite è fissato a 100 comandi per contenere memoria. La cronologia non è persistente fra chiusura sessione/cambio progetto. Undo/Redo modifica lo stato in memoria e marca il progetto non salvato, senza autosave o operazioni sui media. Il salvataggio riuscito aggiorna il clean index; l'importazione conserva il salvataggio immediato preesistente e marca quello stato come pulito.


## Trim live come anteprima transazionale (2026-11-13)
Il trascinamento mostra subito la variazione della geometria, ma non modifica la timeline canonica fino al rilascio. Questo evita che ogni movimento produca un comando o un salvataggio; il rilascio passa dalla validazione esistente e aggiunge un solo snapshot QUndoStack. `Esc` o un candidato rifiutato ripristinano lo snapshot. Il bordo sinistro usa un roll con la scena precedente perché il JSON v1 non rappresenta gap o posizioni assolute; per la prima scena è rifiutato invece di cambiare modello o renderer. Il playhead e l'anteprima seguono il puntatore durante il drag, con frame quantizzati e aggiornamento preview limitato da timer.


## Snap magnetico basato su coordinate viewport (2026-11-13)
Lo snap usa una tolleranza fissa di 10 pixel misurata dopo la trasformazione della QGraphicsView, invece di una soglia in secondi o unità scena; questo mantiene la sensazione coerente al variare dello zoom. I target sono i bordi scena, i confini dei fade e il playhead. La guida appare soltanto quando si entra nella soglia. Per Trim/Roll la base è lo snapshot iniziale del gesto e il playhead viene congelato come target, evitando che i punti di aggancio si muovano durante il drag. Per conservare JSON v1 sequenziale, il drop clip continua a scegliere un punto d'inserimento; non sono introdotte posizioni temporali arbitrarie. Playhead trascinabile dalla scala superiore, evitando conflitti con i click delle clip. `N` e il pulsante controllano l'opzione. La posizione resta frame-aligned anche quando Snap è disattivato; il playhead non aggiunge operazioni Undo perché è un comando di navigazione.

## Waveform senza dipendenze aggiuntive (2026-11-13)
La forma d'onda è derivata dal file sorgente via FFmpeg già richiesto dal progetto; non viene memorizzata nella timeline JSON né nel progetto. I dati sono picchi min/max compatti e indipendenti da volume e `source_in`, che si applicano solo durante il disegno. Una cache locale rigenerabile, invalidata tramite metadati del file, evita decodifiche ripetute. Il calcolo avviene in thread di lavoro e il paint è limitato alla porzione visibile per non bloccare playback/rendering. Non si modifica mai la sorgente audio.

## Copia e duplicazione senza cambiare timeline v1 (2026-11-13)
La copia temporanea resta un dizionario scena in memoria dell'editor. Per evitare clipboard esterni e problemi di riferimenti tra progetti, è locale alla sessione e si azzera al cambio progetto. Duplica/incolla clonano tutti i campi scena supportati e assegnano un ID nuovo; non copiano le transizioni globali come proprietà scena. La timeline v1 non rappresenta posizioni arbitrarie: l'incolla usa il prossimo confine scena al playhead (o la fine), mentre la duplicazione è immediatamente adiacente alla selezione. Ogni inserimento passa dalla validazione delle transizioni e da un unico snapshot QUndoStack. Non si aggiunge multi-selezione perché il modello GUI corrente seleziona un solo elemento.

## Ripple Delete nel modello sequenziale v1 (2026-11-13)
Ripple Delete è un comando separato da **Elimina selezione**, per non cambiare l'uso preesistente. Le scene non hanno posizioni libere: il ripple rimuove la durata effettiva in frame e fa ricominciare subito la successiva; il contributo overlap dei crossfade adiacenti viene sottratto dalla durata rimossa. I due collegamenti di transizione che toccano la scena cancellata vengono rimossi, mentre i fade iniziale/finale restano associati ai bordi globali. Testi/sottotitoli sono intervalli assoluti e vengono trasformati con la stessa mappa temporale del taglio. Le tracce audio v1 sono sorgenti globali senza start/clip editabili, quindi restano continue e ancorate a zero invece di essere tagliate o spostate in modo non rappresentabile. Un'unica candidata e un singolo QUndoCommand mantengono validazione, annulla/ripristina e salvataggio coerenti.

## 2026-11-14 — Track Lock: stato blocco in project.settings, non in timeline JSON v1

- **Decisione:** lo stato di blocco delle cinque tracce (VIDEO, AUDIO, MUSICA, TESTI, SOTTOTITOLI) è salvato in `project.settings["editor"]["track_locks"]` come dizionario booleano, non nello schema timeline JSON v1.
- **Motivo:** il blocco traccia è una preferenza dell'editor, non un attributo del contenuto multimediale. Il renderer FFmpeg, la CLI e il Job Manager non devono conoscere né gestire il blocco; l'export deve funzionare normalmente anche con tracce bloccate. Salvarlo nella timeline v1 avrebbe richiesto modifiche allo schema e alla logica di validazione e rendering, violando il requisito di immutabilità dello schema.
- **Implementazione:** `_read_project_track_locks()` carica lo stato all'apertura del progetto con fallback `False` per ogni traccia mancante; `_write_project_track_locks()` persiste il dizionario nel salvataggio. Nessun campo nuovo viene aggiunto alla timeline JSON v1.
- **Compatibilità:** i progetti creati prima di questa modifica non contengono la chiave `track_locks`; l'editor li apre con tutte le tracce sbloccate, senza migrazione né errori. I progetti con tracce bloccate sono esportabili senza restrizioni: il renderer non legge `project.settings["editor"]["track_locks"]`.
- **Undo/Redo:** le operazioni bloccate non vengono aggiunte alla cronologia `QUndoStack`; lo sblocco di una traccia non ha un proprio comando Undo, è una preferenza di sessione. Il toggle lock marca il progetto non salvato così il valore sopravvive alla chiusura.
- **Verifica:** 6 test specifici (113 test totali passati); verifica manuale dell'apertura/chiusura e dell'export con tracce bloccate rimane consigliata sul PC Windows dell'utente.

## 2026-11-14 — Proxy come cache derivata, asset originale come riferimento canonico

- I proxy video sono cache rigenerabili sotto `<project>/.reel_engine/proxies`, non media inseriti nel documento o nella timeline JSON v1.
- La chiave deriva da percorso canonico e fingerprint del file (dimensione, mtime, ctime e file id); la transcodifica viene scritta temporaneamente e pubblicata solo se il sorgente è ancora identico.
- FFmpeg produce H.264 senza audio, lato massimo 960 px, due thread, preset `ultrafast` e CRF 30; un solo worker per editor riduce contesa CPU/memoria. Gli originali restano sempre nei dati di progetto e nell'export.
- `project.settings.editor.use_proxies` controlla la sola preview e ha default attivo per retrocompatibilità con i progetti esistenti.
## 2026-11-15 — Fase 0: guardia dirty e teardown Qt

- **Decisione:** tutte le uscite dall’editor che rimuovono o sostituiscono il contesto corrente passano dalla medesima scelta esplicita SALVA/SCARTA/ANNULLA. Un errore di scrittura non autorizza la transizione; SCARTA ripristina lo stato letto dal file.
- **Lifecycle:** sostituire lambda che catturano l’editor nei signal undo e crossfade con slot bound QObject e disconnetterli nella chiusura. Fermare timer/player e drenare i proxy cancellabili prima della distruzione GUI; eseguire la pulizia anche quando `close()` è invocato su un widget mai mostrato.
- **Compatibilità:** nessuna modifica a timeline v1, renderer, Job Manager, formato progetto o AI Director.
- **Verifica:** 17 test mirati, suite completa 160/160, `compileall` e `git diff --check` superati.

## 2026-11-15 — Fase 1: struttura UX editor

- **Decisione:** mantenere il progetto lineare e aggiungere una libreria media GUI separata. L’importazione registra i percorsi in `settings.editor.media_library`; inserire scene richiede una selezione esplicita. Le scene esistenti alimentano la libreria in lettura per la compatibilità.
- **Timeline:** header fissi fuori dall’area scrollabile e origine temporale zero nella vista; zoom locale per widget, senza cambiare il modello delle scene o aggiungere livelli.
- **Job Manager:** riusare `list_jobs`, `get_job` e `cancel`; la GUI mostra dati correnti e log senza duplicare la coda né cambiare le regole di rendering.
- **Prestazioni:** miniature video in un worker alla volta; selezione/importazione resta reattiva, workers cancellati alla chiusura.
- **Compatibilità:** nessun cambiamento a timeline JSON v1, renderer FFmpeg, core Job Manager o AI Director. Suite completa 163/163; `compileall` e `git diff --check` superati.
