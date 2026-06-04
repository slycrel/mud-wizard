# AGENTS.md — orientation for AI coding agents (read me first)

You are working on **wizardmud**, an offline, local-LLM-driven MUD (Evennia) where AI
authors validated narrative content and an in-character wizard runs the game live.
This file is the quick map. The deep reference is **`ARCHITECTURE.md`** (§1–8c, plus a
troubleshooting section and a glossary); design influences are in **`DESIGN-NOTES.md`**;
how to run things is in **`README.md`**.

## The golden rule (do not violate)
**Game state is authoritative in code/DB; the LLM only narrates, judges, and fills.**
Flags, quests, inventory, win conditions live in the validated quest graph and on
`character.db.progress`. An LLM result may be flavorful, but it must never be the source
of truth and must never be able to make the game unwinnable. Any change to the quest
graph must be re-checked with `world/solvability.py`.

## Models (LM Studio at `:1234/v1`, OpenAI-compatible; Draw Things at `:7860`)
| Role | Model id | Used by |
|---|---|---|
| Coder (this agent) | `qwen/qwen3.6-27b` | opencode |
| Offline canon author | `nousresearch/hermes-4-70b` | `world/worldbible.py` |
| Content critic (independent) | `qwen/qwen3.6-27b` | `world/worldbible.py` critique stage |
| Live wizard + puzzle judge | `eva-qwen2.5-32b-v0.2-mlx` | `WizardNPC`, `world/puzzle_judge.py` |
| NPC chatter | `google/gemma-4-e4b` | `ChatterNPC` |
| Images | SDXL checkpoints in Draw Things | `world/image_gen.py` |

## Codebase map
```
wizardmud/
├── server/conf/settings.py      LLM_* settings, CONTENT_RATING, MEDIA_ROOT/URL
├── web/urls.py                  serves generated images at /media/
├── commands/
│   ├── default_cmdsets.py       registers all custom commands
│   ├── quest_cmds.py            quests/approach/hint/attempt/bypass/survey
│   ├── image_cmds.py            inspect / illustrate (+ illustrate() helper)
│   └── build_cmds.py            worldinit: materialize the worldbible into rooms/exits/NPCs
├── typeclasses/
│   ├── llm_openai_client.py     OpenAI chat-completions bridge for Evennia's LLM contrib
│   ├── llm_npcs.py              OpenAINPC / WizardNPC (EVA) / ChatterNPC (Gemma)
│   └── rooms.py                 Room + WorldbibleRoom (reactive look via get_display_desc)
└── world/                       # the content pipeline (pure-Python core + offline gen)
    ├── solvability.py           validator: solvable? soft-lock-free? + inert-reward lint
    ├── worldbible.py            offline gen: prose→quests→validate→puzzles→critique→reactions→layout
    ├── worldbible_loader.py     runtime: load generated/, per-player Progress, location_text
    ├── puzzle_judge.py          hybrid attempt judging + stuck-bypass narration (EVA)
    ├── image_gen.py             Draw Things txt2img + render profiles
    ├── reactive.py              flag-gated content resolver (compose)
    ├── layout.py                scene manifest: rooms/exits/NPC placement (derive or load file)
    ├── test_*.py                unittest suites (no network/Evennia needed)
    └── generated/               worldbible.md, *_quests.json, *_puzzles.json,
                                 *_critique.json, *_reactions.json, *_layout.json
```

## Conventions & gotchas (a local model WILL get these wrong otherwise)
- **`world/` dual-import pattern.** Modules do `sys.path.insert(0, <thisdir>)` then
  `from solvability import ...` so they import both standalone (tests, `worldbible.py`)
  and in-game (`world.x`). Keep this; don't switch to package-relative imports.
- **Generous LLM token budgets.** Hermes *and* Qwen emit large hidden `reasoning_content`
  before the answer; a small `max_tokens` truncates the real output to empty. Offline gen
  uses 5k–12k on purpose. The clean reply is in `choices[0].message.content`.
- **Draw Things API quirk.** Switch checkpoint with a **top-level `model` field** in
  `/sdapi/v1/txt2img`. It does NOT support `override_settings`, `sd_model_checkpoint`, or
  POST `/options` (all 404/422). Only `txt2img`/`img2img`/`options` exist.
- **LM Studio context.** Load models with a large context (e.g. `lms load … -c 32768`),
  or agentic prompts fail with "tokens to keep … greater than context length" (the JIT
  default is 4096). `./mud.sh` handles this.
- **Evennia runs on the pinned 3.12 venv** (`.venv`); the system Python is 3.14 and
  breaks Evennia 6. Run game commands from `wizardmud/`. The LLM contrib's `LLM_API_TYPE="openai"`
  is read-but-unused — that's why `llm_openai_client.py` exists.
- **Run `evennia` with `.venv/bin` on PATH.** The launcher spawns `twistd` by bare
  name, so without the venv bin on PATH the Portal/Server die with
  "No such file or directory: 'twistd'". `mud.sh` exports it; if you invoke `evennia`
  directly, `source ../../.venv/bin/activate` (or `export PATH=../../.venv/bin:$PATH`) first.
- **In-game LLM calls run via `deferToThread`** so a slow model never freezes the MUD.
- **Solvability is non-negotiable.** The critique and reactive layers must never break a
  validated graph; the critique-repair explicitly discards a fix that fails `validate()`.

## How to test (offline, no models needed)
```bash
cd wizardmud/world && ../../.venv/bin/python -m unittest discover -p "test_*.py" -v
```
Validate any generated arc: `../../.venv/bin/python solvability.py generated/worldbible_quests.json`

## How to run / generate
```bash
./mud.sh start            # LM Studio (EVA) + Evennia; play at localhost:4001
# In-game, ONCE per world (builder): turn the generated content into a walkable map
#   worldinit             # builds rooms/exits/NPCs from generated/, drops you at the start
#                         # (idempotent — safe to re-run; objects are tagged category 'wb')
# offline content authoring (needs Hermes + the Qwen critic loaded in LM Studio):
cd wizardmud/world && ../../.venv/bin/python worldbible.py "<seed>" --rating mature
#   --critique-only / --reactions-only / --layout-only re-run just that stage
#   the [7/7] layout stage emits generated/worldbible_layout.json (deterministic; preserves
#   an authored file). Edit that file for canon-accurate exit directions / NPC placement.
```
