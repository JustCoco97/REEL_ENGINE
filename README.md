# REEL ENGINE

REEL ENGINE è un motore CLI Python per comporre video da immagini e audio. L'obiettivo è evolvere gradualmente in un'applicazione desktop Windows per la produzione audiovisiva guidata, mantenendo il controllo umano e senza rendere obbligatori provider AI o servizi a pagamento.

## Stato attuale

Il programma supporta il comando legacy `--images`/`--audio` e una timeline JSON `schema_version: 1`. La timeline descrive scene immagine/video, audio principale, sottofondo opzionale, testi temporizzati, sottotitoli e transizioni. Titoli e sottotitoli usano FFmpeg `drawtext`; le transizioni supportano cut, crossfade e fade dal/al nero. Il renderer calcola la durata risultante e mantiene sincronizzati audio e testi.

Il core supporta progetti persistenti JSON versionati: creazione, salvataggio atomico, riapertura, riferimenti media relativi/assoluti, diagnostica dei file mancanti e raccolta esplicita dei media esterni dentro il progetto. Nell'editor usa **Raccogli media** per copiare gli asset esterni in `media/` (font in `fonts/`) e rendere il progetto trasferibile; gli originali restano invariati. La GUI desktop PySide6 include un editor visuale con anteprima immagini/video, timeline a tracce, inspector, importazione tramite selezione file, salvataggio e export tramite Job Manager. La timeline JSON v1 e la CLI esistente restano compatibili. I dettagli verificati e i limiti sono in [PROJECT_STATUS.md](PROJECT_STATUS.md); architettura e decisioni sono in [ARCHITECTURE.md](ARCHITECTURE.md) e [DECISIONS.md](DECISIONS.md).

## Avvio interfaccia desktop

La GUI è un componente opzionale separato dal core Python. Installare PySide6 nel virtualenv e avviare il launcher Windows:

```powershell
$py = 'C:\Users\manue\venvs\video-ai\Scripts\python.exe'
& $py -m pip install -r 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\requirements-gui.txt'
& $py 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\scripts\reel_engine_gui.py'
```

La schermata Home apre e crea progetti `project.json`, mantiene l'elenco recente nelle impostazioni utente e apre la cartella predefinita `Documenti\REEL ENGINE\Projects`. La diagnostica mostra versione/runtime di Python e PySide6 e verifica la presenza e l'avvio di FFmpeg e FFprobe. Dopo l'apertura del progetto si entra nell'editor.

### Editor visuale

La toolbar importa immagini/video come scene in sequenza e audio come traccia principale o musica di sottofondo. Le tracce video, audio, testi e sottotitoli hanno clip selezionabili; l'inspector consente di modificare durate, `source_in`, volume/dissolvenze del sottofondo e contenuto/tempi/font/dimensione/colore/posizione/contorno/sfondo dei testi. `Ctrl+S` salva il progetto e “Esporta video” lo salva prima di inviarlo al Job Manager, che verifica l'export prima della pubblicazione.

Il preview mostra immagini, video, crossfade compositi e sovrapposizioni temporizzate; i controlli sincronizzano audio e posizione temporale. Il player della musica resta aperto e in riproduzione durante i cambi scena: viene riposizionato solo all'avvio o quando si effettua un seek manuale. La durata visualizzata e riprodotta è la maggiore tra la durata delle scene e la fine della musica di sottofondo (durata configurata oppure durata del file dopo `source_in`, arrotondata al fotogramma). Dopo l'ultima scena l'anteprima mostra un intervallo vuoto mentre l'audio continua; l'export FFmpeg prolunga il video con nero e conserva la musica fino alla stessa durata. Se le immagini durano più dell'audio, il video mantiene la durata visiva e l'audio termina con silenzio. L'importazione registra riferimenti ai file esistenti; **Raccogli media** ne copia una selezione completa nel progetto quando vuoi trasferirlo.

Ogni immagine/video importato viene aggiunto a `timeline.scenes` con il proprio asset e un ID distinto; l'importazione multipla conserva l'ordine dei file scelti e salva subito il progetto in modo atomico. Selezionando un clip viene mostrata subito la sua scena nell'anteprima e il clip è evidenziato con bordo chiaro. La traccia VIDEO mostra miniature per le fotografie; la decodifica ridimensionata è memorizzata in una cache LRU limitata e riutilizzata quando la timeline viene ricostruita. Cliccando sulla timeline o spostando il cursore temporale si aggiorna l'anteprima alla scena corrispondente; la riproduzione avanza tra le scene e aggiorna anche la clip selezionata. `Ctrl+S` salva la timeline nel progetto v1; riaprire il progetto ripristina scene e percorsi. Un'immagine che Qt non riesce a decodificare mostra il percorso e il motivo di lettura nell'anteprima e nella barra di stato. Il preview mantiene immagine/stato vuoto e video in pagine esclusive, con i testi sovrapposti su un livello separato; questo evita che il widget video lasci una superficie nera sopra l'immagine quando non è attivo. La verifica e i limiti osservati sono in [PROJECT_STATUS.md](PROJECT_STATUS.md).

Le etichette delle tracce VIDEO, AUDIO, TESTI e SOTTOTITOLI funzionano da intestazioni passive: cliccarle mantiene la clip selezionata e non nasconde Inspector o anteprima. I click sulla zona temporale continuano a spostare la testina; i click sulle clip selezionano la scena e aggiornano il preview.

La traccia audio principale è facoltativa nella timeline v1 (`"audio": null` o campo omesso). Se manca, il sottofondo diventa l'unica sorgente audio; se mancano entrambe le tracce, l'export contiene solo video. La durata continua a seguire i contenuti temporizzati: il sottofondo può estendere il render oltre le scene, mentre l'audio principale da solo mantiene il comportamento precedente. Quando sono presenti entrambe le tracce, vengono mixate come prima.

## API progetti (core Python)

```python
from reel_engine import create_project, open_project, save_project, diagnose_project, collect_project_media

project = create_project("Il mio reel", r"D:\Progetti\Il mio reel")
project.timeline["audio"] = "media/voce.wav"
project.timeline["scenes"] = [
    {"id": "intro", "type": "image", "asset": "media/intro.png", "duration": 3.0}
]
save_project(project)

reopened = open_project(project.path)
for missing in diagnose_project(reopened):
    print(missing.message)

# Facoltativo: copia i riferimenti esterni nel progetto e salva i percorsi relativi.
copied = collect_project_media(reopened)
```

Il file `project.json` registra la propria `format_version` separatamente da `timeline.schema_version: 1`. I media dentro la cartella progetto sono salvati con percorsi relativi; quelli esterni restano assoluti finché non si sceglie **Raccogli media**. La raccolta precontrolla tutti i riferimenti, conserva i file originali, usa nomi derivati dal contenuto e salva atomicamente i nuovi riferimenti; errori o risorse mancanti non lasciano il progetto parzialmente aggiornato. L'apertura risolve i percorsi relativi e permette di recuperare progetti con file mancanti; la diagnostica restituisce i riferimenti da ripristinare. Il progetto vuoto è un punto di partenza editoriale, mentre il rendering continua a richiedere i campi e le scene previsti dalla timeline v1.

## Job Manager

Il rendering da progetto salvato può essere gestito dal core senza GUI. Il manager mantiene una coda FIFO e un worker, espone stati, fase, percentuale, orari, log e cancellazione, e assegna a ogni lavoro una directory indipendente sotto `temp/jobs`.

```python
from reel_engine import JobManager

manager = JobManager()
job = manager.submit(r"D:\Progetti\Il mio reel\project.json", r"D:\Export\reel.mp4")
while not manager.wait(job.id, timeout=0.25):
    print(job.status.value, f"{job.progress:.0%}", job.phase)
if job.status.value == "failed":
    print(job.error, job.log_path)
manager.shutdown()
```

Stati: `queued`, `running`, `completed`, `failed`, `cancelled`. `manager.cancel(job.id)` interrompe in sicurezza il processo FFmpeg attivo o annulla un lavoro ancora in coda. Il risultato viene prima scritto nel workspace del lavoro, poi controllato con FFprobe e decodifica FFmpeg; solo dopo viene pubblicato atomicamente nella destinazione. Progetti con media mancanti e destinazioni coincidenti con progetto/media originali falliscono senza sovrascriverli. Per le firme complete consultare `src/reel_engine/jobs.py`.

Il registro versionato `temp/jobs/job_registry.json` conserva lo stato, i percorsi e gli orari dei lavori; viene aggiornato atomicamente. Alla riapertura, i lavori in attesa riprendono la coda, la cronologia terminale è consultabile e un lavoro trovato in esecuzione viene marcato come interrotto senza rilanciare FFmpeg. Se il registro segnala che il risultato era già stato pubblicato, l'esito viene riconciliato come completato quando il file è presente.

## Diagnostica Ollama e Qwen 3.5 4B (2026-11-15)

Nel test reale del 15/11 Ollama rispondeva e `qwen3.5:4b` risultava installato; `/api/show` dichiara il thinking predefinito attivo. Il messaggio generico precedente non conserva la risposta grezza, quindi non permette di sapere retroattivamente se il contenuto fosse troncato, avvolto da testo o sintatticamente errato. Il punto preciso di rifiuto era `json.loads(message.content)`: il testo non era JSON decodificabile senza normalizzazione. Una riproduzione locale con lo stesso modello/schema ha restituito JSON valido, ma il thinking attivo ha impiegato circa tre minuti e ha prodotto conteggi di scene disallineati; con `think: false` (supportato dal modello) il test diretto semplice ha restituito JSON in circa 5,2 secondi; il flusso aggiornato end-to-end ha completato quattro scene valide in 119,7 secondi. Questo verifica il formato strutturato sulla macchina, ma non ricrea il payload storico perduto.

L’adattatore ora legge `/api/show` e disattiva il thinking soltanto quando il modello dichiara esplicitamente di supportare `false` (altrimenti usa `low` se disponibile o lascia il default). Richiede un oggetto JSON con scene atomiche, normalizza testo introduttivo e code fence, rifiuta chiavi duplicate/troncamenti/campi o ID non validi e consente un solo tentativo di correzione. Ogni generazione Ollama salva una diagnostica locale limitata con contenuto finale grezzo, metadati, errori HTTP e parsing; non salva prompt, percorsi dei media o contenuto thinking, redige percorsi locali, mantiene al massimo 20 file. La risposta grezza potrebbe ripetere il brief. Il percorso viene mostrato in caso di errore.

**Verifica sul PC (15/11):** Ollama 0.40.16 risponde su localhost e `qwen3.5:4b` è presente; il comando `ollama` non è nel PATH. Nessun modello è stato scaricato. L’API Ollama ammette JSON Schema e un’opzione `think`; per i dettagli vedere [chat API](https://docs.ollama.com/api/chat), [thinking](https://docs.ollama.com/capabilities/thinking) e [scheda Qwen3.5:4b](https://ollama.com/library/qwen3.5:4b). Renderer FFmpeg e Timeline JSON v1 non sono stati modificati.

## AI Video Director — Fase 2: provider locale Ollama

Dalla Home, **AI Video Director** offre due modalità: bozza locale deterministica e generazione con un modello locale tramite Ollama. La finestra consente di verificare il server e selezionare fra i modelli già installati; il campo modello è configurabile e il timeout è regolabile. Controllo connessione, elenco modelli e generazione girano in background. REEL ENGINE non installa Ollama, non scarica modelli e non contatta servizi cloud; il provider accetta esclusivamente endpoint HTTP loopback.

L’adapter usa le API locali `GET /api/tags` e `POST /api/chat` con schema JSON strutturato per interpretazione AI del brief, narrativa, script e storyboard. Il prompt fornisce un catalogo controllato con ID stabili, nomi file, tipo e durata, senza percorsi; l’orchestratore valida schema, campi, quantità e ID e risolve ogni scena solo tramite il catalogo effettivo. Una risposta malformata, un modello non installato, timeout o errori di rete interrompono la generazione: non viene presentato un progetto riuscito. La modalità deterministica precedente resta selezionabile. Entrambe preparano un progetto modificabile con Timeline JSON v1 invariata; preview e scelta dell’export restano nell’editor all’utente.

Verifica precedente del 14/11: runtime non rilevato in quel momento. Verifica aggiornata del 15/11 (sezione sopra): server locale e modello sono presenti; non è stato scaricato alcun modello.

## Ambiente attuale

Ambiente verificato nelle fasi precedenti:

- Windows 11 25H2.
- Python 3.14.3 nell'ambiente virtuale `C:\Users\manue\venvs\video-ai`.
- FFmpeg e ffprobe 9.0.2 disponibili nel PATH.
- I test della suite usano `unittest`; i test CLI fanno rendering reale con fixture sintetiche temporanee.

I test richiedono FFmpeg, ffprobe e gli encoder `libx264` e `aac`. I dettagli dei test e dei limiti correnti sono descritti in [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Avvio CLI

Da PowerShell, usare l'interprete del virtualenv e i percorsi effettivi dei media:

```powershell
$py = 'C:\Users\manue\venvs\video-ai\Scripts\python.exe'
$engine = 'D:\VIDEO_AI\SOFTWARE\REEL_ENGINE\scripts\reel_engine.py'
& $py $engine --images 'D:\percorso\immagini' --audio 'D:\percorso\voce.wav' --output 'D:\percorso\reel.mp4'
```

Per una timeline JSON:

```powershell
& $py $engine --timeline 'D:\percorso\timeline.json' --output 'D:\percorso\reel.mp4'
```

Per scegliere uno stile sui crossfade già presenti nella timeline CLI, aggiungi `--crossfade-style wipeleft`. Nell'editor seleziona uno dei 22 stili dal menu **Transizione** nella testata della timeline: la scelta aggiorna subito l'anteprima Qt e viene usata anche dal Job Manager per l'export. La preferenza si salva in `project.settings.editor.crossfade_style`; i progetti precedenti, senza questa impostazione, usano `fade`. Non cambia il JSON della timeline v1. La lista CLI/UI comprende `fade`, `wipeleft`, `wiperight`, `wipeup`, `wipedown`, `slideleft`, `slideright`, `slideup`, `slidedown`, `circleopen`, `circleclose`, `dissolve`, `smoothleft`, `smoothright`, `fadeblack`, `fadewhite`, `radial`, `vertopen`, `vertclose`, `horzopen`, `horzclose` e `zoomin`.

Esempio di traccia di sottofondo nella timeline (relativa al JSON):

```json
{
  "background_audio": {
    "asset": "media/musica.wav",
    "source_in": 12.0,
    "duration": 35.0,
    "volume": 0.18,
    "fade_in": 1.5,
    "fade_out": 2.0
  }
}
```

`duration` omessa usa la durata residua del file dopo `source_in`; una durata esplicita fissa invece la lunghezza della traccia. Se il sottofondo termina dopo le scene, la durata del progetto e del render si estende fino al termine della musica con coda video nera; se termina prima, il video continua e l'audio esportato resta silenzioso fino alla fine. Le dissolvenze sono in secondi e la loro somma deve rientrare nella durata della traccia. `volume` è un guadagno lineare tra 0 e 1; il sottofondo non viene ripetuto automaticamente.

Testi e sottotitoli sono liste separate; `start` e `end` sono secondi sulla timeline video e `end` è esclusivo. Ogni elemento usa `text` per il contenuto. Posizioni disponibili: `top_left`, `top_center`, `top_right`, `center_left`, `center`, `center_right`, `bottom_left`, `bottom_center`, `bottom_right`. `font` accetta un nome di font Windows installato (ad esempio `Arial`) o un percorso `.ttf`, `.otf` o `.ttc`; il renderer usa file UTF‑8 temporanei per testo accentato e punteggiatura.

```json
{
  "texts": [
    {"start": 0.5, "end": 3.0, "text": "Caffè — titolo", "font": "Arial", "size": 52,
     "color": "#FFFFFF", "position": "top_center", "outline_color": "black", "outline_width": 2}
  ],
  "subtitles": [
    {"start": 1.0, "end": 2.4, "text": "L’azione è qui!", "font": "Arial", "size": 34,
     "color": "white", "position": "bottom_center", "background_color": "black@0.55",
     "background_padding": 8}
  ]
}
```

I tempi vengono allineati al fotogramma più vicino e devono rientrare nella durata della timeline. Colori accettati: nomi semplici o `#RRGGBB`/`#RRGGBBAA`, con alpha opzionale come `black@0.55`. Contorno e sfondo sono opzionali; il contorno predefinito è nero.

Le transizioni si configurano in un oggetto opzionale. `between` descrive il passaggio dopo l'ID della scena; i passaggi non elencati sono cut diretti.

```json
{
  "transitions": {
    "start": {"type": "fade_from_black", "duration": 0.4},
    "between": [
      {"after": "opening", "type": "crossfade", "duration": 0.35},
      {"after": "middle", "type": "cut"}
    ],
    "end": {"type": "fade_to_black", "duration": 0.4}
  }
}
```

Le durate sono allineate al fotogramma più vicino. Il crossfade sovrappone le scene e riduce la durata finale della propria durata; fade da e verso il nero non aggiungono né sottraggono tempo. La somma dei crossfade che toccano una scena non può superare la sua durata. Le transizioni si applicano dopo il rendering dei segmenti immagine o video; audio, titoli e sottotitoli seguono la timeline risultante.

`ffmpeg` e `ffprobe` devono essere risolvibili dal PATH. La modalità timeline dichiara l'audio nel JSON e non accetta `--audio` separatamente. Ogni render usa una propria cartella temporanea sotto `temp\renders`.

## Documentazione e collaborazione

La documentazione permanente e il progetto sono la fonte di verità; la memoria o la cronologia di una singola AI non lo sono. Prima di intervenire leggere [AI_START_HERE.md](AI_START_HERE.md), [PROJECT_STATUS.md](PROJECT_STATUS.md), [DECISIONS.md](DECISIONS.md) e i documenti specifici dell'attività.

È consentito un solo operatore autorizzato a modificare file del progetto alla volta. La procedura di passaggio è in [HANDOFF.md](HANDOFF.md); obiettivi e criteri sono in [ROADMAP.md](ROADMAP.md), mentre gli interventi sono registrati in [CHANGELOG.md](CHANGELOG.md).

## Verifica pratica dell'editor — 2026-11-11

È stata completata una prova end-to-end con un progetto indipendente e fixture sintetiche create appositamente: due immagini, un video H.264 e una traccia WAV. L'editor le ha importate, ha mostrato i clip sulle tracce, ha permesso selezione e modifica della durata, preview immagine/video e play/pause. Il progetto è stato salvato, chiuso e riaperto con durata e percorsi media conservati. L'export tramite Job Manager è terminato `completed` al 100%; FFprobe ha rilevato H.264 1080x1920, AAC e 5,5 secondi. Gli hash dei quattro asset sono rimasti invariati. Il progetto e l'export di prova sono in una cartella temporanea separata dal repository e dai media originali (il percorso è riportato in `PROJECT_STATUS.md`).


### Riordino scene nella timeline

Nella traccia VIDEO puoi trascinare una scena prima o dopo un'altra per cambiarne l'ordine. Un click senza trascinamento continua a selezionarla; il riordino inizia soltanto dopo aver superato la soglia di movimento del mouse e mostra un indicatore di rilascio. La timeline ricalcola gli inizi scena e aggiorna l'anteprima, mantenendo asset, durata e `source_in`. Audio, testi e sottotitoli conservano i loro tempi assoluti e non vengono spostati. Le transizioni tra scene restano collegate all'ID della scena `after`; il riordino viene rifiutato se lascia una transizione senza clip successiva o rende una dissolvenza incompatibile con la durata. Dopo il riordino usa **Salva** o `Ctrl+S`. La timeline v1 è sequenziale: non supporta ancora clip collocate liberamente con spazi vuoti o sovrapposizioni arbitrarie.


### Dividere una scena

Seleziona una scena VIDEO, porta la testina nel punto desiderato dentro la clip e usa **Dividi scena · S** oppure premi `S` mentre la timeline è attiva. Il punto viene allineato al fotogramma più vicino secondo gli FPS del progetto; sono richiesti almeno un fotogramma prima e uno dopo. Le immagini mantengono lo stesso asset su entrambe le parti. I video proseguono dalla posizione corretta della sorgente nella seconda parte. Lo split è un cut; un'eventuale transizione in uscita viene spostata dopo la seconda parte. Se una dissolvenza non può stare nella durata risultante, l'operazione viene rifiutata senza cambiare il progetto. Salva con **Salva** o `Ctrl+S`.


### Correzione preview dopo split video — 2026-11-13

Il player dell'anteprima riutilizza la stessa sorgente quando due segmenti consecutivi provengono dallo stesso file. Quando deve caricare un video diverso, attende che Qt segnali `LoadedMedia` o `BufferedMedia` prima di applicare il seek e riprendere la riproduzione. Questo evita che il caricamento asincrono riporti la seconda parte all'inizio del file. Il renderer già usa `source_in` come `-ss` FFmpeg prima dell'ingresso video.


### Trim delle clip

Le clip VIDEO si possono accorciare o allungare trascinando i bordi: il bordo destro cambia la durata, quello sinistro regola il punto di ingresso. Il gesto viene allineato al fotogramma più vicino; una scena non può scendere sotto un fotogramma né superare i limiti del file video. Il trim sinistro sposta il taglio condiviso con la scena precedente (roll): per i video aggiorna `source_in`, mentre le immagini mantengono lo stesso asset. La timeline v1 è sequenziale e non rappresenta spazi vuoti; la prima scena non ha un bordo sinistro modificabile. Le tracce audio, testi, sottotitoli e transizioni restano intatti; se un taglio rendesse impossibile rispettare una transizione o l'intervallo dei testi, viene rifiutato. Il trim è implementato con PySide6 e il core locale, senza nuove dipendenze o servizi a pagamento.


### Annulla e ripristina

Nell'editor usa i pulsanti **Annulla** e **Ripristina**, oppure `Ctrl+Z` e `Ctrl+Y`. Ogni importazione multipla, eliminazione, riordino, split, trim o modifica applicata nell'Inspector è una singola voce. La cronologia ripristina anche selezione e playhead quando validi; è in memoria, limitata agli ultimi 100 comandi e si azzera quando apri un altro progetto. Undo/Redo non salva automaticamente: `*` accanto al nome indica modifiche non salvate. Le importazioni immagini/video mantengono il precedente salvataggio immediato; gli altri cambi richiedono **Salva** o `Ctrl+S`.


### Trim live nell'editor (2026-11-13)
Durante il trascinamento dei bordi VIDEO, la geometria delle clip segue il puntatore a passi di un fotogramma, il playhead si allinea al frame e l'anteprima viene aggiornata con cadenza limitata. Il rilascio valida e registra un solo comando Undo; `Esc` ripristina geometria, playhead e anteprima iniziali. Il bordo destro varia la durata. Il bordo sinistro sposta in modalità roll il taglio condiviso con la scena precedente: per un video avanza/arretra anche `source_in`, per un'immagine mantiene la stessa sorgente. La prima scena non ha un taglio precedente e il suo bordo sinistro non è modificabile nella timeline sequenziale v1.


### Snap magnetico
Lo snap è attivo per impostazione iniziale. Usa il pulsante **Snap: Attivo/Disattivo** sopra la timeline oppure premi `N` quando la timeline ha il focus. Il puntatore si aggancia entro **10 pixel dello schermo**, quindi la soglia non cambia con lo zoom. I punti includono bordi delle scene, tagli, limiti delle transizioni e playhead. La linea guida gialla mostra il punto agganciato. Puoi trascinare il playhead afferrandolo nella scala superiore. Con Snap disattivato il movimento resta libero; il valore finale resta comunque allineato al frame. Ogni riordino, Trim o Roll completato rimane una singola voce Undo; spostare il playhead è navigazione e non aggiunge una modifica alla cronologia.

### Waveform audio nella timeline
La traccia audio mostra una forma d'onda generata localmente con FFmpeg. L'analisi gira in background e usa una cache rigenerabile, senza cambiare il progetto JSON v1 né i file audio originali. Zoom e scorrimento sono gestiti dal disegno della timeline; `source_in` e volume regolano la porzione e l'ampiezza visualizzate.

### Copia, incolla e duplicazione clip
Seleziona una clip immagine o video nella timeline. Premi `Ctrl+C` per copiarla, sposta il playhead e premi `Ctrl+V` per inserirla; `Ctrl+D` crea invece subito una copia dopo la clip selezionata. Asset, durata e `source_in` vengono conservati; l'inserimento aggiorna anteprima e selezione e si può annullare con `Ctrl+Z` o ripristinare con `Ctrl+Y`. La timeline JSON v1 non prevede posizioni libere: se il playhead cade dentro una clip, Incolla inserisce al successivo taglio, senza dividere o sovrapporre la clip. Il buffer è interno all'editor e viene azzerato quando si apre un altro progetto. La selezione multipla non è attualmente prevista.

### Ripple Delete
Seleziona una scena VIDEO e usa il pulsante **Ripple Delete · Shift+Canc** nell'Inspector oppure premi `Shift+Delete` con la timeline attiva. La scena viene rimossa e le successive si riallineano; i testi e i sottotitoli vengono spostati insieme al nuovo tempo e quelli interamente compresi nel tratto rimosso vengono eliminati. Le transizioni sui due tagli adiacenti alla scena rimossa vengono eliminate; gli eventuali fade iniziale/finale rimangono e si adeguano alla durata delle scene rimaste. Audio principale e musica continuano dal tempo zero: il JSON v1 li modella come tracce globali, non come clip con posizione indipendente. L'operazione è singola in Undo/Redo e si salva con **Salva** o `Ctrl+S`. **Elimina selezione** resta disponibile per cancellare senza ripple.

### Blocco e sblocco tracce (Track Lock — 2026-11-14)
Sopra la timeline sono presenti cinque pulsanti toggle per bloccare o sbloccare indipendentemente le tracce: **VIDEO**, **AUDIO**, **MUSICA**, **TESTI** e **SOTTOTITOLI**.
Quando una traccia è bloccata:
- I clip associati mostrano l'etichetta `[LOCKED]` e opacità attenuata.
- Le operazioni di modifica sulla traccia bloccata vengono rifiutate con messaggio chiaro nella barra di stato (importazione media, aggiunta audio/testi, drag di riordino, split `S`, trim live/roll, duplicazione `Ctrl+D`, incolla `Ctrl+V`, eliminazione `Delete` / pulsante, ripple delete `Shift+Delete` e modifiche dall'Inspector).
- L'Inspector disabilita i controlli della clip e mostra `[BLOCCATA]` nel titolo.
- Le altre tracce libere continuano a essere normalmente modificabili.
- L'ascolto, la riproduzione, il seek e la selezione rimangono sempre fruibili.
- Undo e Redo proteggono le tracce bloccate: se un'operazione comporterebbe variazioni a una traccia bloccata, l'azione viene inibita segnalando la traccia interessata; sbloccandola, la cronologia è intatta e l'annullamento/ripristino può procedere.
- Lo stato viene salvato in `project.settings["editor"]["track_locks"]` senza alterare lo schema timeline JSON v1; i progetti con tracce bloccate sono completamente esportabili tramite Job Manager e conservano i blocchi alla riapertura.

### Preview delle dissolvenze tra immagini

Durante seek e riproduzione, il preview compone in tempo reale i crossfade immagine-immagine, immagine-video, video-immagine e video-video. Due decoder Qt senza audio forniscono i frame video asincronamente; le immagini statiche sono mantenute in una cache limitata. Al termine del crossfade viene ripristinato il preview normale della scena. La timeline e l'export restano invariati.

### Proxy video

I video importati vengono convertiti in background in proxy H.264 leggeri, con lato massimo di 960 pixel, e salvati nella cache locale `.reel_engine/proxies` del progetto. Il preview normale e quello dei crossfade usano il proxy quando disponibile; durante la generazione ripiegano sull'originale. La cache è riutilizzata finché percorso e fingerprint del file originale (dimensione, date di modifica/creazione e identificatore file) restano invariati. Modifica o sostituzione del media produce un proxy distinto. **Usa proxy** attiva o disattiva la preferenza, salvata nelle impostazioni editor del progetto. Gli asset in timeline e l'export mantengono sempre i percorsi originali; i proxy non entrano nel renderer. Errori, sorgenti mancanti e annullamenti lasciano intatti progetto e originali.
## Protezione modifiche e chiusura editor — 2026-11-15

Prima di tornare alla Home, sostituire il progetto aperto o chiudere la finestra, l’editor controlla le modifiche non salvate e offre **SALVA / SCARTA / ANNULLA**. Un salvataggio fallito o un annullamento mantiene l’editor aperto. Scarta ricarica dal disco il progetto salvato. Alla chiusura si fermano timer, player e proxy in corso e si disconnettono gli slot Qt che non devono sopravvivere al teardown. Vedi anche [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Editor visuale — libreria, timeline ed esportazioni

La finestra editor dispone la **Libreria media** a sinistra, l’anteprima al centro e le schede **Inspector / Esportazioni** a destra; la timeline resta nella parte inferiore. **Importa media** aggiunge foto e video alla libreria del progetto senza inserirli nella timeline. Ricerca, filtro Foto/Video, miniature e selezione multipla sono disponibili; usa **Aggiungi selezionati alla timeline** per scegliere cosa montare. Le miniature video vengono estratte in background. La libreria è salvata in `project.settings.editor.media_library`, senza cambiare Timeline JSON v1.

Le etichette fisse VIDEO, VOCE/AUDIO, MUSICA, TESTI e SOTTOTITOLI non scorrono insieme ai clip. **Zoom +**, **Zoom −** e **Fit** agiscono sulla timeline e mantengono allineati righello e clip. La scheda Esportazioni mostra fase e percentuale del Job Manager, annullamento, storico, stato/errori e log. Export e renderer continuano a usare il Job Manager esistente.
