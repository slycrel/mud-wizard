"""
OpenAI-compatible LLM client for talking to a local LM Studio server.

Evennia's stock contrib `LLMClient` targets text-generation-webui and hardcodes
the response parse to ``{"results": [{"text": ...}]}``. LM Studio (and any
OpenAI-compatible server) instead returns
``{"choices": [{"message": {"content": ...}}]}``, so we subclass and override
the request body and the response parse. (The contrib exposes an
``LLM_API_TYPE = "openai"`` setting, but in this Evennia version it is read and
never actually used, which is why this bridge exists.)

Configure via ``server/conf/settings.py``::

    LLM_HOST  = "http://127.0.0.1:1234"
    LLM_PATH  = "/v1/chat/completions"
    LLM_MODEL = "<default model id as shown in LM Studio>"

Per-NPC model selection is handled by passing ``model=`` from the NPC typeclass
(see ``llm_npcs.py``).
"""

import json

from django.conf import settings
from twisted.internet.defer import inlineCallbacks

from evennia import logger
from evennia.utils.utils import make_iter
from evennia.contrib.rpg.llm.llm_client import LLMClient

DEFAULT_LLM_MODEL = "local-model"


class OpenAIChatLLMClient(LLMClient):
    """LLMClient variant that speaks the OpenAI /v1/chat/completions schema.

    Reuses the parent's Twisted Agent + ``_get_response_from_llm_server`` (which
    POSTs ``_format_request_body`` to ``LLM_HOST + LLM_PATH``); we only swap the
    body construction and the response parsing.
    """

    def __init__(self, model=None, on_bad_request=None):
        super().__init__(on_bad_request=on_bad_request)
        self.model = model or getattr(settings, "LLM_MODEL", DEFAULT_LLM_MODEL)
        # optional system framing prepended before the per-NPC roleplay prompt
        self.system_prompt = getattr(settings, "LLM_SYSTEM_PROMPT", "")

    def _format_request_body(self, prompt):
        """Build an OpenAI chat-completions body instead of the webui body."""
        prompt = "\n".join(make_iter(prompt))

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt})

        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.request_body.get("temperature", 0.7),
            "max_tokens": self.request_body.get(
                "max_tokens", self.request_body.get("max_new_tokens", 250)
            ),
            "stream": False,
        }

    @inlineCallbacks
    def get_response(self, prompt):
        """POST to the chat endpoint and pull ``choices[0].message.content``.

        Returns an empty string on any error; the NPC handles that gracefully
        with a "sorry, I was distracted" fallback.
        """
        status_code, response = yield self._get_response_from_llm_server(prompt)
        if status_code == 200:
            if settings.DEBUG:
                logger.log_info(f"LLM response: {response}")
            try:
                data = json.loads(response)
                return data["choices"][0]["message"]["content"].strip()
            except (ValueError, KeyError, IndexError) as err:
                logger.log_err(f"LLM parse error: {err} :: {response[:500]!r}")
                return ""
        logger.log_err(f"LLM API error (status {status_code}): {response!r}")
        return ""
