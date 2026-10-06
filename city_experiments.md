# City planner: Jev builds a hollow square (2026-10-06)

Target: perimeter of x,y in 2..7 (20 cells), one street between it and the civic centre (CC, cells 4..5).
Score = houses on target out of 20. Candidate rule for every variant: `reach2` = free cells within
2 cells (any direction) of the CC or a house. Goal text (Tier A, unchanged from v1):
"Build a hollow square of houses around the civic centre: 6 houses along each side, corners shared,
20 houses in all, each house touching its neighbours along the side. Leave one empty street, 1 cell
wide, between the civic centre and the houses."

Rule (Josue): prompts only. No fine-tuning or training, no model changes, and Jev is asked only through
/api/ask. Every variant below is a prompt change: goal text, state lines, option texts, instructions,
the map shown, or the option format. Code only builds those texts and the candidate list.

Probe set (`--probeset`): empty map, row of 5, north side done (6), two sides (11), three sides (16), 3 in the middle.
P(on) = Jev's total probability on on-target options.

| # | variant | what changed | score | first wrong step: Jev's top-3 | next hypothesis |
|---|---|---|---|---|---|
| 1 | coords (v1) | bare coordinates, state = coords list | 1/20 (161633-coords-base) | q2: (2,1) .22, (3,2) .13, (1,1) .10 | numbers ignored, map edge is the salient line |
| 2 | facts | options state CC gap ("one street (1 empty cell) between it and the CC"), side, row/line extended + new length, corner, touches, map-edge distance; state = coords list | 6/20 (162632-facts) | q7 (north side done): (8,2) "extends the row of six to seven" .40, (2,3) .14, (1,2) .12 | "extends the row" beats the gap; row length in numbers ignored |
| 3 | facts_nolines | drop row/line phrases | probes: side1 P(on) .13, (8,2) .68 | (8,2) still wins with "touches one house" | Jev continues the coords list of the state ("in order: (2,2)…(7,2)" → (8,2)) |
| 4 | nl_count | state = house count only, no coords list | **20/20** (162642), P(on) min .32 | — | off-target mass on (0,0) "on the map edge" |
| 5 | nl_nocoords | 4 + no coordinates in option texts | **20/20** (162651), P(on) min .34 | — | same as 4: coordinates in options are harmless once the state list is gone |
| 6 | **nl_noedge** | 4 − map-edge fact (options = side + gap + touches) | **20/20 ×3** (162729, 162739, 162748), P(on) min .37–.39, mean .54 | — | PRIMARY done |
| 7 | nl_nostreet | 6 with gap said as "one empty cell" (no word "street") | 11/20 (162844), 12/20 (162854) | q2/q4: (0,0) and (3,4) "touches the CC" | the goal's own noun ("street") in the option text is what Jev matches; the number alone is not |

## Generalization (variant nl_noedge, no code change: goal text + scoring target only)

| goal | target | scores | where it breaks |
|---|---|---|---|
| square4: 4 per side, **touching** the CC (one street from the CC is geometrically impossible for a 4-side square on this grid) | ring 3..6, 12 | 10/12 (162930), 12/12 (162936) | q6: "one street" (3,2) .14 vs "touches the CC" .13: the word "street" still pulls |
| square8: 8 per side, gap of two empty cells | ring 1..8, 28 | 14/28 (162942), 14/28 (162954) | q1: no on-target candidate (reach2 cannot reach 2 cells out); then "one street" cells .45 |
| triangle: right angle NW of the CC, legs along its north and west (one street), hypotenuse through the CC | 13 | 10/13 (163008), 9/13 (163014) | q8: (7,3) "one street, touches one house" .12 — nothing in the option says which leg/line a cell continues |
| triangle_se: right angle in the SE map corner, legs on the map edges | 15 | 5/15 (163020), 5/15 (163028) | q1: (7,7) "one street" .16: no edge fact, so the shape cannot be named in the option words |

Verdict: the winning design matches one generic property whose wording equals the goal's noun ("street").
It composes for any shape that is "all cells with property P" (square4: 12/12 once), not for shapes that
need a line/direction/edge fact (triangles 5–10/13–15). Next family to try for composability: line facts
that say their consequence in the goal's nouns (e.g. "continues the north side" style words computed
from the row's own position), plus a locality rule that can reach 2+ cells out.

## Round 2: one design for the square and both triangles (2026-10-06)

Task: same builder for every shape; only the goal text and the scoring target change. Targets: NW triangle
(legs y=2 and x=2 from 2..7, plus (6,3),(3,6): 13 houses), SE triangle (legs y=9 and x=9 from 4..9, plus diagonal
(5,8)..(8,5): 15 houses), square6 (20). Probe set per goal = `--probeall`; micro tests = the real prompt with
some option texts patched (scratch scripts, same /api/ask). Every variant is a prompt change only.

| # | variant | prompt change | result (runs / probes) | first wrong step |
|---|---|---|---|---|
| 8 | t1 | options: 8-way position, street gap, map edge only when on it, moves (straight/turn/branch/diagonal + direction); every free cell | probes: square empty → (0,0) "NW corner of the map"; NW 3/6, SE 4/7 | "corner of the map" and "extends the row" pull |
| 9 | t2 / t3 | candidates: touching a house or within 2 / 3 cells of the CC | probes NW 3/6, SE 3/7 | SE cannot reach the map corner at q1 |
| 10 | t3n | options sent only as criteria, NOT listed in the prompt body | square probe P(on) .72/.48/.46 → .90/.72/.74 | — |
| 11 | t4 | state + "Lines so far: an east-west row along the bottom edge of the map, six houses long, its west end …" (no coordinates); option anchors name those lines | NW probes 1/6 → 5/6 | — |
| 12 | t5 / t5r | corner-touch names the corner; gap ≥2 says "more than one street" | NW 5/6, SE 4/7 | — |
| 13 | t6 | anchors name lines without their gap; single houses with their map corner; NW goal names the two CC corners | square 20/20, 17/20 (164534, 164544); NW 12/13 ×2 (164554, 164600) | square q6: (1,2) "extends the row westwards, making it six houses long" .32 vs (7,2) .26 |
| 14 | t7 | candidates: only cells touching a house | square 16/20 ×2 (164724, 164733) | fewer options → the attractor wins more often |
| 15 | t8 | drop "making it N houses long" from options | square 19/20, 18/20; NW 11/13, 10/13 | square q7: (6,1) "turns north …, making a corner" |
| 16 | t9 | only the strongest line relation per option; goals add "Build both legs first; the long side comes last." | square 18/20, 19/20; NW 10/13 ×2; SE 10/15 ×2 | NW: north leg end (6,2) already "north-east" → leg looks done |
| 17 | t10 | position by CC face: "north of the CC, slightly east" / "diagonally north-east of the CC, off its north-east corner"; no "making a corner"; a single house "starts a row"; goals rewritten in these words | square 20/20 ×2 (165338, 165347); NW 13/13 ×2 (165358, 165405); SE 10/15 ×2 | SE: (5,9) and (4,9) both "directly south" → long side starts one house early |
| 18 | t10nc | no coordinates in option texts | square 20/20 ×2; NW 13/13 ×2; SE 4/15, 10/15 (165738, 165746) | SE q2: (8,8) "steps diagonally … from the single house" .36 |
| 19 | t11 | position: no word "diagonally"; straight-on cells name the CC half ("directly south of the CC, below its west half"); SE goal names the half | square 20/20 ×2; NW 13/13 ×2; SE 13/15, 15/15 (165852, 165900) | SE q14: spurious 2-house line in the state |
| 20 | **t12** | a 2-house line whose both houses already sit in longer lines is not reported | **square 20/20 ×3 (165959, 170009, 170020); NW 13/13 ×3 (170031, 170037, 170044); SE 15/15 ×3 (165936, 165944, 165951)**; 3 extra SE runs: 15, 15, 12 (170055, 170103, 170111) | SE 170111 q9: (8,8) diagonal step from a leg end before the legs are done, .20 vs .17 |

Micro tests (real t9/t10 prompt, patched option or goal text; 2 asks each):
- Line length echoing the goal number: "making it six houses long" removed from all options → wrong (1,2) .33 → .08.
- Build order in the goal: "Build both legs first; the long side comes last." → early diagonal (6,8) .31 → .08.
- Compass word inside a shared phrase: goal "starts at the west end of the bottom leg and steps diagonally north-east" →
  (3,8) "steps diagonally north-west from the west end …" .55, the right (5,8) .10.
- Ablation of (8,8) at 4 bottom houses: dropping its anchor phrase "steps diagonally north-west from the east end of
  the row along the bottom edge" → .18 → .03; dropping its position or gap: no change.
- "6 houses each" in the SE goal: early diagonal .16 → .09, but at legs-complete the wrong (3,8) .19 → .37.

### Lessons (rules for writing Jev questions)
1. Tell Jev what is built as named lines in words (where it lies, where each end is), never as a coordinate list.
2. Send options only as criteria; listing them again in the prompt lowers the right answer's probability.
3. Never echo the goal's number in an option ("making it six houses long"): it outweighs every other fact.
4. Say the build order in the goal; Jev does not infer it from the shape.
5. Jev barely separates compass words inside an otherwise identical phrase; make the right and wrong options
   differ in other words (CC face, "below its west half", "off its north-east corner").
6. Write the goal in the exact words of the facts; remove fact words that echo an unrelated goal word
   ("corner of the map" vs "corners shared", "diagonally" in positions vs "diagonal line").
7. Keep the state free of side-effect facts (2-house "lines" made by neighbours); every extra line is a new attractor.
8. Do not shrink the option list to help Jev: with fewer options the one attractor wins more often (t7).

## Round 3: how the state is passed (Josue's hypothesis, 2026-10-06)

Same options as t12 (positions by CC face, street gap, map edge, strongest line relation; options only as
criteria; every free cell). Only the state block changes. 3 full runs per goal.

| variant | state | square6 /20 | NW triangle /13 | SE triangle /15 |
|---|---|---|---|---|
| s_count | "Houses built so far: six." only | 20, 20, 20 (170424, 170434, 170444) | 12, 11, 12 (170455, 170502, 170509) | 6, 6, 6 (170515, 170523, 170530) |
| s_event | count + "Last house: … it extends the east-west row … eastwards. It is now the east end of … Event: turned a corner …" | 6, 6, 14 (170658, 170709, 170718) | 11, 10, 10 (170728, 170734, 170741) | 6, 6, 6 (170747, 170754, 170801) |
| t12 | count + "Lines so far: …" (each line: where it lies, length, where its ends are) | 20 ×3 (165959, 170009, 170020) | 13 ×3 (170031, 170037, 170044) | 15 ×3 (165936, 165944, 165951); +15, 15, 12 |
| t13 | t12 + the s_event line | 20, 20, 17 (170538, 170548, 170558) | 12, 11, 12 (170607, 170614, 170620) | 7, 8, 9 (170626, 170634, 170651) |
| t14 | t12 + "Last house: where it is; which line end it now is" (no action verb) | 6, 7, 7 (170817, 170827, 170837) | 10, 12, 12 (170847, 170853, 170859) | 11, 11, 14 (170906, 170914, 170921) |
| t15 | t14 + the corner event | 8, 7, 13 (170928, 170938, 170953) | 12, 13, 12 (171003, 171011, 171017) | 12, 12, 11 (171023, 171031, 171038) |
| t16 | t12, but a north-south line is named by its west/east side of the CC | 20 ×3 (171108, 171118, 171127) | 13 ×3 (173618, 173626, 175136) | 15, 15 (175143, 175151); 3rd run cut by the lab outage (182236: 5/5 before it) |
| t17 | t16 + "Last house:" where + corner event | not run (lab down) | | |

Hard-step probes (2 asks each, P(on target)): count-only vs lines: SE first diagonal .15 vs .31, NW last diagonal
.40 vs .54, square three sides done .57 vs .78. Square corner turn: count-only .75 (2/2 right) vs count + last-house
event .41 (0/2).

Why the event line hurts: Jev repeats the last move. s_event square: after the 6th house on the north row Jev
turned north (7,1) .42 vs south (7,3) .36, then kept going; t13: NW (8,1) "extends the diagonal line north-eastwards"
.37 right after the diagonal house; square (2,8) "extends southwards" right after a southward house.
t14 square failure: the 2-house stub (7,2)-(7,3) was named "north-south line north of the civic centre"; repeating
that name in "Last house: … it is now the south end of the north-south line north of the civic centre" made Jev
extend it northwards (.41). t16 names that stub "east of the civic centre" (no loss: square 20 ×3).

Lab/Jev outage: 17:13 DNS errors from the Jev endpoint ("nodename nor servname provided") for ~20 min; from 18:25
asks time out at the lab (60 s ×2); at 19:39 a 2-option test ask still got no answer; the Mac clock jumped
18:47 → 19:37 between two commands (looks like sleep). Error run files: 171138, 171341–171342 (t16/t17), 180702 (stopped by me),
182236, 184540, 184740 (lab still timing out at 21:06). Stopped under stop rule (c).

### Lessons from round 3
9. State = what exists now, as lines in words: count-only → lines: NW 11–12/13 → 13/13, SE 6/15 → 15/15
   (s_count 170455–170530 → t12 170031–170044, 165936–165951); the square does not need it (20/20 both).
10. Do not tell Jev what the last answer did ("Last house: … it extends the row eastwards"): it repeats the move.
    Square 20/20 → 6–14/20 (s_event), SE 15/15 → 7–9/15 (t13).
11. Every name in the state is a pull: a wrong-sided line name ("north-south line north of the civic centre")
    repeated in a "Last house" line cut the square to 6–7/20 (t14). Name lines by the side they actually run along.

## Round 4: house questions on a real 0 A.D. map (built 2026-10-06, Jev down, dry runs only)

`city_real.py` (new; city_demo.py untouched except the 60 s ask timeout). Source, read only:
~/Projects/0AD/.claude/strategos/runs/solo/20261006-213701/engine.log, `[strategos] snapshot` of minute 10, its
houses removed (24 structures → 17: CC, 3 storehouses, farmstead, 8 fields, 2 barracks, 2 stables). 12 questions.
- Slots: 14 m lattice (house width) in the civic centre's own frame (angle 2.36), so neighbours stand shoulder to
  shoulder; impossible slots removed (outside territory: 9 footprint points checked; overlapping a structure, a
  tree (1.5 m) or a mine (6 m), or a house). 118 free slots. Locality rule: within 10 m of a building or a house
  → 42 options at question 1 (all three civs).
- Words: CC faces by compass + role (front = its facing, south-east here; flanks north-east / south-west; back
  north-west); corners by compass; gap to CC with "one street" for 1–15 m; "shoulder to shoulder with N houses" /
  "touches a house only at a corner" / "stands apart, N m from the nearest house"; lines of houses in words (their
  direction, the CC side their middle looks at, gap, length, which end a new house extends or turns at). No
  coordinates, no last move; options only as criteria.
- Goal-triggered facts (the builder reads only the goal's words): "field" → nearest field + whether that field
  already has a house beside it; "wood" → distance to the nearest tree + a "Woods, nearest first" state line;
  "storehouse" → storehouse distance + state line; "open ground"/"apart" → open ground around it or not;
  "border"/"edge of"/"empty land" → distance to the territory edge; "enemy" → side toward / away from the enemy.
  spart uses core facts only; iber: storehouse, enemy; germ: field, wood, open, edge, enemy.
- Missing in the snapshot: the enemy (solo run: enemy_cc is empty → state says "Enemy: not on this map; his
  direction is not known."), terrain height (iber "highest ground"), water (germ "spring"), building facing
  (iber "rear walls facing out": every house gets the CC's angle).
- Per-run facts (no target): houses touching another, lines ≥3, gap to CC min/mean, next to a field (≤6 m), next
  to woods (≤10 m), houses per CC side.
- Page: city.html real-map mode (territory, trees/mines, structures as rotated rectangles, fields tan, the slots
  offered in that question dashed, the picked one highlighted, houses numbered, slider, prompt per step).
- Dry runs (first option = nearest the CC, no Jev): 20261006-215200-real-{spart,iber,germ}-dry.
- Jev check at 21:54: a 2-option ask got no answer in 40 s → not run. To run:
  `cd ~/Projects/strategos-lab && for c in spart iber germ; do python3 city_real.py --civ $c --runs 3; done`

### Round 4 results (Jev back, 2026-10-06 22:00), minute-10 start, 12 houses, 42 options at q1
Map fact first: on this start the ring one street from the civic centre has only 5 free slots (right flank /
west corner / back); Petra's 8 fields fill the rest of it, the front included.
- spart, civ line verbatim (220003, 220009, 220015, identical): all 12 houses on the FRONT (south-east) side.
  House 1 one street from the front, then a 6-house line straight away from the civic centre (out to 48 m)
  crossed by a 4-house line: one solid block, 12/12 touching, next to a field 2, next to woods 5.
  Followed "shoulder to shoulder". Ignored "square rings around the civic centre". Went against "gates only
  to the front": the block stands in front of the civic centre. First step against: q2. Top 3: .79 "front;
  20 m, more than one street; shoulder to shoulder with one house; starts a line toward the south-east …" |
  .07 / .06 "right flank; one street (6 m); stands apart …". Next to house 1 the only touching slot pointed
  outward: fields block the ring.
- iber (220022, 220028, 220034): wraps the civic centre's WEST CORNER (right flank 7–8, west corner 1–2,
  back 3), i.e. the 5 free ring slots, then a 2nd and 3rd layer outward (20 m, 34 m); 12/12 touching. This
  is the nearest thing to the line's ring, but it is not closed. Storehouses are 59–93 m away, so not
  inside. "Highest ground" and "gate toward the enemy" have no data. First step against: 220028 q2. Top 3:
  .60 "back; 20 m; shoulder to shoulder; starts a line toward the north-west" (outward) | .21 "west corner;
  one street; starts a line toward the south-west" (around) | .06 "front; one street; stands apart".
- germ (220041, 220048, 220056): two clusters of touching houses (front block of 5–8, back/west group of
  3–5); 12/12 touching, next to a field 4, next to woods 3–4. Followed "next to its own field" when starting
  a cluster: q1 and q6 took "next to a field (0 m), a field with no house beside it yet". Ignored "each
  house stands apart with open ground". First step against: 220041 q2. Top 3: .27 "front; 20 m; shoulder to
  shoulder; starts a line" | .18 "front; 34 m; stands apart, one street (14 m) from the nearest house" | .14
  "back; one street; stands apart, 42 m; next to a field with no house beside it yet".
- real2 (lines and moves also say "around the civic centre" / "away from the civic centre"): the same towns
  (220453, 220459, 220506). Jev took "… shoulder to shoulder …; starts a line toward the south-east away
  from the civic centre" at .79 even with "around the civic centre" in the goal.
- Proposed lines (via --goal-text; civ JSON untouched; texts in the report):
  spart "…houses shoulder to shoulder in lines around the civic centre, the first ring one street from it,
  the next ring one street further out; leave the middle of the front and of the two flanks open as gates":
  same front block (220513, 220519).
  iber "…houses shoulder to shoulder in one closed line around the civic centre, one street from it, then a
  second closed line one street further out; the civic centre and the storehouses stand inside…": worse, it
  built the same front block as spart (220525, 220531).
  germ "…each house stands apart, more than one street from the nearest house, with open ground around it,
  next to a field with no house beside it yet or at the edge of the woods…": touching 12 → 8 of 12
  (220538, 220545), 11 with real1 (220552).

Lesson 12: an option fact the goal names ("shoulder to shoulder") outweighs an explicit direction fact
("away from the civic centre") and the goal's "around": .79 at q2 in every spart/iber run.

## Round 5: real timeline replay (2026-10-06 22:49)

`python3 city_real.py --civ <c> --timeline [--goal-text …]`. Petra's houses are matched by position, because a
finished building gets a new entity id. That gives 13 houses (12 finished + 1 foundation at minute 14), first
seen at minutes 1, 5, 6, 7, 8, 9, 10, 11, 12, 13, 13, 14, 14. Question k is asked on that minute's snapshot: all
non-house structures (foundations too), the resources and the territory, plus Jev's houses so far. Petra's
houses are left out. A Petra structure that overlaps a Jev house is dropped from then on, and the run file
lists it under "dropped". Same builder as round 4 (real1 wording, options within 10 m of a building or a house;
15 options at minute 1, about 45 later).

- spart, civ line verbatim (224921, 224929, 224935, identical): q1–q4 build a row of 4 along the FRONT one
  street out (around the civic centre). From q5 on, lines run straight away from the front, out to 48 m.
  Result: a front block of 13 houses (12 front + 1 east corner), 13/13 touching. Dropped: 4 Petra fields
  (minutes 6, 9, 12, 13). First step against the line: q5, minute 8; no ring continuation was offered, because
  fields and buildings from minutes 5–8 block both ends of the row. Top 3: .35 "front; 20 m, more than one
  street; shoulder to shoulder with one house; turns south-east at the south-west end of the line …" | .19 the
  same at the north-east end | .13 "left flank; 20 m; extends toward the north-east …".
- iber (224942, 224949): q1–q4 build a row of 4 along the RIGHT FLANK (south-west) one street out, reaching the
  west corner. Then layers go outward on the right flank (out to 76 m), with 3 houses on the back. 224955
  started 76 m out, next to a storehouse (10 m), and grew a line inward to the civic centre. 13/13 touching,
  nothing dropped. First step against the line: 224942 q5, minute 8. Top 3: .30 "back; one street; shoulder to
  shoulder; turns north-east at the north-west end …" (ring) | .30* "right flank; 20 m; shoulder to shoulder;
  turns south-west …" (outward, chosen) | .21 "back; 20 m; extends toward the north-west …".
- germ, civ line verbatim (225003, 225011, 225018): q1 front one street, q2 stands apart (14 m). From q3 on,
  shoulder-to-shoulder lines: a front cluster and a back/west cluster. 11–13 of 13 touching, next to a field
  5–6. Dropped: 3 Petra fields (minutes 6, 9, 12). First step against the line: q3, minute 6. Top 3: .19*
  "front; one street; shoulder to shoulder with two houses; starts a line …" | .15 "right flank; one street;
  stands apart, one street (14 m) from the nearest house" | .14 "left flank; stands apart, 28 m; next to a
  field (0 m), a field that already has a house beside it".
- germ, proposed line (225026, 225033, 225040): the first 4–6 houses stand apart; several are "next to a field
  …, a field with no house beside it yet". The first touching house comes at q5–q7. 8–10 of 13 touching, next to
  a field 4–5. Dropped: 2 Petra fields each run. First step against the line: 225033 q6, minute 9. Top 3: .15*
  "right flank; one street; shoulder to shoulder with one house; starts a line …" | .11 "west corner; one street;
  shoulder to shoulder …" | .06 "touches a house only at a corner".
- Versus the minute-10 start: Jev's first houses now come before Petra's fields. Sparta and Iberia first build a
  4-house row along one side of the civic centre, one street out (around the civic centre), before they turn
  outward. Petra's later fields collide with Jev's houses: Sparta 4, Germania 3 (proposed line 2), Iberia 0.

Pages:
- spart http://localhost:8765/static/city.html?run=20261006-224921-timeline-spart
- iber  http://localhost:8765/static/city.html?run=20261006-224942-timeline-iber
- germ  http://localhost:8765/static/city.html?run=20261006-225003-timeline-germ
- germ, proposed line http://localhost:8765/static/city.html?run=20261006-225033-timeline-germ-prop

## Round 6: Petra's buildings count as pieces of lines and rings (2026-10-06 23:39)

`--timeline --variant pieces`: same timeline as round 5. Line, touch and band facts are now built from Jev's
houses AND every structure Petra placed (fields, barracks, storehouses, farmstead, stables; not the civic centre).
- Touch: "shoulder to shoulder with a house and a field" (gap ≤ 3 m, facing across at least 60 % of a side).
- Lines: "line of two houses and a field running …".
- Moves: "extends … / turns … at the … end of … / starts a line … from a field / closes the gap between a field
  and a house".
- Band: "joins touching buildings that then line the front, the back and the right flank of the civic centre
  (before: …)"; only pieces within 30 m of the civic centre count for its sides.
- State: "Lines of buildings so far (houses, fields and Petra's other buildings): …" and "Touching buildings
  along the civic centre: …, lining …".
Petra's placement is unchanged.

- spart, civ line verbatim (233915, 233924, 233931): q1–q8/q9 all one street out, going AROUND the civic centre:
  front row → east corner → right flank → west corner → back. The last one, q8/q9, "closes the gap between a
  field and a house". That makes 8–9 one-street houses (round 5: 4). Houses plus Petra's fields then form one
  touching band lining the front, the right flank and the back (all four sides in 233924, through the fields on
  the left flank). Only then does it go outward, to a second layer at 20–34 m. 13/13 touching. Dropped: 1–2 fields
  (round 5: 4). First step against the line: q9 (233915, 233931) or q10 (233924), when 0 one-street slots were
  left. Top 3 (233915 q9): .33 "right flank; 20 m; shoulder to shoulder with a house; starts a line toward the
  south-west …" | .23 "back; 20 m; …starts a line toward the north-west" | .21 "right flank; 20 m; extends …
  the line of two houses and a field …".
- iber (233937, 233944, 233951): q1–q4 a row along the right flank one street out, to the west corner; 6
  one-street houses in all. Then layers outward on the right flank and the front. Band of 13 houses + 3 fields
  lining the front, back and right flank. Dropped: 0–1 field. First step against the line: q5, minute 8, a tie
  while 3 one-street slots were still free. Top 3 (233937): .26* "right flank; 20 m; turns south-west at the
  north-west end of the line of four houses" | .25 "back; one street; shoulder to shoulder with a house and a
  field; closes the gap between a field and a house …" | .22 "back; 20 m; extends toward the north-west …".
  233944 picked the outward one at .25 against that same .25 option.
- germ, proposed line (233958, 234005, 234013): q1–q4 stand apart (q2 "next to a field …, a field with no house
  beside it yet"). q5 "shoulder to shoulder with a field". From q6/q7, lines of touching houses. 9–11 of 13
  touch another house (round 5: 8–10). Dropped: 2–3 fields. First step against the line: q6, minute 9. Top 3
  (233958): .20* "front; 20 m; shoulder to shoulder with a house; turns south-east at the south-west end of the
  line of a house and a field …" | .11 "back; one street; shoulder to shoulder with a field; starts a line …" |
  .09 "back; 20 m; stands apart, one street (14 m) from the nearest house …".
- Versus round 5: counting fields as ring pieces gives Sparta a ring one street out around three sides (houses)
  plus fields, instead of a 4-house row and then an outward block; Iberia changes little; Germania touches a
  little more.

Pages:
- spart http://localhost:8765/static/city.html?run=20261006-233924-timeline2-spart
- iber  http://localhost:8765/static/city.html?run=20261006-233937-timeline2-iber
- germ, proposed line http://localhost:8765/static/city.html?run=20261006-233958-timeline2-germ-prop

## Round 7 (overnight 2026-10-06/07): one design, each civ's town matches its line (judged by facts)

Judge: `city_judge.py` (evaluation only; the builder never reads it). The checks per civ are data in
`city_data/checks.json`; the code computes only generic facts:
- Ring closed: walking out from the civic centre through free ground, with every ring piece (Jev's houses + Petra's
  structures, each grown by 1.5 m) as a wall. Each widest way out has a narrowest point, which is a gate; it is
  walled off and the search repeats.
- Openings under 6 m are slits, not gates: leftovers between the 14 m lattice and Petra's free-placed buildings.
- Gate sides are measured in the CC frame.
- Houses touching another house (side or corner).
- Houses next to a field (<= 6 m) or the woods (<= 10 m), on the map of the minute the house was built.
  Trees get cut later.
Petra alone, at minute 14, leaves one 56 m opening on the right flank (south-west), plus slits. The fields close
every other side.

Josue's rule (2026-10-06 night): no per-civ code. Word->fact tables and settings now live in data files:
- `city_data/fact_words.json`: which goal words switch on which optional facts.
- `city_data/civ_layout.json`: where the layout line lives. The proposed civ JSON key is "layout"; text[5] is
  the fallback until then.
- `city_data/checks.json`: the judge's checks per civ.

Variants (full 3-run configurations; timeline + pieces; probes via `city_probe.py`):
| # | change | germ (proposed line) | note |
|---|---|---|---|
| 21 | G2 line ("Every house stands apart, more than one street from the nearest house; no house is shoulder to shoulder…") + apart_words | touching 2/4/2 of 13, field/wood 5–7 | apart works, field/woods ignored |
| 22 | + neither_words ("no field and no woods next to it (…)") | touching 6–8, field/wood 9–10 | Jev picks woods, then houses touch |
| 23 | + apart_both ("does not stand apart: shoulder to shoulder with …") | touching 2–5, field/wood 7–8 | trade-off |
| 24 | + apart_plain, no_far, compose (one phrase: "stands apart, 28 m from the nearest house, at the edge of the woods (5 m …)"), gate_lines (line/band facts only if the goal says ring/wall/line/row/shoulder to shoulder/back to back), gate_cc (CC gap only if the goal says street/civic centre), near_first (goal-noun facts lead the option); G7 line | touching 0 ×3, field/wood 8–9 | |
| 25 | + far_bare ("on bare ground" instead of naming fields/woods) | touching 0 ×3, field/wood 7 | worse; dropped |
| 26 | **variant 24 with G8 line (near first, two sentences)** | **MATCH 3/3** (235751, 235758, 235806: touching 0, field/wood 10, 12, 10 of 13); repeat **3/3** (000042, 000048, 000056: 12, 12, 12) | |
| 26 | same flags, spart verbatim | **MATCH 3/3** (235820, 235827, 235834); repeat **3/3** (235958, 000005, 000012) | |
| 26 | same flags, iber verbatim | 2/3 (235841 open: the first house went next to a storehouse 76 m out, "next to a storehouse" leads) | |
| 27 | same flags, iber proposed I1 (storehouse sentence dropped) | **MATCH 3/3** (235924, 235932, 235940); repeat **3/3** (000020, 000027, 000034) | |

Final design = variant 26/27 flags: apart_both, apart_plain, neither_words, no_far, compose, gate_lines, gate_cc,
near_first (plus pieces, timeline). The same code and flags for all three civs; only the goal text differs.

Towns (final runs):
- spart: 8 houses one street (6 m) out, going around: front 3, right flank 3, west corner 1, back 1, then an outer
  layer at 20–34 m. With Petra's fields this closes the ring (no gate wider than 6 m). Dropped: 1 field (minute 6).
- iber (I1): the same shape as Sparta in the final runs.
- germ (G8): 13 houses, none touching another. 10–12 of 13 next to a field (q2–q4) or at the edge of the woods
  (later houses, 34–90 m out). Dropped: up to 4 fields and 1 storehouse (minutes 8–14) that Petra later put where
  Jev's houses stood.
- Caveat: fields do not block units in the game (template_structure_resource_field.xml: BlockMovement false). The
  rings are closed in the facts Jev reads; for walking units, closed_without_fields is False in every run.

Proposed civ-line wording (civ files untouched):
- iber I1: "The houses are the wall: on the highest ground near the civic centre, houses back to back in a closed ring, rear walls facing out, one street inside, one gate toward the enemy with a tower on each side. If we start with stone walls, keep them and close any gap with houses."
- germ G8: "The village is spread out. Every house stands at the edge of the woods or next to a field with no house beside it yet. Every house stands apart and never touches another house. Build nothing toward the enemy."
- spart: verbatim.

Lessons (round 7):
13. Every option clause that repeats a goal noun pulls, even when negated: "no field and no woods next to it" won
    at .12–.14 over "at the edge of the woods" (.09–.12).
14. A goal phrase that holds two conditions ("apart … , next to a field") is read as one; give each condition its
    own sentence and put the rarer one first (G7 8–9/13 → G8 10–12/13).
15. State lines are pulls too: "Touching buildings … lining the left flank, the front and the back" made a
    spread-out civ fill the missing right flank. Facts the goal does not talk about must be left out (gate by
    goal words, from data).
16. Composing two facts that hold together into one clause ("stands apart, 28 m …, at the edge of the woods")
    is stronger than two separate clauses.

### No per-civ code (follow-up, 2026-10-07)
- Layout line: the lab now reads the named field `params.city.layout` (configured in `city_data/civ_layout.json`).
  It looks in the civ JSON first, then in the lab-side override `city_data/civ_overrides/<civ>.json`, which holds
  the verbatim lines copied once. There is no text[] index. A civ without the field stops with an error.
  Proposal for Josue: add `params.city.layout` to each civ JSON.
- Word → fact table: `city_data/fact_words.json` (field, wood, storehouse, open, edge, enemy, lines, band,
  cc_gap). The code computes only generic geometry; the words choose which facts appear.
- Judge checks per civ: data in `city_data/checks.json`, used for evaluation only.
- No `if civ` branch, no per-civ dict, no per-civ tuning in city_real.py, city_judge.py or city_probe.py
  (grep: civ codes appear only in usage examples).
