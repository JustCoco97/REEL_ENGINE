## Diagnostica Ollama/Qwen 3.5 — 2026-11-15

Ollama 0.40.16 risponde a localhost e `qwen3.5:4b` è installato, anche se CLI assente dal PATH. `/api/show` dichiara thinking predefinito attivo e `false` supportato. Prova sintetica: output JSON valido in ~5,2 s con `think:false`; ~3 min e liste scena disallineate con default. Il run originario non salvava risposta grezza: è noto il fallimento di `json.loads(message.content)`, non la forma esatta dell’output.

Completati: gestione capability, schema atomico, normalizzazione e validazione, un solo retry, diagnostica persistente limitata e redatta; 20 test mirati e 154 test complessivi superati; prova Qwen end-to-end riuscita con 4 scene in 119,71 s. Backup `backups/ai_director_ollama_json_diag_2026-11-15`.

**Prossimo blocco concreto:** ripetere il brief e il catalogo media reali dalla GUI. In caso di errore, esaminare il file diagnostico indicato dalla finestra, inclusi tempo, metadati e risposta grezza limitata. Non scaricare modelli né cambiare renderer/Timeline v1.

## AI Video Director Fase 2 — Ollama locale (2026-11-14)

È disponibile la modalità **AI locale tramite Ollama** oltre alla bozza deterministica Fase 1. `OllamaProvider` implementa `DirectorProvider` con `GET /api/tags` e `POST /api/chat`, JSON Schema, timeout, limite di risposta e sole connessioni loopback. Il dialogo GUI offre verifica, lista/configurazione modello e timeout; richieste e generazione sono asincrone. Il modello deve essere già installato: nessun download o servizio remoto. Il catalogo inoltrato contiene ID e metadati senza percorsi; gli ID sconosciuti/risposte malformate vengono rifiutati, senza fallback di successo. Output in Timeline JSON v1 e metadati `project.settings.ai_director`; preview e export sono ancora sotto controllo umano.

La rilevazione del 14/11, quando il server non rispondeva, è superata dal test del 15/11 riportato sopra; CLI ancora assente dal PATH. Nessun software/modello installato o scaricato durante questa diagnosi.

Suite completa Fase 2: **149 test superati, 0 falliti** in 48,55 s; AST di sorgenti e test valido.

**Prossimo blocco concreto:** dopo che l’utente avrà installato/avviato Ollama e scelto/caricato un modello localmente, provare su brief/media di test isolati, misurare memoria/tempo/qualità, correggere le differenze del modello/schema e verificare il round-trip del progetto prima di raccomandare un modello predefinito.

## AI Video Director — primo flusso operativo (2026-11-14)

La Home ora apre **AI Video Director · bozza guidata**. L’utente inserisce argomento, durata, formato, istruzioni e seleziona immagini/video; dopo una preview della bozza può creare il progetto e continuare nell’editor esistente. `reel_engine.director` orchestra interpretazione, narrativa, script guida, storyboard, abbinamento media e preparazione Timeline JSON v1. Brief e storyboard vengono conservati in `project.settings.ai_director`; l’export resta una decisione manuale dell’utente.

**Trasparenza:** non è configurato alcun provider AI. Il solo generatore incluso (`local-template`) è deterministico e non usa un modello; non vengono fatte chiamate cloud né applicati costi. Il contratto `DirectorProvider` è pronto per un adapter futuro. Per video soli, la durata richiesta deve essere coperta dalla durata totale delle sorgenti; immagini selezionate possono coprire il resto. Backup prima della modifica: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\ai_video_director_fase1_2026-11-14`. Test mirati: `python -m unittest tests.test_director -v`. Verifica finale: **142 test superati, 0 falliti** in 41,34 s; AST di sorgenti e test valido.

**Prossimo blocco concreto:** scegliere in modo esplicito runtime AI locale oppure provider cloud; prima di integrare un servizio esterno occorre autorizzazione e definizione di costi, privacy, licenze e consenso. Non installare dipendenze o inviare brief/media finché questa scelta non è presa.

# Riprendere REEL ENGINE

## Confronto backend e funzione CLI successiva — 2026-11-14

Decisione architetturale: mantenere FFmpeg come unico motore di export; nessun MLT/GES aggiunto. Per un futuro editor con clip sovrapposte/effetti multitraccia, pilotare GES per primo e confrontarlo con MLT su fixture identiche, preview, seek, CPU/RAM e packaging Windows. Nella macchina corrente MLT/GES non sono installati; la valutazione fatta ora è documentale, non benchmark. La build FFmpeg locale dichiara GPLv3 statica e `libx264`: prima della distribuzione commerciale serve revisione delle licenze effettive e dei codec/patenti; vedere matrice, fonti ufficiali e gates in `ARCHITECTURE.md` e `DECISIONS.md`.

Gli stili `xfade` sono selezionabili anche dall'editor: menu **Transizione**, anteprima composita live e preferenza `project.settings.editor.crossfade_style`; la stessa preferenza salvata viene usata dal Job Manager. Vecchi progetti usano `fade`, Timeline JSON v1 non cambia. Il CLI conserva `--crossfade-style`. Test mirati: `python -m unittest tests.test_editor.TimelineEditorTests.test_crossfade_style_picker_defaults_to_fade_and_persists_outside_timeline tests.test_editor.TimelineEditorTests.test_selected_wipe_style_changes_preview_pixels tests.test_editor.TimelineEditorTests.test_all_supported_crossfade_styles_compose_preview_frames tests.test_jobs.JobManagerTests.test_job_manager_uses_saved_crossfade_style_from_editor_settings tests.test_jobs.JobManagerTests.test_old_project_job_manager_defaults_to_fade_crossfade_style -v`. Suite completa: da aggiornare dopo l'esecuzione finale.

## Ultimo incremento — raccolta media (2026-11-14)

Nel progetto aperto, premi **Raccogli media** per copiare gli asset esterni referenziati in `media/` e i font in `fonts/`. Il comando lascia gli originali intatti, salva riferimenti relativi e non modifica timeline JSON v1. Tutti i riferimenti sono controllati prima della copia; file mancanti o errori di scrittura non aggiornano parzialmente il progetto. API core: `reel_engine.collect_project_media(project)`, che restituisce il numero di nuovi file copiati. Backup precedente alle modifiche: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\raccolta_media_progetto_2026-11-14`.

Test mirati: `python -m unittest tests.test_project.ProjectTests.test_collect_external_assets_copies_and_persists_relative_refs tests.test_project.ProjectTests.test_collect_media_missing_reference_aborts_without_partial_copy tests.test_project.ProjectTests.test_collect_media_save_failure_restores_refs_and_removes_new_copies tests.test_project.ProjectTests.test_collect_media_interrupted_copy_cleans_temporary_file_and_folder -v`. Suite completa finale: **126 test superati, 0 falliti** in 50,41 s; AST di `src/` e `tests/` valido. Dopo il riepilogo `OK` compaiono callback Qt tardive su `QUndoStack` già distrutta, ma il processo termina con codice 0. TEMP/TMP usati: `C:\Users\manue\Documents\Codex\reel_engine_test_temp`.

Prossimo controllo: prova manuale di raccolta e riapertura su una copia di progetto Windows, poi misurare l'esperienza con file molto grandi; la raccolta GUI attuale è sincrona. La prossima attività in ROADMAP è il prototipo comparativo FFmpeg/MLT/GES, seguito dalla decisione del renderer. In questa installazione sono presenti FFmpeg ma non `melt`, GStreamer/GES o i binding Python; il confronto richiede una configurazione d'ambiente dedicata e una valutazione licenze/packaging prima di scegliere un backend.

Queste istruzioni servono per continuare il lavoro da un altro account ChatGPT o Codex. Sono pensate come promemoria operativo: non serve conoscere il codice per aprire l'app o per chiedere una nuova verifica.

## Dove si trova il progetto

La cartella principale è `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE`.

Chiedi a Codex di aprire questa cartella come progetto. Prima di modificare qualcosa, chiedigli di leggere `CONTINUE_HERE.md`, `AGENTS.md`, `PROJECT_STATUS.md`, `README.md`, `ARCHITECTURE.md`, `DECISIONS.md` e `ROADMAP.md`. I backup sono conservati sotto `backups/`; non ripristinarli automaticamente.

## Aprire REEL ENGINE

Sul PC Windows usiamo Python nel virtualenv:

`C:\Users\manue\venvs\video-ai\Scripts\python.exe`

La GUI usa PySide6; installazione dichiarata in `requirements-gui.txt`. FFmpeg e FFprobe devono essere disponibili nel PATH (ambiente verificato: FFmpeg 9.0.2). Apri PowerShell ed esegui:

```powershell
$py = 'C:\Users\manue\venvs\video-ai\Scripts\python.exe'
& $py 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\scripts\reel_engine_gui.py'
```

Dalla Home puoi creare o aprire un progetto; il progetto si apre nell'editor. “Salva” o `Ctrl+S` conserva le modifiche. “Esporta video” invia il progetto al Job Manager e pubblica il risultato soltanto dopo i controlli.

## Eseguire i test

I test automatici usano unittest e alcuni creano media sintetici con FFmpeg. Per tenere TEMP/TMP separati dal materiale del progetto:

```powershell
$py = 'C:\Users\manue\venvs\video-ai\Scripts\python.exe'
$testTemp = 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\temp\test_sessione'
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
$env:TEMP = $testTemp
$env:TMP = $testTemp
$env:QT_QPA_PLATFORM = 'offscreen'
& $py -m unittest discover -s 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\tests' -v
```

La suite completa ha superato 67 test il 2026-11-12 dopo l’incremento importazione/preview/miniature; un precedente checkpoint della correzione click intestazioni riportava 63 test. Per ripeterla dopo modifiche, usare TEMP/TMP in una cartella isolata e mantenere `QT_QPA_PLATFORM=offscreen` come nell'esempio.

## Come è organizzato il programma

- `src/reel_engine/project.py`: crea, apre e salva `project.json` versionato; la timeline resta JSON v1 e i riferimenti interni al progetto possono essere relativi.
- `src/reel_engine/gui/app.py` e `gui/editor.py`: finestre, editor, anteprima e timeline visuale. La GUI è separata dal motore.
- `src/reel_engine/jobs.py`: coda seriale di rendering, stato, progresso, annullamento, log, cartelle isolate e verifica dell'esportazione.
- `src/reel_engine/rendering.py` e `validation.py`: validazione e composizione con FFmpeg.
- `scripts/reel_engine.py`: CLI legacy, da mantenere compatibile.
- `scripts/reel_engine_gui.py`: avvio dell'app desktop.

## Decisioni già prese

Mantenere CLI legacy e timeline JSON v1. Non modificare i media originali. I lavori FFmpeg restano isolati e l'export va pubblicato solo dopo i controlli. La GUI non è una dipendenza del core. I media importati sono riferimenti ai file originali, non copie. Il preview mostra immagini, video e overlay ma non compone in tempo reale i crossfade.

## Fasi già completate

Le fasi timeline (immagini/video, audio, testi/sottotitoli e transizioni), progetti persistenti (D), Job Manager persistente (E) e GUI Fase F primo e secondo incremento (Home/progetti e editor visuale) sono implementati. La correzione editor del 2026-11-11 ha verificato preview/selezione, seek/playback, round-trip e un export H.264/AAC con due immagini e audio sintetici. La suite conta 62 test superati. Vedere `PROJECT_STATUS.md` per dettagli e limiti.

## Prossimo incarico consigliato

La correzione editor del 2026-11-11 verifica importazione di più immagini, selezione clip, aggiornamento preview durante seek/playback, salvataggio/riapertura e messaggio per immagine illeggibile. La correzione del 2026-11-12 intercetta i click sulle intestazioni VIDEO/AUDIO/TESTI/SOTTOTITOLI, mantenendo selezione e visibilità dei widget nel test Qt; il seek e il click clip sono verificati nella stessa prova. Backup: `backups/fase_f_click_intestazioni_2026-11-12` (oltre al backup precedente `backups/fase_f_editor_preview_selezione_2026-11-11`). Il problema di scomparsa pannelli nell'app Windows usata dall'utente non è ancora confermato visivamente fuori dal test widget.

Prossimo passo: verificare manualmente nell'app Windows dell'utente che click su tutte le intestazioni mantengano effettivamente Inspector e gli altri pannelli visibili. Il test automatico conferma il routing e la proprietà `isVisible()` dei widget, ma non dimostra che non vi sia un distinto problema di layout/DPI nella finestra completa. In seguito, fare una breve prova su un progetto reale e raccogliere eventuali altri problemi riproducibili. Per ogni modifica futura, fare prima un backup mirato, conservare i cambiamenti non committati e non eseguire commit/reset/clean senza richiesta.

## Ultimo punto di ripresa — 2026-11-12

- Percorso progetto: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE` (non usare la copia sincronizzata `sources/` come cartella di lavoro).
- Python: `C:\Users\manue\venvs\video-ai\Scripts\python.exe`; GUI opzionale PySide6; FFmpeg/FFprobe richiesti nel PATH. Avvio GUI: `& $py 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\scripts\reel_engine_gui.py'`. Suite: `& $py -m unittest discover -s tests -v`; per i test GUI impostare `QT_QPA_PLATFORM=offscreen` e usare `TEMP`/`TMP` in una cartella temporanea dedicata.
- Architettura: GUI isolata in `src/reel_engine/gui/`, progetto JSON versionato con timeline v1 nel core, renderer FFmpeg in `rendering.py`, lavori seriali/persistenti in `jobs.py`. Leggere `ARCHITECTURE.md`, `DECISIONS.md`, `PROJECT_STATUS.md` e `ROADMAP.md` prima di continuare.
- Decisioni recenti: importazione immagini/video subito persistita a lotto; preview esclusivo immagine/video con overlay separato; miniature solo per le foto nella traccia VIDEO con cache LRU di 96 voci. Nessuna modifica a schema v1, renderer o Job Manager.
- Fasi completate: core timeline/progetti/rendering, Job Manager, GUI Home/editor base, selezione e anteprima scene, click intestazioni e questo incremento importazione/preview/miniature. I test mirati e la suite completa sono passati: 67 test PASS con FFmpeg reale e Qt offscreen, esito registrato in `PROJECT_STATUS.md`.
- Backup mirato: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\fase_f_editor_import_preview_miniature_2026-11-12`.
- Prossimo incarico: prova pratica interattiva su Windows con due fotografie reali in un progetto indipendente, verifica persistenza dopo chiusura/riapertura e valutazione leggibilità miniature. Non modificare `D:\VIDEO_AI\PROGETTI\TEST_PRIMO_REEL_2` o i media originali senza richiesta esplicita. I test automatizzati non sostituiscono questa verifica sull'ambiente dell'utente.

## Ultimo incremento — continuità audio e durata progetto — 2026-11-12

Il player della musica resta continuo quando cambia la fotografia. La timeline si prolunga fino alla fine della musica; nel tratto dopo l'ultima immagine il preview mostra una schermata vuota e il cursore continua. L'export mantiene il suono e aggiunge nero fino alla fine. Se le immagini durano di più, il video conserva la sua durata e l'audio viene completato con silenzio.

- Backup: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\fase_f_audio_continuita_durata_2026-11-12`.
- Verifica: 70 test superati: editor Qt 11, Home Qt 10, core/Job Manager/FFmpeg 49, eseguiti in tre processi separati con `C:\Users\manue\venvs\video-ai\Scripts\python.exe`; usare `TEMP`/`TMP` isolati sotto `C:\Users\manue\Documents\Codex\work` e `QT_QPA_PLATFORM=offscreen`. La discovery di tutti i moduli in un solo processo stampa `OK` per i 70 test ma il runtime Qt termina con access violation in chiusura; vedi dettaglio in `PROJECT_STATUS.md`.
- Il test Qt registra il punto di sincronizzazione e porta il clock della macchina a stati attraverso scene e coda, senza un dispositivo audio nativo; il rendering audio è verificato da FFmpeg/FFprobe reali. Il test di ascolto e sincronizzazione con dispositivo audio reale resta il prossimo controllo sul PC Windows dell'utente.
- Non modificare progetti o media dell'utente per questa verifica; creare una copia di progetto e media di test sotto TEMP. JSON v1 e CLI sono compatibili.


## Ultimo incremento — audio principale facoltativo — 2026-11-12

La timeline può ora avere `audio: null` (o non dichiarare il campo). Il sottofondo funziona da solo, entrambe le tracce continuano a essere mixate, e una timeline senza audio produce un file video-only. Il Job Manager applica il controllo FFprobe coerente con le tracce dichiarate. JSON v1, CLI, editor e sincronizzazione restano invariati.

- Interprete: `C:\Users\manue\venvs\video-ai\Scripts\python.exe`; FFmpeg/FFprobe dal PATH.
- Test mirati FFmpeg/FFprobe: musica sola più lunga delle immagini, nessun audio, progetto silenzioso nel Job Manager; audio principale e mix sono coperti dai test già esistenti. Suite: 73 test `OK`; al termine del processo Qt editor è comparsa access violation. Separando i gruppi, core/FFmpeg (52) e GUI (10) escono correttamente; gli 11 test editor sono `OK` ma il processo Qt va in access violation in chiusura.
- Backup mirato: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\fase_f_audio_principale_opzionale_2026-11-12`.
- Per verificare l'intera suite, usare `python -m unittest discover -s tests -v` con `TEMP` e `TMP` in una cartella dedicata sotto `C:\Users\manue\Documents\Codex\work`, e `QT_QPA_PLATFORM=offscreen`.
- Prossimo passo consigliato: completare una prova audio end-to-end con uscita sonora Windows reale; i render sintetici e i controlli stream sono già automatizzati.


## Ultimo incremento — riordino clip VIDEO — 2026-11-12

Puoi trascinare una clip VIDEO prima o dopo un'altra. Il gesto si attiva solo dopo uno spostamento visibile del mouse; il click semplice continua a selezionare. Il rilascio aggiorna ordine, tempi derivati e anteprima. Durata, punto di ingresso, musica/voce e testi/sottotitoli non vengono cambiati. Le transizioni seguono la scena di riferimento; gli ordini che rendono una transizione impossibile sono bloccati. Premi **Salva** o `Ctrl+S` per conservare il lavoro.

- Il modello JSON v1 resta sequenziale: niente gap o posizioni temporali libere. Non sono stati cambiati renderer, progetti o media utente.
- Il modello JSON v1 resta sequenziale: niente gap o posizioni temporali libere. Non sono stati cambiati renderer, progetti o media utente.
- Suite a gruppi: 52 test core/FFmpeg PASS (exit 0), 10 GUI PASS (exit 0), 14 editor `OK` con access violation Qt del processo in chiusura; il nuovo test drag isolato termina con exit 0. `git diff --check` PASS.
- Backup: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\fase_successiva_riordino_clip_2026-11-12` (oppure suffisso numerico se il nome era già occupato).
- Verifica manuale Windows: apri una copia di progetto, prova click semplice su una clip, poi trascinala oltre la soglia e rilasciala prima/dopo un'altra. Controlla selezione, preview, durata e `source_in` nell'Inspector; modifica la testina e verifica che voce/musica e overlay restino agli stessi secondi. Premi Salva, chiudi, riapri e controlla ordine e transizioni. Infine esporta la copia e verifica il risultato.


## Ultimo incremento — split scena — 2026-11-13

Nell'editor seleziona una scena VIDEO e posiziona la testina all'interno. Premi **Dividi scena · S** oppure `S` con la timeline attiva. Il taglio si allinea al frame più vicino e richiede almeno un frame per ciascun lato. Per le immagini, le due scene puntano allo stesso file; per i video la seconda parte riprende dal corretto `source_in`. La transizione in uscita viene spostata alla seconda metà. Se le transizioni non sono più compatibili, lo split viene bloccato senza cambiare i dati. Audio, testi e sottotitoli restano ai loro tempi assoluti. Salva con **Salva** o `Ctrl+S`.

- Formato timeline v1, renderer e progetto core invariati. Il nuovo taglio è cut: non viene aggiunta una dissolvenza automatica.
- Backup: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\fase_split_clip_2026-11-13` (con suffisso se necessario).
- Per verifica manuale: apri una copia di progetto con immagine e video; seleziona ciascuno, posiziona la testina lontano dai bordi, dividi, verifica preview e proprietà, salva/chiudi/riapri. Per il video verifica il punto di ingresso della seconda parte; prova anche un taglio troppo vicino ai bordi e uno che renderebbe incompatibile una dissolvenza.
- Test separati: core/FFmpeg 52 PASS (exit 0), GUI 10 PASS (exit 0), editor 17 `OK` ma Qt termina con l’anomalia già nota in chiusura. `git diff --check` PASS.


## Correzione continuità preview video — 2026-11-13

La seconda clip aveva già il corretto `source_in` nel progetto e il renderer FFmpeg lo usava. La ripartenza riguardava il preview Qt: il seek partiva prima che il video fosse caricato, e il player riapriva la stessa sorgente al passaggio tra segmenti. Ora il player mantiene l'asset già aperto per segmenti dello stesso file e differisce il seek di nuove sorgenti fino al caricamento.

- Test render FFmpeg: video di 3 s blu/rosso, due segmenti da 1,5 s con `source_in` 0 e 1,5; durata 3 s e colori corretti ai due lati del cut.
- Test Qt: anteprima a 1,5 s, split, salvataggio/riapertura e playback oltre il taglio. I test mirati sono passati.
- Backup: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\correzione_split_preview_video_2026-11-13` (con suffisso se il nome esisteva già).
- Prossimo controllo manuale: sul PC Windows, apri una copia del progetto con un video di 3 s, dividilo a 1,5 s, salva e riapri; verifica anteprima e playback attraversando il taglio. Il test sintetico è superato, ma la prova con il file specifico dell'utente resta da fare.

- Suite separata: core/FFmpeg 53 PASS (exit 0), GUI 10 PASS (exit 0), editor 18 `OK` con anomalia Qt nota alla chiusura del gruppo. Test nuovi FFmpeg e preview eseguiti individualmente PASS; il test preview individuale termina con exit 0.


## Funzione recente: trim delle clip

Nell'editor, passa il puntatore vicino al bordo sinistro o destro di una clip VIDEO finché compare il cursore orizzontale, poi trascina. Il bordo destro cambia la durata. Il bordo sinistro per un video cambia il punto d'ingresso nel file e la durata; per un'immagine cambia solo la durata. Il taglio segue i fotogrammi del progetto. Le clip non possono durare meno di un fotogramma e i video non possono superare il file sorgente. La timeline v1 non crea spazi vuoti: le scene seguenti si riallineano. Se una transizione o un testo diventerebbe non valido, l'editor mostra il motivo e lascia il progetto invariato.

### Verifica manuale Windows consigliata

1. Avvia l'applicazione con i comandi sopra e apri una copia di un progetto di prova.
2. Importa almeno un'immagine, un video e una musica; aggiungi testo e una transizione, poi salva.
3. Trascina il bordo destro del video di alcuni fotogrammi e controlla durata e posizione delle scene seguenti.
4. Trascina il bordo sinistro: la scena deve continuare dal nuovo punto nel video sorgente. Controlla l'anteprima prima e dopo il taglio.
5. Prova ad accorciare oltre il minimo o oltre la fine del file e verifica il messaggio; il contenuto non deve cambiare.
6. Salva, chiudi e riapri il progetto, poi esporta. Il JSON v1 deve rimanere leggibile e le tracce accessorie devono mantenere i propri tempi.

Il test automatico Qt/FFmpeg è stato eseguito nell'ambiente di sviluppo; la prova sul PC e sui media dell'utente non è stata eseguita da questa sessione. Il prossimo incarico consigliato è svolgere questa verifica manuale senza intervenire sui file originali.


## Funzione recente: Annulla e Ripristina

Nell'editor sono disponibili i pulsanti **Annulla** e **Ripristina**, oltre a `Ctrl+Z` e `Ctrl+Y`. Ogni operazione completata è una singola voce; il drag non crea una voce per ogni movimento del mouse. La selezione e il playhead vengono ripristinati con i contenuti. Se il nome del progetto ha `*`, premi **Salva** o `Ctrl+S`: annullare o ripristinare non scrive automaticamente il file. Un'importazione immagini/video continua a salvare subito come nelle versioni precedenti; puoi comunque annullarla e salvare nuovamente quando vuoi. Aprire un altro progetto svuota la cronologia, che non viene conservata quando chiudi l'app. Non vengono eliminati o modificati i file multimediali originali.

Per provarla, crea/apri una copia di progetto e importa una foto; prova Annulla/Ripristina sia dai pulsanti sia con le scorciatoie. Ripeti con spostamento, split, trim, modifica audio, testo e sottotitolo. Salva, fai un'altra modifica, annullala, verifica `*`, salva e riapri il progetto; infine esegui un export della copia. Il test automatico del renderer rimane nella suite FFmpeg. Verifica in questa sessione: 27 test editor e 63 test GUI/core/Job Manager/FFmpeg hanno riportato tutti `OK` (90 totali). Il processo editor termina tuttavia con codice 1 in chiusura Qt, anomalia già nota; gli altri gruppi terminano con codice 0. I test FFmpeg reali sono passati. L'anomalia preesistente di chiusura di alcuni processi test Qt può comparire dopo un riepilogo `OK`; esegui i gruppi GUI separatamente quando serve e controlla le singole righe di test.


## Aggiornamento 2026-11-13: trim live
Il trim dei bordi ora mostra il movimento durante il drag a precisione di frame e aggiorna preview/playhead. Il rilascio crea un solo comando Undo; `Esc` o un trim non valido ripristinano lo stato. Il bordo sinistro è un roll con la scena precedente; sulla prima scena è disabilitato perché JSON v1 non supporta uno spazio iniziale. Test automatici mirati superati: drag foto/video, roll sinistro video, una voce Undo, Escape, salvataggio/riapertura ed export reale via Job Manager con FFprobe.

Per riprendere: progetto `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE`; Python `C:\Users\manue\venvs\video-ai\Scripts\python.exe`. Avvio GUI: `python -m reel_engine.gui`. Test: `python -m unittest discover -v`. Per evitare artefatti Qt nei test, usare `QT_QPA_PLATFORM=offscreen` e `TEMP`/`TMP` dedicati; eseguire i test editor separatamente se si presenta l'anomalia già documentata di chiusura Qt. Prossimo passo: verifica manuale in Windows dell'utente sui bordi destro foto/video e sinistro video, ascoltando/guardando l'anteprima durante il movimento e controllando Undo/Redo, salvataggio e export. Non è stata verificata una sessione manuale sull'installazione dell'utente in questo incremento.


## Ultimo incremento — Snap magnetico (2026-11-13)
In timeline, Snap è attivo all'avvio. Attivalo o disattivalo con il pulsante sopra la timeline o con `N` mentre la timeline ha il focus. Il playhead si trascina afferrandolo nella scala temporale superiore. La guida gialla mostra il punto agganciato; la soglia è 10 pixel dello schermo e vale con zoom differenti. Riordino, Trim Live e Roll Edit agganciano i propri punti mobili a bordi scene, transizioni e playhead. Il frame alignment rimane sempre attivo. Un edit completato crea una sola voce Undo; lo spostamento del playhead non cambia la cronologia. La timeline/progetto JSON v1 e il renderer sono invariati.

- Progetto: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE`; ambiente Python: `C:\Users\manue\venvs\video-ai\Scripts\python.exe`. Avvia con `& $py 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\scripts\reel_engine_gui.py'`.
- Test: `python -m unittest discover -s tests -v`; usa `QT_QPA_PLATFORM=offscreen` e `TEMP`/`TMP` separati sotto `C:\Users\manue\Documents\Codex\work`.
- Test automatici dell'incremento: snap playhead al bordo, scatto disattivato, soglia a zoom 1x/2x, target fade/crossfade, reorder con Undo/Redo, salvataggio/riapertura ed export con FFprobe, trim destro e roll video con aggancio al playhead.
- Prossimo passo: prova manuale con mouse nella GUI Windows, controllando sensazione della soglia ai livelli di zoom usati dall'utente e l'aggancio ai fade/transizioni del progetto reale. Non usare media originali per i test automatici.
- Backup mirato di codice, test e documenti: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\snap_magnetico_professionale_2026-11-13`.


### Verifica automatica Snap (2026-11-13)
La suite completa finale ha superato **95 test** (32 editor Qt e 63 GUI/core/Job Manager/FFmpeg) in 30,7 secondi, con processo terminato correttamente (codice 0). I tre test Snap eseguiti isolatamente sono passati; la prova di riordino ha salvato/riaperto il progetto e prodotto un MP4 verificato con FFprobe. In un'esecuzione separata del solo gruppo editor i 32 test avevano riportato `OK`, ma il teardown Qt aveva restituito il codice Windows `-1073740791`; l'anomalia non si è ripetuta nella suite completa finale. Nessun test ha usato progetti o media originali.

## Waveform audio (2026-11-13)
La timeline ora genera automaticamente la waveform per audio e background audio con FFmpeg in background. Cache locale: `%LOCALAPPDATA%\REEL_ENGINE\waveforms` (o cache utente se manca LocalAppData); è rigenerabile e non fa parte del JSON progetto. Seleziona/riapri un progetto normalmente; la cache evita di ricalcolare i picchi. Il volume ne cambia l'ampiezza visuale e `source_in` il punto mostrato. Per riprendere il lavoro: verificare manualmente nella GUI Windows una traccia audio lunga, zoom e scroll della waveform, cambio del file, Undo/Redo, salvataggio/riapertura. Test automatici: `python -m unittest discover -s tests -v` nel venv indicato sopra, con `TEMP`/`TMP` dedicati e `QT_QPA_PLATFORM=offscreen`. Ultima verifica automatica: 100 test superati; la prova GUI manuale sulla postazione dell'utente non è stata eseguita.

## Copia, incolla e duplicazione clip (2026-11-13)
Nella timeline seleziona una clip immagine o video e usa `Ctrl+C`; porta il playhead sul punto desiderato e premi `Ctrl+V`, oppure `Ctrl+D` per duplicare subito dopo l'originale. `Ctrl+Z` e `Ctrl+Y` annullano/ripristinano l'inserimento come singola operazione. La timeline JSON v1 è sequenziale: se il playhead è dentro una clip, Incolla si posiziona al successivo taglio; non crea spazi vuoti e non divide automaticamente. Il buffer è temporaneo e si svuota aprendo un altro progetto. Multi-selezione non disponibile. Test specifici: `python -m unittest tests.test_editor.TimelineEditorTests.test_copy_paste_image_uses_playhead_cut_and_undo_redo_round_trip tests.test_editor.TimelineEditorTests.test_duplicate_video_preserves_properties_transitions_and_other_tracks tests.test_editor.TimelineEditorTests.test_clip_copy_operations_remain_compatible_with_trim_split_and_snap -v`; suite completa con `python -m unittest discover -s tests -v`, in ambiente e TEMP/TMP indicati sopra. Ultima suite: 103 test hanno riportato `OK` in 30,13 s; `py_compile` è passato. Il processo unittest termina comunque con codice Windows -1073740791 (access violation in chiusura Qt, anomalia già nota). Prossimo controllo: prova manuale Windows con immagini, video con `source_in`, undo/redo e progetto salvato/riaperto.

## Ripple Delete (2026-11-13)
Seleziona una scena VIDEO e premi **Ripple Delete · Shift+Canc** nell'Inspector oppure `Shift+Delete` con la timeline attiva. La scena viene tolta e le successive si spostano in avanti nel montaggio; testi e sottotitoli vengono riallineati al tempo ridotto. I cue completamente dentro il tratto eliminato vengono rimossi. Le transizioni ai due tagli vicini alla scena vengono rimosse; i fade ai bordi restano. Audio e musica non si spostano: nel progetto v1 sono tracce globali continue da zero. **Elimina selezione** resta il comando precedente senza ripple. Undo/Redo ripristina l'intera timeline in un solo passaggio; salva con `Ctrl+S`. Test mirati: `python -m unittest tests.test_editor.TimelineEditorTests.test_ripple_delete_first_scene_shortcut_and_round_trip tests.test_editor.TimelineEditorTests.test_ripple_delete_middle_shifts_timed_tracks_and_preserves_global_audio tests.test_editor.TimelineEditorTests.test_ripple_delete_last_scene_preserves_end_fade_and_handles_empty_selection -v`. Suite completa: `python -m unittest discover -s tests -v` nel venv documentato, con `QT_QPA_PLATFORM=offscreen` e TEMP/TMP dedicati. Ultima suite completa: 107 test hanno riportato `OK` in 34,36 s e `py_compile` è passato; l’uscita del processo è -1073740791 per l’anomalia nota di teardown Qt dopo il riepilogo. Prossimo controllo: prova manuale Windows sui casi prima, centrale, ultima e unica scena, ascoltando una traccia musica e controllando gli overlay dopo salvataggio/riapertura.

## Blocco e sblocco tracce — Track Lock (2026-11-14)
Completata l'implementazione del blocco e sblocco indipendente per le cinque tracce: VIDEO, AUDIO, MUSICA, TESTI e SOTTOTITOLI.
- Pulsanti toggle nella testata della timeline (`VIDEO: BLOCCATA/LIBERA`, `AUDIO: BLOCCATA/LIBERA`, `MUSICA: BLOCCATA/LIBERA`, `TESTI: BLOCCATA/LIBERA`, `SOTTOTITOLI: BLOCCATA/LIBERA`).
- Clip contrassegnate graficamente con `[LOCKED]` e opacità attenuata.
- Rifiuto preventivo di tutte le modifiche su tracce bloccate: importazione media, aggiunta audio/testi, drag reorder, split `S`, trim live/roll, duplicate `Ctrl+D`, paste `Ctrl+V`, delete `Delete`/pulsante, ripple delete `Shift+Delete` e applicazione proprietà in Inspector.
- Inspector riflette lo stato disabilitando i campi e indicando `[BLOCCATA]` nel titolo.
- Le altre tracce rimangono indipendenti e modificabili.
- Ripple delete rispetta le tracce bloccate: se testi o sottotitoli si trovano dopo il taglio e verrebbero alterati/spostati, l'azione viene rifiutata finché la traccia non viene sbloccata.
- Undo/Redo integrato: se un'operazione comporterebbe variazioni a una traccia attualmente bloccata, l'annullamento/ripristino viene inibito con messaggio esplicito senza corrompere la cronologia; una volta sbloccata la traccia, l'operazione può procedere regolarmente.
- Persistenza pulita in `project.settings["editor"]["track_locks"]` senza alterare lo schema timeline JSON v1; salvataggio e riapertura conservano fedelmente i blocchi; i progetti con tracce bloccate sono completamente esportabili via Job Manager.
- Backup pre-modifica: `D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\backups\track_lock_fase_2026-11-14`.
- Verifica: suite completa di 113 test superati con esito positivo (6 nuovi test mirati in `test_editor.py` e 107 test preesistenti, 0 falliti). Prossimo controllo: verifica visiva e interattiva sull'app desktop Windows con mouse e tastiera.

## Preview transizioni immagine/video — 2026-11-14
Il preview compone crossfade immagine-immagine e le combinazioni con video tramite `QMediaPlayer`/`QVideoSink`; i seek posizionano entrambi i source sulla timeline, la riproduzione corregge drift solo quando supera 100 ms e le immagini statiche usano cache LRU. Test mirati: `python -m unittest tests.test_editor.TimelineEditorTests.test_image_crossfade_is_composited_in_preview_at_playhead tests.test_editor.TimelineEditorTests.test_video_crossfade_preview_composites_all_image_video_combinations tests.test_editor.TimelineEditorTests.test_video_crossfade_seeks_both_sources_to_the_timeline_time -v`. Verificare manualmente su Windows transizioni tra video reali con seek ripetuti e riproduzione; i test Qt iniettano frame sintetici per restare deterministici.

## Proxy video — 2026-11-14
I proxy sono generati con `ProxyCache` in `src/reel_engine/gui/proxies.py`, sotto `.reel_engine/proxies` per progetto. Le scene continuano a puntare ai file originali; `TimelineEditorPage._preview_source_path()` risolve le copie solo per i player della preview e dei crossfade. Il toggle `Usa proxy` è salvato in `settings.editor.use_proxies`. Una generazione già attiva si annulla alla chiusura dell'editor; output temporanei non vengono pubblicati. Test mirati: `python -m unittest discover -s tests -p test_proxies.py -v` e test `test_proxy_*` in `tests/test_editor.py`. Ultima suite completa: **122 test superati, 0 falliti**. Verificare manualmente toggle e playback con media Windows reali; la suite controlla che il progetto conservi gli originali e che i proxy non diventino riferimenti timeline.
## Completamento corrente — Fase 0 GUI (2026-11-15)

AUD-01 e AUD-02 implementati: la guardia dirty protegge Home, sostituzione progetto e chiusura; il teardown ferma le attività editor e disconnette le callback Qt a rischio. Dettagli e verifiche finali sono in `PROJECT_STATUS.md`. Backup: `backups/phase0_project_integrity_gui_stability_2026-11-15`.

La Fase 0 è conclusa: la suite completa ha superato 160 test; `compileall` e `git diff --check` sono puliti. La Fase 1 è stata implementata nel seguito descritto sotto; il suo run finale è in corso.

## Fase 1 — UX e organizzazione editor (2026-11-15)

Implementato layout libreria/preview/Inspector/timeline, import separato dall’inserimento, ricerca e filtri media, miniature video in background, header fissi, zoom/fit e pannello export con stato/cancel/storico/log. Backup: `backups/phase1_editor_ux_2026-11-15`. Suite completa **163/163**, `compileall` e `git diff --check` superati. Fase 1 conclusa; la Fase 2 non è stata iniziata.
