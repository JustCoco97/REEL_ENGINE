# Stato del progetto

Ultimo aggiornamento documentale: 2026-11-10. Questo documento fotografa verifiche effettuate entro questa data; verificare nuovamente prima di usarlo per una modifica successiva.

## Stato verificato del codice

- `scripts/reel_engine.py` è il motore CLI corrente e contiene parsing argomenti, modello dati, validazione JSON e rendering FFmpeg.
- Comandi supportati: modalità legacy `--images` con `--audio`; modalità `--timeline` con audio dichiarato nel JSON; entrambe accettano `--output`.
- Schema timeline v1: canvas (default 1080x1920, 30 fps), un asset audio e una o più scene. Le scene possono essere di tipo immagine o video e descrivono id, asset, durata e `source_in`.
- La validazione controlla schema, campi, percorsi, suffissi, ID duplicati, durata, `source_in` e dimensioni pari del canvas.
- Il renderer implementa scene immagine: scala/ritaglio, zoompan, segmenti H.264, concat e audio AAC. Scene video sono validate ma il renderer si ferma con errore esplicito; `source_in` non è ancora applicato nel rendering.
- Gli intermedi di produzione sono scritti in `temp/renders` con nomi fissi. Non c'è isolamento per render concorrenti né protezione completa contro collisioni o residui.
- Non risultano GUI, coda lavori, progress reporting strutturato, annullamento, progetto persistente, gestione proxy/cache o AI integrate.

## Test e verifiche

`tests/test_timeline.py` contiene 10 test: otto test di caricamento/validazione e due test CLI di rendering reale (legacy e `--timeline`). Le fixture immagine PNG e audio WAV per i render sono generate dai test con la libreria standard e vivono in directory temporanee; il codice motore copiato per i test è anch'esso eseguito in sandbox temporanee. Il preflight verifica FFmpeg, ffprobe, `libx264` e `aac`.

Nella Fase 9 i 10 test sono stati riportati PASS, dopo la correzione della regex encoder. Questa documentazione non esegue né ripete i test; lo stato va aggiornato dopo una nuova esecuzione autorizzata.

## Ambiente e macchina

- Windows 11 25H2, build 26200.9711, edizione Home secondo i dati raccolti.
- Python 3.14.3 nel virtualenv `C:\Users\manue\venvs\video-ai`.
- FFmpeg/ffprobe 9.0.2 presenti; la build include `libx264`, `aac`, NVENC e supporto dichiarato a CUDA/DXVA/D3D.
- Hardware rilevato: Intel Core i5-11400F, 6 core/12 thread; 16 GB RAM; NVIDIA GeForce GTX 1660 SUPER, 6 GB VRAM.
- Al rilevamento Fase 11 la RAM libera era circa 1,23 GiB; è un dato istantaneo e non un requisito permanente.
- Volumi rilevati: C: circa 510,7 GB (265,1 GB liberi), etichetta Acer; D: Seagate Basic, circa 2 TB (1,95 TB liberi). La tecnologia fisica e la velocità dei dischi non sono state determinate.
- PySide6, PyAV, OpenCV, PyTorch, DirectML e ONNX Runtime non erano presenti nel virtualenv controllato.

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
