# Design Notes — lessons from text-game history

Distilled from **_50 Years of Text Games_** (Aaron A. Reed, 2023), kept at
`~/llm-local/50 years of text games/` (epub, pdf, and a plain-text chapter set under
`50YearsOfTextGames-Text/` — one `.txt` per year/game). These are the threads most
relevant to an LLM-driven MUD; they directly motivate the decisions in `ARCHITECTURE.md`.

## AI Dungeon (2019) — the cautionary predecessor, and why our architecture exists

AI Dungeon was the first viral LLM-driven text game (GPT-2, then GPT-3). Its core flaw,
in the book's words, was a **"looming brick wall": GPT had no world model.** "Words went
in and new words came out. Nothing was kept track of or simulated." Adding an inventory
system to a normal parser game is trivial; teaching it to a black-box LLM was "almost
impossible." Their workarounds were all *shoving more text into the box*: pin-able facts,
a rolling window of the last ~8 turns, and even a separate ML model trained to **detect
strings** like "you have claimed the sword!" to guess when a quest finished.

**Our design is the direct answer to every one of those:**

| AI Dungeon's problem | wizardmud's answer |
|---|---|
| No world model (black box) | **Evennia's DB is the authoritative world model**; the LLM only narrates (§9) |
| Guessing quest completion from prose | A **deterministic flag graph** + solvability validator — the quest *grants* `has_sword`, we don't detect it from text (§8) |
| Pinned facts / 8-turn window | The **worldbible canon** as grounding context + per-NPC memory; canon authored once, stays consistent (§7) |
| "Responds with absolute confidence" even when wrong | State authority — the model can misremember prose, but it cannot move a flag |
| Tay-style unfiltered toxicity | Deliberately **local, open-weight models** + the `CONTENT_RATING` knob, not an unfiltered cloud box |

In short: AI Dungeon proved an LLM *can* improvise a world; it also proved that without
authoritative state the world won't *hold together*. We keep the improvisation and add the
skeleton it was missing.

## MUD (1980) — our genre's origin, and lessons that still bite

Trubshaw & Bartle's MUD (Essex University). Several things map straight onto our build:

- **The "wizard" is canonical.** MUD's top rank, *wiz*, earned world-editing powers
  (debugging verbs rebranded as magic spells: move any object, drop a monster on someone).
  Our LLM **wizard is the benevolent, automated version of that role** — a game master with
  authoring power, kept honest by the state model.
- **MUDDL ≈ our declarative quest/flag graph.** MUD shipped a *MUD Definition Language*:
  rooms, exits, and **conditional exits** ("cross the stream only with a boat, or when it's
  not raining") defined in data, no engine changes. That gating condition is exactly our
  `requires`/flags. MUDDL's object-action rules are a good model for declarative content.
- **"The world had to assume dominance, not the problem-solving."** Bartle found
  hand-authored *puzzles* don't survive multiplayer; what endures is a living, social world.
  A caution for our puzzle pass: puzzles shine single-player or instanced, but the long-term
  draw of a MUD is the *place and the people*, not the riddles.

### ⚠️ Bartle's "lamp problem" = our consumable soft-lock, but worse in multiplayer

Bartle: object puzzles "don't work at all: if you need the lamp to go underground, the
first person to grab it prevents anyone else from following." **This is the same failure our
validator catches** (a consumed resource the path still needs) — but in a *shared* world it's
harder, because our solvability validator reasons about a **single** player's run. For a real
multiplayer MUD, shared consumable resources need design care: per-player/instanced quest
items, respawning resources, or simply **avoiding shared consumables for required progression**.
(Our worldbible runs so far use almost no `consumes`, which sidesteps this — fine for now;
revisit if we add resource puzzles to shared space.)

- **Procedural text + "mobiles."** MUD already templated combat text (`"You :r :p with a
  :r :r!"`) to avoid repetition, and coined *mobs* — autonomous creatures with "instincts."
  Our LLM does both far richer, but the lesson stands: variety + autonomous behavior make a
  world feel alive.

## Chapters worth a deeper read later (flagged, not yet read)

- **2000 Galatea** — a single-room conversational NPC; the canonical study in text-game
  conversation. Direct input for our NPC/wizard dialogue design.
- **1990 LambdaMOO** — player-extensible social world (the MOO/MUSH lineage). Lessons on
  user-created content and governance.
- **2009 Fallen London** — *quality-based narrative*: storylets gated by stats/flags.
  Essentially a polished version of our flag graph; strong patterns for state-reactive content.
- **2013 Versu** — explicit social/character simulation; a structured contrast to LLM improv.

*Source: 50 Years of Text Games, Aaron A. Reed (2023).*
