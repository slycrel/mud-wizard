"""
Image visualization commands.

`inspect [<target>]`  — look closely at an object (or the room); on the web client the
                        Wizard renders an illustration. Telnet players just get the text
                        (graceful degradation — the `image=[...]` kwarg is ignored there).
`illustrate <t> [= profile]` — (Builder) pre-render at a chosen quality profile.

Renders go through Draw Things (world.image_gen), are cached on the object
(`obj.db.image_url` keyed by a desc+profile signature), saved under MEDIA_ROOT, and
served at /media/ (see web/urls.py). The render runs off-reactor (deferToThread) so a
multi-second image never freezes the MUD; failures degrade to "no image", text intact.

Design rule: the image is cached *state* on the object; the LLM/diffusion only fills it.
"""

import hashlib
import os

from django.conf import settings
from evennia import Command
from twisted.internet import threads

from world import image_gen


def _sig(desc, profile):
    return hashlib.md5(f"{profile}|{desc}".encode()).hexdigest()[:12]


def _paths(obj, profile):
    rel = f"obj/{obj.id}_{profile}.png"
    return (os.path.join(settings.MEDIA_ROOT, rel),
            settings.MEDIA_URL.rstrip("/") + "/" + rel)


def illustrate(viewer, obj, profile=image_gen.DEFAULT_PROFILE, force=False):
    """Show `obj`'s picture to `viewer` (web only), rendering + caching if needed."""
    desc = obj.db.desc or obj.key
    sig = _sig(desc, profile)
    if not force and obj.db.image_sig == sig and obj.db.image_url:
        viewer.msg(image=[obj.db.image_url])  # cached
        return
    viewer.msg("|x(the Wizard conjures an image...)|n")
    out_path, url = _paths(obj, profile)
    prompt = image_gen.prompt_for(obj.key, desc, profile)

    def _done(result):
        if result:
            obj.db.image_url = url
            obj.db.image_sig = sig
            viewer.msg(image=[url])
        else:
            viewer.msg("|x(the image fails to form — the aether is clouded.)|n")

    (threads.deferToThread(image_gen.render, prompt, out_path, profile)
            .addCallback(_done)
            .addErrback(lambda f: viewer.msg("|x(no image this time.)|n")))


class CmdInspect(Command):
    """
    Inspect something closely. On the web client the Wizard renders an image of it.

    Usage:
        inspect            (the room you're in)
        inspect <target>
    """

    key = "inspect"
    aliases = ["study"]
    locks = "cmd:all()"

    def func(self):
        target = self.args.strip()
        obj = self.caller.location if not target else self.caller.search(target)
        if not obj:
            return
        name = obj.get_display_name(self.caller) if hasattr(obj, "get_display_name") else obj.key
        self.caller.msg(f"|w{name}|n\n{obj.db.desc or 'You see nothing special.'}")
        illustrate(self.caller, obj)


class CmdIllustrate(Command):
    """
    (Builder) Pre-render an object's illustration at a chosen quality profile —
    e.g. a hero object you want in full quality rather than the fast default.

    Usage:
        illustrate <target> [= painterly | painterly_hq | photoreal | versatile]
    """

    key = "illustrate"
    locks = "cmd:perm(Builder)"
    help_category = "Building"

    def func(self):
        if "=" in self.args:
            tname, profile = (s.strip() for s in self.args.split("=", 1))
        else:
            tname, profile = self.args.strip(), image_gen.DEFAULT_PROFILE
        obj = self.caller.search(tname) if tname else self.caller.location
        if not obj:
            return
        if profile not in image_gen.PROFILES:
            self.caller.msg(f"Unknown profile. Choose: {', '.join(image_gen.PROFILES)}")
            return
        self.caller.msg(f"Rendering |c{obj.key}|n at |c{profile}|n ...")
        illustrate(self.caller, obj, profile=profile, force=True)
