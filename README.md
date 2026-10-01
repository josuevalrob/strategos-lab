# Strategos Lab

A local tool to **see** how Jev / Laya are asked in the Strategos project (0 A.D.),
**see** what happened in a game, and **change** how the model is asked — without
reading code or logs. It reads the 0 A.D. repo and its run results; it writes only the
Q&A files you edit, and commits only those.

Needs: Python 3.12+ (stdlib only; developed on 3.14), git. Cytoscape.js (MIT) is vendored in
`static/vendor/`. Nothing to install.

## Start

```sh
cd ~/Projects/strategos-lab
python3 lab.py                                   # repo ~/Projects/0AD, port 8765
python3 lab.py --repo ~/Projects/0AD --port 8765 # the same, explicit
```

Open <http://127.0.0.1:8765/>. The server listens on 127.0.0.1 only. Stop it with Ctrl-C.
The badge at the top right shows the repo's branch and HEAD; it turns red when the repo
is not on `strategos/mvp1` (then Apply / Restore / Clone are refused). `LIVE` shows while
a game is running.

## The views

**Map** (one civ; Spartans today). Five columns:
inputs (civ file, every hero file, the game state, the opponent read) →
*why asked* (the parts of the play question: playbook order with and without a pass,
hero_next, tower_site, garrison_now, wall_now, Petra's own orders) →
the **play options** inside the `play` question box (economy, hold / strike / fallback,
fortify / garrison / standdown, hero:&lt;x&gt;, tower:&lt;site&gt;, garrison:&lt;tower&gt;, wall,
advance, train:workers, train:soldiers) → the **Petra action** (stratagem/order) →
the **Petra managers or queues** that carry it out.
Click any node: what it is, what the model reads for it (criteria text, civ/hero lines),
when it is asked, and chips `head.js:1574 hero_offer` that open the source lines.
An **Edit** button jumps to the file (for an option: its criteria row in `play.json`).
Red dashed nodes cannot work as they stand (e.g. a hero with no template for that civ).
`+ New civ` starts the clone flow (below).

**Game** (a run, after or during a game). Pick a run and a question kind (default
`play`). Each row: game minute and q#, why it was asked (the events, in words),
the options (rule's pick underlined, the option the game used filled, with p),
and what Petra did (from `engine.log`). Click a row:
- the map above lights the path: parts asked, options offered, the chosen option,
  the Petra action and managers; everything else greys out;
- the **In → out** panel: prompt, request, raw reply, and the `engine.log` lines.
  L3 runs show the **exact** logged prompt / request / reply. Older runs show the
  prompt **rebuilt** with the advisor's own code and Q&A files as of the run's commit
  (`git archive` into `.cache/snapshots/`, never the 0 A.D. tree), with a length check
  against the logged `meta.chars` (it matched for all 111 play rows of 12c-2 Jev live that logged one);
  if that commit is gone it says **rebuilt from current files**. For an L3 run the
  rebuild also lays the run's `qa_snapshot` over the commit (it equals the exact prompt).
- **Live (newest run)**: follows the newest run, polling every 2 s; new questions appear
  and (with *follow newest question*) light up the map. A new game switches over by itself.

How "Petra did" is read: the L3 line `pP q#N applied <choice> -> <order> via <managers>`
when there is one (a repeated order says `-> none`: not re-sent); else the q#-tagged
order lines (`q#N/pass_order hold-the-pass/hold -> defenseManager, …`,
`q#N play=economy: no order sent`, …). A question the model did not answer shows the
**rule's pick, marked "by rule"**. Old runs have no line for a repeated order: those rows
say **unknown** — nothing is guessed.

**Models**. Per model (adapter + version, prompt variant) × Q&A version (the header's
`qa.commit`, else `mod_sha`): for one question kind, how often each option was chosen,
across every run found. Counts only — no win rates, no scores. Example: pick
`pass_order` to see 12c-1 Jev live next to 12c-1 Laya live.

**Edit**. Every Q&A file:
- questions: `source/tools/strategos/questions/*.json` (instructions, criteria per option,
  context suffix);
- civs: `binaries/data/mods/strategos/simulation/data/strategos/civs/<civ>.json`
  (name, text lines, heroes in prompt order, playbook = `params.doctrine` /
  `params.withoutChoke` which decide the military options, the hero order, other params);
- heroes: `…/heroes/<civ>/<name>.json` (name, battle, templates, text, playbook, params);
- doctrine: `…/strategos/doctrine.json` — each stratagem's **orders** list IS the list of
  military play options (hold / strike / fallback, fortify / garrison / standdown …) for a
  civ or hero playing it, in that order. The form lists who plays each stratagem, lets you
  remove, reorder and re-add orders (only the ones head.js lets that stratagem issue),
  set needsChoke, and edit params / rule as JSON; `live`, the `civs` rows and `about` are
  edited in Raw JSON. doctrine.json is hand-formatted, so the lab rewrites only the values
  you changed (dropping `strike` is a one-line diff).

On the Map, every military option is labelled `(doctrine.json)`; its panel names the
exact place (`doctrine.json:38 stratagems.hold-the-pass.orders`) with a button that opens
that stratagem in the editor. The wording the model reads stays in `play.json`.

Form or **Raw JSON**. Edits are a *draft* (kept in this browser until applied or
discarded). Validation runs as you type and only removes the impossible: invalid JSON,
an option the game never offers, a hero with no file or no template, a stratagem that is
not in `doctrine.json` or that head.js cannot dispatch, an order that head.js does not let
that stratagem issue (`STRATAGEM_ORDERS`) or that does not exist (`ORDER_ROUTES`), a
stratagem removed while a civ or hero still plays it. It never picks. A problem the
committed file already had is a warning, not a refusal. **Diff** shows draft vs disk.

## Where edits go (Apply)

Apply writes the file(s) and runs `git commit --only -- <those paths>` in the 0 A.D.
repo with the message `Strategos lab: <your summary>`:
- refused unless the repo is on `strategos/mvp1`;
- only the Q&A paths you edited are written and committed — other dirty or staged files
  in the repo stay exactly as they were;
- if the file already had uncommitted changes, you must tick a box to commit them too;
- if the file changed on disk since you opened it, Apply is refused (reload);
- nothing is ever pushed. The file's own JSON style (tabs / 1 space / 2 spaces, and
  numbers like `1.0`) is kept, so a one-word edit is a one-line diff.

A game that is running keeps the Q&A files it started with when its log header has a
`qa` snapshot (lab L3): the note in Edit says the edit reaches the next game. For an
older advisor the note warns that its next prompt may already use the edit.
`doctrine.json` is read once by head.js at game start, so a running game keeps what it
read; the note says so when you edit it.

## History and restore

The right column of Edit is `git log` of that file: **View** (the file at that commit),
**Its change** (that commit's diff), **Diff vs now**, **Restore**. Restore writes the
file as it was at that commit and commits it as a *new* commit
(`Strategos lab: restore <file> to <sha>`) — no checkout, no branch change, nothing else
touched. Each run's header records `mod_sha` / `qa.commit`, so a game ties to the version
it played with.

## New civ ("+")

Pick a playable civ code (from the public mod's `simulation/data/civs`), a name, Preview.
The lab copies the source civ file and its hero files (templates `units/<src>/…` become
`units/<dst>/…`) and lists what cannot work there yet: hero templates the civ does not
have (and the ones it does), the hero building, the raid leader, formations, towers,
stratagem orders head.js cannot dispatch, text lines that still describe the source civ.
**Clone & commit** writes and commits those files through the same Apply path.

## Tests

```sh
python3 -m unittest discover -s tests
```

Covers: the head.js parsers (`ORDER_ROUTES`, `STRATAGEM_ORDERS`, constants) and every
hand-kept anchor (must occur exactly once in the current code — the test fails loudly
when head.js moves on); the map against REQUIREMENTS.md's option → Petra table and every
option in `play.json` and in the 12c-2 live log; the timeline of the real 12c-2 Jev live
run (114 play rows, 3 rows checked against raw log lines); the L3 contract (the real
`lab-l3-jev-live` run and a synthetic one); old-run prompt rebuild (length = `meta.chars`,
Jev, Laya and the `script` prompt variant); models grouping; editor apply / refusals /
history / diff / restore, doctrine.json (order validation, one-line diffs, map links,
not in qa_snapshot), clone, and "the next game's header shows the new commit"; live
tail with a fake game writing partial lines; an HTTP smoke test of every endpoint against
lab.py started as a process; `node --check` on every JS file.

Write tests never touch the real repo: they build a throw-away fixture git repo from
copies of the real files (`tests/fixture.py`). The real repo is only read (`git show`,
`git archive`, `git log`, run logs).

## Layout

```
lab.py              entry point
stlab/config.py     paths in the 0 A.D. repo
stlab/code.py       head.js parsers + the hand-kept parts/actions table with anchors
stlab/mapgraph.py   L1 map
stlab/runs.py       L2/L6 runs, advisor log + engine.log join, incremental tail
stlab/prompts.py    L4 exact / rebuilt prompts
stlab/editor.py     L5 validation (incl. doctrine.json), apply, history, diff, restore
stlab/clone.py      L8 clone a civ
stlab/models.py     L7 models over time
stlab/server.py     HTTP server + JSON API
static/             index.html, style.css, js/*.js, vendor/cytoscape.min.js
tests/              unittest suite + fixture builder
```

## Known limits

- The map is the play flow (12c-2 on). Raid questions (raid_target, raid_return,
  camp_move, rams_tower) show on the timeline, not on the map.
- Why each part is asked is a hand-kept table (`stlab/code.py`). The anchor test catches
  moved or rewritten lines, not a changed meaning.
- Old runs: heroes alive / fallen are not logged, so a rebuilt play prompt assumes none;
  the length check says when that was wrong. Their raw reply was never logged (the
  logged probabilities are shown instead). Repeated orders are "unknown".
- "Which lines drove the answer" (leave-one-line-out, `why_attrib.py`) is not in the lab:
  it needs model calls.
- Follow-up questions (answer → next question) are not built (PLAN: later).
- `doctrine.json` is **not** in the advisor's `qa_snapshot` (lab L3 copies `questions/`,
  `civs/`, `heroes/` only; checked on `lab-l3-jev-live` and by a test). A run's log
  records the commit (`qa.commit` / `mod_sha`), and `qa.dirty` covers only the snapshot
  files, so uncommitted doctrine.json changes at game start are not visible afterwards.
  The game reads doctrine.json once at start (head.js), so a mid-game edit does not
  reach the running game either way.
- The doctrine form covers the stratagems; `live`, the `civs` rows (civs without a
  script) and `about` are Raw JSON only.
- Known options of the non-play question files are a hand-kept list
  (`stlab/code.py KNOWN_OPTIONS`).
- "Live" = a run without `summary.json` written in the last 120 s; polling every 2 s.
  Checked with a fake game writer, not yet during a real game.
- Drafts live in the browser's localStorage. One user; Apply is serialized.

## MCP server (for agents)

`mcp_server.py` (stdlib, stdio) exposes the lab to other agents. Registered at user scope:
`claude mcp add strategos-lab --scope user -- python3 ~/Projects/strategos-lab/mcp_server.py`.
Needs the lab running (`python3 lab.py`; LAB_URL overrides http://127.0.0.1:8765).
Tools: `list_runs`, `list_questions(run, kind)`, `get_question(run, idx)`,
`ask(run, idx, prompt?, question?, models?)`, `list_files`, `get_file(path)`,
`try_files(run, idxs≤50, edits{path: text}, models?)`, `get_stats(run, minute?, series?, raw?)`. Read + ask only: nothing is written.
