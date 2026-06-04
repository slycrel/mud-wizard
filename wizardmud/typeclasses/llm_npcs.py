"""
LLM-backed NPC typeclasses for wizardmud.

- ``OpenAINPC``  : base NPC that talks to LM Studio via OpenAIChatLLMClient.
- ``WizardNPC``  : the world-building "game master" (bigger model, GM prompt).
- ``ChatterNPC`` : lightweight ambient dialogue on the small/fast model.

Create one in-game (as a builder)::

    create/drop The Wizard:typeclasses.llm_npcs.WizardNPC
    talk The Wizard = where am I?

Per-instance model override (no code change needed)::

    set The Wizard/llm_model = qwen3.6-35b-a3b

IMPORTANT: the ``llm_model`` strings below must match the model ids LM Studio
reports (check ``lms ps`` or the LM Studio server log). They are best-guess
placeholders — fix them once your models are downloaded.
"""

import re

from evennia.contrib.rpg.llm.llm_npc import LLMNPC

from typeclasses.llm_openai_client import OpenAIChatLLMClient


def _section(md: str, heading: str) -> str:
    """Pull the prose under a '## <heading>' section of the worldbible markdown,
    whitespace-collapsed. Returns '' if not found."""
    if not md:
        return ""
    m = re.search(rf"^#+\s*{re.escape(heading)}\s*$(.*?)(?=^#+\s|\Z)", md, re.M | re.S)
    return " ".join(m.group(1).split()) if m else ""


class OpenAINPC(LLMNPC):
    """LLMNPC that uses the OpenAI-compatible client and a per-NPC model."""

    # Override per-class below; or set ``npc.db.llm_model`` per-instance in-game.
    llm_model = None

    @property
    def llm_client(self):
        if not self.ndb.llm_client:
            model = self.db.llm_model or self.llm_model
            self.ndb.llm_client = OpenAIChatLLMClient(model=model)
        return self.ndb.llm_client


class WizardNPC(OpenAINPC):
    """The game-master wizard: richer model, narrates and improvises the world.

    Design rule: the wizard NARRATES, it does not own state. Never let it
    invent inventory, exits, or mechanics — Evennia's DB is the source of truth.
    """

    llm_model = "eva-qwen2.5-32b-v0.2-mlx"  # the live in-character wizard (RP-tuned)

    prompt_prefix = (
        "You are the Wizard — the game master and guide of THIS specific dark-fantasy text "
        "adventure, speaking with {character} in {location}. Stay in character: wise, weathered, "
        "a little mischievous. Keep replies to 2-4 vivid sentences. Never break character and "
        "never mention being an AI. CRUCIAL: the facts below are the only truth of this world — "
        "do NOT invent places, exits, objects, items, NPCs, or mechanics that are not given to "
        "you. If asked about something that does not exist here, do not pretend it does; instead "
        "draw the seeker's attention, in character, to what truly surrounds them. Do not prefix "
        "your reply with your name."
    )

    def build_prompt(self, character, speech):
        """Ground the Wizard in the validated worldbible — its setting, the player's current
        reactive surroundings, the quests open to them, and their deeds — so it speaks about
        THIS world (not generic fantasy) and steers the player to the real commands. The facts
        are appended last (closest to generation) for strongest adherence. State stays
        authoritative in code; this only narrates."""
        prompt = super().build_prompt(character, speech)
        brief = self._world_brief(character)
        return f"{prompt}\n\n[The truth of this world — speak only of this:]\n{brief}" if brief else prompt

    def _world_brief(self, character):
        try:
            from commands.quest_cmds import get_wb
            from world.worldbible_loader import Progress
            wb = get_wb()
        except Exception:
            return ""
        p = Progress.from_dict(character.attributes.get("progress", default=None))
        if not p.flags and not p.done:
            p.flags = set(wb.world.start)

        lines = []
        setting = _section(wb.canon, "Tone & Setting")
        if setting:
            lines.append("SETTING: " + setting)

        loc = getattr(getattr(character, "location", None), "db", None)
        wb_loc = loc.wb_location if loc else None
        if wb_loc:
            txt = wb.location_text(wb_loc, p)
            if txt:
                lines.append(f"WHERE THEY STAND ({character.location.key}): {txt}")

        avail = wb.available(p)
        if avail:
            lines.append("OPEN TO THEM NOW (guide toward these): "
                         + "; ".join(f"{q.id} — {q.summary}" for q in avail[:5]))
        if p.done:
            lines.append("ALREADY ACCOMPLISHED (acknowledge if it fits): " + ", ".join(sorted(p.done)))

        lines.append(
            "HOW THEY ACT: the seeker progresses with the commands 'quests', 'approach <quest>', "
            "'attempt <quest> = <what they do>', 'hint <quest>', 'survey', 'look', and by walking "
            "the exits. They cannot 'take' or 'use' arbitrary things — when they seem lost, nudge "
            "them (in character) toward a quest or one of these commands. Do not output pipe (|) "
            "characters."
        )
        return "\n".join(lines)


class ChatterNPC(OpenAINPC):
    """Ambient NPC for quick, in-character small talk on the small model."""

    llm_model = "google/gemma-4-e4b"  # confirmed present in LM Studio

    prompt_prefix = (
        "You are roleplaying as {name}, a {desc} in {location}. "
        "Answer in one or two short, natural sentences. "
        "Only respond as {name} would; never mention being an AI. "
        "From here on, the conversation between {name} and {character} begins."
    )
