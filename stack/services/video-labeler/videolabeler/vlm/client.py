"""OpenAI-compatible chat-completions client (images as base64 data URLs).

Works against Ollama's /v1 endpoint (the M2 default, qwen2.5vl:32b) and any
OpenAI-compatible server (vLLM serve later -- config-only swap). When the
base url looks like Ollama, the keep_alive extra-body param is passed through
so the model stays resident between windows instead of reloading per call.

Tests inject a mock through set_client_factory() -- the prelabel job always
obtains its client via get_client(cfg), never by constructing VlmClient
directly.
"""
from __future__ import annotations

import base64
import logging

import httpx

log = logging.getLogger("videolabeler.vlm")


class VlmError(RuntimeError):
    """Transport/HTTP/shape failure talking to the VLM server."""


def image_part(jpeg_bytes: bytes) -> dict:
    """JPEG bytes -> OpenAI image content part (base64 data URL)."""
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return {"type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


class VlmClient:
    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_s: float = 180.0, keep_alive: str = "10m"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive

    @classmethod
    def from_config(cls, cfg) -> "VlmClient":
        return cls(cfg.vlm_base_url, cfg.vlm_model, cfg.vlm_api_key,
                   cfg.vlm_timeout_s, cfg.vlm_keep_alive)

    def _is_ollama(self) -> bool:
        return ":11434" in self.base_url or "ollama" in self.base_url.lower()

    def chat(self, messages: list, json_mode: bool = False) -> str:
        """POST /chat/completions, return the assistant text. json_mode sets
        response_format json_object; servers that reject it (400) get ONE
        retry without it ("when supported")."""
        try:
            return self._chat_once(messages, json_mode)
        except VlmError as e:
            if json_mode and "response_format" in str(e):
                log.warning("server rejected response_format, retrying"
                            " without it: %s", e)
                return self._chat_once(messages, json_mode=False)
            raise

    def _chat_once(self, messages: list, json_mode: bool) -> str:
        body: dict = {"model": self.model, "messages": messages,
                      "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self._is_ollama():
            body["keep_alive"] = self.keep_alive
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise VlmError(f"VLM request failed: {e}") from e
        if resp.status_code != 200:
            raise VlmError(f"VLM HTTP {resp.status_code}:"
                           f" {resp.text[:1000]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise VlmError(f"unexpected VLM response shape:"
                           f" {resp.text[:500]}") from e
        if not isinstance(content, str):
            raise VlmError("VLM returned no text content")
        return content


# Tests (and future alternates) swap the construction path here; production
# code never instantiates VlmClient directly.
_client_factory = None


def set_client_factory(factory) -> None:
    """factory: Config -> client object with .chat(messages, json_mode) and
    .model. Pass None to restore the real client."""
    global _client_factory
    _client_factory = factory


def get_client(cfg):
    if _client_factory is not None:
        return _client_factory(cfg)
    return VlmClient.from_config(cfg)
