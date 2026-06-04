# ✈️ Pre-flight checklist — local LLM MUD

Everything here must pass **on the ground, with WiFi**, before you trust it at 35,000 ft.
The whole point is air-gapped operation, so the last step is a real WiFi-off test.

Project root: `~/llm-local/mud-wizard`
Venv (Python 3.12): `.venv/` — activate with `source .venv/bin/activate`

---

## 1. Download the models (WiFi required — the long pole)

```bash
open -a "LM Studio"                 # run once to finish first-launch setup
~/.lmstudio/bin/lms bootstrap       # if `lms` isn't on PATH yet, then reopen shell
bash download-models.sh             # edit ids if any fail to resolve
lms ls                              # confirm all three are present
```

Lineup — ✅ ALL DOWNLOADED & VERIFIED (real LM Studio ids, already in all configs):
- **Coder** → `qwen/qwen3.6-27b` (MLX, 16.08 GB)
- **Wizard / GM** → `qwen/qwen3.6-35b-a3b` (MLX MoE, 20.43 GB)
- **NPC chatter** → `google/gemma-4-e4b` (6.86 GB, supports tool calling)
- MLX runtime (`mlx-llm-mac-arm64`) installed automatically.

> ✅ The Evennia bridge was tested live against LM Studio and returned a
> correct in-character reply, so the inference path is proven. What still
> needs YOUR test: opencode tool-calling (§3) and the WiFi-off run (§5).

## 2. Start the LM Studio server + raise context

- LM Studio → **Developer** tab → **Start Server** (port **1234**), or `lms server start`.
- For each model's load config, set **context length ≥ 16K** (you have the RAM — go higher for the coder). Enable tool use for the coder model.
- Sanity ping:
  ```bash
  curl -s http://127.0.0.1:1234/v1/models | head
  ```

## 3. opencode (the coding TUI)

```bash
cd ~/llm-local/mud-wizard
opencode            # picks up ./opencode.json -> lmstudio/qwen3.6-27b
```
- Ask it to make a trivial edit and **confirm tool-calling works** (read/write a file).
  If tool calls misbehave, switch the default model to a Coder-tuned variant.

## 4. Evennia (the game server)

```bash
source .venv/bin/activate
cd wizardmud
evennia migrate                      # already validated during setup
evennia start                        # create the superuser when prompted
```
- Web client: <http://localhost:4001>   ·   Telnet/MUD client: `localhost:4000`
- Spawn the wizard and talk to it:
  ```
  create/drop The Wizard:typeclasses.llm_npcs.WizardNPC
  talk The Wizard = where am I?
  ```
- Spawn an ambient NPC on the small model:
  ```
  create/drop Old Gus:typeclasses.llm_npcs.ChatterNPC
  set Old Gus/desc = a grizzled tavern keeper
  talk Old Gus = got any rumors?
  ```

## 5. 🔌 THE REAL TEST — turn WiFi OFF and repeat 3 & 4

If opencode still edits code and the Wizard still answers with WiFi disabled,
you're flight-ready. If anything reaches for the network, fix it now.

## 6. Battery / thermal sanity

Run a ~20 min opencode session + the game on battery. Note drain rate and fan
noise so there are no surprises over the Pacific. Grab a seat with power.

---

## Quick reference

| Thing | Value |
|---|---|
| LM Studio server | `http://127.0.0.1:1234/v1` |
| opencode default model | `lmstudio/qwen/qwen3.6-27b` |
| Evennia web client | `http://localhost:4001` |
| Evennia telnet | `localhost:4000` |
| LLM bridge | `wizardmud/typeclasses/llm_openai_client.py` |
| NPC typeclasses | `wizardmud/typeclasses/llm_npcs.py` |
| LLM settings | `wizardmud/server/conf/settings.py` (LLM_* block) |

**Design rule that keeps the project sane:** game *state* lives in Evennia's DB.
The LLM *narrates and generates* — it never owns inventory, exits, or mechanics.
