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

from evennia.contrib.rpg.llm.llm_npc import LLMNPC

from typeclasses.llm_openai_client import OpenAIChatLLMClient


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
        "You are the Wizard, the game master of a text adventure world. "
        "You are speaking with {character}, who currently stands in {location}. "
        "Stay in character as a wise, slightly mischievous wizard. "
        "Describe places and events vividly but in 2-4 sentences. "
        "Never break character, never mention being an AI, and never invent "
        "game mechanics, exits, or inventory the player does not actually have. "
        "From here on, the conversation between the Wizard and {character} begins."
    )

    def build_prompt(self, character, speech):
        """Inject the player's accomplishments so the Wizard reacts to their deeds
        (the Galatea/Fallen-London 'world acknowledges what you've done' pattern)."""
        prompt = super().build_prompt(character, speech)
        try:
            from world.worldbible_loader import Progress
            p = Progress.from_dict(character.attributes.get("progress", default=None))
            if p.done:
                deeds = ", ".join(sorted(p.done))
                prompt = (f"(The one you speak with has accomplished: {deeds}. "
                          "Acknowledge their deeds naturally if it fits.)\n" + prompt)
        except Exception:
            pass
        return prompt


class ChatterNPC(OpenAINPC):
    """Ambient NPC for quick, in-character small talk on the small model."""

    llm_model = "google/gemma-4-e4b"  # confirmed present in LM Studio

    prompt_prefix = (
        "You are roleplaying as {name}, a {desc} in {location}. "
        "Answer in one or two short, natural sentences. "
        "Only respond as {name} would; never mention being an AI. "
        "From here on, the conversation between {name} and {character} begins."
    )
