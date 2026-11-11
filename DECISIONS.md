# Decisioni e questioni aperte

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

- La timeline corrente è schema v1 con canvas, un audio e scene sequenziali immagine/video; il rendering gestisce immagini soltanto.
- I test sono 10: otto unitari di validazione e due CLI con rendering reale. Nella Fase 9 sono stati riportati tutti PASS.
- Ambiente verificato: Python 3.14.3, FFmpeg/ffprobe 9.0.2; macchina con 16 GB RAM e GPU NVIDIA GTX 1660 SUPER da 6 GB.
- FFmpeg locale include `libx264`, `aac` e supporto dichiarato CUDA/NVENC; la distribuzione della build attuale richiede una revisione delle licenze.
- Tipo/velocità fisica dei dischi non sono stati rilevati.

## Proposte non ancora approvate

- Usare un file progetto JSON versionato come modello persistente.
- Conservare media originali come riferimenti e offrire una raccolta media esplicita.
- Usare proxy/thumbnail e workspace separati per preview e job.
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
