# REEL ENGINE

REEL ENGINE è un motore CLI Python per comporre video da immagini e audio. L'obiettivo è evolvere gradualmente in un'applicazione desktop Windows per la produzione audiovisiva guidata, mantenendo il controllo umano e senza rendere obbligatori provider AI o servizi a pagamento.

## Stato attuale

Il programma supporta il comando legacy `--images`/`--audio` e una timeline JSON `schema_version: 1`. La timeline può descrivere scene immagine o video, ma il renderer attuale produce soltanto scene immagine. Le scene video vengono validate ma il rendering non è implementato. Il renderer applica zoom/crop alle immagini, genera segmenti H.264 e concatena l'audio AAC.

Non è ancora presente una GUI desktop. Non sono implementati editing multitraccia, preview interattiva, progetti persistenti, coda lavori o funzionalità AI. I dettagli verificati sono in [PROJECT_STATUS.md](PROJECT_STATUS.md); l'architettura approvata e i punti aperti sono in [ARCHITECTURE.md](ARCHITECTURE.md) e [DECISIONS.md](DECISIONS.md).

## Ambiente attuale

Ambiente verificato nelle fasi precedenti:

- Windows 11 25H2.
- Python 3.14.3 nell'ambiente virtuale `C:\Users\manue\venvs\video-ai`.
- FFmpeg e ffprobe 9.0.2 disponibili nel PATH.
- I test della suite usano `unittest`; i due test CLI fanno rendering reale con fixture sintetiche temporanee.

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

`ffmpeg` e `ffprobe` devono essere risolvibili dal PATH. La modalità timeline dichiara l'audio nel JSON e non accetta `--audio` separatamente. Il renderer usa attualmente `temp\renders` all'interno del progetto per i file intermedi: non avviare render concorrenti con questa implementazione.

## Documentazione e collaborazione

La documentazione permanente e il progetto sono la fonte di verità; la memoria o la cronologia di una singola AI non lo sono. Prima di intervenire leggere [AI_START_HERE.md](AI_START_HERE.md), [PROJECT_STATUS.md](PROJECT_STATUS.md), [DECISIONS.md](DECISIONS.md) e i documenti specifici dell'attività.

È consentito un solo operatore autorizzato a modificare file del progetto alla volta. La procedura di passaggio è in [HANDOFF.md](HANDOFF.md); obiettivi e criteri sono in [ROADMAP.md](ROADMAP.md), mentre gli interventi sono registrati in [CHANGELOG.md](CHANGELOG.md).
