# AI_START_HERE — Istruzioni per collaborare

## Fonte di verità

Il codice, i test e i documenti versionati del progetto sono la fonte di verità. Non assumere che la memoria, una chat o il riepilogo di una singola AI siano completi o aggiornati. Se il codice contraddice una descrizione, verificare e segnalare la discrepanza prima di basarsi su di essa.

## Lettura obbligatoria prima di agire

1. [README.md](README.md) e [PROJECT_STATUS.md](PROJECT_STATUS.md) per scopo e stato reale.
2. [DECISIONS.md](DECISIONS.md) per distinguere decisioni approvate da proposte e questioni aperte.
3. [ARCHITECTURE.md](ARCHITECTURE.md) per i confini architetturali approvati.
4. [ROADMAP.md](ROADMAP.md) e [HANDOFF.md](HANDOFF.md) per attività e vincoli del passaggio.
5. Codice e test direttamente coinvolti nell'attività.

## Regole operative

- Verificare il working tree e i file interessati prima di ogni modifica; non presumere che siano invariati rispetto all'ultima sessione.
- Un solo operatore autorizzato può modificare i file del progetto alla volta. Gli altri possono fare analisi in sola lettura; coordinare il passaggio e registrare chi ha il controllo.
- Rispettare l'ambito esplicito dell'istruzione corrente. Non modificare file estranei, non installare pacchetti e non avviare test o rendering se non richiesti o autorizzati.
- Non cancellare, spostare o sovrascrivere asset, render, backup o altri materiali esistenti. Chiedere chiarimenti se un'operazione autorizzata rischia di toccarli.
- Non dichiarare implementata una funzione solo perché è descritta in architettura o roadmap. Aggiornare lo stato soltanto dopo aver verificato codice e prove.
- Per ogni intervento riportare file modificati, motivazione, verifiche realmente eseguite, esiti ed eventuali limiti.
- Se la richiesta impone di fermarsi a un errore, fermarsi senza tentare correzioni autonome.

## Aggiornamento documentale

Quando autorizzato, aggiornare [PROJECT_STATUS.md](PROJECT_STATUS.md) dopo verifiche significative, [DECISIONS.md](DECISIONS.md) quando viene approvata o sostituita una decisione, [ROADMAP.md](ROADMAP.md) quando cambiano priorità o criteri e [CHANGELOG.md](CHANGELOG.md) per modifiche effettivamente completate. Preparare [HANDOFF.md](HANDOFF.md) alla chiusura di una sessione che lascia lavoro in corso.

I documenti non sostituiscono i test, il codice o l'approvazione dell'utente. Registrare separatamente fatti osservati, decisioni dell'utente, raccomandazioni e questioni aperte.
