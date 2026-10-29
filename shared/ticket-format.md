# Ticket qualification note — format

Template of the note produced by `/gm:ticket-refine` and posted on the Mantis ticket. Later consumers (`/gm:ticket`, the orchestrator) parse the first two lines: they are a hard contract.

## Rendering

Mantis Markdown rendering is not guaranteed, so the note must read well as plain text: uppercase section titles followed by `:`, `- ` or `1.` lists, no table, no HTML.

## Template (output in French)

```
[gm:ticket-refine] Note de qualification du <JJ/MM/AAAA>
Verdict : <prêt | prêt avec hypothèses | pas prêt — ce qui manque>
<si la note remplace une précédente : Remplace la note de qualification du <JJ/MM/AAAA>.>

=== POUR LE CHEF DE PROJET ===

OBJECTIF :
<une ou deux phrases, sans jargon technique>

HORS PÉRIMÈTRE :
- <ce que le ticket ne couvre pas> | (rien à signaler)

QUESTIONS OUVERTES (à transmettre au client) :
1. <question formulée pour être copiée telle quelle au client> | (aucune)

COMMENT RECETTER :
- <étapes de vérification compréhensibles par un non-développeur>

=== POUR LE DEV ===

OBSERVÉ / ATTENDU :            <section présente uniquement pour un bug>
- Observé : <comportement actuel>
- Attendu : <comportement voulu>

REPRODUCTION :
- Environnement : <local | preprod | prod> — URL : <url>
- Compte / rôle : <rôle, jamais d'identifiants>
- Données : <conditions de données nécessaires>
- Étapes :
  1. <étape>
- Fréquence : <systématique | intermittent | inconnue>
<ou, pour un bug dont les étapes manquent : Non reproductible en l'état : <ce qui manque pour reproduire>>
<ou, pour un ticket qui n'est pas un bug : Sans objet (demande / évolution) : <comment observer l'état actuel, si utile>>

CRITÈRES D'ACCEPTATION :
1. <critère vérifiable par une observation concrète> <(inféré) si déduit>

ENVIRONNEMENT DE VÉRIFICATION :
<local | preprod | prod (lecture seule)> — <pourquoi>
```

## Verdict rules

- `pas prêt`: as soon as one question is blocking, or no acceptance criterion is verifiable.
- `prêt avec hypothèses`: at least one criterion marked "(inféré)" or one unconfirmed assumption.
- `prêt`: otherwise.

## Writing rules

- Never invent reproduction steps: for a bug whose steps are missing, write "Non reproductible en l'état : …". For a ticket that is not a bug (request, configuration, feature), there is nothing to reproduce: write "Sans objet (demande / évolution) : …" instead, never "Non reproductible en l'état".
- Every acceptance criterion is checkable by observing the product (a page, a behaviour, a log, a test result). Answers or communication to the client are never criteria: they belong to QUESTIONS OUVERTES or the project-manager part.
- Never point to code (file, module, class): that is the dev skill's job.
- Never include credentials or passwords.
- The project-manager part has no technical jargon.
- Open questions are worded so they can be forwarded to the client as-is.
- Keep the note to about 60 lines at most.
