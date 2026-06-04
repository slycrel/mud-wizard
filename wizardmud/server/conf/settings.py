r"""
Evennia settings file.

The available options are found in the default settings file found
here:

https://www.evennia.com/docs/latest/Setup/Settings-Default.html

Remember:

Don't copy more from the default file than you actually intend to
change; this will make sure that you don't overload upstream updates
unnecessarily.

When changing a setting requiring a file system path (like
path/to/actual/file.py), use GAME_DIR and EVENNIA_DIR to reference
your game folder and the Evennia library folders respectively. Python
paths (path.to.module) should be given relative to the game's root
folder (typeclasses.foo) whereas paths within the Evennia library
needs to be given explicitly (evennia.foo).

If you want to share your game dir, including its settings, you can
put secret game- or server-specific settings in secret_settings.py.

"""

# Use the defaults from Evennia unless explicitly overridden
from evennia.settings_default import *

######################################################################
# Evennia base server config
######################################################################

# This is the name of your game. Make it catchy!
SERVERNAME = "wizardmud"


######################################################################
# Local LLM (LM Studio, OpenAI-compatible) settings
######################################################################
# LM Studio exposes an OpenAI-compatible server. Start it from the app
# (Developer tab -> Start Server) or `lms server start`. Default port 1234.
# The OpenAI chat-completions bridge lives in
# typeclasses/llm_openai_client.py; NPC typeclasses in typeclasses/llm_npcs.py.
LLM_HOST = "http://127.0.0.1:1234"
LLM_PATH = "/v1/chat/completions"
LLM_API_TYPE = "openai"
LLM_HEADERS = {"Content-Type": ["application/json"]}
# Fallback model id when an NPC doesn't set its own. Must match the id LM Studio
# reports (`lms ps`). Per-NPC ids are set in typeclasses/llm_npcs.py.
LLM_MODEL = "qwen/qwen3.6-35b-a3b"
LLM_REQUEST_BODY = {
    "max_new_tokens": 300,  # mapped to OpenAI "max_tokens" by the bridge
    "temperature": 0.8,
}
# Content rating, injected into the wizard's judging/narration prompts and (later)
# image prompts. One knob governs tone everywhere: pg13 | mature | explicit.
CONTENT_RATING = "mature"

# Generated images (Draw Things) are written here and served at /media/ (see web/urls.py),
# so the web client can show them via msg(image=[url]).
import os as _os
MEDIA_ROOT = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "web", "media")
MEDIA_URL = "/media/"


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")
