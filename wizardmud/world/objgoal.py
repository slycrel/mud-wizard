"""
Object-goal conditions — the bridge from manipulable objects to quest completion.

This is the "object-aware puzzle" half of the hybrid. A puzzle MAY carry an optional
``object_goal``: a checkable condition over object STATES that, when satisfied, completes the
quest *mechanically* (the command layer then applies the quest's validated flags). A clever
freeform action still completes the same quest via the LLM judge — both paths grant the SAME
flags, so neither can break solvability, and the stuck→bypass safety net still guarantees no
dead end.

Condition shape (stored per-quest in worldbible_puzzles.json):

    "object_goal": {
      "all": [                                  # every clause; use "any" for OR
        {"obj": "iron chest", "is": "open"},     # is ∈ open|closed|locked|unlocked
        {"obj": "tarnished amulet", "is": "held"}#        held|worn|in_room|taken|exists
      ]
    }

Pure & deterministic (no Evennia, no LLM): `evaluate(goal, snapshot)` runs against a snapshot
dict the command layer builds from live objects, so it unit-tests standalone. `lint(...)`
statically checks a goal is even achievable (objects exist; locked things have a reachable
key), which is what keeps the generated mechanical path solvable.
"""

from __future__ import annotations

_STATES = {"open", "closed", "locked", "unlocked", "held", "worn", "in_room", "taken", "exists"}
# "taken" is an alias of "held"; "closed"/"unlocked" are negations.


def _match(name, snapshot):
    """Find a snapshot entry for a thing the goal names (exact key, else substring)."""
    n = (name or "").strip().lower()
    if not n:
        return None
    if n in snapshot:
        return snapshot[n]
    for key, st in snapshot.items():
        if n in key or key in n:
            return st
    return None


def _clause_holds(clause, snapshot) -> bool:
    if not isinstance(clause, dict):
        return False
    st = _match(clause.get("obj"), snapshot)
    if not st or not st.get("exists"):
        return False
    want = str(clause.get("is", "exists")).lower().strip()
    if want == "open":
        return bool(st.get("open"))
    if want == "closed":
        return not st.get("open")
    if want == "locked":
        return bool(st.get("locked"))
    if want == "unlocked":
        return not st.get("locked")
    if want in ("held", "taken"):
        return bool(st.get("held"))
    if want == "worn":
        return bool(st.get("worn"))
    if want == "in_room":
        return bool(st.get("in_room"))
    if want == "exists":
        return True
    return False


def evaluate(goal, snapshot) -> bool:
    """True if `goal` is satisfied by `snapshot`. Empty/None goal is never satisfied
    (a quest with no object_goal is completed only by the LLM judge)."""
    if not goal or not isinstance(goal, dict):
        return False
    if "all" in goal:
        clauses = goal["all"]
        return bool(clauses) and all(_clause_holds(c, snapshot) for c in clauses)
    if "any" in goal:
        return any(_clause_holds(c, snapshot) for c in goal["any"])
    # a bare single clause is also accepted
    return _clause_holds(goal, snapshot)


def referenced_objects(goal) -> list:
    """Object names a goal mentions (for linting)."""
    if not isinstance(goal, dict):
        return []
    clauses = goal.get("all") or goal.get("any") or ([goal] if "obj" in goal else [])
    return [c.get("obj") for c in clauses if isinstance(c, dict) and c.get("obj")]


def _flatten_objects(objects_manifest):
    """Flatten the layout 'objects' list (incl. nested container contents) into
    {name_lower: {takeable, wearable, container, locked, key_item}}."""
    out = {}

    def add(spec):
        kind = spec.get("kind", "item")
        out[spec.get("key", "").lower()] = {
            "takeable": kind in ("item", "wearable"),
            "wearable": kind == "wearable",
            "container": kind == "container",
            "locked": bool(spec.get("locked")),
            "key_item": (spec.get("key_item") or "").lower(),
        }
        for child in spec.get("contains", []):
            add(child)

    for spec in objects_manifest or []:
        add(spec)
    return out


def lint(goals_by_quest, objects_manifest) -> list:
    """Static reachability check: every object a goal names must exist; anything required
    'held'/'worn'/'taken' must be takeable; any container required 'open' that is locked must
    have a key_item that itself exists and is takeable. Returns human-readable warnings.

    This is what keeps a *generated* object-puzzle solvable — if it fails, the freeform judge
    and the stuck-bypass still prevent dead ends, but the intended mechanical path is broken."""
    known = _flatten_objects(objects_manifest)

    def find(name):
        n = (name or "").lower()
        if n in known:
            return known[n]
        for k, v in known.items():
            if n in k or k in n:
                return v
        return None

    warns = []
    for qid, goal in (goals_by_quest or {}).items():
        clauses = goal.get("all") or goal.get("any") or ([goal] if "obj" in goal else [])
        for c in clauses:
            if not isinstance(c, dict):
                continue
            obj, want = c.get("obj"), str(c.get("is", "exists")).lower()
            meta = find(obj)
            if not meta:
                warns.append(f"{qid}: object_goal references unknown object '{obj}'")
                continue
            if want in ("held", "worn", "taken") and not meta["takeable"]:
                warns.append(f"{qid}: needs '{obj}' {want}, but it is not takeable")
            if want == "worn" and not meta["wearable"]:
                warns.append(f"{qid}: needs '{obj}' worn, but it is not wearable")
            if want == "open" and meta["container"] and meta["locked"]:
                keyitem = meta["key_item"]
                kmeta = find(keyitem) if keyitem else None
                if not keyitem:
                    warns.append(f"{qid}: needs locked '{obj}' open, but it has no key_item")
                elif not kmeta:
                    warns.append(f"{qid}: needs '{obj}' open; its key '{keyitem}' does not exist")
                elif not kmeta["takeable"]:
                    warns.append(f"{qid}: needs '{obj}' open; its key '{keyitem}' is not takeable")
    return warns
