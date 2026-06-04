"""
Natural-language play — the primary way you interact with the world.

Registered on the catch-all `CMD_NOMATCH` key: anything the player types that isn't a
known command lands here and becomes an in-world action. You write what you *do*
("search the collapsed library for a hidden way", "decipher the storm-runes", "head down
to the marsh") and the game figures out the rest. No quest ids, no slots to fill.

Flow:
  1. Movement is resolved deterministically in code (no LLM, instant) — "go south",
     "head to the marsh", etc. map to a real exit.
  2. Everything else goes to world/interpreter.py (one local LLM call, run off the reactor
     via deferToThread): it picks which objective *available at this spot* your action
     pursues (or none) and judges it. The validated quest graph then applies the result, so
     a success only ever grants already-proven flags — play can never reach a dead end.

The old explicit `quests/approach/attempt/...` commands still exist as optional/advanced
tools, but you never need them.
"""

from evennia import Command, syscmdkeys
from twisted.internet import threads

from world import interpreter as interp
from world import puzzle_judge
from commands.quest_cmds import get_wb, get_progress, save_progress, _rating

_MOVE_VERBS = {"go", "walk", "head", "move", "travel", "run", "wander", "proceed", "venture"}


class CmdInterpret(Command):
    """Turn free-form input into a single in-world action (catch-all)."""

    key = syscmdkeys.CMD_NOMATCH
    locks = "cmd:all()"

    def func(self):
        caller = self.caller
        raw = (self.raw_string or self.args or "").strip()
        if not raw:
            return
        loc = caller.location

        # 1) deterministic movement — no LLM
        if loc:
            ex = self._match_exit(raw, loc)
            if ex:
                caller.execute_cmd(ex.key)
                return

        # 2) interpret everything else against the objectives available *here*
        wb, p = get_wb(), get_progress(caller)
        save_progress(caller, p)
        wb_loc = getattr(loc.db, "wb_location", None) if loc else None
        cand = [(q, wb.puzzle(q.id)) for q in wb.available(p)
                if not wb_loc or q.location == wb_loc]
        exits = [e.key for e in loc.exits] if loc else []
        desc = (wb.location_text(wb_loc, p) if wb_loc else "") or (loc.db.desc if loc else "") or ""

        caller.msg("|x( the world takes a breath... )|n")
        d = threads.deferToThread(
            interp.interpret, raw,
            location=(loc.key if loc else "nowhere"), location_desc=desc,
            exits=exits, quests_with_puzzles=cand, canon=wb.canon, rating=_rating())
        d.addCallback(self._resolve)
        d.addErrback(lambda f: caller.msg("The world blurs for a moment. Try putting it another way."))

    def _match_exit(self, raw, loc):
        """Map 'go south' / 'head to the marsh' / 'the whispering marsh' to a real exit."""
        words = raw.lower().strip(" .!,").split()
        if words and words[0] in _MOVE_VERBS:
            words = words[1:]
        if words and words[0] in ("to", "into", "toward", "towards", "the"):
            # allow "go to the marsh" -> drop filler
            words = [w for w in words if w not in ("to", "into", "toward", "towards", "the")]
        phrase = " ".join(words).strip()
        if not phrase:
            return None
        for e in loc.exits:
            names = {e.key.lower(), *[a.lower() for a in e.aliases.all()]}
            if phrase in names:
                return e
            if e.destination:
                dest = e.destination.key.lower()
                if phrase in dest or any(w in dest.split() for w in phrase.split()):
                    return e
        return None

    def _resolve(self, result):
        caller = self.caller
        kind, narration = result["kind"], result["narration"]

        if kind == "look":
            caller.execute_cmd("look")
            return
        if kind == "move" and result["direction"] and caller.location:
            ex = self._match_exit(result["direction"], caller.location)
            if ex:
                caller.execute_cmd(ex.key)
                return

        wb, p = get_wb(), get_progress(caller)
        caller.msg(f"|y{narration}|n")
        qid, verdict = result.get("quest_id"), result.get("verdict")
        if kind == "action" and qid and wb.quest(qid) and qid not in p.done:
            q = wb.quest(qid)
            if verdict == "success" and (q.requires <= p.flags):
                wb.complete(p, qid, via="action")
                caller.msg("|g  ~ something shifts; the way forward widens ~|n")
            elif verdict == "fail":
                wb.record_fail(p, qid)
                if wb.bypass_available(p, qid):
                    # guaranteed no-dead-end: after repeated failure, an alternative opens.
                    # Use the puzzle's authored reward flavor (no extra LLM call on the reactor).
                    alt = wb.puzzle(qid).get("reward_flavor") or \
                        "A chance turn of events carries you past the impasse."
                    wb.complete(p, qid, via="bypass")
                    caller.msg(f"|c{alt}|n\n|g  ~ aided by fortune, you press on ~|n")
            # 'partial' → no state change; narration already signals you're close
            if wb.is_won(p):
                caller.msg("|gThe tale reaches its end — you have decided the keep's fate.|n")
        save_progress(caller, p)
