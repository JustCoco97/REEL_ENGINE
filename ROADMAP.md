# Roadmap REEL ENGINE

Le fasi sono ordinate per dipendenza; le voci marcate completate sono verificate nel codice e nei test. Vedere anche [DECISIONS.md](DECISIONS.md) per le questioni ancora aperte.

## Fase A — Baseline e sicurezza del progetto

- Registrare versioni dell'ambiente, comandi CLI, schema v1 e hash/backup autorizzati.
- Definire esecuzione dei test da directory isolata e protezione dei media esistenti.
- **Esito verificabile:** baseline riproducibile e 10 test documentati senza modificare gli asset originali.

## Fase B — Rifattorizzazione interna senza cambio funzionale

- Separare gradualmente modello, validazione, CLI e render service.
- Mantenere comando legacy e schema timeline v1.
- **Test:** i 10 test correnti; test di compatibilità dei comandi e del JSON; nessuna differenza non intenzionale negli output.

## Fase C — Progetto persistente e GUI essenziale

- **Core completato nell'incremento Fase D del 2026-11-11:** formato progetto JSON versione 1; creazione, apertura, salvataggio atomico, aggiornamento timeline, percorsi media relativi/assoluti e diagnostica asset mancanti.
- Il shell desktop e il primo editor visuale sono implementati negli incrementi Fase F descritti sotto; restano libreria media, funzioni avanzate di editing e migrazioni oltre la versione 1. Raccolta esplicita dei media completata il 2026-11-14.
- **Test core:** round-trip progetto, gestione percorsi relativi/assoluti, asset mancanti e fallimenti di salvataggio atomico.

## Fase D — Job manager e rendering affidabile

- **Completata nell'incremento Fase E del 2026-11-11:** coda FIFO a worker singolo, stati, progresso FFmpeg, cancellazione, log, workspace isolati, preflight risorse, pubblicazione dopo QC e registro persistente con recupero dei lavori in attesa.
- I job attivi trovati dopo un riavvio sono marcati interrotti; non viene tentato il resume a metà render.
- I media mancanti, l'output non valido o una destinazione che coincide con progetto/media fanno fallire il job senza pubblicazione/sovrascrittura.
- **Verifica completata:** 12 test Job Manager e suite totale di 47 test; verificati FIFO multi-progetto, avanzamento osservato da FFmpeg, cancellazione FFmpeg tramite manager, recovery persistente, isolamento, diagnostica/QC e pubblicazione reale.

## Fase E — Video sorgente, preview e proxy

- **Video sorgente e `source_in` completati.** Proxy video automatici e cache rigenerabile per progetto implementati nell'incremento 2026-11-14; i proxy sono limitati alla preview e l'export usa sempre l'originale. Restano da validare i preset e i codec su installazioni Windows diverse.
- Benchmark leggero solo quando autorizzato, sulle risoluzioni/codec target.
- **Test:** durata, seek, frame rate, audio, sincronizzazione e ripresa da progetto su percorsi diversi.

## Fase F — Timeline multitraccia e decisione renderer

- **Primo incremento desktop completato il 2026-11-11:** shell Windows PySide6 con Home, creazione/apertura progetto, elenco recenti, apertura cartella progetti, diagnostica dipendenze e gestione errori. Il package GUI è separato dal core. Verificati 10 test GUI, incluso avvio dell'event loop Qt.
- **Secondo incremento editor visuale completato il 2026-11-11:** preview immagini/video e overlay temporizzati; timeline visuale con scala e righe video/audio/testi/sottotitoli; selezione clip, inspector, importazione media, playback/seek, salvataggio v1 ed export tramite Job Manager. Verificati 3 test dedicati, incluso render FFmpeg di immagini/video/audio/testo e riapertura del progetto.
- Il preview composito in tempo reale delle dissolvenze immagine-immagine, immagine-video, video-immagine e video-video è implementato nell'incremento 2026-11-14. Riordino, trim visuale, proxy per il preview e raccolta facoltativa dei media sono completati negli incrementi successivi.
- **Correzione editor completata il 2026-11-11:** ogni immagine importata diventa una scena v1; selezione clip, seek e playback mostrano la scena corretta, con indicatore di selezione visibile. Salvataggio/riapertura e messaggi per immagini illeggibili sono verificati con test Qt e una prova indipendente. Non cambia il formato progetto.
- **Click intestazioni timeline corretto il 2026-11-12:** cliccare le intestazioni traccia non azzera la selezione grafica; seek e click sulle clip sono rimasti attivi. Test Qt controlla selezione, Inspector/preview visibili, seek e anteprima dopo click clip. Resta da confermare manualmente che non vi sia una distinta anomalia di layout nella finestra/risoluzione usata dall'utente.
- **Importazione, preview e miniature corrette il 2026-11-12:** le scene importate vengono persistite subito; immagini/stato vuoto e video usano pagine preview esclusive; le foto hanno miniature con cache limitata in traccia VIDEO. Test Qt verifica due foto salvate e riaperte, preview e riuso cache. Rimane la verifica sul PC dell'utente con `TEST_PRIMO_REEL_2` e fotografie originali, non alterati durante lo sviluppo.
- **Continuità audio e durata progetto corrette il 2026-11-12:** il player non viene riavviato ai confini di scena; editor e render seguono la durata massima tra scene e musica. Preview mostra il tratto finale senza immagine; export prolunga con nero e mantiene l'audio fino alla fine. Test Qt, FFmpeg e FFprobe coprono musica lunga e immagini lunghe. Da verificare ancora l'uscita audio sul PC Windows dell'utente.
- **Confronto architetturale completato il 2026-11-14:** vedi `ARCHITECTURE.md` e `DECISIONS.md`. Decisione: FFmpeg unico renderer attuale; GES primo candidato per un pilot quando serve NLE multitraccia; MLT resta comparatore. Nessuna dipendenza installata e nessun benchmark runtime alternativo.
- **Gate di prodotto aperto:** la build locale FFmpeg è statica GPLv3 con libx264; fare inventario SBOM/licenze/codec e validazione brevetti, quindi produrre/testare profilo di distribuzione compatibile prima di pubblicare commercialmente. Build corrente e x264 non sono ancora un pacchetto commerciale approvato.
- **Stili crossfade completati in GUI/preview/export il 2026-11-14:** menu per i 22 stili, preferenza in `settings.editor.crossfade_style`, compositing live Qt e stesso stile nel render Job Manager. Vecchi progetti default `fade`, Timeline JSON v1 invariata. Da fare solo la verifica manuale di fedeltà frame-per-frame fra Qt e FFmpeg; resta il pilot misurato GES/MLT quando si definisce un requisito multitraccia.
- Completare editing multitraccia, animazioni e trasferibilità del progetto.
- **Test:** composizione, sincronizzazione, transizioni, mix, rendering e trasferibilità del progetto.

## Fase G — Quality control e preset

- Controlli tecnici e preset di esportazione con esito pass/warning/fail.
- **Test:** stream, dimensioni, frame rate, durata, decodificabilità, audio, clipping/silenzio/nero e sottotitoli.

## Diagnostica Ollama/Qwen 3.5 — 2026-11-15

- Output strutturato, controllo del thinking supportato, parser rigoroso, singolo retry e diagnostica locale limitata/redatta implementati; 20 test mirati e 154 test complessivi superati; prova Qwen end-to-end valida (4 scene) in 119,71 s.
- Ollama 0.40.16 e Qwen 3.5 4B rilevati via API. Prova sintetica valida con `think:false`; il contenuto grezzo del precedente errore non era stato conservato, dunque la causa testuale specifica resta non verificabile.
- Prossimo: test GUI con brief e media reali, verificando coerenza, latenza e diagnostica. Non valutare modelli alternativi prima del risultato.

## Fase H — AI opt-in

- **AI Video Director Fase 1 completata:** bozza deterministica, non AI, per brief/narrativa/script/storyboard e progetto v1.
- **Fase 2 Ollama locale implementata:** provider HTTP loopback, modelli già installati, JSON Schema, validazione catalogo/ID, timeout e GUI asincrona; modalità template invariata. Nessuna dipendenza di terzi, download o cloud.
- **Baseline del 14/11 (storica):** Ollama non rilevato. La disponibilità è stata verificata il 15/11 e il seguito è tracciato nella sezione Diagnostica Ollama/Qwen 3.5 sopra.
- **Prossimo gate concreto:** installazione/avvio e scelta/caricamento manuale del modello; test su asset isolati, misure CPU/RAM/VRAM e latenza; migliorare prompt/schema in base ai limiti osservati. Cloud resta escluso salvo autorizzazione esplicita e futura revisione privacy/costi/licenze.

## Fase I — Distribuzione Windows

- Pacchetto portable/installer testato su PC pulito, senza Python o FFmpeg di sviluppo.
- Licenze, versioni, runtime, codec, firma, aggiornamento e recovery documentati.
- **Esito verificabile:** aprire progetto, importare media, renderizzare e aggiornare su un secondo Windows mantenendo i dati dell'utente.

### Verifica pratica Fase F — 2026-11-11

- Prova end-to-end superata per importazione, timeline, selezione/proprietà, preview/playback, salvataggio/riapertura e export verificato tramite Job Manager con media sintetici indipendenti.
- Prima di aggiungere funzioni: fare un controllo manuale dei dialoghi file e dell'usabilità visiva. Poi raccogliere eventuali problemi; non è stata richiesta né introdotta altra funzione in questo passaggio.


## Aggiornamento 2026-11-12 — audio opzionale

Completato: export con sottofondo senza audio principale, export senza audio, e controllo qualità Job Manager coerente con la timeline. Verificati render FFmpeg/FFprobe per musica più lunga delle scene e video-only; i test preesistenti coprono solo audio principale e mix. Resta utile una prova d'ascolto su dispositivo Windows reale, non necessaria per la validazione del render.


## Aggiornamento 2026-11-12 — riordino scene

Completato il trascinamento prima/dopo nella traccia VIDEO, con soglia anti-spostamento accidentale, feedback d'inserimento, preview aggiornata e persistenza del nuovo ordine. Verificata la conservazione di durata, `source_in`, audio, testi e sottotitoli; transizioni non compatibili vengono rifiutate. Prossimi miglioramenti editoriali restano split/trim visivo, undo/redo, zoom/snap e posizionamento temporale libero (quest'ultimo richiede un modello distinto dalla timeline v1 sequenziale).


## Aggiornamento 2026-11-13 — split clip

Completato split di immagini e video al frame più vicino alla testina, con scorciatoia `S`, continuità `source_in`, durata conservata, gestione conservativa delle transizioni e round-trip progetto. Resta fuori ambito il trim visuale delle estremità, l'undo/redo e l'eventuale dissolvenza configurabile al nuovo punto di taglio.


## Aggiornamento 2026-11-13 — continuità video dopo split

Completata la correzione del seek asincrono nel preview. Verificati un render FFmpeg blu/rosso con taglio a 1,5 s e il player Qt dopo split, salvataggio, riapertura e playback oltre il taglio. Nessuna modifica a renderer o timeline v1. Resta consigliata una prova manuale sul PC Windows dell'utente con il progetto effettivamente segnalato.


## Stato – Trim professionale

**Completato nell'incremento del 2026-11-13:** trim dei bordi destro e sinistro delle clip video, quantizzazione a frame, minimo di un frame, aggiornamento `source_in` per video, gestione della durata immagine, riallineamento sequenziale delle scene, aggiornamento playhead/anteprima e controlli di compatibilità con transizioni e testi temporizzati. Non sono state aggiunte dipendenze né campi al JSON v1.

**Prossimo passaggio:** verifica manuale nell'app Windows con un progetto di prova contenente immagini, video, musica, testi e transizioni; osservare l'uso delle maniglie e il comportamento con audio lungo.


## Aggiornamento 2026-11-13 — Undo/Redo

Completati pulsanti Annulla/Ripristina e scorciatoie Ctrl+Z/Ctrl+Y con `QUndoStack`. Operazioni di importazione, eliminazione, riordino, split, trim, proprietà audio/video/testo/sottotitolo e mutazioni di transizione legate alle scene sono comandi atomici. Clean state e asterisco distinguono le modifiche non salvate; cambio progetto azzera la cronologia. La cronologia è limitata alla sessione, non viene salvata nel progetto. Prossimo controllo: verifica manuale desktop su Windows con un progetto di prova, inclusi Undo dopo un salvataggio, riapertura ed export.


### Aggiornamento 2026-11-13 — Trim live
Completato il feedback interattivo dei bordi, il ridimensionamento live della clip, l'aggiornamento frame-aligned di playhead/anteprima, la conferma con una sola voce Undo e il ripristino con `Esc` o input invalido. Verificati bordi destri di foto/video, bordo sinistro video in modalità roll, Undo/Redo, riapertura e export FFmpeg. Da verificare manualmente sull'installazione Windows dell'utente. Il bordo sinistro della prima scena resta non disponibile nel modello v1 sequenziale.


## Aggiornamento 2026-11-13 — Snap magnetico
Completato aggancio di riordino, Trim/Roll e playhead a bordi clip e transizioni, con guida visiva. La soglia resta 10 pixel del viewport con zoom diversi; pulsante e `N` attivano/disattivano Snap. Frame alignment e singolo Undo per gesto restano validi. Verificati snap attivo/disattivo, più scale, playhead, riordino, Trim/Roll, Undo/Redo, round-trip e export FFmpeg. Da fare soltanto la conferma manuale della sensazione di aggancio con mouse e zoom nella finestra Windows dell'utente.


### Verifica automatica Snap (2026-11-13)
La suite completa finale ha superato **95 test** (32 editor Qt e 63 GUI/core/Job Manager/FFmpeg) in 30,7 secondi, con processo terminato correttamente (codice 0). I tre test Snap eseguiti isolatamente sono passati; la prova di riordino ha salvato/riaperto il progetto e prodotto un MP4 verificato con FFprobe. In un'esecuzione separata del solo gruppo editor i 32 test avevano riportato `OK`, ma il teardown Qt aveva restituito il codice Windows `-1073740791`; l'anomalia non si è ripetuta nella suite completa finale. Nessun test ha usato progetti o media originali.

## Stato waveform — 2026-11-13
Completati generazione automatica locale, cache RAM/disk, invalidazione alla modifica del media, disegno con zoom/scroll e supporto alle tracce audio esistenti. La waveform è solo un aiuto visivo e non interviene nel renderer. Prossimo controllo: conferma manuale su Windows con un file audio lungo e verifica visiva durante zoom, scroll, Undo/Redo e riapertura.

## Copia/incolla clip — 2026-11-13
Completati copia, incolla al confine valido del playhead, duplicazione adiacente, ID univoci, Undo/Redo e round-trip progetto per immagini e video. Nessuna modifica alla timeline JSON v1 o ai media. Da verificare manualmente nella GUI Windows con mouse/tastiera e con clip video dotate di source-in; resta fuori ambito la selezione multipla, non presente nell'architettura attuale.

## Ripple Delete — 2026-11-13
Completata rimozione ripple per una scena selezionata, con tempi frame-aligned, testo/sottotitoli rimappati, transizioni adiacenti gestite, audio globale continuo, Undo/Redo e round-trip. Rimane la verifica manuale della scorciatoia e del feedback nella GUI Windows; l'architettura non ha ancora clip audio collocate o selezione multi-clip.


## Track Lock — Blocco e sblocco tracce (2026-11-14)

**Completato.** Cinque pulsanti toggle sopra la timeline consentono di bloccare indipendentemente VIDEO, AUDIO, MUSICA, TESTI e SOTTOTITOLI. Ogni traccia bloccata mostra feedback visivo (`[LOCKED]`, opacità 0,58, cursore soppresso) e impedisce qualsiasi operazione di modifica: importazione, aggiunta overlay, eliminazione, Ripple Delete, split, trim, riordino, copia/incolla/duplicazione, Inspector "Applica" e tutte le shortcut. Undo/Redo rispetta i blocchi. Lo stato è persistito in `project.settings["editor"]["track_locks"]`; schema JSON v1, renderer FFmpeg e Job Manager invariati; retrocompatibilità con i vecchi progetti garantita; export con tracce bloccate funzionante. 6 nuovi test; suite completa: 113 test PASS, nessuna regressione.

**Verifiche manuali ancora consigliate:** aprire la GUI Windows, testare i cinque pulsanti lock con mouse e tastiera, osservare il feedback visivo `[LOCKED]` e l'opacità ridotta sui clip, confermare che i dialoghi di importazione non si aprano su tracce bloccate, verificare il messaggio nella barra di stato in italiano, aprire/chiudere un progetto con tracce bloccate, esportare e controllare l'MP4 risultante.
## Fase 0 — Integrità progetti e stabilità GUI

- **Implementata il 2026-11-15:** guardia SALVA/SCARTA/ANNULLA per Home, cambio progetto e chiusura; salvataggio fallito blocca l’uscita, scarto ricarica lo stato persistito. Teardown Qt ferma timer/player/proxy, converte callback undo/crossfade in slot bound e lega il refresh watcher a un timer figlio; `close()` esegue la pulizia anche sugli editor mai mostrati.
- **Test aggiunti:** scelte di salvataggio, errore, ritorno Home, cambio progetto, chiusura, attività asincrona e teardown ripetuto. Suite completa: **160 test superati, 0 falliti**; `compileall` e `git diff --check` superati.

## Fase 1 — UX e organizzazione dell’editor

- **Implementata il 2026-11-15:** pannelli adattabili Libreria/Preview/Inspector/Timeline; catalogo Foto/Video con ricerca, filtri e thumbnail video asincrone; importazione separata dall’inserimento; header fissi per le cinque tracce visive; zoom +/−/Fit; pannello Job Manager con progress, fase, annullamento, storico, errori e log.
- La libreria è un’impostazione editor del progetto, non una modifica al formato Timeline JSON v1. Nessun livello timeline sovrapposto è stato introdotto.
- **Verifica:** test mirati superati; suite completa **163 test, 0 falliti**; `compileall` e `git diff --check` superati. Fase 2 non iniziata.
