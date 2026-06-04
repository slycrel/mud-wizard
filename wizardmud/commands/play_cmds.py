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
            exits=exits, quests_with_puzzles=cand, objects_block=self._objects_block(caller),
            canon=wb.canon, rating=_rating())
        d.addCallback(self._resolve)
        d.addErrback(lambda f: caller.msg("The world blurs for a moment. Try putting it another way."))

    # --- object world-state helpers ------------------------------------
    @staticmethod
    def _is_wb_obj(o):
        return o.is_typeclass("typeclasses.objects.WorldbibleObject", exact=False)

    def _objects_block(self, caller):
        """The feedback loop: tell the interpreter the CURRENT state of things here, what the
        player carries, and what's worn — so it never re-improvises an already-opened chest."""
        loc = caller.location
        lines = []
        here = [o for o in (loc.contents if loc else []) if self._is_wb_obj(o)]
        if here:
            lines.append("THINGS HERE:")
            for o in here:
                state = []
                if o.db.wb_openable:
                    state.append("locked" if o.db.wb_locked else ("open" if o.db.wb_is_open else "closed"))
                if o.db.wb_takeable:
                    state.append("can be taken")
                if o.db.wb_wearable:
                    state.append("wearable")
                tag = f" ({', '.join(state)})" if state else ""
                lines.append(f"  - {o.key}{tag}")
                if o.db.wb_container and o.db.wb_is_open:
                    inside = [c.key for c in o.contents]
                    lines.append(f"      inside: {', '.join(inside) if inside else 'empty'}")
        carried = [o for o in caller.contents if self._is_wb_obj(o) and not o.db.wb_worn_by]
        if carried:
            lines.append("YOU CARRY: " + ", ".join(o.key for o in carried))
        worn = []
        for holder in ([caller] + [c for c in (loc.contents if loc else []) if c != caller]):
            for o in holder.contents:
                if self._is_wb_obj(o) and o.db.wb_worn_by:
                    who = "you" if holder == caller else holder.key
                    worn.append(f"{o.key} (worn by {who})")
        if worn:
            lines.append("WORN: " + ", ".join(worn))
        return "\n".join(lines)

    def _find_thing(self, name, caller):
        """Resolve a thing the interpreter named: room objects, open-container contents, and
        what the player carries/wears. Exact key match wins, else substring."""
        loc = caller.location
        pool = []
        for o in (loc.contents if loc else []):
            if self._is_wb_obj(o):
                pool.append(o)
                if o.db.wb_container and o.db.wb_is_open:
                    pool.extend(c for c in o.contents if self._is_wb_obj(c))
        pool.extend(o for o in caller.contents if self._is_wb_obj(o))
        n = (name or "").strip().lower()
        if not n:
            return None
        for o in pool:
            if o.key.lower() == n:
                return o
        for o in pool:
            if n in o.key.lower() or o.key.lower() in n:
                return o
        return None

    def _find_actor(self, name, caller):
        """Resolve who receives/wears something: the player (self/none) or an NPC in the room."""
        loc = caller.location
        n = (name or "").strip().lower()
        if not n or n in ("me", "myself", "self", "i"):
            return caller
        for o in (loc.contents if loc else []):
            if o != caller and not self._is_wb_obj(o) and not o.destination:
                if o.key.lower() == n or n in o.key.lower() or o.key.lower() in n:
                    return o
        return caller

    def _apply_ops(self, caller, ops):
        """Apply interpreter ops to real, persistent object state. Returns corrective notes."""
        loc = caller.location
        notes = []
        for o in ops:
            target = self._find_thing(o["target"], caller)
            if not target:
                continue
            op = o["op"]
            if op in ("open", "close"):
                if not target.db.wb_openable:
                    continue
                if op == "open":
                    if target.db.wb_locked:
                        notes.append(f"The {target.key} is locked.")
                        continue
                    target.db.wb_is_open = True
                else:
                    target.db.wb_is_open = False
            elif op == "unlock":
                keyname = target.db.wb_key
                has_key = (not keyname) or any(
                    keyname.lower() in it.key.lower() for it in caller.contents if self._is_wb_obj(it))
                if has_key:
                    target.db.wb_locked = False
                else:
                    notes.append(f"The {target.key} needs a key you don't carry.")
            elif op == "lock":
                target.db.wb_locked = True
            elif op == "take":
                if not target.db.wb_takeable:
                    notes.append(f"The {target.key} won't come loose.")
                    continue
                target.move_to(caller, quiet=True, move_type="get")
            elif op == "drop":
                if target.location == caller:
                    target.db.wb_worn_by = None
                    target.move_to(loc, quiet=True, move_type="drop")
            elif op == "wear":
                if not target.db.wb_wearable:
                    continue
                wearer = self._find_actor(o.get("recipient"), caller)
                if target.location != wearer:
                    target.move_to(wearer, quiet=True, move_type="get")
                target.db.wb_worn_by = wearer
            elif op == "remove":
                target.db.wb_worn_by = None
            elif op == "give":
                who = self._find_actor(o.get("recipient"), caller)
                if who and who != caller:
                    target.db.wb_worn_by = None
                    target.move_to(who, quiet=True, move_type="give")
            elif op == "put":
                cont = self._find_thing(o.get("container") or "", caller)
                if cont and cont.db.wb_container and cont.db.wb_is_open and cont != target:
                    target.db.wb_worn_by = None
                    target.move_to(cont, quiet=True, move_type="drop")
            # "use": no state change beyond narration
        return notes

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
        # apply persistent object-state changes BEFORE narrating the outcome
        notes = self._apply_ops(caller, result.get("ops") or [])
        caller.msg(f"|y{narration}|n")
        for note in notes:
            caller.msg(f"|x{note}|n")
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
