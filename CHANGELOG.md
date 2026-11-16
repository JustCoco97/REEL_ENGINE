# Changelog

## 2026-11-15 — Diagnostica Ollama e robustezza Qwen 3.5 4B

- Ollama 0.40.16 e `qwen3.5:4b` rilevati via API; `think:false` supportato. Prova sintetica con thinking default lenta (~3 min) e con JSON valido in ~5,2 s a thinking disabilitato. La risposta grezza del fallimento precedente non esiste: è accertato il rifiuto in `json.loads(message.content)`, non la specifica forma del testo che l’ha causato.
- Aggiunti schema atomico, selezione thinking via capability, normalizzazione testo/fence, validazione stretta e un unico retry correttivo. Diagnostica persistente con output limitato, metadati, errori HTTP/parser, redazione dei percorsi, retention; prompt e testo thinking non vengono salvati. Timeout GUI a 300 s.
- Backup `backups/ai_director_ollama_json_diag_2026-11-15`; 20 test mirati e 154 test complessivi superati. Prova end-to-end Qwen valida (4 scene) in 119,71 s.

## 2026-11-14 — AI Video Director Fase 2: Ollama locale

- Aggiunto `OllamaProvider` su `urllib`: controllo connessione/`/api/tags`, generazione `/api/chat` con JSON Schema, modello esatto già installato, timeout e limite risposta; endpoint vincolato a loopback, nessun download/pull o traffico cloud.
- GUI: selezione fra draft deterministico e Ollama, scelta modello, timeout e verifica modelli; operazioni di rete e pianificazione eseguite in background.
- Catalogo media con ID stabili senza percorsi nel prompt. Validazione severa di schema, campi e ID: media inventati o risposte errate fanno fallire esplicitamente il draft.
- Modalità deterministica conservata; progetto/Timeline JSON v1, renderer ed export restano invariati. Ollama non è installato/raggiungibile nel sistema verificato; nessun modello scaricato.
- Test con server HTTP simulato su loopback (senza Internet), UI asincrona, ID sconosciuti, modello mancante e timeout. Verifica: **15 test mirati e 149 test nella suite completa superati, 0 falliti** in 48,55 s; AST sorgenti/test valido.

## 2026-11-14 — AI Video Director Fase 1

- Aggiunto alla Home il flusso guidato per argomento, durata, formato, istruzioni e selezione dei media.
- Aggiunto `DirectorOrchestrator` con contratto provider-neutral, fasi brief/narrativa/script/storyboard/asset matching e adapter locale a template; la GUI dichiara che il provider AI non è configurato e non fa chiamate esterne.
- Il draft controlla le durate video con FFprobe, produce scene Timeline JSON v1 e salva brief e storyboard nelle impostazioni progetto. L’utente rivede il draft, apre il progetto nell’editor e decide se esportarlo.
- Nessuna dipendenza o API a pagamento aggiunta; timeline v1 e renderer non modificati. Backup: `backups/ai_video_director_fase1_2026-11-14`. Verifica finale: **9 test del flusso AI Director e 142 test nella suite completa superati, 0 falliti** (41,34 s); AST sorgenti/test valido. Gli adapter vengono rifiutati se la risposta strutturata è incompleta o malformata.

## 2026-11-14 — Stili crossfade nella GUI, preview ed export

- Integrato nella GUI il selettore di tutti i 22 stili crossfade; l’anteprima riproduce lo stile selezionato.
- La preferenza è salvata nelle impostazioni del progetto e il Job Manager la inoltra al renderer per l’export.
- I vecchi progetti usano `fade` come default. Timeline JSON v1 non è stata modificata.
- Backup separato: `backups/editor_crossfade_style_2026-11-14`. Verifica completa: **133 test superati, 0 falliti**.

## 2026-11-14 — decisione backend video e stili crossfade CLI

- Confrontati FFmpeg, MLT e GES rispetto a timeline/tracce, transizioni, effetti, audio, preview, Python/Windows, licenze e distribuzione commerciale; documentate decisione e fonti ufficiali in `ARCHITECTURE.md`/`DECISIONS.md`.
- Decisione: mantenere FFmpeg come unico renderer d'export; MLT/GES non installati e non benchmarkati. GES è candidato al primo pilot quando REEL ENGINE richiederà un vero modello NLE multitraccia.
- Registrato il gate commerciale: la build FFmpeg locale è statica GPLv3 con libx264; review delle licenze/codec e brevetti necessaria prima del rilascio.
- Aggiunto `--crossfade-style` con 22 transizioni `xfade`; default e GUI restano invariati, Timeline JSON v1 non cambia.
- Test mirati passati; suite completa: **128 test superati, 0 falliti** in 51,24 s; AST valido. La chiusura del worker proxy attende ora 5 s (oltre i 3 s del timeout di terminazione FFmpeg) per evitare teardown con file ancora aperti.

## 2026-11-14 — Raccolta media nel progetto

- Aggiunta l'azione **Raccogli media** nell'editor e API core `collect_project_media` per copiare i riferimenti esterni in `media/` e `fonts/`, con riferimenti relativi persistenti e nomi basati sul contenuto.
- Il preflight rifiuta file mancanti prima di copiare; errori durante copia/salvataggio ripristinano riferimenti e rimuovono i nuovi asset. Gli originali, Timeline JSON v1 e renderer restano invariati.
- Aggiunti test per copia/ripresa, diagnostica, file mancante, rollback salvataggio e copia interrotta. Suite completa: **126 test superati, 0 falliti**; parsing AST di sorgenti e test superato.

## 2026-11-14 — Proxy video automatici

- I video importati vengono transcodificati in background in proxy H.264 a lato massimo 960 px, cache per progetto con riuso su sorgente invariato e rigenerazione al cambio del fingerprint.
- Toggle **Usa proxy** persistente nelle impostazioni editor. Preview normale e crossfade usano proxy disponibili; timeline v1 ed export mantengono gli asset originali.
- Aggiunti test per generazione, riuso, invalidazione, errori, cancellazione, generazione asincrona e scelta del sorgente di preview.
- Verifica: suite completa, **122 test superati, 0 falliti**; controllo sintattico AST superato.

## 2026-11-14 — Preview dissolvenze tra immagini

- Il preview GUI compone le transizioni crossfade tra immagini al tempo del playhead e aggiorna l'opacità durante la riproduzione.
- Aggiunto test Qt che verifica la fusione dei pixel nel punto intermedio della transizione.
- Limite: il compositing del preview per transizioni che coinvolgono video resta aperto; timeline JSON v1 e renderer FFmpeg non sono cambiati.
- Verifica: suite completa, 114 test superati; controllo sintattico Python superato.

## 2026-11-14 — Crossfade preview con clip video

- Esteso il compositing ai casi video-video, immagine-video e video-immagine con due decoder Qt silenziosi e `QVideoSink`; le immagini statiche riusano una cache limitata.
- I seek posizionano entrambi i decoder usando `source_in` e l'offset della scena sulla timeline. In playback il drift viene corretto solo oltre 100 ms, con controllo ogni 250 ms.
- Aggiunti test Qt deterministici per le tre combinazioni e il mapping dei tempi; nessuna modifica a timeline JSON v1 o renderer.
- Verifica finale: suite completa, 116 test superati (0 fallimenti); controllo sintattico AST superato.

## 2026-11-12 — Fase F: continuità audio e durata progetto

- Il player musicale continua attraverso i cambi scena; la sincronizzazione viene aggiornata all'avvio e sui seek manuali.
- La durata progetto considera la fine della musica di sottofondo (durata esplicita o durata FFprobe dopo `source_in`). Il preview prosegue in un intervallo vuoto; il renderer aggiunge una coda nera e mantiene audio/video alla stessa durata.
- Quando immagini/video superano l'audio, il video non viene troncato: l'audio esportato viene completato con silenzio.
- Test Qt su due foto e una traccia audio; test FFmpeg/FFprobe per musica più lunga, immagini più lunghe e transizioni con durata export estesa.
- JSON v1 invariato; nessuna dipendenza aggiunta.

## 2026-11-12 — Fase F: importazione, anteprima e miniature timeline

- L'importazione di immagini/video valida il gruppo e salva immediatamente le scene nel progetto; un errore di salvataggio non lascia importazioni solo in memoria.
- Il preview usa pagine esclusive per messaggio vuoto/immagini e video, con overlay separato, prevenendo la copertura da parte della superficie video inattiva.
- Aggiunte miniature delle immagini nella traccia VIDEO, con decodifica ridimensionata e cache LRU limitata; altre tracce non cambiano.
- Test Qt: due foto persistenti e riaperte, percorsi validi, pagina vuota/immagine/video e cache miniature. Nessuna dipendenza aggiunta.
- La verifica con il progetto e i media originali dell'utente resta da eseguire sul PC Windows dell'utente.

## 2026-11-12 — click intestazioni timeline

- Click su VIDEO, AUDIO, TESTI e SOTTOTITOLI ora consumato come click d'intestazione, preservando selezione clip, Inspector e preview nel test Qt.
- La zona temporale continua a spostare la testina e i clip continuano a selezionarsi/aggiornare l'anteprima. Nessuna modifica a JSON v1, renderer o Job Manager.
- La suite completa e il test non attestano ancora la verifica manuale della finestra Windows sullo schermo dell'utente.

## 2026-11-11 — Correzione funzionale editor Fase F

- La selezione di una clip aggiorna immediatamente l'anteprima e viene evidenziata chiaramente; seek e playback sincronizzano anteprima e scena corrente.
- La decodifica immagini ora riporta un errore comprensibile con il percorso e non viene nascosta dal messaggio generico d'importazione.
- Aggiunta copertura Qt per importazione di più immagini, timeline, selezione, seek, playback, salvataggio/riapertura e immagini danneggiate. Compatibilità con timeline JSON v1 conservata.

Le voci descrivono modifiche realmente completate. Proposte e attività pianificate appartengono a `ROADMAP.md`.

## 2026-11-11 — Fase C: secondo incremento, timing a fotogrammi

- Aggiunto timebase condiviso: `source_in` e durata di immagini e video sono quantizzati al fotogramma più vicino in base agli FPS del canvas; il rendering impone il conteggio intero dei frame per segmento.
- Conservati CLI legacy e schema timeline JSON v1. Nessuna dipendenza aggiunta.
- Verifica: suite completa, 17 test PASS; ffprobe controlla frame e durata finale; `git diff --check` senza errori.

## 2026-11-11 — Fase C: scene video nel renderer

- Aggiunto probe ffprobe per le sorgenti video e rifiuto di file illeggibili o trim oltre la durata disponibile.
- Il renderer applica source_in e durata alle scene video; segmenti video e immagine condividono codec e concatenazione, supportando timeline miste.
- Verifica: suite completa: 15 test PASS; `git diff --check` senza errori.

## 2026-11-11 — Fase B: primo incremento conservativo

- Separati modello timeline (`src/reel_engine/model.py`), caricamento e validazione JSON v1 (`validation.py`) e rendering FFmpeg (`rendering.py`); `scripts/reel_engine.py` mantiene parsing e interfaccia CLI legacy.
- Aggiornato il sandbox dei test CLI per includere i moduli `src`.
- Verifica: suite `unittest` completa, 10 test PASS, compresi render legacy e timeline v1; `--help` verificato. Non introdotte dipendenze Python. GUI e multitraccia restano pianificate.

## 2026-11-10 — Documentazione iniziale

- Creati README, istruzioni per AI, stato progetto, architettura, registro decisioni, roadmap e modello handoff.
- Registrato lo stato verificato del CLI, la compatibilità legacy, la timeline v1 e l'esito dei test riportato nella Fase 9.
- Registrati i limiti del renderer, le risorse hardware rilevate e le questioni aperte per distribuzione e licenze.


## 2026-11-12 — Audio principale facoltativo

- `audio` della timeline JSON v1 ora accetta `null` o omissione.
- Con audio principale assente, FFmpeg usa `background_audio` come unica traccia; senza nessuna traccia crea un video senza stream audio. Il mix e i percorsi audio preesistenti restano invariati.
- Il Job Manager accetta correttamente video-only e continua a richiedere audio quando è dichiarato.
- Aggiunti test di rendering reali per musica senza audio principale, coda musicale oltre le immagini, nessun audio e pubblicazione silenziosa tramite Job Manager.
- Verifica mirata: 3 test PASS. Suite completa: 73 test `OK`, con access violation Qt alla chiusura; gruppi separati: core/FFmpeg 52 PASS ed exit 0, GUI 10 PASS ed exit 0, editor 11 `OK` con la stessa access violation Qt in uscita.


## 2026-11-12 — Riordino clip VIDEO

- Le clip della traccia VIDEO si trascinano prima o dopo altre scene; il click semplice continua a selezionare. Una soglia di movimento previene riordini involontari.
- La preview/playhead segue la scena spostata; durata, `source_in`, audio, testi e sottotitoli restano invariati. Il progetto conserva il nuovo ordine al salvataggio/riapertura.
- Le transizioni restano legate alla scena `after`; riordini che le rendono invalide sono rifiutati con diagnostica nella barra di stato. JSON v1 e renderer invariati.
- Test Qt aggiunti per drag, click, proprietà, tracce temporizzate, transizioni e round-trip progetto.
- Test Qt aggiunti per drag, click, proprietà, tracce temporizzate, transizioni e round-trip progetto. Gruppi separati: 52 core/FFmpeg e 10 GUI terminano correttamente; 14 editor riportano `OK` e riproducono l'access violation Qt già noto alla chiusura. Test drag isolato PASS.


## 2026-11-13 — Split scena

- Aggiunto comando `Dividi scena · S` e scorciatoia `S` sulla timeline attiva.
- Lo split allinea al frame più vicino, rifiuta i bordi, conserva durata e media; il video continua tramite `source_in` aggiornato.
- Le transizioni in uscita seguono la seconda parte; se la nuova durata non può rispettare le transizioni esistenti, l'operazione viene rifiutata senza mutazioni. Audio/testi/sottotitoli restano invariati e il progetto v1 salva/riapre lo split.

- Verifica in gruppi: 52 test core/FFmpeg e 10 GUI terminati con exit 0; 17 test editor riportano `OK`, ma Qt termina con l’anomalia in chiusura già nota. `git diff --check` PASS.


## 2026-11-13 — Correzione continuità preview split video

- Il player non ricarica il medesimo file quando passa tra due segmenti della stessa sorgente. Per un nuovo video, seek e ripresa vengono applicati dopo il caricamento Qt (`LoadedMedia`/`BufferedMedia`).
- Aggiunti test con video sintetico blu/rosso per verificare il render source_in a 1,5 s e la riproduzione preview attraverso il taglio dopo salvataggio e riapertura.
- Il controllo FFmpeg ha confermato che `-ss` è già applicato; nessuna modifica al renderer o ai tempi audio/testi/sottotitoli/transizioni.

- Suite: 53 test core/FFmpeg e 10 GUI terminati con exit 0; tutti i 18 test editor risultano `OK`, con l'anomalia Qt nota alla chiusura del gruppo. I nuovi test mirati di render/preview passano singolarmente.


## 2026-11-13 – Trim professionale

- Aggiunto il trim delle clip video trascinando i bordi sinistro e destro, con precisione a frame.
- Il trim video sinistro aggiorna `source_in`; le immagini conservano il proprio contenuto. Scene successive ricalcolate nella sequenza JSON v1.
- Aggiunti controlli per durata minima, confini del file sorgente, transizioni e intervalli di testo/sottotitoli.
- Aggiunti test automatici di rendering/asset sintetico FFmpeg, persistenza e interazione Qt. Nessuna dipendenza nuova.


## 2026-11-13 — Undo/Redo editor

- Aggiunti pulsanti Annulla/Ripristina e shortcut `Ctrl+Z`/`Ctrl+Y`, con disponibilità automatica in base alla cronologia.
- Introdotti snapshot undo atomici per importazione/rimozione, riordino, split, trim, proprietà di scene/audio/testi/sottotitoli e transizioni modificate da operazioni sulle scene.
- Ripristino include timeline, anteprima, selezione e playhead; nessun autosalvataggio su undo/redo, clean marker e indicatore progetto non salvato.
- Cronologia limitata a 100 comandi e azzerata al cambio progetto; JSON v1, renderer e media originali invariati.
- Verifica: 90 test superati in gruppi separati (27 editor, riepilogo `OK` con codice di uscita Qt 1 già noto; 63 GUI/core/FFmpeg, codice 0).


## 2026-11-13 — Trim live professionale
- Il bordo della clip e la sua lunghezza seguono il mouse durante il trascinamento, con precisione al frame.
- Playhead e anteprima seguono il trim; il rilascio conferma una sola operazione Undo/Redo.
- `Esc` e trim non validi ripristinano lo stato iniziale. Il bordo sinistro usa un roll con la scena precedente; il primo bordo sinistro è disabilitato per compatibilità con JSON v1.
- Aggiunti test GUI, persistenza e rendering reale FFmpeg/Job Manager. Nessun cambio al renderer o al formato progetto.


## 2026-11-13 — Snap magnetico professionale
- Aggiunti punti di aggancio a bordi scene/tagli, confini transizioni e playhead, con guida tratteggiata.
- Snap attivo/disattivabile da pulsante o `N`; soglia di 10 pixel dello schermo indipendente dallo zoom e output sempre allineato al frame.
- Integrato con riordino, Trim Live/Roll Edit e playhead trascinabile dalla scala superiore; ogni edit drag resta una singola voce Undo.
- Test Qt per Snap on/off, soglia a scale diverse, playhead, riordino, Trim/Roll, Undo/Redo, salvataggio/riapertura ed export FFmpeg.


### Verifica automatica Snap (2026-11-13)
La suite completa finale ha superato **95 test** (32 editor Qt e 63 GUI/core/Job Manager/FFmpeg) in 30,7 secondi, con processo terminato correttamente (codice 0). I tre test Snap eseguiti isolatamente sono passati; la prova di riordino ha salvato/riaperto il progetto e prodotto un MP4 verificato con FFprobe. In un'esecuzione separata del solo gruppo editor i 32 test avevano riportato `OK`, ma il teardown Qt aveva restituito il codice Windows `-1073740791`; l'anomalia non si è ripetuta nella suite completa finale. Nessun test ha usato progetti o media originali.

## 2026-11-13 — Waveform audio
- Visualizzazione della waveform nelle tracce audio con allineamento a zoom e scorrimento e ampiezza influenzata dal volume; `source_in` sposta la finestra mostrata.
- Generazione FFmpeg in background e cache compressa rigenerabile, con invalidazione quando cambia il file. Analisi a blocchi e paint limitato per file lunghi.
- Compatibilità JSON v1; nessuna modifica a renderer o asset sorgenti. Aggiunti test FFmpeg, cache, durata lunga, paint e ciclo editor/render.

## 2026-11-13 — Copia, incolla e duplicazione clip
- Aggiunti shortcut timeline `Ctrl+C`, `Ctrl+V` e `Ctrl+D` per scene immagine/video, con ID univoci e conservazione di durata, source-in e altri campi scena.
- L'incolla usa i tagli consentiti dalla timeline sequenziale v1, validando transizioni e evitando gap/sovrapposizioni.
- Ogni inserimento è una singola operazione Undo/Redo; selezione e playhead seguono la copia. Nessuna modifica al JSON o ai media sorgenti.

## 2026-11-14 — Track Lock: blocco e sblocco tracce

- Aggiunti cinque pulsanti toggle sopra la timeline per bloccare indipendentemente le tracce VIDEO, AUDIO, MUSICA, TESTI e SOTTOTITOLI.
- Le clip delle tracce bloccate mostrano etichetta `[LOCKED]` e opacità ridotta a 0,58; il cursore di ridimensionamento è soppresso.
- Tutte le operazioni di modifica sono bloccate sulla traccia corrispondente: importazione, aggiunta overlay, eliminazione, Ripple Delete, split, trim, riordino, copia/incolla/duplicazione, Inspector "Applica" e shortcut da tastiera (`Del`, `Ctrl+C/V/D`, `S`, `Shift+Del`).
- Undo/Redo rispetta i blocchi: i pulsanti Annulla/Ripristina rimangono disabilitati se lo snapshot coinvolge una traccia bloccata.
- Ripple Delete controlla esplicitamente TESTI e SOTTOTITOLI: se esistono testi/sottotitoli nel tratto da collassare e la traccia è bloccata, l'operazione è rifiutata con messaggio in italiano.
- Lo stato dei blocchi è salvato in `project.settings["editor"]["track_locks"]` — **non** in timeline JSON v1 — e sopravvive al salvataggio e alla riapertura del progetto.
- I progetti precedenti si aprono con tutte le tracce sbloccate (retrocompatibilità completa). L'export via Job Manager/FFmpeg funziona normalmente anche con tracce bloccate.
- Aggiunti 6 nuovi test specifici; la suite completa conta **113 test PASS** (nessuna regressione).
- JSON v1, renderer FFmpeg e Job Manager invariati.

## 2026-11-13 — Ripple Delete
- Aggiunti pulsante Inspector e shortcut `Shift+Delete` per eliminare una scena e chiudere il tratto nella timeline sequenziale. Il normale **Elimina selezione** non cambia.
- Riallineati testi/sottotitoli e rimossi i crossfade adiacenti; fade terminali controllati sui nuovi bordi. Audio globale e musica restano continui.
- Undo/Redo in un solo comando, preview/playhead aggiornati, progetto salvabile senza modifiche a JSON v1, renderer o media originali. Test prima/centro/ultima, crossfade e riapertura.
## 2026-11-15 — Fase 0: integrità progetto e stabilità GUI

- Aggiunta conferma **SALVA / SCARTA / ANNULLA** prima di Home, cambio progetto e chiusura finestra; errori di salvataggio e annullamento impediscono la transizione. Scarta ricarica la versione persistita.
- Stabilizzato il teardown Qt con slot bound e disconnessione delle callback undo/player, arresto di timer e player e gestione del refresh audio differito.
- Aggiunti test GUI per le tre scelte, errori, navigazione, cambio progetto, chiusura e test lifecycle editor. Suite completa: **160 test superati, 0 falliti**; `compileall` e `git diff --check` superati.

## 2026-11-15 — Fase 1: organizzazione UX dell’editor

- Riorganizzato l’editor in libreria media, anteprima, schede Inspector/Esportazioni e timeline ridimensionabili.
- Aggiunti catalogo Foto/Video con ricerca, filtri, miniature video asincrone e inserimento esplicito dei media selezionati; nessun inserimento automatico all’importazione.
- Aggiunti header tracce fissi, colori distinti per voce/musica e comandi Zoom +, Zoom − e Fit.
- Esposti progresso, fase, annullamento, storico, messaggi e log del Job Manager nella GUI.
- Test mirati superati; suite completa: **163 test superati, 0 falliti**. `compileall` e `git diff --check` superati.
