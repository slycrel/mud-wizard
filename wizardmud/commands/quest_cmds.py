"""
Live quest/puzzle commands — the playable surface of the worldbible.

Wires the runtime loader (world.worldbible_loader) + the hybrid judge
(world.puzzle_judge) onto characters:

    quests                       list what you can pursue right now
    approach <quest>             face a quest's challenge (see the puzzle)
    hint <quest>                 ask the Wizard for the next hint
    attempt <quest> = <action>   try to solve it — the Wizard judges (hybrid)
    bypass <quest>               take the Wizard's guaranteed alternative (only when stuck)

State lives on `caller.db.progress` (a Progress dict). The LLM only narrates/judges;
flags are applied here from the quest's *validated* grants, so play can never reach an
unwinnable state. LLM calls run via deferToThread so a slow model never freezes the MUD.
"""

from django.conf import settings
from evennia import Command
from twisted.internet import threads

from world.worldbible_loader import Worldbible, Progress
from world import puzzle_judge

_WB = None


def get_wb():
    global _WB
    if _WB is None:
        _WB = Worldbible.load()
    return _WB


def get_progress(caller):
    p = Progress.from_dict(caller.attributes.get("progress", default=None))
    if not p.flags and not p.done:
        p.flags = set(get_wb().world.start)  # seed the start flags on first play
    return p


def save_progress(caller, p):
    caller.db.progress = p.to_dict()


def _rating():
    return getattr(settings, "CONTENT_RATING", "mature")


class CmdQuests(Command):
    """
    Show the quests you can pursue, what you've finished, and bonus objectives.

    Usage:
        quests
    """

    key = "quests"
    aliases = ["journal"]
    locks = "cmd:all()"

    def func(self):
        wb, p = get_wb(), get_progress(self.caller)
        save_progress(self.caller, p)  # persist seeded start flags
        out = ["|wQuests you can pursue:|n"]
        avail = wb.available(p)
        if not avail:
            out.append("  (nothing right now — finish what you've started, or explore)")
        for q in avail:
            tag = "|x[side]|n " if q.optional else ""
            out.append(f"  - {tag}|c{q.id}|n ({q.location}): {q.summary}")
        out.append(f"|wCompleted:|n {len(wb.completed(p))} quest(s)")
        bonus = wb.bonus_achieved(p)
        if bonus:
            out.append(f"|wBonus objectives reached:|n {', '.join(bonus)}")
        if wb.is_won(p):
            out.append("|gYou have achieved the goal — the tale is won.|n")
        self.caller.msg("\n".join(out))


class CmdApproach(Command):
    """
    Approach a quest to face its challenge.

    Usage:
        approach <quest>
    """

    key = "approach"
    locks = "cmd:all()"

    def func(self):
        wb, p = get_wb(), get_progress(self.caller)
        qid = self.args.strip()
        q = wb.quest(qid)
        if not q:
            self.caller.msg("No such quest. Try |wquests|n.")
            return
        if qid in p.done:
            self.caller.msg("You've already completed that one.")
            return
        if not (q.requires <= p.flags):
            self.caller.msg("You're not ready for that yet — other things must come first.")
            return
        pz = wb.puzzle(qid)
        self.caller.msg(
            f"|y{pz.get('setup', q.summary)}|n\n\n{pz.get('challenge', '')}\n\n"
            f"(|wattempt {qid} = <what you do>|n  ·  stuck? |whint {qid}|n)"
        )


class CmdHint(Command):
    """
    Ask the Wizard for a hint on a quest.

    Usage:
        hint <quest>
    """

    key = "hint"
    locks = "cmd:all()"

    def func(self):
        wb, p = get_wb(), get_progress(self.caller)
        qid = self.args.strip()
        if not wb.quest(qid):
            self.caller.msg("No such quest.")
            return
        hint, remaining = wb.next_hint(p, qid)
        save_progress(self.caller, p)
        if hint:
            tail = f"  ({remaining} more)" if remaining else ""
            self.caller.msg(f"|cThe Wizard murmurs:|n {hint}{tail}")
        elif wb.bypass_available(p, qid):
            self.caller.msg(
                "|cThe Wizard senses your struggle and speaks of another path.|n "
                f"Type |wbypass {qid}|n to take it."
            )
        else:
            self.caller.msg(f"The Wizard has no more hints — keep trying. (|wattempt {qid} = ...|n)")


class CmdAttempt(Command):
    """
    Attempt to overcome a quest's challenge. The Wizard judges your approach —
    the right idea, or a clever alternative, will do.

    Usage:
        attempt <quest> = <what you do>
    """

    key = "attempt"
    locks = "cmd:all()"

    def parse(self):
        qid, _, what = self.args.partition("=")
        self.qid, self.what = qid.strip(), what.strip()

    def func(self):
        wb, p = get_wb(), get_progress(self.caller)
        q = wb.quest(self.qid)
        if not q:
            self.caller.msg("No such quest. Try |wquests|n.")
            return
        if self.qid in p.done:
            self.caller.msg("You've already completed that one.")
            return
        if not (q.requires <= p.flags):
            self.caller.msg("You're not ready for that yet.")
            return
        if not self.what:
            self.caller.msg("Attempt what? Usage: |wattempt <quest> = <what you do>|n")
            return
        self.caller.msg("|xThe Wizard watches your attempt...|n")
        pz, canon = wb.puzzle(self.qid), wb.canon
        d = threads.deferToThread(puzzle_judge.judge_attempt, pz, self.what, canon, _rating())
        d.addCallback(self._resolve)
        d.addErrback(lambda f: self.caller.msg("The Wizard is distracted; try again."))

    def _resolve(self, verdict):
        wb, p = get_wb(), get_progress(self.caller)
        self.caller.msg(f"|y{verdict.get('narration', '')}|n")
        v = verdict.get("verdict")
        if v == "success":
            wb.complete(p, self.qid, via="solved")
            self.caller.msg(f"|g✓ '{self.qid}' complete.|n")
            if wb.is_won(p):
                self.caller.msg("|gYou have achieved the goal — the tale is won.|n")
        elif v == "fail":
            wb.record_fail(p, self.qid)
            if wb.bypass_available(p, self.qid):
                self.caller.msg(
                    f"|cThe Wizard, sensing your struggle, hints at another path:|n |wbypass {self.qid}|n"
                )
        # 'partial' = on the right track; no completion, no fail recorded
        save_progress(self.caller, p)


class CmdBypass(Command):
    """
    Take the Wizard's guaranteed alternative route past a quest you're stuck on.
    Only offered after you've tried and exhausted your hints.

    Usage:
        bypass <quest>
    """

    key = "bypass"
    locks = "cmd:all()"

    def func(self):
        wb, p = get_wb(), get_progress(self.caller)
        qid = self.args.strip()
        q = wb.quest(qid)
        if not q:
            self.caller.msg("No such quest.")
            return
        if qid in p.done:
            self.caller.msg("You've already completed that one.")
            return
        if not wb.bypass_available(p, qid):
            self.caller.msg("The Wizard offers no shortcut yet — try the challenge and your hints first.")
            return
        self.caller.msg("|xThe Wizard weaves an alternative...|n")
        pz, canon = wb.puzzle(qid), wb.canon
        d = threads.deferToThread(puzzle_judge.narrate_alternative, pz, canon, _rating())
        d.addCallback(self._resolve, qid)
        d.addErrback(lambda f, qid=qid: self._resolve(
            wb.puzzle(qid).get("reward_flavor", "You find another way through."), qid))

    def _resolve(self, narration, qid):
        wb, p = get_wb(), get_progress(self.caller)
        wb.complete(p, qid, via="bypass")
        save_progress(self.caller, p)
        self.caller.msg(f"|y{narration}|n\n|g✓ '{qid}' resolved by another path.|n")
        if wb.is_won(p):
            self.caller.msg("|gYou have achieved the goal — the tale is won.|n")


class CmdSurvey(Command):
    """
    Take in your surroundings — the world reflects what you've done so far.

    Usage:
        survey [<location>]      (no arg uses your room's tagged location)
    """

    key = "survey"
    aliases = ["recap"]
    locks = "cmd:all()"

    def func(self):
        wb, p = get_wb(), get_progress(self.caller)
        loc = self.args.strip()
        if not loc and self.caller.location:
            loc = self.caller.location.db.wb_location or ""
        if not loc:
            locs = sorted(wb.reactions.keys())
            self.caller.msg("Survey where? Known locations: "
                            + (", ".join(locs) if locs else "(none generated yet)"))
            return
        text = wb.location_text(loc, p)
        if not text:
            self.caller.msg(f"You sense nothing special about '{loc}'.")
            return
        self.caller.msg(f"|y{text}|n")
