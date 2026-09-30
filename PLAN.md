# Strategos Lab — plan (draft 2026-09-30)

Source of needs: `REQUIREMENTS.md`. Checked against Strategos PLAN.md Goal (civ + hero
behaviour from an editable script the model reads) and Non-goals (no win-rate gates; code
removes impossible options only, never picks).

## Shape
- **Lab code** lives here (`~/Projects/strategos-lab`, own git repo). Never in 0 A.D.
- **Q&A files** stay where the game reads them (0AD repo, branch `strategos/mvp1`):
  - `binaries/data/mods/strategos/simulation/data/strategos/civs/<civ>.json`
  - `binaries/data/mods/strategos/simulation/data/strategos/heroes/<civ>/*.json`
  - `source/tools/strategos/questions/*.json`
- **History = git in the 0AD repo**: "Apply" commits only the edited paths (other dirty
  files untouched). "Restore version X" = write the file as it was at X + new commit
  (no branch checkout).
- **Stack (default, cheap to change)**: Python 3 stdlib server (can import
  `advisor_adapters.render_play` to rebuild prompts) + one HTML/JS page; graph drawn with
  a vendored JS graph lib (Cytoscape.js, MIT); live updates by polling.
- **Small changes inside 0AD `strategos/mvp1`** (never upstream): logging + version pin
  (L3). Everything else read-only on the 0AD side.

## Phases (each: smallest version, evidence before "done")

**L0 — Skeleton.** Repo init, `lab.py` serves the page, config points at `~/Projects/0AD`.
Accept: page lists the Spartan civ file, 4 hero files, question files.

**L1 — Map (read-only).** Spartan graph: inputs (civ, heroes, state features) → parts
(hero_next, playbook/pass_order, tower_site, garrison_now, wall_now) → `play` options
(economy + heroes + military) → Petra action (from `ORDER_ROUTES`, `head.js:51`, and the
direct actions `advance` / `train:*`, `head.js:2085-2123`) → trigger per part.
Accept: every option in `play.json` and in the 12c-2 live log appears with its Petra
action; matches the table in REQUIREMENTS.md.

**L2 — Timeline, after the game (read-only).** Load a run dir (advisor `*.jsonl` +
`engine.log`). Per question: game minute, trigger (`features.events`), options, rule's
pick, model's choice + p, applied order (joined on `q#N` lines in engine.log). Click →
map highlights the path taken, others grey.
Accept: `results/phase12c2-jev-live` shows 114 `play` questions; 3 spot-checked vs raw log.

**L3 — Logging + pin (0AD `strategos/mvp1`).**
- Advisor log: exact prompt + raw reply per question (today only `meta.chars`).
- Engine.log: one line per applied answer, `q#N → order → managers`, if the join in L2
  proves not enough.
- Pin: advisor snapshots the Q&A files at game start into the run dir and reads only the
  snapshot → mid-game edits don't touch the running game; header records commit + hashes.
Accept: unit tests; one short game **only on Josue's word** → log has prompt + reply; a
civ-file edit mid-game leaves the prompt hash unchanged.

**L4 — In → out view.** Click a question: exact prompt, raw reply, choice, Petra command.
Old runs (pre-L3): prompt rebuilt via `render_play`, marked "rebuilt".
Accept: one L3 run + one old run shown.

**L5 — Editor + git.** Edit question wording / criteria / options, civ file (playbook,
military orders), hero files. Draft → Apply = write + commit those paths. History list,
diff, restore. Validation removes impossible options only: JSON valid, option = a known
order / Petra action, hero exists for the civ.
Accept: edit → Apply → commit touches only that file; restore brings it back; next game's
header shows the new commit.

**L6 — Timeline live.** Tail the running game's advisor log + engine.log; new questions
appear on the timeline and map.
Accept: during a game (**Josue's word**), a new question shows in ≤5 s.

**L7 — Models over time.** Per model (Jev / Laya, version) × Q&A version: answers per
question/option across runs. No win rates.
Accept: 12c-1 Jev-live vs Laya-live runs side by side.

**L8 — "+" / clone civ.** Copy Spartan files to a new civ (Romans, Gauls); flag what
can't work there (hero templates missing, orders the civ can't do).
Accept: clone to Romans lists its issues; files committed.

**Later (not planned yet):** follow-up questions (answer → next question). Needs game-side
(JS) support; design when Josue asks.

## Stop rules
- A phase needs C++ or upstream changes → stop, report.
- A 0AD change would alter what the model is offered or picked → stop, report (Non-goals).
- Games / model runs only on Josue's word in the current turn.
