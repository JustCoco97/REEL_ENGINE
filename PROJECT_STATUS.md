# Stato del progetto

Ultimo aggiornamento documentale: 2026-11-15. La GUI, il preview e l’export tramite Job Manager supportano i 22 stili crossfade; la preferenza è persistita nelle impostazioni progetto, con default `fade` per i progetti precedenti, senza modificare Timeline JSON v1. La baseline è del 14/11; la diagnosi Ollama aggiornata è riportata nella sezione seguente.

## Diagnostica Ollama — 2026-11-15

- Ollama 0.40.16 risponde via localhost; `qwen3.5:4b` installato. `/api/show` dichiara `thinking.default=true` e supporta `false`, sebbene il comando CLI non sia nel PATH.
- Il vecchio errore generico corrisponde al rifiuto in `json.loads(message.content)`. Poiché il testo grezzo non veniva salvato, la forma esatta della risposta storica non è recuperabile. Prova sintetica: thinking default circa 3 minuti e scene disallineate; `think:false` JSON valido in circa 5,2 s.
- Adapter ora usa schema atomico, controllo capability thinking, normalizzazione wrapper, parser/validator rigoroso, un solo tentativo correttivo e diagnostica locale limitata/redatta. GUI mostra la posizione della diagnostica; timeout predefinito a 300 s.
- Backup: `backups/ai_director_ollama_json_diag_2026-11-15`. Test mirati: 20 superati; suite completa: 154 test superati, 0 falliti in 55,99 s. Prova end-to-end Qwen: 4 scene valide, 119,71 s, un tentativo. Timeline JSON v1 e renderer invariati.

## Stato verificato del codice

- `scripts/reel_engine.py` resta l’entry point CLI compatibile e coordina parsing argomenti e modalità legacy/timeline. Il modello dati è in `src/reel_engine/model.py`, la validazione JSON v1 in `src/reel_engine/validation.py` e il rendering FFmpeg in `src/reel_engine/rendering.py`.
- Comandi supportati: modalità legacy `--images` con `--audio`; modalità `--timeline` con audio dichiarato nel JSON; entrambe accettano `--output`.
- Schema timeline v1: canvas (default 1080x1920, 30 fps), audio principale opzionale, sottofondo opzionale e una o più scene. Le scene possono essere di tipo immagine o video e descrivono id, asset, durata e `source_in`.
- La validazione controlla schema, campi, percorsi, suffissi, ID duplicati, durata, `source_in` e dimensioni pari del canvas.
- Il renderer implementa scene immagine e video: scala/ritaglio, zoompan, segmenti H.264 compatibili, concat e audio AAC. `source_in` e durata vengono allineati ai fotogrammi del canvas con arrotondamento al più vicino (mezzo frame verso l’alto); ogni segmento è limitato a un numero intero di frame. Le sorgenti video vengono sondate con ffprobe e i trim oltre la durata vengono rifiutati. Scene immagine e video possono convivere nella stessa timeline.
- Ogni rendering FFmpeg usa una directory temporanea univoca sotto `temp/renders` per segmenti e lista concat; la directory viene rimossa al termine o in caso di errore, senza riutilizzare gli intermedi condivisi.
- La timeline v1 può includere `background_audio` opzionale con asset, `source_in`, durata, volume tra 0 e 1, `fade_in` e `fade_out`; il mix è limitato alla durata visiva e viene codificato in AAC.
- La timeline v1 supporta inoltre `texts` e `subtitles`, con intervalli allineati ai frame, posizionamento a nove ancoraggi, font Windows o file TTF/OTF/TTC, dimensione, colore, contorno e box di sfondo. I testi sono UTF-8 e vengono composti da FFmpeg `drawtext` dopo il montaggio delle scene.
- L’oggetto opzionale `transitions` supporta fade_from_black, cut, crossfade e fade_to_black. Le durate si allineano alla griglia frame; ogni crossfade riduce la durata finale e i limiti impediscono sovrapposizioni eccessive. Il renderer compone segmenti immagine e video con filtri `concat`/`xfade` e mantiene audio e overlay sul tempo finale.
- Il core `src/reel_engine/project.py` crea, apre e salva progetti `project.json` formato versione 1 con timeline JSON v1, impostazioni ed estensioni conservate. Il salvataggio è atomico nella cartella progetto. I riferimenti media interni vengono serializzati relativi e risolti assoluti in memoria; quelli esterni restano assoluti. `diagnose_project` segnala media/font mancanti senza impedire l'apertura. API esportate: `create_project`, `open_project`, `save_project`, `update_project_timeline`, `diagnose_project`.
- Il core `src/reel_engine/jobs.py` gestisce lavori da progetto salvato con coda FIFO e worker seriale, stati queued/running/completed/failed/cancelled, avanzamento percentuale/fase, cancellazione FFmpeg, log per lavoro e workspace distinti in `temp/jobs`. Il registro `job_registry.json` è versionato e atomico; conserva la cronologia, ripristina la coda pending e marca come interrotto ogni lavoro running rilevato all'avvio (senza resume automatico). La pubblicazione è atomica dopo controllo presenza, stream audio/video, durata e decodifica FFmpeg; destinazione uguale al progetto o a media/font originali viene rifiutata.
- AI Video Director Fase 2: provider Ollama locale via HTTP loopback e modalità template; stato corrente e diagnostica aggiornati nella sezione del 2026-11-15 sopra. La nota di indisponibilità nella baseline 14/11 è storica e superata.
- `src/reel_engine/gui/` contiene la GUI opzionale PySide6: Home, creazione/apertura e recenti, diagnostica, editor visuale, anteprima immagini/video, timeline con tracce video/audio/testi/sottotitoli, inspector, importazione come riferimenti media, salvataggio del progetto e export attraverso Job Manager. Launcher: `scripts/reel_engine_gui.py`; dipendenza separata in `requirements-gui.txt`. Il core resta importabile senza Qt.
- Il preview riproduce in sequenza le sorgenti e sincronizza il cursore con audio principale e sottofondo; mostra gli overlay durante i rispettivi intervalli. La GUI offre un selettore per i 22 stili crossfade e il preview Qt compone lo stile selezionato; il Job Manager inoltra la stessa preferenza al renderer FFmpeg. La scelta è salvata nelle impostazioni del progetto, fuori dalla Timeline JSON v1; i progetti precedenti usano `fade` come default. L'importazione non copia i media; i file restano alle posizioni originali.
- Nella GUI, l'importazione immagine/video valida l'intero lotto e persiste le nuove scene subito tramite il salvataggio atomico del progetto; i percorsi restano risolvibili alla riapertura. Il preview separa lo stack esclusivo immagine/stato vuoto/video dal widget overlay, per evitare che la superficie video inattiva copra la pagina attiva. Le fotografie sono mostrate come miniature nella traccia VIDEO tramite decodifica Qt ridimensionata e cache LRU per widget limitata a 96 voci; le altre tracce non sono state ridisegnate.
- Un progetto vuoto è salvabile e modificabile, ma non renderizzabile finché non contiene audio principale, scene e risorse valide. Proxy avanzati restano futuri; generazione tramite vero provider AI, media/voce sintetici e servizio cloud restano non disponibili; waveform e drag clip sono disponibili.

## Test e verifiche

AI Video Director Fase 2 baseline 14/11: **149 test superati**. Aggiornamento 15/11: 20 test mirati e 154 test complessivi superati; prova locale Qwen end-to-end valida in 119,71 s.

`tests/test_timeline.py` contiene 26 test: diciassette di caricamento/validazione, uno di allineamento ai frame, sette render CLI reali e un controllo di isolamento concorrente. `tests/test_project.py` aggiunge 9 test per creazione, round-trip, conservazione dei dati, percorsi relativi e assoluti, file mancanti, errori di formato e fallimento atomico. `tests/test_jobs.py` contiene 12 test per FIFO su più progetti, stati/progresso reali, cancellazione in coda e tramite Job Manager di FFmpeg attivo, errori e destinazioni protette, isolamento, render/pubblicazione FFmpeg, persistenza della cronologia, recupero della coda e riconciliazione dopo il riavvio. `tests/test_gui.py` aggiunge 10 test per diagnostica dipendenze, creazione/apertura progetto, recenti, navigazione, dialogo, cartella progetti, validazione nome, messaggi d'errore e avvio event loop PySide6. I test CLI copiano anche `src/reel_engine/` nel sandbox temporaneo per verificare l’entry point rifattorizzato. Le fixture immagine PNG e audio WAV per i render sono generate dai test con la libreria standard e vivono in directory temporanee; il codice motore copiato per i test è anch'esso eseguito in sandbox temporanee. Il preflight verifica FFmpeg, ffprobe, `libx264` e `aac`.

Nella Fase B, il 2026-11-11, la suite completa ha riportato 11 test PASS. Nella Fase C, il 2026-11-11, sono stati aggiunti probe e render di scene video/miste; nell’incremento audio, test reali di mix, trim, volume, dissolvenze e durata; nell’incremento testi, validazione e render con font Windows e testo Unicode; nelle transizioni, test di crossfade misti, fade dal/al nero e sincronizzazione. La Fase D progetto è stata verificata con 35 test PASS. La Fase E Job Manager con 47 test PASS. La Fase F primo incremento desktop è stata verificata con 57 test PASS. Il secondo incremento aggiunge 3 test editor, incluso un rendering reale tramite Job Manager da un progetto con immagine, video, audio e testo. Suite documentata alla verifica precedente: **60 test PASS in 50,5 secondi**; comprende avvio dell'event loop Qt su Windows, persistenza e rendering FFmpeg. Esecuzione nel virtualenv documentato, con `TEMP`/`TMP` dedicati e `QT_QPA_PLATFORM=offscreen`; `git diff --check` completato senza errori (Git segnala solo la consueta conversione LF/CRLF per `ARCHITECTURE.md`). L'app è stata inoltre avviata su Windows e la finestra REEL ENGINE rilevata. Anteprima crossfade, proxy/cache, spostamento clip via drag, audio per scena, loop automatico sottofondo, animazioni/template e controlli QC avanzati restano pianificati.

## Ambiente e macchina

- Windows 11 25H2, build 26200.9711, edizione Home secondo i dati raccolti.
- Python 3.14.3 nel virtualenv `C:\Users\manue\venvs\video-ai`.
- FFmpeg/ffprobe 9.0.2 presenti; la build include `libx264`, `aac`, NVENC e supporto dichiarato a CUDA/DXVA/D3D.
- Hardware rilevato: Intel Core i5-11400F, 6 core/12 thread; 16 GB RAM; NVIDIA GeForce GTX 1660 SUPER, 6 GB VRAM.
- Al rilevamento Fase 11 la RAM libera era circa 1,23 GiB; è un dato istantaneo e non un requisito permanente.
- Volumi rilevati: C: circa 510,7 GB (265,1 GB liberi), etichetta Acer; D: Seagate Basic, circa 2 TB (1,95 TB liberi). La tecnologia fisica e la velocità dei dischi non sono state determinate.
- PySide6 6.12.0 è stato installato nel virtualenv per questa fase; PyAV, OpenCV, PyTorch, DirectML e ONNX Runtime non sono stati installati.

## Backup e materiali

- Backup pre-integrazione del motore presente in `backups/pre_fasi_1_2_2026-11-10/scripts/reel_engine.py`.
- Backup integrale esterno verificato nella fase precedente: `D:\VIDEO_AI\BACKUPS\REEL_ENGINE\REEL_ENGINE_full_2026-11-10`.
- `temp`, `temp/renders`, `temp/test_assets`, gli asset e i render sono materiali esistenti: non cancellarli o sovrascriverli come parte di una normale modifica al codice.

## Fasi documentate

- Fasi 1–2: analisi e progettazione iniziale; le modifiche timeline della copia di lavoro sono state integrate in script e test, preservando la modalità legacy.
- Fasi 7–9: test CLI resi isolati con fixture sintetiche; regex di riconoscimento encoder corretta; 10 test riportati PASS nella Fase 9.
- Fasi 10–11: ricerca tecnologica e ricognizione hardware in sola lettura.
- Fase 12: direzione architetturale approvata a livello di principi; dettagli e scelte tecniche aperte in `DECISIONS.md`.
- Fase 13: documentazione permanente iniziale.

Per il dettaglio delle decisioni vedere `DECISIONS.md`; per attività future vedere `ROADMAP.md`.

## Verifica pratica editor — 2026-11-11

Prova eseguita con l'interprete del virtualenv Windows e il codice del repository. Progetto indipendente: `C:\Users\manue\.codex\.chatgpt-projects\g-p-6af4d6a9ab1481919829d94dfe3460ba\pratica_editor_2026-11-11\project\project.json`; media sintetici: sottocartella `media`; export: `render\verifica_editor.mp4`. Nessun progetto o media preesistente è stato usato o alterato.

Verificati: apertura del progetto nell'editor; importazione di due immagini, un video e una traccia audio; presenza dei clip sulle tracce; selezione scena e modifica della durata; anteprima immagine e video decodificato da Qt; play/pause e posizione; salvataggio, chiusura e riapertura con durata e percorsi conservati; export tramite Job Manager. Esito `completed`, avanzamento 100%; FFprobe conferma H.264 1080x1920, AAC e durata 5,5 s. Gli hash SHA-256 dei quattro media sorgente prima e dopo il render coincidono.

La prova esercita l'editor Qt e gli stessi metodi chiamati dai pulsanti, ma la selezione file è stata passata ai metodi d'importazione in modo automatico, senza interazione manuale con la finestra di dialogo. L'interazione CUA non riusciva ad attivare la finestra minimizzata; il runtime Qt nativo Windows è stato comunque avviato e verificato. Non sono emersi problemi funzionali che richiedano correzioni al codice. La verifica non sostituisce un breve test manuale dei dialoghi file e dell'usabilità visiva su questa macchina.

## Correzione editor Fase F — 2026-11-11

- **Problemi riprodotti e corretti:** il feedback della selezione e della scena corrente era poco leggibile; la selezione ora ha un bordo giallo evidente. Il preview delle immagini passa esplicitamente dalla decodifica Qt con messaggi per immagine mancante/illeggibile e il messaggio d'importazione non copre più l'errore. La selezione di una clip mostra subito la scena; seek e playback aggiornano preview e clip selezionata a ogni cambio scena. L'importazione multipla associa ciascun percorso a una scena timeline distinta; salva e riapri conservano scene e asset senza variazioni allo schema v1.
- **Test automatici:** suite completa `unittest discover -s tests -v`, 62 test superati con Python `C:\Users\manue\venvs\video-ai\Scripts\python.exe`; `TEMP` e `TMP` puntavano a una cartella isolata sotto il progetto ChatGPT. Sono inclusi due immagini di colore distinguibile, seek, selezione/evidenziazione, avanzamento temporizzato, round-trip progetto, immagine danneggiata e rendering FFmpeg/Job Manager.
- **Prova pratica Windows/Qt:** finestra `TimelineEditorPage` mostrata e pilotata con un progetto nuovo in `C:\Users\manue\.codex\.chatgpt-projects\g-p-6af4d6a9ab1481919829d94dfe3460ba\work\pratica_editor_correzione_2026-11-11_completa\progetto`. Importate due immagini sintetiche e audio WAV più lungo della timeline, verificati preview, seek, playback, salvataggio/chiusura/riapertura; Job Manager ha prodotto e pubblicato `render\reel.mp4`. FFprobe ha trovato H.264 e AAC, durata 3,3 s (coerente con le scene); FFmpeg ha decodificato il file senza errori. Cartella indipendente dal progetto `ESEMPIO` e dai suoi media.
- **Compatibilità/limiti:** timeline JSON v1, CLI, rendering e struttura GUI conservati. Il preview delle transizioni resta un taglio diretto (crossfade e fade sono visibili nell'export). Prima della correzione audio descritta più sotto, l'export terminava alla fine dell'audio principale quando questo era più corto della timeline; la nuova verifica copre esplicitamente il caso inverso e mantiene la durata visiva. Nessun media originale è stato modificato.
- **Backup mirato pre-modifica:** `backups/fase_f_editor_preview_selezione_2026-11-11`.

## Click intestazioni timeline — 2026-11-12

- `TimelineWidget` intercetta i click nella colonna delle quattro intestazioni e li consuma senza inoltrarli alla scena Qt; la selezione grafica non viene cancellata da questi click. I click temporali emettono ancora `seek_requested`; i click sulle clip continuano a selezionare e passare la selezione all'editor.
- **Test mirato:** superato `test_track_header_click_keeps_selection_and_timeline_clicks_still_work`, che prova ciascuna intestazione, stato selezionato, visibilità dei widget Inspector/preview, seek e selezione/anteprima della seconda clip.
- **Suite completa:** 63 test superati con `C:\Users\manue\venvs\video-ai\Scripts\python.exe -m unittest discover -s tests -v`; `TEMP` e `TMP` isolati sotto `C:\Users\manue\Documents\Codex\work\reel_engine_temp\fase_f_headers_full_2026-11-12`.
- **Portata della verifica:** il test Qt controlla che i widget Inspector e preview restino visibili durante i click sulle intestazioni. Non è stata eseguita una verifica manuale della finestra completa nell'ambiente Windows dell'utente; quindi non dichiaro risolto l'eventuale problema distinto di scomparsa dei pannelli osservato lì.
- **Compatibilità e sicurezza:** nessuna modifica al JSON v1, core, renderer o Job Manager; ESEMPIO e media originali non usati o modificati. Backup: `backups/fase_f_click_intestazioni_2026-11-12`.

## Fase F — importazione, anteprima e miniature — 2026-11-12

Problema riprodotto nel codice: le fotografie importate erano aggiunte solo alla timeline in memoria e il messaggio chiedeva di salvarle; la chiusura prima del salvataggio lasciava `timeline.scenes` vuoto nel progetto. Il progetto `TEST_PRIMO_REEL_2` osservato in precedenza contiene effettivamente una timeline vuota; i percorsi originali delle due fotografie non erano disponibili in questa sessione. Non è stato modificato alcun progetto o media dell'utente.

Correzioni implementate: importazione a lotto con salvataggio atomico prima della conferma in GUI e rollback in memoria in caso di errore; preview in pagine esclusive immagine/stato vuoto e video con overlay separato; miniature delle sole scene immagine nella traccia VIDEO. Cache LRU in memoria (massimo 96 voci), chiave con percorso, mtime e dimensione, lettura ridimensionata e riuso tra rebuild. JSON v1, renderer, Job Manager e dipendenze invariati.

Test mirati iniziali: `tests.test_editor`, 9 test PASS, includendo persistenza/riapertura di due foto, preview vuoto/immagine/video, riuso cache e rendering Job Manager FFmpeg reale. Suite completa eseguita con `C:\Users\manue\venvs\video-ai\Scripts\python.exe -m unittest discover -s tests -v`, `TEMP`/`TMP` isolati e `QT_QPA_PLATFORM=offscreen`: **67 test PASS in 12,6 secondi**, con FFmpeg reale nei test render e runtime Qt. `git diff --check` è pulito. Stato Git in sola lettura: file modificati/non tracciati preesistenti restano presenti insieme al lavoro di questa fase; nessuna operazione Git di modifica è stata eseguita. La prova pratica con le fotografie e `TEST_PRIMO_REEL_2` sul PC dell'utente non è stata eseguita; non dichiarare quel caso risolto fino a tale prova.


## Continuità audio e durata progetto — 2026-11-12

Diagnosi del comportamento precedente: i confini scena richiamavano `_configure_audio_players`, che riposizionava e riavviava la musica; la durata editor era solo quella delle scene; il renderer tagliava il mix a tale durata e usava `-shortest` quando non c'era sottofondo, consentendo a una voce più corta di troncare il video.

Correzioni: il player audio viene sincronizzato all'avvio e sui seek espliciti, non al cambio scena. Editor e renderer calcolano la fine come massimo tra scene e sottofondo; senza `background_audio.duration`, FFprobe misura il file e si sottrae `source_in`, con allineamento al frame. Nel tratto dopo l'ultima scena l'anteprima mostra una schermata vuota e il playhead continua. L'export estende il video con nero e mantiene l'audio. Se le scene sono più lunghe, l'export conserva i frame e completa l'audio con silenzio. Formato timeline v1 e dipendenze invariati.

Verifica mirata: test Qt su due fotografie e musica (punto di sincronizzazione registrato: una chiamata all'avvio e nessuna ai cambi scena; clock portato fino alla coda vuota e alla fine); test FFmpeg con musica 2,0 s oltre scene 1,0 s (FFprobe 2,0 s, tono musica presente a 1,7 s e frame nero); caso inverso con scene 1,0 s e voce 0,3 s (video 1,0 s, stream audio mantenuto fino alla fine); transizioni con visual 2,0 s e musica 2,5 s (50 frame a 20 fps).

Suite completa: **70 test superati** in tre gruppi che terminano correttamente nel processo richiesto: editor Qt 11, GUI Home 10, core/Job Manager/FFmpeg 49. La discovery unificata esegue tutti i 70 test senza errori/assertion, ma il processo Python termina con un access violation nativo dopo il riepilogo `OK` quando i due moduli Qt condividono un solo processo; i gruppi Qt separati terminano con codice 0. Non ho nascosto questo limite del runner. La verifica effettiva dell'uscita audio e della sincronizzazione percepita sul PC Windows dell'utente resta da eseguire.


## Fase F — audio principale facoltativo — 2026-11-12

- La validazione accetta `audio: null` e il campo audio omesso mantenendo schema timeline JSON v1. Il modello tipizza ora l'audio principale come opzionale.
- Il renderer usa il sottofondo come sorgente unica quando non esiste l'audio principale; con entrambe le tracce conserva il mix esistente, con il solo audio principale conserva il percorso esistente e senza audio genera un video senza stream audio.
- Il Job Manager richiede una traccia audio in controllo qualità solo quando la timeline dichiara audio principale o sottofondo.
- Test reali mirati FFmpeg/FFprobe superati: musica sola (incluse 2 immagini più musica più lunga), nessun audio, export silenzioso via Job Manager. I test preesistenti verificano audio principale solo e mix delle due tracce. Copertura CLI e JSON v1.
- Test mirati eseguiti con il virtualenv indicato e TEMP/TMP isolati: 3 test nuovi PASS. Suite completa: `unittest` ha riportato 73 test e `OK`, ma il processo è terminato con access violation Qt in chiusura. Esecuzione separata: 52 test core/FFmpeg PASS con exit code 0; 10 test GUI PASS con exit code 0; 11 test editor riportano `OK` ma riproducono access violation Qt dopo i test.
- Nessuna modifica a editor, GUI o sincronizzazione. Nessuna dipendenza aggiunta.


## Riordino clip video — 2026-11-12

- Aggiunto drag and drop nella sola traccia VIDEO per inserire una scena prima o dopo le altre. Il gesto si attiva oltre la soglia standard Qt e mostra una linea d'inserimento; il click semplice resta selezione.
- L'ordine della lista `scenes` è il modello temporale: il renderer calcola la posizione in sequenza da durata e crossfade. Il riordino conserva gli oggetti scena senza alterare asset, durata o `source_in`; aggiorna start, playhead, selezione e preview. Audio, testi e sottotitoli restano invariati nei rispettivi tempi assoluti.
- Le transizioni `between` restano associate all'ID `after`; il riordino è rifiutato se rende una transizione orfana, supera la durata di una scena con crossfade o rende troppo breve la scena iniziale/finale per le dissolvenze terminali.
- Persistenza verificata con salvataggio e riapertura progetto. JSON v1 e renderer non modificati.
- Limite: il modello resta sequenziale; non sono supportati gap, sovrapposizioni libere o spostamento temporale indipendente dall'ordine.
- Verifica suite separata: 52 test core/FFmpeg PASS (exit 0), 10 test GUI PASS (exit 0), 14 test editor riportano `OK` ma il processo Qt termina con access violation in chiusura (stesso limite già osservato). Il nuovo test drag isolato passa con exit 0. `git diff --check` senza errori.


## Split scene — 2026-11-13

- L'Inspector offre `Dividi scena · S`; il tasto `S` opera quando la timeline ha il focus, senza interferire con i campi testo dell'Inspector. La scena selezionata viene divisa al frame più vicino alla testina, richiedendo almeno un frame per ogni parte.
- Le due scene mantengono l'asset e tutte le proprietà; le immagini riutilizzano lo stesso file. Per i video il secondo `source_in` è il punto ingresso precedente più la durata frame-aligned della prima parte. La somma delle durate mantiene la durata originale.
- Lo split aggiunge un cut. La transizione in uscita collegata alla scena originale viene trasferita alla seconda parte. I limiti delle transizioni esistenti sono controllati prima di mutare la timeline; un caso incompatibile viene rifiutato senza perdita di dati. Audio, testi e sottotitoli restano invariati ai tempi assoluti.
- Compatibilità timeline JSON v1; renderer e progetto core invariati. Test Qt coprono immagini, video/source_in, soglie ai bordi, scorciatoia, transizioni, tracce temporizzate e riapertura progetto.
- Limite: lo split è un taglio diretto; nessuna transizione viene creata automaticamente nel punto di divisione. La posizione è quantizzata ai frame, non a sub-frame.
- Test separati: 52 core/FFmpeg e 10 GUI PASS con exit code 0; 17 test editor riportano `OK`, ma il processo Qt termina con l'access violation in chiusura già osservata nelle sessioni precedenti. `git diff --check` senza errori.


## Correzione continuità preview video dopo split — 2026-11-13

- Diagnosi: split produce correttamente `source_in` della seconda clip (`source_in` originale + frame della prima parte); project JSON salva e riapre il valore; il renderer applica `-ss` alla sorgente prima di `-i`. Il difetto era nel preview Qt: `setPosition()` e `play()` erano chiamati subito dopo `setSource()`, mentre la lettura è asincrona, e lo stesso file veniva ricaricato al passaggio tra segmenti.
- Correzione circoscritta al player: per segmenti sulla stessa sorgente non ricarica il file; per nuove sorgenti applica seek e play dopo `LoadedMedia`/`BufferedMedia`.
- Verifica FFmpeg reale con sorgente blu nei primi 1,5 s e rossa negli ultimi 1,5 s: export da due scene sullo stesso video, `source_in` 0/1,5 e durate 1,5/1,5; FFprobe conferma 3 s e i frame confermano il passaggio blu/rosso.
- Verifica Qt: split video, preview alla posizione 1,5 s, salvataggio e riapertura, seek e playback attraverso il taglio senza reset.
- Il renderer, audio, testi, sottotitoli, transizioni e JSON v1 non sono stati modificati.

- Suite separata dopo la correzione: 53 test core/FFmpeg PASS (exit 0), 10 GUI PASS (exit 0), 18 editor tutti `OK`; il processo del gruppo editor termina con il problema di chiusura Qt già noto. I due test mirati preview e render sono stati eseguiti anche singolarmente; il test preview isolato termina con exit 0.


## Incremento TRIM professionale (2026-11-13)

Completate le maniglie di trim sulle clip video: bordo destro per cambiare durata, bordo sinistro per regolare il punto d'ingresso. I delta vengono quantizzati al frame del canvas; il minimo è un frame. Per video, `source_in` viene aggiornato e il renderer conserva il punto di uscita sorgente; per immagini cambia soltanto la durata. Le scene successive vengono ricalcolate secondo la sequenza JSON v1. Audio, testi, sottotitoli e transizioni non vengono riscritti; tagli incompatibili con transizioni o con gli intervalli di testo vengono rifiutati. Sono stati aggiunti test di trascinamento Qt, limiti temporali, persistenza e video FFmpeg sintetico. Il codice e i test sono verificati nell'ambiente Windows di sviluppo; la prova manuale sull'installazione dell'utente resta da eseguire.


## Incremento Undo/Redo editor (2026-11-13)

Completata la cronologia locale basata su `QUndoStack`/`QUndoCommand` PySide6, con snapshot prima/dopo della timeline e contesto selezione/playhead. Sono coperti importazione multipla, rimozione, riordino, split, trim, durata/`source_in`, audio, testi/sottotitoli e transizioni coinvolte dalle operazioni. Pulsanti e scorciatoie `Ctrl+Z`/`Ctrl+Y` seguono lo stato della cronologia. Il marker pulito della cronologia segnala le modifiche non salvate; Undo/Redo non eseguono scritture. L'importazione conserva il precedente salvataggio immediato. Il limite è 100 operazioni; la cronologia non viene serializzata e viene svuotata quando si apre un progetto. Verifica: 27 test editor superati (riepilogo unittest `OK`, con codice di uscita 1 alla chiusura Qt già nota) e 63 test GUI/core/Job Manager/FFmpeg superati con codice 0; 90 test totali. I test coprono stato vuoto dopo Undo e rendering reale esistente.


## Trim live professionale (2026-11-13)
Il drag dei bordi delle clip è interattivo: clip e scene successive si ridisegnano durante il movimento con quantizzazione al frame; playhead e anteprima seguono la posizione. Il rilascio produce un solo snapshot Undo/Redo; `Esc` o una modifica non valida ripristinano lo stato precedente. Il bordo sinistro esegue un roll con la scena precedente, mantenendo la fine della clip selezionata e aggiornando `source_in` per i video; per la prima scena il bordo sinistro è intenzionalmente indisponibile poiché JSON v1 non rappresenta gap iniziali. Sono stati verificati trim destro foto/video, trim sinistro video, annullamento, Undo/Redo, salvataggio/riapertura e un export reale con FFmpeg/Job Manager e durata verificata con FFprobe. La verifica manuale sulla postazione Windows dell'utente resta da eseguire.


## Snap magnetico professionale (2026-11-13)
Aggiunto snap nella timeline per riordino, Trim Live, Roll Edit e trascinamento del playhead dalla scala superiore. I punti includono inizi/fini scena, confini interni delle transizioni e playhead. La guida tratteggiata gialla segnala l'aggancio; la tolleranza è fissa a 10 pixel del viewport e viene misurata dopo la trasformazione visiva, indipendentemente dallo zoom. Pulsante e scorciatoia `N` attivano/disattivano la funzione. Anche a snap spento gli spostamenti restano frame-aligned. Riordino e ciascun gesto Trim/Roll producono una sola voce Undo; il playhead è navigazione e non genera una voce di modifica. Nessun cambiamento a JSON v1, renderer o media.


### Verifica automatica Snap (2026-11-13)
La suite completa finale ha superato **95 test** (32 editor Qt e 63 GUI/core/Job Manager/FFmpeg) in 30,7 secondi, con processo terminato correttamente (codice 0). I tre test Snap eseguiti isolatamente sono passati; la prova di riordino ha salvato/riaperto il progetto e prodotto un MP4 verificato con FFprobe. In un'esecuzione separata del solo gruppo editor i 32 test avevano riportato `OK`, ma il teardown Qt aveva restituito il codice Windows `-1073740791`; l'anomalia non si è ripetuta nella suite completa finale. Nessun test ha usato progetti o media originali.

## Waveform audio — 2026-11-13
Implementata la forma d'onda per le tracce audio e background audio. FFmpeg analizza il segnale in un worker Qt; i picchi compatti sono mantenuti in cache RAM e in cache locale compressa, identificata da percorso e metadati del file. La timeline disegna soltanto la regione esposta, adeguandosi a zoom, scorrimento, `source_in` e volume. Le modifiche del file vengono rilevate e invalidano la cache. Il modello JSON v1, il renderer e i media originali non sono cambiati. Verifica: suite completa, 100 test superati (30,6 s), inclusi FFmpeg reale, cache, asset lungo, paint/zoom, Undo/Redo, salvataggio/riapertura e render Job Manager. Resta da fare una conferma visiva manuale nella finestra Windows dell'utente.

## Copia, incolla e duplicazione clip — 2026-11-13
Nell'editor `Ctrl+C` copia la scena selezionata (immagine o video), `Ctrl+V` la inserisce al taglio valido corrispondente al playhead e `Ctrl+D` la duplica immediatamente dopo l'originale. Ogni inserimento assegna un ID univoco e conserva proprietà scena e asset; la timeline viene validata con le regole esistenti per le transizioni. Inserimento/duplicazione producono ciascuno un solo comando Undo e ripristinano selezione/playhead; salvataggio e riapertura mantengono le scene. Audio, musica, testi, sottotitoli e transizioni non vengono riscritti. Il buffer è interno all'editor, non tocca i file multimediali e si svuota al cambio progetto. La selezione multipla non esiste nell'editor attuale e non è stata introdotta. La sequenza JSON v1 limita l'incolla ai tagli fra clip; playhead interno viene mappato al taglio successivo. Verifica finale: tutti i 103 test riportano `OK` in 30,13 s, inclusi i tre test di copia/incolla/duplicazione e i test esistenti FFmpeg/Qt; `py_compile` superato. Il processo termina però con codice Windows -1073740791 (access violation) durante la chiusura Qt, dopo il riepilogo `OK`, anomalia già osservata in precedenza nei test dell’editor.

## Ripple Delete — 2026-11-13
Aggiunto il comando per rimuovere una scena video selezionata e compattare la sequenza. Il ripple usa tempi frame-aligned e considera gli overlap crossfade ai due lati; rimuove le transizioni incidenti, conserva quelle non coinvolte e mantiene i fade terminali entro la durata delle scene rimaste. Gli intervalli di testi/sottotitoli vengono rimappati sul nuovo tempo: quelli tagliati si accorciano, quelli completamente nel tratto rimosso vengono eliminati, quelli successivi arretrano. Audio e background audio restano tracce continue ancorate a zero, come nel modello v1; non sono audio clip indipendenti. Anteprima, selezione e playhead si aggiornano tramite il percorso snapshot. Pulsante Inspector e shortcut `Shift+Delete`; una voce Undo/Redo e salvataggio/riapertura compatibili. Il comando esistente **Elimina selezione** resta invariato. Test specifici coprono prima, centrale, ultima e unica scena, crossfade, audio, testi/sottotitoli, Undo/Redo, round-trip e compatibilità con Snap, Copy/Paste, riordino, Trim, Split e waveform. La suite completa riporta 107 test `OK` in 34,36 s e `py_compile` passa; il processo termina con codice Windows -1073740791 durante il teardown Qt dopo il riepilogo `OK`, anomalia già nota.

## Blocco e sblocco tracce (Track Lock) — 2026-11-14
Implementato il blocco e sblocco indipendente per ciascuna delle cinque tracce: `VIDEO` (`scene`), `AUDIO` (`audio`), `MUSICA` (`background_audio`), `TESTI` (`text`) e `SOTTOTITOLI` (`subtitle`).
- I pulsanti toggle nella testata della timeline sincronizzano il proprio stato e permettono di bloccare singole tracce lasciando le altre modificabili.
- Le clip sulle tracce bloccate mostrano etichetta `[LOCKED]`, opacità ridotta (0.58) e inibizione del cursore di trim/drag sui bordi.
- Tutte le azioni di scrittura sono protette: `add_media_paths`, `import_video_or_images`, `add_audio_path`, `import_audio`, `add_overlay`, `move_scene`, `split_selected`, `trim_scene`, `copy_selected_scene`/`paste_scene`/`duplicate_selected_scene`, `delete_selected`, `ripple_delete_selected` e `apply_properties` nell'Inspector.
- Ripple Delete controlla le tracce testo e sottotitoli: se queste tracce sono bloccate e contengono elementi successivi al taglio che verrebbero accorciati o traslati, il ripple viene rifiutato con messaggio di blocco dedicato.
- Undo e Redo controllano preventivamente se il comando da annullare/ripristinare tocca campi di tracce attualmente bloccate; in tal caso l'azione viene rifiutata senza intaccare lo stack, e ridiventa disponibile appena la traccia viene sbloccata.
- Lo stato viene salvato in `project.settings["editor"]["track_locks"]`; la timeline JSON v1 resta inalterata. Il salvataggio riuscito aggiorna il dirty indicator, e la riapertura ripristina lo stato dei pulsanti. I progetti con tracce bloccate restano validati ed esportabili dal Job Manager e dal renderer FFmpeg.
- Verifica completa: 113 test superati in 99,1 s (6 nuovi test mirati in `tests/test_editor.py` e 107 test preesistenti, 0 errori/fallimenti).

## Preview crossfade immagine/video — 2026-11-14
Il preview GUI compone crossfade immagine-immagine, immagine-video, video-immagine e video-video durante seek e riproduzione. Due `QMediaPlayer` senza uscita audio e due `QVideoSink` acquisiscono frame video in modo asincrono; i frame statici vengono riusati da una cache LRU limitata a 16 immagini. Un seek aggiorna i decoder; durante la riproduzione la sincronizzazione corregge derive superiori a 100 ms con intervallo minimo di 250 ms, evitando seek continui e decodifiche ripetute. Le proporzioni restano preservate e il preview ordinario viene ripristinato dopo la dissolvenza. Timeline JSON v1 e renderer FFmpeg invariati.
Verifiche: test mirati per tutte le combinazioni immagine/video e l'allineamento dei due source time; suite completa: **116 test superati, 0 falliti**. Controllo sintattico AST superato. Resta consigliata una prova manuale su Windows con clip reali.

## Proxy video — 2026-11-14
Proxy H.264 fino a 960 px generati in background in `.reel_engine/proxies` nel progetto. La cache è indicizzata con hash del percorso canonico e fingerprint (dimensione, mtime, ctime e identificatore file); un originale cambiato/replacement non riusa una versione obsoleta. La conversione viene pubblicata atomicamente solo dopo verifica che il sorgente non sia cambiato durante il lavoro. Il toggle **Usa proxy** persiste in `project.settings.editor.use_proxies`, con default attivo per i progetti esistenti. Il JSON timeline conserva sempre l'asset originale: preview normale e decoder dei crossfade risolvono il proxy solo per la riproduzione, mentre export e Job Manager leggono l'originale. Errori e annullamento lasciano l'originale utilizzabile. Verifica: suite completa **122 test superati, 0 falliti**; controllo sintattico AST superato.
# Raccolta esplicita dei media — 2026-11-14

Implementata l'azione **Raccogli media** nell'editor e l'API core `collect_project_media`. L'azione precontrolla tutte le risorse referenziate, copia quelle esterne in `media/` e i font in `fonts/`, assegna nomi basati sul contenuto, aggiorna i riferimenti e salva il progetto. Le sorgenti originali restano intatte; i riferimenti già interni al progetto non vengono copiati. Se un asset manca, l'operazione si ferma prima di copiare; se la copia o il salvataggio falliscono, i riferimenti vengono ripristinati e le nuove copie rimosse. Timeline JSON v1, renderer ed export non sono stati modificati. La copia GUI è sincrona; una prossima estensione può aggiungere progresso/cancellazione per file molto grandi. Verifica finale: **126 test superati, 0 falliti** in 50,41 s; parsing AST di tutto il codice `src/` e `tests/` superato. Dopo il riepilogo `OK` PySide6 emette warning di callback `QUndoStack` distrutta già osservati nel teardown dei test, senza cambiare l'esito della suite (exit code 0).
## Fase 0 — Integrità progetto e stabilità GUI (2026-11-15)

- AUD-01 risolto: Home, sostituzione progetto e chiusura principale passano da un’unica guardia dirty. Il dialogo esplicito offre **SALVA / SCARTA / ANNULLA**; il fallimento del salvataggio blocca la transizione. SCARTA rilegge il file progetto salvato e azzera lo stato dirty senza cambiare il formato.
- AUD-02 risolto nel codice: le callback `canUndoChanged`/`canRedoChanged` e quelle dei player crossfade sono slot bound al widget, disconnessi nel teardown. La chiusura della finestra invoca il teardown editor che ferma timer e player, cancella e attende i proxy; il refresh differito del watcher audio ora usa un timer figlio e uno slot bound.
- Test aggiunti per scelte e fallimento salvataggio, Home/cambio progetto/chiusura, teardown con attività timer, notifiche undo tardive e creazione/chiusura ripetuta dell’editor. Test mirati: 17 superati; suite completa: **160 test superati, 0 falliti** in 53,60 s. `compileall` e `git diff --check` superati.
- La chiusura cancella i proxy e attende il completamento dei worker cancellabili prima del teardown. Non modifica l’esecuzione del Job Manager né forza la cancellazione dei render attivi.
- Backup pre-modifica: `backups/phase0_project_integrity_gui_stability_2026-11-15` (11 file sorgente/test/documentazione).

## Fase 1 — UX e organizzazione editor (2026-11-15)

- Layout adattabile con libreria media a sinistra, preview centrale, Inspector/Esportazioni a destra e timeline inferiore; pannelli ridimensionabili tramite splitter.
- Import foto/video indipendente dall’inserimento in timeline; ricerca, filtri Foto/Video, selezione multipla, nomi/percorso tooltip e miniature foto/video. Le miniature video sono estratte da FFmpeg in worker con cancellazione alla chiusura. La libreria viene persistita in `project.settings.editor.media_library`; i media selezionati si inseriscono esplicitamente con il pulsante dedicato. Le scene già presenti nei vecchi progetti compaiono nella libreria.
- Intestazioni esterne e fisse per VIDEO, VOCE/AUDIO, MUSICA, TESTI e SOTTOTITOLI; colori distinguono voce e musica. Aggiunti Zoom +, Zoom − e Fit; origine temporale condivisa tra righello, clip, seek, drag, trim, snap e playhead.
- Scheda Esportazioni collegata al Job Manager attuale: barra percentuale, fase, annullamento, cronologia, stato/errori e log selezionabile. Nessun cambiamento al core Job Manager.
- Verifiche mirate: test libreria/thumbnail/filtri/inserimento/persistenza, header/zoom/scroll/allineamento e Job Manager progress/cancel/storico/log superati. Suite completa: **163 test superati, 0 falliti** in 52,10 s; `compileall` e `git diff --check` superati.
- Backup pre-modifica (10 file): `backups/phase1_editor_ux_2026-11-15`.
