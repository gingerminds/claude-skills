# Ticket qualification note — format

Template of the note produced by `/gm:ticket-refine` and posted on the Mantis ticket. Later consumers (`/gm:ticket`, `/gm:merge-request`, the orchestrator) parse the first two lines: they are a hard contract.

## Rendering

Mantis renders **BBCode**, not Markdown. Use only these tags:

- `[b]…[/b]` for section titles; `[u][b]…[/b][/u]` for the two part headers;
- `[list]` / `[list=1]` with one `[*]` per item, closed by `[/list]` (nested lists allowed); no blank line inside a list block;
- `[code]…[/code]` for an error message or a log excerpt quoted from the ticket;
- `[i]`, `[s]`, `[quote]` only if genuinely needed.

No Markdown (`**`, `#`, `- ` or `1.` lists, backticks), no table, no HTML. The first two lines (marker and verdict) stay plain text with no tag: they are a parsing contract. The `[gm:ticket-refine]` marker is not a BBCode tag and is displayed as is.

## Template (output in French)

```
[gm:ticket-refine] Note de qualification du <JJ/MM/AAAA>
Verdict : <prêt | prêt avec hypothèses | pas prêt — ce qui manque, sans tiret cadratin>
<si la note remplace une précédente : Remplace la note de qualification du <JJ/MM/AAAA>.>

[u][b]POUR LE CHEF DE PROJET[/b][/u]

[b]OBJECTIF :[/b]
<une ou deux phrases, sans jargon technique>

[b]HORS PÉRIMÈTRE :[/b]
[list]
[*]<ce que le ticket ne couvre pas>
[/list]
<ou, si rien : (rien à signaler)>

[b]QUESTIONS OUVERTES (à transmettre au client) :[/b]
[list=1]
[*]<question formulée pour être copiée telle quelle au client>
[/list]
<ou, si rien : (aucune)>

[b]COMMENT RECETTER :[/b]
[list]
[*]<étape de vérification compréhensible par un non-développeur>
[/list]

[u][b]POUR LE DEV[/b][/u]

[b]OBSERVÉ / ATTENDU :[/b]            <section présente uniquement pour un bug>
[list]
[*]Observé : <comportement actuel>
[*]Attendu : <comportement voulu>
[/list]

[b]REPRODUCTION :[/b]
[list]
[*]Environnement : <local | preprod | prod> — URL : <url>
[*]Compte / rôle : <rôle, jamais d'identifiants>
[*]Données : <conditions de données nécessaires>
[*]Étapes :
[list=1]
[*]<étape>
[/list]
[*]Fréquence : <systématique | intermittent | inconnue>
[/list]
<ou, pour un bug dont les étapes manquent : Non reproductible en l'état : <ce qui manque pour reproduire>>
<ou, pour un ticket qui n'est pas un bug : Sans objet (demande / évolution) : <comment observer l'état actuel, si utile>>

[b]CRITÈRES D'ACCEPTATION :[/b]
[list=1]
[*]<critère vérifiable par une observation concrète> <(inféré) si déduit>
[/list]
<ou, si aucun : (aucun critère vérifiable en l'état)>

[b]ENVIRONNEMENT DE VÉRIFICATION :[/b]
<local | preprod | prod (lecture seule)> — <pourquoi>
```

## Verdict rules

- `pas prêt`: as soon as one open question is blocking, or no acceptance criterion is verifiable (CRITÈRES D'ACCEPTATION then contains `(aucun critère vérifiable en l'état)`).
- `prêt avec hypothèses`: at least one open question that is not blocking, at least one criterion marked "(inféré)", or one unconfirmed assumption.
- `prêt`: only if there is no open question and no inferred criterion.
- The text after `pas prêt — ` must not itself contain ` — ` (use commas): consumers may split the line from the right.

## Writing rules

- Never invent reproduction steps: for a bug whose steps are missing, write "Non reproductible en l'état : …". For a ticket that is not a bug (request, configuration, feature), there is nothing to reproduce: write "Sans objet (demande / évolution) : …" instead, never "Non reproductible en l'état".
- Every acceptance criterion is checkable by observing the product (a page, a behaviour, a log, a test result). Answers or communication to the client are never criteria: they belong to QUESTIONS OUVERTES or the project-manager part.
- Never point to code (file, module, class): that is the dev skill's job.
- Never include credentials or passwords.
- The project-manager part has no technical jargon.
- Open questions are worded so they can be forwarded to the client as-is.
- Keep the note to about 60 lines of content at most (lines holding only `[list]`, `[list=1]` or `[/list]` do not count).

## For consumers

Rules for skills that read the note (`/gm:ticket`, `/gm:merge-request`, the orchestrator). The producer guarantees them; rely on them, do not guess beyond them.

- **Finding the note.** A qualification note is identified by the prefix of its line 1: `[gm:ticket-refine] Note de qualification du `, followed by the date `JJ/MM/AAAA`. It lives in `.gm/handoff/<id>/refine.md` (line 1 of the file) and in the private Mantis notes. In the `mantis-issue.sh` digest each note is printed as `- [<reporter>] <text>`, so the prefix appears right after that `- [<reporter>] `; the note runs until the next `- [` note line or the next `## ` section.
- **No wrapping.** The note is never wrapped in a code fence and never preceded by a preamble: the marker is the very first line and the verdict the second. A text whose line 1 does not start with the prefix is not a qualification note.
- **Verdict.** Line 2 is the verdict, exactly one of these three labels:
  - `Verdict : prêt`
  - `Verdict : prêt avec hypothèses`
  - `Verdict : pas prêt — <ce qui manque>`

  `<ce qui manque>` never contains ` — `, so a line that appends more ` — ` fields after the verdict can be split from the right.
- **Malformed note.** A text whose first two lines break this contract (line 1 without the prefix and a `JJ/MM/AAAA` date, or line 2 other than one of the three labels above) is **not a usable qualification note**: ignore it entirely, fall back to the most recent valid note, else to the raw ticket, and say so in the output.
- **Several notes.** Among the valid notes only, keep the most recent one, by the date of line 1. On equal dates, prefer the local `refine.md` (it is rewritten at every run, published or not), then the Mantis note that comes last in the digest (notes are listed oldest first). An optional line 3 `Remplace la note de qualification du <JJ/MM/AAAA>.` only records history; it never changes which note to keep.
