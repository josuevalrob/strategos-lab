# Strategos Lab — requirements (draft 2026-09-30)

Side project. Lives outside the 0 A.D. repo; never committed there.
Reads the 0 A.D. repo (`~/Projects/0AD`, branch `strategos/mvp1`) and its run results.

## Goal
One place where Josue can **see** how Jev is set up and how it behaved in a game, and
**change** how Jev is asked and what its answers do — without reading code or logs.

## Scope
- v1: **Spartans only**, **economy + heroes**.
- Later: a **"+" / clone** action starts another civ (Romans, Gauls) from an existing one.

## What Josue wants to see and do

### 1. Map (per civ)
- Every question, its options, and what each option makes Petra do.
- What goes **in** (civ file, hero files, game state) and what comes **out** (choice +
  probabilities → Petra command).
- Edit any node: question wording, options, criteria, civ/hero text.
- Later: add / remove **follow-up questions** (answer 2 of A → question B).
- Later: connect nodes to Petra's existing workflows (managers/queues).

### 2. In → out (one decision)
- Exact prompt Jev received, its raw reply, the choice applied, the Petra command sent.

### 3. Timeline (per game, from logs)
- Each question in game order: **why it was asked** (trigger), **why that option**
  (probabilities, which lines drove it), **what Petra did** after.
- The map shows the timeline's current moment (which nodes were active).

## What exists today (facts, file:line in 0AD repo)
- **One `play` question** per decision point (Phase 12c-2), merged from pending parts
  — `head.js:2158`. Hero part = `hero_next` (`head.js:1528`), asked on events (open,
  opponent read changed, rule changed); options = heroes not yet trained + `wait`.
- Option names: `hero:<name>`, `wait`→`economy` — `head.js:1989` (`playToken`).
- Prompt built in `advisor_adapters.py:507`: `civs/spart.json` (heroOrder stripped,
  12d-2) + `heroes/spart/{agis,brasidas,leonidas,pausanias}.json` + state features.
  Script root: `binaries/data/mods/strategos/simulation/data/strategos/`.
- Wording/criteria: `source/tools/strategos/questions/play.json`.
- Sent to Jev: `advisor_jev.py:414` (UE OpenAI layer, `response_format.type=questions`).
- Answer back into game: `advisor.py:97,153` → `advisor.js:153` (desk) → no answer by
  deadline = rule's pick (`advisor.js:237`).
- Answer → Petra: `applyPlayAnswer` `head.js:2194` → `dispatch` `head.js:811` →
  `ORDER_ROUTES` (top of `head.js`) → Petra managers.

| Option | Petra action |
|---|---|
| `hero:X` | hero stratagem trains X at hero building (`hero.js:15,91`) |
| `advance` | majorTech queue, next phase (`head.js:2085`) |
| `train:workers` / `train:soldiers` | villager / citizenSoldier TrainingPlan (`head.js:2105-2123`) |
| `economy` | no order; Petra's own economy continues |
| `hold` / `strike` / `fallback` | defenseManager / attackManager (choke) |
| `fortify` / `garrison` | defenseManager + garrisonManager (tower line) |

- **No chains today**: questions are independent; answers affect later questions only
  through game state. Raid questions (`raid.js`) are a separate set.
- Logs per run: `results/<run>/advisor/*.jsonl` (options, features, rule, choice, p,
  latency), `engine.log`, `replay.json`.
- Reusable tools: `prompt_bench.py` (re-ask Jev over a logged game), `why_attrib.py`
  (leave-one-line-out attribution), `trace_viewer.py` / `why_dashboard.py` (HTML pattern).

## Known gaps
- Advisor log does **not** keep the exact prompt or Jev's raw reply (`meta.chars` only).
- Log does not say directly which Petra command an answer produced (must join engine.log).

## Non-goals
- Not committed to 0 A.D.; no upstream lint.
- No win-rate / beat-Petra gates (Strategos goal).
- No model internals for Jev (remote black box).

## Decisions (Josue, 2026-09-30)
- **Timeline live** during a game too, not only after.
- **Edit history = git**: "Apply" writes the real files + commits; jump back = check out
  an older version. Each run's log header already records `mod_sha` + question-spec
  hashes, so a game can be tied to the version it played with.
- **Model-agnostic**: Q&A (question, options, criteria, civ/hero text) is shared by every
  model plugged in. No per-model limit noise in the UI.
- **Models over time**: with 1–2 models plugged in (Jev and/or Laya), see how each one
  develops — its answers across games and Q&A versions.
- **Mid-game edits allowed, no effect on the running game.** Needs a change: today the
  Python side reads files per question (see below), so the running game must be pinned
  to the version it started with.
- **Overwrite an answer = takes effect next game** (for now). No forced re-runs.

## Mid-game editing (what the code allows today)
- Python side reads `questions/*.json` (`advisor_adapters.py:431`) and civ/hero files
  (`advisor_adapters.py:521,526`) **per question**, no cache → wording + civ/hero text
  Jev reads can change mid-game; next question uses it. Untested live.
- Game side (JS) reads civ/hero files **once at game start** (`head.js:351,365`) → options
  offered, triggers, option→Petra action are fixed for that game.
- Risk: mid-game edit → prompt text and game's own copy of civ file disagree.
- Replays still hold: answers enter the game as commands.

## Who triggers the military `play` options (code, not model, not Petra)
- `hold` / `strike` / `fallback` / `standdown`: the civ's **playbook** (from the civ file)
  lists the orders; code rule `rules.decideReactive` reads game state (enemy near the
  choke, opponent read, hero hp) → asked when something changes: enemy moves, opponent
  read, phase, playbook (`head.js:1347-1388`). The other player's moves cause the change.
- `fortify` / `garrison`: tower_site / garrison_now rules find a candidate with ≥2
  options (`head.js:1730-1748`, `1776-1794`).
- The model only picks among what code offers.

## Decision: military `play` options
- **Shown in v1, editable** like the rest (Josue expects to change them early).

## Open questions
- None blocking. See PLAN.md.

## Pipe view (2026-10-02, v2) — replaces the mvp1 Map
- One lane per block file (`blocks/*.json`): game facts → option filters (+ pending) →
  context steps (block "context" list, in order) → wording → model → answer → actor
  (send → rlgame clock → "strategos-request" → strategos AI → Petra addPlan) → acks →
  back into pending. Shared nodes drawn once; each node shows its params and, on click,
  file:line + snippet.
- Built only by reading the files (JSON, Python ast, JS regex); nothing hand-written.
- Live: server polls mtimes every 1 s; page updates over SSE without reload; a new block
  file = a new lane. A block without "context" shows its lane without steps.
- Optional overlay: newest run's last question per block (offered, dropped + reasons,
  answer, applied/started/finished).
- Plain 2D SVG, fixed columns, lab theme, readable at 1440 px. The old mvp1 Map (3D,
  mapgraph, code anchors, warnings) is removed.

