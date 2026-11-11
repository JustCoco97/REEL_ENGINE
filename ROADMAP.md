# Roadmap REEL ENGINE

Le fasi sono proposte ordinate per dipendenza. Non indicano funzionalità già implementate. Criteri e test dovranno essere adattati e approvati prima delle rispettive implementazioni. Vedere anche [DECISIONS.md](DECISIONS.md) per le questioni ancora aperte.

## Fase A — Baseline e sicurezza del progetto

- Registrare versioni dell'ambiente, comandi CLI, schema v1 e hash/backup autorizzati.
- Definire esecuzione dei test da directory isolata e protezione dei media esistenti.
- **Esito verificabile:** baseline riproducibile e 10 test documentati senza modificare gli asset originali.

## Fase B — Rifattorizzazione interna senza cambio funzionale

- Separare gradualmente modello, validazione, CLI e render service.
- Mantenere comando legacy e schema timeline v1.
- **Test:** i 10 test correnti; test di compatibilità dei comandi e del JSON; nessuna differenza non intenzionale negli output.

## Fase C — Progetto persistente e GUI essenziale

- Definire formato/versionamento progetto; apri/salva; libreria media e gestione asset mancanti.
- Creare una GUI PySide6 minima per importare, ordinare scene, impostare durata e preview semplice.
- **Test:** round-trip progetto, migrazione schema, percorsi relativi/assoluti e uso senza asset del repository.

## Fase D — Job manager e rendering affidabile

- Workspace univoco, progresso strutturato, annullamento, log e gestione output.
- Probe media e report di errore; verifica dell'esportato prima della pubblicazione.
- **Test:** cancellazione, fallimento encoder, output esistente, path con spazi, cleanup limitato al workspace del job.

## Fase E — Video sorgente, preview e proxy

- Implementare video sorgente e `source_in`; generare proxy riproducibili e thumbnail.
- Benchmark leggero solo quando autorizzato, sulle risoluzioni/codec target.
- **Test:** durata, seek, frame rate, audio, sincronizzazione e ripresa da progetto su percorsi diversi.

## Fase F — Timeline multitraccia e decisione renderer

- Prototipo comparativo FFmpeg, MLT e GStreamer Editing Services per tracce, sovrapposizioni, dissolvenze, preview ed export.
- Scegliere dopo prove tecniche e valutazione licenze/packaging.
- Aggiungere tracce audio/video, titoli e sottotitoli per incrementi.
- **Test:** composizione, sincronizzazione, transizioni, mix, rendering e trasferibilità del progetto.

## Fase G — Quality control e preset

- Controlli tecnici e preset di esportazione con esito pass/warning/fail.
- **Test:** stream, dimensioni, frame rate, durata, decodificabilità, audio, clipping/silenzio/nero e sottotitoli.

## Fase H — AI opt-in

- Prima provider trascrizione/sottotitoli; successivamente immagini, voce, generazione video e upscaling.
- Preflight hardware, licenza e spazio; provenienza salvata; revisione umana.
- **Test:** AI spenta senza bloccare l'editor, consenso cloud, costi espliciti, output tracciabile e verifiche manuali.

## Fase I — Distribuzione Windows

- Pacchetto portable/installer testato su PC pulito, senza Python o FFmpeg di sviluppo.
- Licenze, versioni, runtime, codec, firma, aggiornamento e recovery documentati.
- **Esito verificabile:** aprire progetto, importare media, renderizzare e aggiornare su un secondo Windows mantenendo i dati dell'utente.
