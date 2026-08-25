# Root cause investigation — bug tickets

Adapted from the `systematic-debugging` discipline, bounded to what `/gm:ticket` can do **before any code is written**: end with a well-supported hypothesis, never a fix. Consumed by `/gm:ticket` when a ticket classifies as a bug.

## Founding principle

No fix attempt without an identified root cause. Correcting a symptom without understanding the cause is a failure, even when it "works" — a bug ticket's job here is to name the cause, not silence the symptom.

## Targeted investigation (not a survey)

- Read the bug report in full: the error (and its exact message/code if any), the expected vs. observed behavior, the environment and data conditions.
- Look at recent, relevant changes: what the ticket itself mentions, plus a quick `git log`/`git diff` on the area the report points at — not a blind trawl of the whole history.
- Read the **hot spots** the brief's "À regarder" already points at, closely enough to test the hypothesis below — not the module/app/package's full surface. That deep survey stays the dev skill's job.

## Pattern check (lightweight)

If a similar, working example surfaces naturally during the targeted reading, note it as a comparison point for the dev skill (what does the working case do differently?). Don't go hunting for one if nothing stands out — this is a bonus, not a required step.

## One hypothesis, precisely stated

Formulate a single, precise hypothesis: "I think X causes Y because Z." Never a list of untested leads presented as equally likely. If the reading leaves genuine ambiguity, say so as an open question in the brief — don't guess to fill the slot.

## What's left for the dev skill (stated, not performed here)

The brief should remind whoever picks up the hypothesis to:

- Write a test that reproduces the bug — it must fail before the fix.
- Apply one change at a time, one variable per attempt.
- Verify the fix works and nothing else breaks.
- **Rule of three:** after three failed fix attempts, stop. That's no longer a hypothesis problem — it's an architecture problem. Discuss before a fourth attempt.

## Warning signs

These thoughts mean the investigation skipped straight to a fix — flag them if they show up in your own reasoning while investigating, and don't let them stand in for the hypothesis above:

- "Quick fix, I'll investigate properly after."
- "Let me change X and see if it helps."
- "I see the problem, let me just fix it."
