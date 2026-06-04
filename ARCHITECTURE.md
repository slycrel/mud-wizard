# wizardmud — Architecture & Reference

The "what's going on here and why" doc. README.md tells you how to *run* it;
this tells you how it *works*, how to *debug* it, and what the words mean.
Written for someone fairly new to running local LLMs.

**Status legend:** ✅ built & verified · 🟡 planned/scaffolding · 🔭 future idea

---

## Table of contents
1. [Mental model (read this first)](#1-mental-model)
2. [The stack & how the pieces talk](#2-the-stack)
3. [The model lineup](#3-the-model-lineup)
4. [The LLM bridge (and the Evennia gotcha)](#4-the-llm-bridge)
5. [The context-length gotcha (the #1 thing that breaks)](#5-context-length)
6. [Image visualization pipeline](#6-image-visualization)
7. [Worldbible: offline narrative generation](#7-worldbible)
8. [Solvability validator & "narrative CI"](#8-solvability)
8b. [Live layer — playing the content](#8b-live-layer)
8c. [Reactive content — the world acknowledges you](#8c-reactive-content)
9. [The one design rule](#9-design-rule)
10. [Operations: ports, paths, commands](#10-operations)
11. [Troubleshooting / FAQ](#11-troubleshooting)
12. [Glossary](#12-glossary)

---

## 1. Mental model

Three programs, all on your laptop, all offline:

- **LM Studio** runs the AI models and exposes them over a local web API (port 1234).
  Think of it as "a private OpenAI server on localhost."
- **opencode** is the coding assistant (a terminal app). It talks to LM Studio to help you write the game.
- **Evennia** is the game server (the MUD). It *also* talks to LM Studio so the
  in-game Wizard and NPCs can speak. Players connect to Evennia via a web browser or a MUD client.

Everything routes through LM Studio's one API. Nothing needs the internet once
models are downloaded.

```
   you (coding)            players (in the game)
       │                          │
   ┌───▼────┐               ┌─────▼──────┐
   │opencode│               │  Evennia   │  ← game logic, rooms, state (SQLite)
   └───┬────┘               └─────┬──────┘
       │ OpenAI API               │ OpenAI API (via our bridge)
       └──────────┬───────────────┘
            ┌──────▼───────┐
            │  LM Studio   │  :1234/v1   ← runs the models (MLX)
            └──────────────┘
   (separate) Draw Things :7860-ish ← runs the image model 🟡
```

## 2. The stack

| Layer | What | Port | Notes |
|---|---|---|---|
| Inference server | LM Studio (MLX backend) | `1234` | OpenAI-compatible `/v1/chat/completions` |
| Coding TUI | opencode | — | config in `opencode.json`, reads CWD |
| Game server | Evennia 6 (Python 3.12) | `4001` web, `4000` telnet | Django + Twisted under the hood |
| Image server | Draw Things (local API) 🟡 | (its own) | separate from LM Studio |

**Why LM Studio and not Ollama:** LM Studio uses Apple's **MLX** engine (faster on
Apple Silicon) while still giving us the OpenAI-compatible API and multi-model
loading that everything else expects.

**Why these are decoupled:** opencode and Evennia don't know or care which model
is loaded — they just POST to `:1234`. You can swap models without touching code.

## 3. The model lineup

| Role | LM Studio id | Type | Size | Context | Status |
|---|---|---|---|---|---|
| Coder (opencode) | `qwen/qwen3.6-27b` | dense 27B | 16 GB | **32768** (must be large) | ✅ |
| Wizard / game master (live) | **EVA-Qwen2.5-32B** (RP-tuned, low-refusal) | dense 32B | ~35 GB (8-bit) | 16384 | ✅ |
| (fast fallback wizard) | `qwen/qwen3.6-35b-a3b` | MoE (3B active) | 20 GB | 16384 | optional |
| NPC chatter | `google/gemma-4-e4b` | 4B | 6.86 GB | JIT default | ✅ |
| Narrative / worldbible (offline) | **Hermes 4 70B** (Nous, low-refusal, steerable) | dense 70B | 39.7 GB | large | ✅ offline-only |
| Image — painterly (default) | **DreamShaper XL** / ZavyChroma (SDXL) + a **Lightning** variant for the fast pass | diffusion | ~7 GB | — | 🟡 |
| Image — photoreal (on-demand) | **RealVisXL** / Juggernaut XL (SDXL) | diffusion | ~7 GB | — | 🟡 |

**Why different models for different jobs:**
- *Coder* — dense model tuned for code; needs a **big context** because opencode's
  system prompt + your files are large.
- *Wizard (live)* — a **role-play fine-tune** (EVA-Qwen). It's tuned to stay in
  character and not moralize/refuse, which matters for an immersive, adult-audience
  game master. It doesn't need heavy structural reasoning live — it leans on the
  canon Hermes already produced. (The MoE `qwen3.6-35b-a3b` is a faster, more
  "vanilla" fallback if you want speed over RP flavor.)
- *NPC chatter* — tiny + fast; NPC small-talk doesn't need a big brain, and a
  different model (Gemma) gives NPCs a distinct "voice" from the Wizard.
- *Narrative / Hermes 70B* — used **offline, once**, to write the world bible.
  Slowness doesn't matter for a batch job; 70B gives the best long-form coherence,
  and Hermes is low-refusal + schema-adherent (good for the quest JSON). Because it
  runs separately, it doesn't compete for RAM with the game models — load it alone,
  generate, unload.

**Division of labor:** **Hermes 70B is the offline canon architect** (rigorous,
structured, runs once); **EVA-Qwen is the live in-character wizard** (fast, immersive,
leans on the canon). Heavy reasoning happens once, offline; the live model just
performs.

### EVA-Qwen wizard config (the "extra setup" from its model card)
EVA-Qwen needs specific settings to behave well — set these in **LM Studio →
My Models → EVA-Qwen → gear** (save as default so JIT uses them):
- **Prompt template: ChatML** (usually auto-detected; verify).
- **Samplers: Temperature 1.0, Min-P 0.05, Top-A 0.2, Repetition Penalty 1.03.**
- **Context: ~16384** (the card lists ~10K trained; our prompts are small).

How this flows through our bridge: we call `/v1/chat/completions`, so LM Studio
applies the ChatML template automatically. Min-P / Top-A / rep-penalty aren't sent
by our bridge, so **LM Studio's per-model defaults apply** — which is why you set
them in the GUI. Temperature *is* sent by the bridge, so the wizard's temperature
is set per-NPC in code (→ 1.0 for EVA) while the Gemma chatter stays lower.
TODO when wiring EVA: send the persona as a ChatML `system` message and memory as
alternating turns (the bridge has an `LLM_SYSTEM_PROMPT` hook for this) for best RP.

## 4. The LLM bridge

**File:** `wizardmud/typeclasses/llm_openai_client.py`

Evennia ships an LLM contrib (`evennia.contrib.rpg.llm`), but it was written for
a different server format (text-generation-webui). It POSTs a bare `prompt` and
parses the reply as `{"results":[{"text":...}]}`. **LM Studio speaks the OpenAI
format** instead: it wants `{"messages":[...]}` and returns
`{"choices":[{"message":{"content":...}}]}`. The contrib has an
`LLM_API_TYPE="openai"` setting but in this Evennia version **it's read and never
used** — the parser is hardcoded.

So `OpenAIChatLLMClient` subclasses the contrib's `LLMClient` and overrides two things:
- `_format_request_body()` → builds the OpenAI `messages` body (maps the contrib's
  `max_new_tokens` → OpenAI `max_tokens`).
- `get_response()` → parses `choices[0].message.content`.

NPC typeclasses (`llm_npcs.py`) point at it: `WizardNPC` (uses the 35B), `ChatterNPC`
(uses Gemma), both subclassing `OpenAINPC` which selects the model per-NPC. Verified
end-to-end (returns real in-character text).

**Relevant settings** (`wizardmud/server/conf/settings.py`):
```python
LLM_HOST = "http://127.0.0.1:1234"
LLM_PATH = "/v1/chat/completions"
LLM_MODEL = "qwen/qwen3.6-35b-a3b"   # default if an NPC doesn't set its own
LLM_REQUEST_BODY = {"max_new_tokens": 300, "temperature": 0.8}
```

## 5. Context-length gotcha

**This is the #1 thing that will break and confuse you.** Symptom in opencode:

> *"The number of tokens to keep from the initial prompt is greater than the
> context length. Try to load the model with a larger context length…"*

**Why:** every model is loaded with a fixed **context window** (how many tokens of
conversation it can hold). LM Studio's default when it auto-loads a model ("JIT")
is only **4096** tokens. opencode's system prompt + tool definitions are *bigger
than that*, so the server rejects the request before your message even matters.
It has nothing to do with your input size.

**Fix (any one):**
- `./mud.sh start` / `./mud.sh code` loads the coder at **32768** and pins it.
- Manually: `lms load qwen/qwen3.6-27b -c 32768 --gpu max -y`
- Permanent, in the GUI: **My Models → `qwen/qwen3.6-27b` → gear → Context Length
  32768, GPU Offload Max**, save as default. Then even auto-loads use it.

Bigger sessions: `CODER_CTX=65536 ./mud.sh start`. Larger context uses more RAM
(KV cache) and slows prefill, but you have 128 GB to spend.

## 6. Image visualization
✅ **built** — `world/image_gen.py` + `commands/image_cmds.py` (`inspect` / `illustrate`)

**Goal:** rooms get establishing shots; objects get item art via `inspect <obj>`.

**Display side (easy):** Evennia's webclient has a `multimedia.js` plugin that
renders `image`/`audio`/`video` messages as inline HTML. Server side is one call:
```python
obj.msg(image=["/media/objects/sword.png"])   # web players see it; telnet ignores it
```
Telnet/MUD clients can't show images, so they just get the text — **graceful
degradation** for free. (Optional: pipe the image through `chafa` to make ANSI
art for terminal players.)

**Generation side (separate stack):** **Draw Things** runs a diffusion model
locally and exposes an API. Note: this is *not* LM Studio — LM Studio runs
text/vision LLMs (gemma-4's "image input" is image *understanding*, not
*generation*). Diffusion models **don't "refuse" like LLMs** — there's no RLHF
refusal vector. The only gates are (a) training coverage and (b) an *optional*
safety checker that Draw Things doesn't force. So we pick an **SDXL-family
checkpoint** (good artistic/anatomical coverage; classical nudity like a statue is
in-range) rather than fighting a filter.

**Draw Things API (verified end-to-end):** HTTP server on `localhost:7860`,
A1111-compatible (enable in-app: server-stack icon → API Server → Server Online,
HTTP). `POST /sdapi/v1/txt2img` with `{prompt, steps, width, height, cfg_scale,
seed}` returns a base64 PNG **and** writes a file to `~/llm-local/gen-images` (set
via "Save Generated Media to"). Switch checkpoint **per render with a top-level `model`
field** — Draw Things does NOT support A1111's `override_settings` or a top-level
`sd_model_checkpoint` (both 422), nor POST `/sdapi/v1/options` (404). Checkpoint names
are the `*_f16.ckpt` forms (read the current one at `GET /sdapi/v1/options`):
`dreamshaper_xl_v2.1_turbo_f16.ckpt`, `zavychromaxl_v100_f16.ckpt`,
`realvisxlv50_v50lightningbakedvae_f16.ckpt`, `juggernautxl_ragnarokby_f16.ckpt`. Only a
**subset** of the A1111 API exists — `txt2img`, `img2img`, `options` work; `sd-models`,
`samplers`, `progress`, and POST `options` return 404/422.

**Live wiring (built):** `world/image_gen.py` (the 4 render profiles + the `model`-field
call; fails soft to `None` so a down image server just means "no picture") and
`commands/image_cmds.py` — `inspect [<target>]` (player; web clients get the picture,
telnet just text) and `illustrate <t> [= profile]` (builder pre-render at a chosen
quality). Images cache on `obj.db.image_url` (keyed by desc+profile), save under
`MEDIA_ROOT`, and serve at `/media/` (route in `web/urls.py`); Evennia's default
webclient loads `multimedia.js`, so `msg(image=[url])` renders inline. Renders run via
`deferToThread` so the MUD never blocks. Verified live across all four profiles.

**Render profiles (style × quality, resource-aware):** `illustrate(obj, profile=…)`
picks a named profile, so style and tier are config, not code. Profiles are
swappable; checkpoints live in **Draw Things**.

- **`painterly` (default, fast)** — **DreamShaper XL v2.1 Turbo**, a few seconds;
  the everyday `inspect`/room pass. May stay warm or quick-load.
- **`painterly-hq` (on-demand)** — **ZavyChromaXL** (richer/darker fantasy), full
  steps, for hero objects / "render this nicely" requests.
- **`photoreal` (fast)** — **RealVisXL v5.0 Lightning** (baked VAE), realistic
  statues/materials.
- **`photoreal-hq` / versatile (on-demand)** — **Juggernaut XL (Ragnarok)**,
  straddles real↔painterly.
- **future: `noir` / `comic`** — a dedicated ink/graphic-novel checkpoint, or just
  style-prompt the painterly model. Drop-in as a new profile.

On-demand profiles use a **load → render → unload** helper so heavy checkpoints
aren't resident during play (mirrors the LLM load/unload pattern; keeps RAM/GPU
free). Only the fast default may stay warm. The `CONTENT_RATING` knob applies to all
profiles equally (it's injected into the prompt, independent of checkpoint).

**Caching = state authority:** store the result path as `obj.db.image_path`.
Generate on first inspect, regenerate only if the description changes. The Wizard
LLM writes the *image prompt* from the object's name+desc; the render runs
**async** (Twisted) so the game never blocks — reuse the contrib's existing
"thinking…" placeholder pattern.

**Content control (one knob, built in up front):** a single `CONTENT_RATING`
setting (`pg13` | `mature` | `explicit`) is injected into both the wizard's system
prompt *and* the image-prompt generation, so prose and images stay at the same
level. Because the **LLM writes the image prompt**, the wizard's persona/rating *is*
the content governor — you steer at the prompt layer, not by fighting the diffusion
model. Building this seam now is ~free. **User age-gating** (verifying *who* may set
`mature`) is a separate, multi-user concern — easily added later at Evennia's
**account layer** (a flag set at registration), so it does not need to exist until
you open the game to others.

## 7. Worldbible
✅ **built** — `wizardmud/world/worldbible.py` → `world/generated/{worldbible.md, worldbible_quests.json, worldbible_puzzles.json, worldbible_critique.json}`

A **separate, offline** pass with **Hermes 4 70B** writes the canon — geography,
factions, the main arc, NPC dossiers (`worldbible.md`) — **and a machine-readable
quest schema** alongside the prose, then runs it through the validator (§8) in a
**generate → validate → repair** loop so the saved quest graph is provably winnable.
(Hermes passed clean on the first run; if a future run doesn't, the loop feeds the
validator's errors back to Hermes for a fix, up to `--max-repairs` times.) Honors the
`--rating` (CONTENT_RATING) knob. Example quest:
```json
{ "id": "open_vault", "requires": ["has_key","knows_password"],
  "grants": ["vault_open"], "consumes": ["has_key"], "optional": false,
  "location": "old_keep", "summary": "..." }
```
This file is **authoritative canon**. At runtime the fast Wizard is fed the
*relevant slice* as context, so dynamically generated rooms/objects/quests stay
on-canon. The big model writes scripture once; the fast model preaches it.

### Quality critique — the "is it worth playing?" gate
✅ **built** — the worldbible pipeline is five stages: prose → quests → **validate
structure** (§8) → puzzles → **critique quality**. The critique uses an *independent*
model — **Qwen 3.6 27B critiques what Hermes generated** (the adversarial-review
principle: different eyes catch what the author won't) — scoring 0-10 on
coherence / meaningfulness / puzzle-quality / variety / pacing and listing concrete,
id-referenced issues. Blocker/major issues are fed back to **Hermes to revise**, and
crucially **a revision that breaks solvability is discarded** — structure validity is
non-negotiable; quality is layered on top, never at its expense. `--critique-only`
re-grades existing content without regenerating; `--critique-rounds N` pushes further.

> **Two checks, two jobs.** The deterministic validator (§8) answers *"can it be won?"*.
> The LLM critic answers *"is it worth playing?"*. In a demo pass the critic caught
> disconnected sidequests and a tonal clash with the mature canon; one Hermes repair
> round (re-validated solvable) lifted the scores coherence 6→8, meaningfulness 4→6.
>
> Budgets are deliberately **generous** (critique ~12k tokens): both models *reason*
> heavily before emitting JSON, and this is an offline batch pass, so a starved budget
> truncates the answer to nothing. Latency doesn't matter here; completeness does.

## 8. Solvability
✅ **built** — `wizardmud/world/solvability.py` (+ `test_solvability.py`, `sample_world.json`)

The quest set is a **flag-dependency graph**: flags are state (a flag *consumed* by
any quest is a spendable **resource**, otherwise a permanent **fact**), quests
require/grant/consume them, and the win condition is a target flag set. The
deterministic **validator** runs a **bounded state-space search** — states are *sets
of completed quests*; facts/resources are derived from the start state plus those
completions — which unifies the checks we care about into one correct analysis:

- **Solvable?** Is there *any* ordering of quests that reaches the goal? (A fast
  optimistic flag-closure pre-filter catches "nothing ever grants X" cheaply; the
  full search then proves a real ordering exists under resource constraints — e.g.
  it catches "one key, two doors that each consume it," which a naive reachability
  check misses.)
- **Soft-lock-free?** Is every *reachable* state still able to reach the goal? If one
  can't, the player could strand themselves — and the validator **names the quest**
  that crosses into the trap, flagging whether it was `optional` (a sidequest
  soft-lock) vs. required (self-inflicted).

The search is exponential worst-case, so it's capped (`node_cap`, default 200k) and
**warns loudly if truncated** — no silent "looks fine." `requires`-gating prunes hard,
so MUD-sized arcs finish fast (the sample arc explores 11 states).

**Why a validator instead of trusting the LLM:** solvability is a hard invariant; a
stochastic model can't *guarantee* it and its "rescue" content can introduce new
soft-locks. So:
- **Authoring loop:** LLM proposes → validator disposes → feed failures back → LLM
  repairs. Creative content *with* a winnability guarantee.
- **Runtime gating:** when the Wizard improvises a sidequest, it's expressed as flag
  changes and re-validated before committing; reject/regenerate if it would soft-lock.

**Narrative CI:** `world/test_solvability.py` (unittest; `pytest` runs it too) covers
the five failure modes. Use the CLI as a gate against any generated bible:
`python world/solvability.py world.json` (exit 0 = solvable & soft-lock-free). This is
the line between a demo and a real game.

## 8b. Live layer
✅ **built** — `world/worldbible_loader.py`, `world/puzzle_judge.py`, `commands/quest_cmds.py`

Turns the offline-generated, validated content into actual play:

- **Loader + per-player state** (`worldbible_loader.py`) — loads canon + quest graph +
  puzzles; tracks each character's `flags` / `done` / failed `attempts` / `hints_used`
  on `caller.db.progress`. The **same `solvability.World`** powers offline validation
  and live play, so what was proven winnable is exactly what runs.
- **EVA-Qwen is the live wizard *and* the judge** — `WizardNPC` runs
  `eva-qwen2.5-32b-v0.2-mlx`; thematically the GM judges your attempts.
- **Commands** (`quest_cmds.py`): `quests` (flag-gated list + bonus objectives + win
  state), `approach <q>` (face the puzzle), `hint <q>` (escalating hints),
  `attempt <q> = <action>` (hybrid judging), `bypass <q>` (the stuck-player alternative).
- **Hybrid judging** (`puzzle_judge.py`) — EVA accepts the canonical solution *or* a
  genuinely clever alternative, gives partial credit, rejects nonsense, narrates in
  character, and never leaks the solution. Verified live (strong→success, vague→partial,
  nonsense→fail).
- **Guaranteed alternative when stuck** — after enough failed attempts AND exhausted
  hints, the Wizard offers `bypass`, narrating a *different* route that grants the
  quest's **already-validated flags**. Because the flag outcome is fixed, every path to
  completion (solve / clever-alt / bypass) is solvability-safe — **the player can never
  dead-end.**

State authority in action: the LLM judges and narrates; flags are applied here from
validated grants; LLM calls run off-reactor via `deferToThread` so the MUD never freezes.

## 8c. Reactive content
✅ **built** — `world/reactive.py`, worldbible `--reactions`, the `survey` command, `WizardNPC.build_prompt`

The world acknowledges what you've done — Fallen London's quality-based narrative + Galatea's
reactive NPC (both from DESIGN-NOTES). A location has a base description plus **flag-gated
fragments**; at look-time `reactive.compose()` appends every fragment whose `requires`/`forbids`
match the player's flags:

- **Generated** by `worldbible.py --reactions` — Hermes writes per-location base text + fragments,
  gated ONLY on real quest flags; a **lint** reports any fragment referencing an unknown flag
  (so it can't silently never-fire).
- **Played** via `survey [<location>]` — composes the location text from your current progress.
  *Demo:* the Whispering Marsh gains *"The mists now murmur with secrets unearthed…"* only after
  `marsh_secrets_learned`.
- **The Wizard reacts too** — `WizardNPC.build_prompt` injects your completed quests into its
  prompt, so `talk` acknowledges your deeds.

Same shape as everything else: a deterministic resolver (pure, tested), content generated offline,
composed at runtime from authoritative flags.

## 8d. World materialization — the walkable layer
✅ **built** — `world/layout.py`, worldbible `--layout`, `WorldbibleRoom`, the `worldinit` builder command

The quest graph and reactive text describe *what* the world contains; this turns it into somewhere
you can actually **walk into**. Without it a fresh login lands in empty default Limbo with none of
the generated content wired in — the "we should bootstrap the dive better" gap.

- **Scene manifest** (`world/layout.py`): a `Layout` of `{start, rooms, exits, npcs}`. Either
  authored as `generated/worldbible_layout.json` (hand/LLM-tuned: canon-accurate exit directions,
  room names, NPC placement) **or** `derive_layout(wb)` — a deterministic, no-LLM fallback that
  builds a connected hub-and-spoke map (start room ↔ every other location) with the Wizard at the
  start. So *any* generated world is walkable even with no layout file.
- **Part of generation** (`worldbible.py --layout`, step `[7/7]`): every generation run emits the
  manifest. It **preserves** an existing authored layout (your hand edits survive a re-gen) and
  only derives one when absent; `--layout-only` re-derives on demand.
- **Materialized in-game** by the `worldinit` builder command (`commands/build_cmds.py`): creates a
  `WorldbibleRoom` per location, bidirectional exits, and the NPCs, then drops the builder in the
  start room with an in-character intro + command crib. **Idempotent** — every object is tagged
  (category `wb`) and reused on re-run, so it repairs/extends rather than duplicating.
- **Reactive rooms** (`WorldbibleRoom.get_display_desc`): plain `look` composes the same flag-gated
  location text as `survey`, so the room visibly evolves as you complete quests.

Still the golden rule: the layout only places *scenery*. Flags, win state, and progression stay in
the validated graph — `worldinit` never invents mechanics.

## 8e. Natural-language play — the LLM as a parser over state
✅ **built** — `world/interpreter.py`, `commands/play_cmds.py` (`CMD_NOMATCH` catch-all)

The play surface used to be engine-facing: the player typed `attempt investigate_keep = …`, naming
an internal quest token and filling a slot. That feels like operating the machine, not playing. This
inverts it — the player describes what they **do**, in plain words, and the LLM interprets *them*.

- **Catch-all** (`CmdInterpret`, keyed on `evennia.syscmdkeys.CMD_NOMATCH`): any input that isn't a
  known command becomes an in-world action. (So there are no more "Command not available" walls.)
- **Movement is deterministic** — `_match_exit` resolves "go south" / "head to the marsh" to a real
  exit in code, no LLM, instant. Only non-movement goes to the model.
- **One interpret call** (`world/interpreter.py`, run via `deferToThread`): given the player's text,
  their location + reactive description, the exits, and *the objectives available at this spot* (with
  secret solutions for judging), EVA returns `{kind, direction, quest_id, verdict, narration}`. It
  both **resolves which objective** the action targets (or none) **and judges** it (success / partial
  / fail) in a single pass — reusing puzzle_judge's HTTP/JSON plumbing.
- **State stays authoritative**: the command applies a success only through `worldbible_loader`
  (the quest's validated grants), so interpretation is flavorful but can never corrupt state. A
  no-target action is pure flavor (no change). Win/solvability are unaffected.
- **No dead ends, no commands**: after repeated failure on a needed objective the world opens an
  alternative on its own (the bypass, narrated from the puzzle's authored reward flavor — no extra
  reactor-blocking call). The old `quests/approach/attempt/hint/bypass/survey` commands remain as
  optional/advanced tools; normal play never needs them.

This is the answer to "AI Dungeon has no world model": the model proposes (intent + verdict), the
validated graph disposes (flags + win). Quest ids never reach the player.

## 8f. World-state objects — persistent, manipulable things
✅ **built** — `typeclasses/objects.py` (`WorldbibleObject`), layout `objects`, interpreter `ops`

The flag spine is coarse state; this is the *fine* state that makes it a world and not a 1-shot
prompt. A chest, a key, a cloak, a door are **real Evennia objects with saved state** — open a chest
and it *stays* open; take a key and it's in your pack across logins; wear a cloak (or put one on an
NPC) and it *stays* worn.

- **Objects** (`WorldbibleObject`): db flags `wb_takeable / wb_wearable / wb_container / wb_openable /
  wb_is_open / wb_locked / wb_key / wb_worn_by / wb_fixture`. `look` shows state ("an iron chest
  (closed)") and an open container reveals its contents.
- **Authored in the manifest** (`worldbible_layout.json` → `objects`): each entry is
  `{key, location, kind: item|wearable|container|fixture, desc, is_open?, locked?, key_item?,
  contains?[]}`. `worldinit` spawns them (nested container contents too), idempotently, tagged `wb`.
- **The interpreter emits `ops`** — `open/close/unlock/lock/take/drop/wear/remove/give/put/use` —
  against the things it's shown; `commands/play_cmds.py` applies them to real object state.
- **Closed feedback loop**: each turn the interpreter is fed THINGS HERE (+states), YOU CARRY, and
  WORN, so it never re-improvises an already-opened chest. That loop is the whole point — the world
  remembers.
- **Still authoritative**: the engine validates each op (a locked chest won't open without its key;
  a fixture won't be taken) and emits a corrective note if the model overreaches. Object play is
  free sandbox; quest flags/win remain the spine, and the two can advance in the same turn.

**Object-aware puzzles (the hybrid).** Generation co-designs objects *with* quests: the `[8/8]`
objects stage (`worldbible.py --objects`, `world/objgoal.py`) asks the author model for tangible
objects per location AND a per-quest `object_goal` — a checkable condition over object states (e.g.
`chest open AND amulet held`) that grounds the puzzle. It writes objects into the layout and merges
`object_goal`s into `worldbible_puzzles.json`, then **lints reachability** (the key for a locked chest
must exist and be takeable) so the generated mechanical path is solvable.

At runtime a quest completes by **either** path, both granting the same validated flags:
- **Mechanical** — `commands/play_cmds.py` snapshots object state after each turn's ops and, if a
  local quest's `object_goal` is satisfied, completes it (no matter how you got there).
- **Freeform** — the interpreter judges a clever in-world attempt as success.
And the stuck→bypass net still guarantees no dead end. `objgoal.evaluate`/`lint` are pure + unit-tested.

## 9. Design rule

> **Game *state* is authoritative in Evennia's DB. The LLM only *narrates and
> generates* — it never owns inventory, exits, HP, quest flags, or mechanics.**

Every feature here obeys it: images are cached state the LLM only *prompts*; the
worldbible is canon state the LLM *authors then must respect*; quest flags live in
the validated graph, not in the model's imagination. Keep this line and the project
stays coherent instead of hallucinating itself into chaos.

## 10. Operations

| Thing | Value |
|---|---|
| Project root | `~/llm-local/mud-wizard` |
| Python venv | `.venv` (3.12 — Evennia 6 breaks on 3.14) |
| LM Studio API | `http://127.0.0.1:1234/v1` |
| Draw Things API | `http://localhost:7860` (HTTP) — enable in-app: server-stack icon → API Server → Server Online, HTTP |
| Evennia web client | `http://localhost:4001` |
| Evennia telnet | `localhost:4000` |
| `lms` CLI | `~/.lmstudio/bin/lms` |

```bash
./mud.sh start|code|stop|restart|status   # the control script
lms ps                                     # what's loaded + CONTEXT column
lms server status                          # is the API up?
cd wizardmud && ../.venv/bin/evennia status|start|stop|reload
```

## 11. Troubleshooting / FAQ

**opencode: "tokens to keep … greater than context length"**
→ Coder loaded at 4096. See §5. `./mud.sh code` or set 32768 in LM Studio. Verify with `lms ps` (CONTEXT column).

**opencode: tool calls fail / it won't edit files**
→ Local models are flakier at tool-calling. Make sure you're on the *coder* model, not a tiny one. Try a smaller, simpler request to confirm the loop works.

**LM Studio won't launch ("can't run from this location")**
→ It must live in **/Applications**, not `~/Applications`. Move it there.

**Wizard NPC replies "…I was distracted. Can you repeat?"**
→ The bridge got an empty/error response. Checklist: is LM Studio server up
(`lms server status`)? Is the model id right (`lms ps` vs `LLM_MODEL`)? Watch the
server log (LM Studio **Developer** tab, or `lms log stream`). Turn on `DEBUG` in
settings to log the raw request/response.

**Evennia won't start / import errors**
→ Are you using the **3.12 venv** (`../.venv/bin/evennia`), not system Python 3.14?
Run from the `wizardmud/` dir. Check `evennia --log`.

**Nothing responds with WiFi off**
→ That's expected to *work* — everything is localhost. If it doesn't: the LM Studio
server isn't running (`./mud.sh start`) or no model is loaded (`lms ps`).

**Connection refused on :1234**
→ Server not started. `lms server start` or `./mud.sh start`.

**Out of memory / model won't load**
→ Unload others (`lms unload --all`), lower context, or load fewer models. Check
`lms ps` for what's resident. You have 128 GB but big context + multiple big
models adds up.

**Where are the logs?**
→ Evennia: `evennia --log` (and `server/logs/`). LM Studio: Developer tab or
`lms log stream`.

## 12. Glossary

- **Token** — a chunk of text (~¾ of a word) the model reads/writes. Limits and
  speeds are measured in tokens.
- **Context window / context length** — how many tokens the model can "see" at once
  (prompt + conversation + reply). Too small → the §5 error.
- **KV cache** — memory the model uses to hold the context while generating. Bigger
  context = more RAM used.
- **Parameters (B)** — model size in billions of weights. More ≈ more capable + bigger.
- **Dense vs MoE** — dense models use *all* parameters per token; **Mixture-of-Experts**
  (MoE) activates only a few "experts" (e.g. 3B of 35B), so it's faster for its size.
- **Quantization (Q4, 4-bit, etc.)** — compressing weights to fewer bits to shrink
  RAM use, with small quality loss. (`MLX` and `GGUF` are two quantized formats.)
- **MLX** — Apple's ML framework; fastest inference backend on Apple Silicon.
- **GGUF** — a popular cross-platform quantized model format (llama.cpp). MLX is
  the Apple-optimized alternative we prefer here.
- **Inference** — running a model to get output (as opposed to training it).
- **JIT load** — LM Studio auto-loading a model on first API request (with default
  settings, incl. the dreaded 4096 context). "Pinning" with `lms load` avoids this.
- **TTL** — time-to-live; how long an idle loaded model stays before auto-unloading.
- **System prompt** — hidden instructions sent before the user's message (opencode's
  is large — hence §5).
- **Tool calling / function calling** — the model emitting structured calls so an
  agent (opencode) can read/write files, run commands, etc.
- **OpenAI-compatible API** — the de-facto standard HTTP shape
  (`/v1/chat/completions`) that LM Studio mimics so generic tools work with it.
- **Telnet vs WebSocket** — telnet = the classic text MUD connection (`:4000`, text
  only). The web client (`:4001`) uses WebSockets and can show HTML/images.
- **MUD / MUSH** — Multi-User Dungeon / Shared Hallucination: text-based multiplayer
  worlds. Evennia builds these.
- **Typeclass** — Evennia's term for a game-object class (rooms, NPCs, items) backed
  by the database.
- **Cmdset** — a set of in-game commands available to a player/object (where `talk`,
  `inspect` get added).
- **Twisted / async / Deferred / inlineCallbacks** — Evennia's networking is
  asynchronous (Twisted). LLM calls return a `Deferred` (a future result) so a slow
  model doesn't freeze the game.
- **Diffusion model** — the kind of model that generates images (FLUX, SDXL). Runs in
  Draw Things, separate from the text LLMs.
- **Turbo / Schnell models** — fast, few-step diffusion variants (seconds per image)
  vs higher-quality slow ones (FLUX-dev).
- **chafa / sixel / ANSI art** — ways to render images as text/blocks in a terminal,
  for the telnet players who can't see real images.
- **Soft-lock** — a game state where you can no longer win but the game doesn't end
  (e.g. you used the only key on the wrong door). The validator (§8) prevents these.
- **DAG** — Directed Acyclic Graph; the shape of the quest dependency structure.
- **Reachability** — can you get from the start state to the win state? The core
  solvability check.
