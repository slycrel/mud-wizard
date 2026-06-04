"""
Draw Things image generation for wizardmud.

Talks to Draw Things' A1111-compatible HTTP API on :7860 (enable in-app: server-stack
icon -> API Server -> Server Online, HTTP). Render PROFILES map a style+quality to a
checkpoint + sampler settings:

  - turbo/lightning checkpoints  -> few steps, low CFG  (fast, the everyday default)
  - HQ checkpoints               -> more steps          (Draw Things swaps the checkpoint
                                                          in on demand; keep these rare)

`render()` returns the saved PNG path so the game can serve it to the web client. Pure
Python (urllib); call it off the reactor (deferToThread) so the MUD never blocks. It
never raises into the game — returns None on any failure (so a down image server just
means "no picture," and the text still works: graceful degradation).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request

DRAW_THINGS = os.environ.get("DRAW_THINGS_URL", "http://localhost:7860")

# checkpoint names exactly as Draw Things reports them (GET /sdapi/v1/options 'model').
PROFILES = {
    "painterly":    {"checkpoint": "dreamshaper_xl_v2.1_turbo_f16.ckpt",        "steps": 8,  "cfg": 2.0},
    "painterly_hq": {"checkpoint": "zavychromaxl_v100_f16.ckpt",                 "steps": 28, "cfg": 6.0},
    "photoreal":    {"checkpoint": "realvisxlv50_v50lightningbakedvae_f16.ckpt", "steps": 8,  "cfg": 2.0},
    "versatile":    {"checkpoint": "juggernautxl_ragnarokby_f16.ckpt",           "steps": 30, "cfg": 6.5},
}
DEFAULT_PROFILE = "painterly"

NEGATIVE = "blurry, low quality, deformed, watermark, signature, text, extra limbs"

STYLE_SUFFIX = {
    "painterly":    "fantasy concept art, painterly, dramatic lighting, highly detailed",
    "painterly_hq": "fantasy concept art, oil painting, intricate, dramatic lighting, masterpiece",
    "photoreal":    "photorealistic, cinematic lighting, highly detailed, sharp focus",
    "versatile":    "cinematic, dramatic lighting, highly detailed",
}


def prompt_for(name: str, desc: str, profile: str = DEFAULT_PROFILE) -> str:
    """Build an image prompt from an object's name + description + a style suffix.
    (The wizard could author richer prompts later; deriving from the desc keeps it
    dependency-free and on-content.)"""
    base = f"{name}. {desc}".strip().rstrip(".")
    return f"{base}. {STYLE_SUFFIX.get(profile, STYLE_SUFFIX[DEFAULT_PROFILE])}"


def render(prompt: str, out_path: str, profile: str = DEFAULT_PROFILE,
           width: int = 1024, height: int = 1024, negative: str = NEGATIVE,
           timeout: int = 300):
    """Generate an image to out_path. Returns out_path on success, None on failure."""
    spec = PROFILES.get(profile, PROFILES[DEFAULT_PROFILE])
    body = {
        "prompt": prompt,
        "negative_prompt": negative,
        "steps": spec["steps"],
        "cfg_scale": spec["cfg"],
        "width": width,
        "height": height,
        # Draw Things selects the checkpoint via a top-level `model` field
        # (it does NOT support A1111's override_settings / sd_model_checkpoint).
        "model": spec["checkpoint"],
    }
    try:
        req = urllib.request.Request(
            DRAW_THINGS + "/sdapi/v1/txt2img",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        images = data.get("images") or []
        if not images:
            return None
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(base64.b64decode(images[0]))
        return out_path
    except Exception:
        return None  # image server down / error -> graceful no-op (text still works)
