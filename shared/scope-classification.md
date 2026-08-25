# Scope classification — feature / refactor tickets

Adapted from the `brainstorming` discipline, bounded to what `/gm:ticket` can do at brief time: classify and, when useful, sketch options — never a blocking approval gate, never a detailed design. Consumed by `/gm:ticket` when a ticket classifies as feature or refactor.

## Classify the size

- **Bounded** — the flow being changed already exists and can be read in the code today; the change is localized (a new flag, a small endpoint, a one-file fix). Bounded measures what's already in the repo, not familiarity with this kind of app — if there's no existing flow to change, it isn't bounded.
- **Architectural** — a new subsystem, a change that restructures how components fit together, or a scope wide/vague enough that it doesn't reduce to a single flow.

State which one applies and why, in one line, in the brief.

## Sketch approaches — only when non-obvious

When the ticket doesn't already point to one clear approach, sketch 2-3: one line per approach, its trade-off, and a recommendation. Skip this entirely for a ticket whose approach is obvious from the description — don't manufacture three options to pad the brief.

## Flag decomposition candidates (YAGNI)

If the ticket actually describes several independent sub-features bundled together, say so in the brief instead of sketching approaches for each piece in a pile. Decomposition is a decision for the user/dev skill to make, not something to resolve here.

## Non-goals

- No detailed design, no file-by-file plan — that's the dev skill's job (or a full `brainstorming`-style pass if the work turns out to be architectural once started).
- No blocking approval gate at this stage — the classification and sketch inform the dev skill; they don't stop the hand-off.
