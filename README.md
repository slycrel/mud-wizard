# wizardmud — a local-LLM text adventure (offline)

An [Evennia](https://www.evennia.com/) MUD whose NPCs and a game-master "Wizard"
run on **local** LLMs served by **LM Studio** (MLX, Apple Silicon). Built to run
fully offline. Coding is done with **opencode** pointed at the same local server.

**Location:** `~/llm-local/mud-wizard` (kept separate from `~/develop` work projects).

> 📖 New here or hit a confusing error? **`ARCHITECTURE.md`** explains how the
> whole thing works and has a troubleshooting section + glossary.
> 🤖 Coding with a local AI agent (opencode)? Start with **`AGENTS.md`** — model
> lineup, codebase map, and the non-obvious gotchas, written for offline assistants.

---

## TL;DR — start / stop

```bash
cd ~/llm-local/mud-wizard
./mud.sh start      # server + models (correct context) + game
./mud.sh code       # just the coding stack (server + coder), no game
./mud.sh status     # what's running (check the CONTEXT column!)
./mud.sh stop       # tear it all down
```

Then:
- **Code:** `cd ~/llm-local/mud-wizard && opencode`
- **Play:** web client <http://localhost:4001>, or telnet `localhost:4000`

> First `./mud.sh start` ever will prompt you to **create an Evennia superuser**
> in the terminal — that's normal, do it once.

## The one gotcha (read this if opencode errors)

If opencode says:

> *"The number of tokens to keep from the initial prompt is greater than the
> context length…"*

…the coder model got loaded with LM Studio's **default 4096 context**, which is
smaller than opencode's system prompt. Fix:

- `./mud.sh start` (or `code`) loads it with **32768** automatically, **or**
- set it permanently in LM Studio: **My Models → `qwen/qwen3.6-27b` → gear →
  Context Length 32768, GPU Offload Max**, save as default (JIT will then use it).

Bump it higher for big coding sessions: `CODER_CTX=65536 ./mud.sh start`.

## Models (all downloaded, MLX)

| Role | LM Studio id | Context |
|---|---|---|
| Coder (opencode) | `qwen/qwen3.6-27b` | 32768 (must be large) |
| Wizard / game master (live + puzzle judge) | `eva-qwen2.5-32b-v0.2-mlx` | 16384 |
| Worldbible canon author (offline) | `nousresearch/hermes-4-70b` | 16384 |
| NPC chatter | `google/gemma-4-e4b` | JIT default (prompts are tiny) |

## Play commands (in-game, as the superuser/builder)

**First, raise the world.** A fresh login lands in empty default Limbo — run this once
(builder-only) to materialize the generated worldbible into walkable rooms, exits, and the
Wizard, and drop you at the start with an intro:
```
worldinit          # builds rooms/exits/NPCs from world/generated/ (safe to re-run)
```
Then move around with the usual exits (`south`, `west`, …) — `look` shows each location's
**reactive** description (it changes as you complete quests). Talk to the Wizard who's now
standing in the keep:
```
talk The Wizard = where am I?
```
Want an ambient NPC too?
```
create/drop Old Gus:typeclasses.llm_npcs.ChatterNPC
set Old Gus/desc = a grizzled tavern keeper
talk Old Gus = got any rumors?
```

Play the generated worldbible quests (loaded from `world/generated/`):
```
quests                       # flag-gated list + bonus objectives + win state
approach investigate_keep    # see the puzzle's setup + challenge
attempt investigate_keep = I wait for moonlight and follow the glowing runes to the tapestry
hint investigate_keep        # escalating hints if you're stuck
bypass investigate_keep      # the Wizard's guaranteed alternative (offered only when stuck)
survey whispering_marsh      # reactive location text — changes as you complete quests
```
The Wizard (EVA-Qwen) judges attempts — the canonical solution **or** a clever
alternative succeeds, vague tries get partial credit, nonsense fails — and the
`bypass` safety valve means you can never dead-end.

> Generate a fresh world first with `python world/worldbible.py "<seed>" --rating mature`,
> or play the one already in `world/generated/`.

See the world — on the web client, the Wizard renders an image (Draw Things must be
running with its API server on, port 7860):
```
inspect                        # the room you're in
inspect statue                 # an object — web clients get a picture, telnet just text
illustrate statue = photoreal  # (builder) pre-render a hero object in HQ
```

## Layout

```
mud-wizard/
├── ARCHITECTURE.md              # how it all works + troubleshooting + glossary (refer here when stuck)
├── DESIGN-NOTES.md              # lessons from text-game history (50 Years of Text Games), applied here
├── mud.sh                       # start/stop/status control script
├── opencode.json                # opencode -> LM Studio (:1234/v1)
├── download-models.sh           # re-pull models if ever needed
├── FLIGHT-CHECKLIST.md          # offline test steps
└── wizardmud/                   # the Evennia game (Python 3.12 venv in ../.venv)
    ├── server/conf/settings.py  # LLM_* settings block
    ├── commands/
    │   ├── default_cmdsets.py   # registers talk / quests / worldinit / inspect …
    │   ├── quest_cmds.py        # quests/approach/hint/attempt/bypass/survey
    │   └── build_cmds.py        # worldinit — builds the walkable world from the worldbible
    ├── typeclasses/
    │   ├── llm_openai_client.py # OpenAI chat-completions bridge (verified working)
    │   ├── llm_npcs.py          # WizardNPC + ChatterNPC
    │   └── rooms.py             # WorldbibleRoom (reactive look)
    └── world/
        ├── layout.py            # scene manifest (rooms/exits/NPC placement)
        └── generated/           # the playable content, incl. worldbible_layout.json
```

## How it hangs together

opencode and Evennia both speak the **OpenAI chat-completions** API to LM Studio
on `:1234`. Evennia's stock LLM contrib only speaks the text-generation-webui
format (its `LLM_API_TYPE="openai"` is read-but-unused), so
`typeclasses/llm_openai_client.py` bridges to `/v1/chat/completions`.

**Design rule:** game *state* is authoritative in Evennia's DB. The LLM only
*narrates and generates* — it never owns inventory, exits, HP, or mechanics.

## Notes

- Python: the venv is pinned to **3.12** (Evennia 6 doesn't run on the system's 3.14).
- LM Studio must live in **/Applications** (it refuses to run elsewhere).
- Everything runs offline once models are downloaded — verify with WiFi off.
