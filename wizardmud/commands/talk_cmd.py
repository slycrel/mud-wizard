"""
`talk` — converse with an LLM-backed NPC (the Wizard, ambient NPCs).

Replaces Evennia's contrib `CmdLLMTalk` only to fix its argument parsing: the contrib
splits on ``=`` solely when the literal substring ``"s="`` appears, otherwise it splits on
the first space — so a multi-word name like "The Wizard" gets mangled and stray ``=`` text
leaks into the message. This version splits on ``=`` whenever present (the reliable form for
multi-word names) and falls back to the first space. All the actual LLM behaviour is
inherited unchanged.
"""

from evennia.contrib.rpg.llm.llm_npc import CmdLLMTalk


class CmdTalk(CmdLLMTalk):
    """
    Talk to an NPC.

    Usage:
        talk <npc> = <something>     (reliable, especially for multi-word names)
        talk <npc> <something>

    Example:
        talk The Wizard = where am I, and what should I do?
    """

    def parse(self):
        args = self.args.strip()
        if "=" in args:
            name, _, speech = args.partition("=")
        else:
            name, _, speech = args.partition(" ")
        self.target_name = name.strip()
        self.speech = speech.strip()
