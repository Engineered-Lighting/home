"""Extended OpenAI Conversation agent entity."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Literal

from openai import OpenAIError
import yaml

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
    async_get_chat_log,
)
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm, template
from homeassistant.helpers.chat_session import async_get_chat_session
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ExtendedOpenAIConfigEntry
from .const import (
    CONF_FUNCTION_TOOLS,
    CONF_PROMPT,
    CONF_SKILLS,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DEFAULT_PROMPT,
    DEFAULT_WORKING_DIRECTORY,
    DOMAIN,
    EVENT_CONVERSATION_FINISHED,
    PERSON_NAME_ALIASES,
)
from .entity import ExtendedOpenAIBaseLLMEntity
from .exceptions import (
    FunctionLoadFailed,
    FunctionNotFound,
    InvalidFunction,
    ParseArgumentsFailed,
    TokenLengthExceededError,
)
from .functions import get_function
from .helpers import get_exposed_entities
from .room_binding import bind, parse_marker, resolve
from .skills import Skill, SkillManager

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Phase 4A.7 — HA-side ASR-correction backstop. The bridge applies the
# same corrections at MoshiListenerUpstream._on_parakeet_transcript,
# but Voice PE bypasses the bridge entirely (Voice PE → Wyoming →
# assist_pipeline → here). Without this backstop, "Marcello" mishears
# from Voice PE arrive uncorrected, the agent's anti-hallucination rule
# fires ("no record of Marcello"), and the user sees the spelling bug
# they thought was fixed. Catches the same patterns from the same dict.
# ─────────────────────────────────────────────────────────────────────
_ASR_CORRECTION_RE = (
    re.compile(
        r"\b(" + "|".join(re.escape(k) for k in PERSON_NAME_ALIASES) + r")\b",
        re.IGNORECASE,
    )
    if PERSON_NAME_ALIASES
    else None
)


def _apply_ha_asr_correction(text: str) -> str:
    """Whole-word, case-insensitive substitution of known ASR errors.
    No-op when PERSON_NAME_ALIASES is empty or text is empty."""
    if not text or _ASR_CORRECTION_RE is None:
        return text
    return _ASR_CORRECTION_RE.sub(
        lambda m: PERSON_NAME_ALIASES[m.group(0).lower()],
        text,
    )


# ─────────────────────────────────────────────────────────────────────
# Phase 4A.9 — Jarvis mute system (Addendum 9). Whole-utterance
# anchored patterns prevent embedded phrases from accidentally muting
# (e.g., "you should shut up the alarm" would have triggered an
# unanchored \b(shut up)\b). Resume + mute patterns are symmetric:
# both demand the phrase be essentially the WHOLE directed command.
# ─────────────────────────────────────────────────────────────────────
_RESUME_PATTERNS = re.compile(
    r"^\s*(hey\s+)?(jarvis,?\s+)?"
    r"(resume|wake up|come back|talk to me( again)?|"
    r"you can talk( again| now)?|unmute|i'?m back|"
    r"listen up|stop being quiet|let's talk|start listening)"
    r"\s*\.?\s*$",
    re.IGNORECASE,
)

_MUTE_PATTERNS = [
    # Explicit mute phrasings — whole-utterance anchored.
    (re.compile(
        r"^\s*(hey\s+)?(jarvis,?\s+)?"
        r"(shut up|be quiet|go quiet|hush|silence|"
        r"shut it|zip it|stop talking|stop responding|"
        r"stop it|just stop|please stop|quiet please|quiet down)"
        r"\s*\.?\s*$", re.IGNORECASE),
     {"kind": "explicit"}),
    # Bare "stop" only when it's basically the whole sentence —
    # "stop the music" / "stop the timer" must NOT trigger mute.
    (re.compile(
        r"^\s*(hey\s+)?(jarvis,?\s+)?stop(\s+(it|please|now|jarvis))?\s*\.?\s*$",
        re.IGNORECASE),
     {"kind": "explicit"}),
    # Movie / show / TV intent — broader since the phrase is unambiguous.
    (re.compile(
        r"\b(watching|watch|starting|put(ting)? on)\s+"
        r"(a |the |my |some )?"
        r"(movie|show|film|tv|television|game|netflix|hbo|hulu|"
        r"max|prime|disney|series|episode)\b",
        re.IGNORECASE),
     {"kind": "movie"}),
    # Explicit duration: "mute for 30 minutes", "stop for 2 hours"
    (re.compile(
        r"\b(stop|mute|quiet|silence)\s+for\s+(\d+)\s+(min|minute|hour|hr)s?\b",
        re.IGNORECASE),
     {"kind": "timer"}),
    # Transient unmute marker — process one turn normally without
    # clearing the mute state. Lets the user ask a quick question
    # during a movie without the 3-step (wake → ask → re-mute) friction.
    (re.compile(
        r"\b(just this once|one quick question|real quick|"
        r"quick question|briefly|just one thing)\b",
        re.IGNORECASE),
     {"kind": "transient_unmute"}),
]


def _is_resume_command(text: str) -> bool:
    return bool(_RESUME_PATTERNS.search(text or ""))


def _parse_mute_command(text: str) -> dict | None:
    """Returns intent dict {kind, duration_min?} or None."""
    t = text or ""
    for rx, base in _MUTE_PATTERNS:
        m = rx.search(t)
        if not m:
            continue
        out = dict(base)
        if base["kind"] == "timer":
            n = int(m.group(2))
            unit = m.group(3).lower()
            out["duration_min"] = n * 60 if unit.startswith("h") else n
        return out
    return None


def _humanize_duration(mins: int) -> str:
    if mins >= 60 and mins % 60 == 0:
        h = mins // 60
        return f"{h} hour" + ("s" if h > 1 else "")
    return f"{mins} minutes"


# TTS-safe error mapping. Lifted out of the conversation entity as a
# pure function so it's unit-testable without HA imports.
# Earlier bug (2026-05-18): ParseArgumentsFailed.__str__ contained the
# raw 642-char tool-args JSON; the f"Something went wrong: {err}" path
# fed all of it to TTS → user heard "gibberish nonstop". This helper
# enumerates known error classes by their TYPE NAME (not isinstance —
# so the test suite doesn't need the HomeAssistantError parent class)
# and falls back to a 180-char cap for anything unknown.
_FRIENDLY_ERROR_SPEECH = {
    "ParseArgumentsFailed": (
        "That command was too big for me to finish in one go. "
        "Try breaking it into smaller pieces."
    ),
    "TokenLengthExceededError": (
        "I ran out of room while thinking through that one. "
        "Try asking something more specific."
    ),
}


def _friendly_error_speech(err: Exception) -> str:
    """Map an exception → short TTS-safe sentence.

    Returns at most ~180 chars. Never returns raw JSON or tracebacks.
    Pure function — safe to call from any thread + easy to unit-test
    by passing any object with a __class__.__name__ and a __str__.
    """
    name = type(err).__name__
    if name in _FRIENDLY_ERROR_SPEECH:
        return _FRIENDLY_ERROR_SPEECH[name]
    if name == "FunctionNotFound":
        fn = getattr(err, "function", None)
        if fn:
            return (
                f"I don't have a tool for that ({fn}). "
                "Say 'agent tools' to hear what I can do."
            )
    msg = str(err)
    if 0 < len(msg) <= 180:
        return f"I had trouble with that: {msg}"
    return "I had trouble completing that. Try a simpler request."


_VISUAL_QUERY_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        # Mobile keyboards commonly replace ASCII apostrophes with smart
        # quotes.  Keep the classifier's grammar ASCII-only and canonicalize
        # harmless presentation variants before applying it.
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u02bc": "'",
        "\uff07": "'",
        # Treat typographic/non-breaking dashes as word separators when
        # matching aliases such as "coffee table".
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)


def _normalize_visual_query_text(text: str) -> str:
    """Canonicalize harmless Unicode typography for visual routing only."""
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.translate(_VISUAL_QUERY_PUNCTUATION_TRANSLATION)
    return " ".join(normalized.split())


_VISUAL_QUERY_RE = re.compile(
    r"\b("
    r"what(?:'s|s| is)?\s+(?:going on|happening|in|inside|on|at)|"
    r"what\s+do\s+you\s+see|"
    r"look(?:\s+at|\s+in|\s+inside)?|"
    r"check\s+(?:the\s+)?(?:camera|feed|view)|"
    r"show\s+me\s+(?:the\s+)?(?:camera|feed|view)|"
    r"can\s+you\s+see|"
    r"do\s+you\s+see|"
    r"describe\s+(?:the\s+)?(?:camera|feed|view|scene)"
    r")\b",
    re.IGNORECASE,
)

# A direct-looking phrase can be embedded in a historical or supplied-media
# question ("Do you remember what's on ...?").  Those must continue through
# the normal conversation/memory path rather than silently inspecting a live
# home camera.
_NONLIVE_VISUAL_CONTEXT_RE = re.compile(
    r"\b(?:do|can|could|would)\s+you\s+(?:remember|recall)\b|"
    r"\b(?:remember|recall)\s+(?:what|where|who)\b|"
    r"\b(?:earlier|yesterday|previously|usually|normally|typically|"
    r"last\s+(?:time|night|week|month|year))\b|"
    r"\b(?:in|from)\s+(?:(?:this|that|the|my|a|an)\s+)?"
    r"(?:photo|photograph|image|picture|screenshot|recording|video|clip)\b|"
    r"\baccording\s+to\s+(?:(?:your|the)\s+)?(?:memory|history)\b",
    re.IGNORECASE,
)

_VISUAL_ROOM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("living_room", ("living room", "coffee table", "couch", "sofa", "tv", "bicycle")),
    ("kitchen", ("kitchen", "counter", "island", "sink", "stove", "oven")),
    ("dining_room", ("dining room", "dining table", "chairs")),
    ("workshop", ("workshop", "office", "desk")),
    ("driveway", ("driveway", "outside", "front yard", "street", "car", "vehicle")),
)


def _infer_visual_room(text: str) -> str:
    """Infer the room/camera for deterministic visual routing."""
    q = _normalize_visual_query_text(text).casefold().replace("-", " ")
    for room, terms in _VISUAL_ROOM_ALIASES:
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", q) for term in terms):
            return room
    return ""


def _should_preroute_grounded_look(text: str) -> dict[str, Any] | None:
    """Return grounded_look args for direct visual questions.

    Tool-calling from the local OpenAI-compatible model is not guaranteed.
    For direct "what do you see" camera questions, deterministic routing is
    safer than hoping the model obeys the prompt.
    """
    text = (text or "").strip()
    normalized = _normalize_visual_query_text(text)
    if (
        not normalized
        or _NONLIVE_VISUAL_CONTEXT_RE.search(normalized)
        or not _VISUAL_QUERY_RE.search(normalized)
    ):
        return None
    room = _infer_visual_room(normalized)
    if not room:
        return None
    return {
        "room": room,
        "question": text,
        "allow_illumination": room not in {"driveway", "outside", "front_yard", "back_yard"},
    }


# Module-import sentinel: writes a line every time this module is loaded.
# If this file exists, my code is in the loaded module. If not, the
# integration somehow loaded a different file or this one never executed.
try:
    with open("/config/asr_debug.log", "a") as _f:
        _f.write(f"{time.time():.3f} MODULE LOADED conversation.py from Phase 4A.9\n")
except Exception:  # noqa: BLE001
    pass


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ExtendedOpenAIConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OpenAI Conversation entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue

        async_add_entities(
            [ExtendedOpenAIAgentEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


# --- conversation memory: device_id -> (last_seen_ts, conversation_id) ---
# Bridges across wake-word activations so a follow-up after the mic
# closes still has prior turns in the chat log. 15-minute TTL.
DEVICE_CONV_MEMORY: dict[str, tuple[float, str]] = {}
DEVICE_CONV_TTL_SEC: float = 15 * 60

class ExtendedOpenAIAgentEntity(
    ConversationEntity,
    conversation.AbstractConversationAgent,
    ExtendedOpenAIBaseLLMEntity,
):
    """Extended OpenAI conversation agent."""

    _attr_supports_streaming = True
    _attr_supported_features = ConversationEntityFeature.CONTROL
    skill_manager: SkillManager

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @property
    def skills(self) -> list[str]:
        """Get the enabled skills list for this entity."""
        return self.subentry.data.get(CONF_SKILLS, []) or []

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

        # Calculate skills directory based on working directory
        working_dir = DEFAULT_WORKING_DIRECTORY
        if Path(working_dir).is_absolute():
            skills_dir = Path(working_dir) / "skills"
        else:
            skills_dir = Path(self.hass.config.config_dir) / working_dir / "skills"

        self.skill_manager = await SkillManager.async_get_instance(
            self.hass, user_skills_dir=str(skills_dir)
        )

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    # ── Jarvis mute helpers (Addendum 9) ─────────────────────────
    def _is_muted(self) -> bool:
        """Read binary_sensor.jarvis_muted_effective_2. Fails OPEN
        (returns False) if the helpers aren't deployed yet — the
        agent stays usable rather than going silent on a missing
        entity."""
        state_obj = self.hass.states.get("binary_sensor.jarvis_muted_effective_2")
        if state_obj is None:
            return False
        return state_obj.state == "on"

    async def _apply_mute(self, mute_intent: dict, user_input: ConversationInput) -> None:
        """Set the appropriate mute signal based on parsed intent.
        Also evicts the device's DEVICE_CONV_MEMORY entry so any
        confused-by-TV conversation context dies the moment mute fires.
        Also fires media_player.media_stop on Sonos + a tts_cancel HA
        bus event the bridge listens for to kill in-flight Kokoro
        streams."""
        dev = getattr(user_input, "device_id", None)
        if dev and dev in DEVICE_CONV_MEMORY:
            del DEVICE_CONV_MEMORY[dev]
        kind = mute_intent.get("kind")
        if kind == "explicit":
            await self.hass.services.async_call(
                "input_boolean", "turn_on",
                {"entity_id": "input_boolean.jarvis_muted_explicit"},
                blocking=False,
            )
            await self.hass.services.async_call(
                "input_text", "set_value",
                {"entity_id": "input_text.jarvis_mute_reason",
                 "value": "voice command"},
                blocking=False,
            )
        elif kind == "movie":
            await self.hass.services.async_call(
                "input_boolean", "turn_on",
                {"entity_id": "input_boolean.homeai_movie"},
                blocking=False,
            )
        elif kind == "timer":
            from datetime import datetime, timedelta, timezone
            until = datetime.now(timezone.utc) + timedelta(minutes=mute_intent["duration_min"])
            await self.hass.services.async_call(
                "input_datetime", "set_datetime",
                {"entity_id": "input_datetime.jarvis_mute_until",
                 "datetime": until.replace(tzinfo=None).isoformat(timespec="seconds")},
                blocking=False,
            )
        # Stop any in-flight Sonos TTS so the current sentence cuts.
        try:
            await self.hass.services.async_call(
                "media_player", "media_stop",
                {"entity_id": [
                    "media_player.living_room",
                    "media_player.kitchen_stereo",
                ]},
                blocking=False,
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("media_stop failed (non-fatal): %s", e)
        # Fire HA event for bridge to kill in-flight Kokoro stream.
        self.hass.bus.async_fire(
            "extended_openai_conversation.tts_cancel",
            {"trigger": "jarvis_mute"},
        )

    async def _clear_mute_state(self, user_input: ConversationInput) -> None:
        """Clear manual mute signals. Auto-detect signals (movie_mode,
        TV active) are NOT cleared — those reflect real state."""
        await self.hass.services.async_call(
            "input_boolean", "turn_off",
            {"entity_id": "input_boolean.jarvis_muted_explicit"},
            blocking=False,
        )
        await self.hass.services.async_call(
            "input_datetime", "set_datetime",
            {"entity_id": "input_datetime.jarvis_mute_until",
             "datetime": "2000-01-01T00:00:00"},
            blocking=False,
        )
        # Evict DEVICE_CONV_MEMORY so next turn after resume gets a
        # fresh conversation_id (don't carry over TV-confusion context).
        dev = getattr(user_input, "device_id", None)
        if dev and dev in DEVICE_CONV_MEMORY:
            del DEVICE_CONV_MEMORY[dev]

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Process a sentence.

        Conversation memory: if a voice device spoke within the last
        DEVICE_CONV_TTL_SEC, silently reuse the prior conversation_id so
        the chat log loads with prior context. Bridges across wake-word
        activations.
        """
        # Phase 4A.7: HA-side ASR-correction backstop. Apply BEFORE any
        # classifier or prompt work so the rest of the turn (routing,
        # tool calls, prompt rendering) sees the canonical spelling.
        # Critical for Voice PE which bypasses the bridge's same
        # correction (see _apply_ha_asr_correction docstring above).
        # Logs every APPLIED substitution to /config/asr_debug.log for
        # ongoing observability — `ha core logs` doesn't reliably surface
        # _LOGGER.warning from custom-component code paths; the file is
        # the source of truth for "did the backstop fire?"
        if user_input.text:
            corrected = _apply_ha_asr_correction(user_input.text)
            if corrected != user_input.text:
                try:
                    with open("/config/asr_debug.log", "a") as _f:
                        _f.write(
                            f"{time.time():.3f} APPLIED "
                            f"{user_input.text[:80]!r} → {corrected[:80]!r}\n"
                        )
                except Exception:  # noqa: BLE001
                    pass
                _LOGGER.info(
                    "asr_correction: %r → %r",
                    user_input.text[:60], corrected[:60],
                )
                user_input.text = corrected

        # ── Jarvis mute gate (Addendum 9) ───────────────────────
        # Composite mute state read from binary_sensor.jarvis_muted_effective_2
        # (template: explicit toggle OR timer OR movie_mode OR TV active).
        # When muted, the agent returns an empty silent response with
        # continue_conversation=False to close Voice PE's mic. Resume
        # commands ("hey jarvis, wake up") bypass the gate. Mute commands
        # ("hey jarvis, stop") apply the mute deterministically without
        # going to the LLM. Transient unmute ("real quick, what time
        # is it?") processes one turn but keeps mute state on.
        if user_input.text:
            _raw_text = user_input.text
            _mute_intent = _parse_mute_command(_raw_text)
            _is_muted_now = self._is_muted()
            _is_resume = _is_resume_command(_raw_text)
            _is_transient = bool(_mute_intent and _mute_intent.get("kind") == "transient_unmute")
            # Reset transient flag from any prior turn.
            self._transient_this_turn = False
            # 1. Resume command — always honored.
            if _is_resume:
                _LOGGER.info("jarvis-mute: resume command %r", _raw_text[:80])
                try:
                    with open("/config/asr_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} JARVIS_RESUME {_raw_text[:80]!r}\n")
                except Exception:  # noqa: BLE001
                    pass
                await self._clear_mute_state(user_input)
                resp = intent.IntentResponse(language=user_input.language)
                resp.async_set_speech("OK, I'm listening.")
                return conversation.ConversationResult(
                    response=resp,
                    conversation_id=user_input.conversation_id,
                    continue_conversation=False,
                )
            # 2. Transient unmute — process this turn normally, mute holds.
            if _is_muted_now and _is_transient:
                _LOGGER.info("jarvis-mute: transient unmute %r", _raw_text[:80])
                # Strip the marker phrase so the LLM gets the actual question.
                user_input.text = re.sub(
                    r"\b(just this once|one quick question|real quick|"
                    r"quick question|briefly|just one thing)\s*,?\s*",
                    "",
                    _raw_text,
                    flags=re.IGNORECASE,
                ).strip()
                self._transient_this_turn = True
                # Fall through to normal processing; continue_conversation
                # forced False by the response-builder override.
            # 3. Muted with no special handling — drop silently.
            elif _is_muted_now:
                _LOGGER.info("jarvis-mute: dropped %r", _raw_text[:80])
                try:
                    with open("/config/asr_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} JARVIS_MUTED_DROP {_raw_text[:80]!r}\n")
                except Exception:  # noqa: BLE001
                    pass
                resp = intent.IntentResponse(language=user_input.language)
                # Explicit empty to suppress any HA default ack speech.
                resp.async_set_speech("")
                return conversation.ConversationResult(
                    response=resp,
                    conversation_id=user_input.conversation_id,
                    continue_conversation=False,
                )
            # 4. Mute command (currently unmuted) — apply + ack.
            elif _mute_intent and _mute_intent["kind"] != "transient_unmute":
                _LOGGER.info("jarvis-mute: apply %s", _mute_intent)
                try:
                    with open("/config/asr_debug.log", "a") as _f:
                        _f.write(
                            f"{time.time():.3f} JARVIS_MUTE_APPLY "
                            f"{_mute_intent!r} from {_raw_text[:80]!r}\n"
                        )
                except Exception:  # noqa: BLE001
                    pass
                await self._apply_mute(_mute_intent, user_input)
                resp = intent.IntentResponse(language=user_input.language)
                if _mute_intent.get("duration_min"):
                    ack = f"Going quiet for {_humanize_duration(_mute_intent['duration_min'])}."
                elif _mute_intent.get("kind") == "movie":
                    ack = "Movie mode — I'll be quiet until it's over."
                else:
                    ack = "Going quiet."
                resp.async_set_speech(ack)
                return conversation.ConversationResult(
                    response=resp,
                    conversation_id=user_input.conversation_id,
                    continue_conversation=False,
                )
            # 5. Not muted, no mute/resume command — fall through to normal processing.

        dev = getattr(user_input, "device_id", None)
        # Proactive room binding: the Home app tags a room-entry prompt's
        # extra_system_prompt with [[hg-room:<room>]]. Parse it here, then
        # cache it (below) keyed by the post-DEVICE_CONV_MEMORY-rewrite
        # conversation_id AND the device_id, so every turn of this
        # conversation can resolve the correct area. See room_binding.py.
        room = parse_marker(getattr(user_input, "extra_system_prompt", None))
        _LOGGER.debug(
            "hg-diag async_process IN cid=%s dev=%s room_marker=%s",
            user_input.conversation_id,
            dev,
            room,
        )
        if dev:
            last = DEVICE_CONV_MEMORY.get(dev)
            if last and (time.time() - last[0]) < DEVICE_CONV_TTL_SEC:
                user_input.conversation_id = last[1]
        if room:
            bind(room, user_input.conversation_id, dev)

        # ── External Reasoning Fallback + routing instrumentation ───
        # Mirrors the Tauri-side router in home/app/src/home-external.jsx
        # so voice + chat-via-HA get the same general-knowledge routing
        # that typed-via-Tauri already gets. Runs BEFORE the home-context
        # prompt is assembled below, so external never sees entity IDs /
        # room state / perception. See external_routing.py for the full
        # safety + privacy posture.
        #
        # Every conversation turn writes one line to
        # /config/external_routing.log so the workstation-side analyzer
        # can detect misroutes (e.g. local-routed turns where the agent
        # said "I don't know"). See plan ~/.claude/plans/keen-doodling-parasol.md.
        # Deterministic visual routing. The local model occasionally ignores
        # the "must call grounded_look" prompt and hallucinates that camera
        # access is disabled. Direct visual questions are cheap to classify,
        # so execute the vision tool before the LLM can dodge it.
        _grounded_args = _should_preroute_grounded_look(user_input.text or "")
        if _grounded_args:
            try:
                function = get_function("world_state")
                llm_context = user_input.as_llm_context(DOMAIN)
                tool_result = await function.execute(
                    self.hass,
                    {"type": "world_state", "name": "grounded_look"},
                    _grounded_args,
                    llm_context,
                    self._get_exposed_entities(),
                )
                answer = (
                    (tool_result or {}).get("suggested_phrasing")
                    or ((tool_result or {}).get("data") or {}).get("answer")
                    or ""
                )
                if not answer:
                    err = (tool_result or {}).get("error")
                    answer = (
                        f"I couldn't get a fresh look at the "
                        f"{_grounded_args['room'].replace('_', ' ')} right now."
                    )
                    if err:
                        answer += f" ({err})"
                answer = _apply_ha_asr_correction(answer)
                resp = intent.IntentResponse(language=user_input.language)
                resp.async_set_speech(answer)
                try:
                    self.hass.bus.async_fire(
                        EVENT_CONVERSATION_FINISHED,
                        {
                            "conversation_id": user_input.conversation_id,
                            "agent_id": self.subentry.subentry_id,
                            "user_input_text": user_input.text or "",
                            "assistant_text": answer,
                            "route": "grounded_look_preroute",
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
                if dev and user_input.conversation_id:
                    DEVICE_CONV_MEMORY[dev] = (
                        time.time(),
                        user_input.conversation_id,
                    )
                return ConversationResult(
                    response=resp,
                    conversation_id=user_input.conversation_id,
                    continue_conversation=False,
                )
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning(
                    "grounded_look preroute failed; falling back to LLM: %s",
                    e,
                    exc_info=True,
                )

        _ext_routing_state = {
            "src": "voice" if dev and ("voice" in dev or "satellite" in dev or "wyoming" in dev) else "chat",
            "intent": "local",
            "matched": "-",
            "key_present": False,
            "dispatched": "local",
            "ext_status": None,
            "ext_latency_ms": None,
            "ext_reply_chars": None,
            "ext_reply_snippet": None,
        }
        _log_decision_fn = None  # populated if external_routing is loadable
        try:
            from .external_routing import (
                classify_intent_with_rule as _classify_intent_with_rule,
                ask_external as _ask_external,
                get_api_key as _get_api_key,
                log_decision as _log_decision_fn,
            )
            _api_key = await _get_api_key(self.hass)
            _ext_routing_state["key_present"] = bool(_api_key)
            _intent_label, _matched_rule = _classify_intent_with_rule(user_input.text or "")
            _ext_routing_state["intent"] = _intent_label
            _ext_routing_state["matched"] = _matched_rule

            if _api_key and _intent_label == "external":
                _LOGGER.info(
                    "external routing: dispatching '%s' externally (matched=%s, text len=%d)",
                    (user_input.text or "")[:60].replace("\n", " "),
                    _matched_rule,
                    len(user_input.text or ""),
                )
                _ext_t0 = time.time()
                try:
                    _answer = await _ask_external(self.hass, user_input.text or "", _api_key)
                    _ext_routing_state["dispatched"] = "external"
                    _ext_routing_state["ext_status"] = "ok"
                    _ext_routing_state["ext_latency_ms"] = int((time.time() - _ext_t0) * 1000)
                    _ext_routing_state["ext_reply_chars"] = len(_answer)
                    _ext_routing_state["ext_reply_snippet"] = _answer[:500]
                    # Phase 4A.7: output-side ASR backstop. Even with RULE 0.5
                    # in the prompt, the model occasionally hallucinates
                    # "Marcello"; apply the same substitution to the response
                    # so the user never sees the forbidden spelling.
                    if _answer:
                        _corrected = _apply_ha_asr_correction(_answer)
                        if _corrected != _answer:
                            try:
                                with open("/config/asr_debug.log", "a") as _f:
                                    _f.write(
                                        f"{time.time():.3f} OUTPUT_APPLIED ext "
                                        f"{_answer[:100]!r} → {_corrected[:100]!r}\n"
                                    )
                            except Exception:  # noqa: BLE001
                                pass
                            _answer = _corrected
                            _ext_routing_state["ext_reply_snippet"] = _answer[:500]
                    _resp = intent.IntentResponse(language=user_input.language)
                    _resp.async_set_speech(_answer)
                    try:
                        await _log_decision_fn(self.hass, {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "conv_id": user_input.conversation_id,
                            "text": user_input.text or "",
                            "text_len": len(user_input.text or ""),
                            "local_reply_chars": None,
                            "local_reply_snippet": None,
                            **_ext_routing_state,
                        })
                    except Exception:  # noqa: BLE001 — logging must never break routing
                        pass
                    # Fire conversation_finished for external too. SLIM
                    # payload: only the user text + assistant response —
                    # NOT the full chat_log (which can balloon past 32 KB
                    # for tool-using turns, exceeding HA's recorder + WS
                    # delivery limits and breaking transcript rendering
                    # in the Home app). See plan Addendum 4 fix-up.
                    try:
                        self.hass.bus.async_fire(
                            EVENT_CONVERSATION_FINISHED,
                            {
                                "conversation_id": user_input.conversation_id,
                                "agent_id": self.subentry.subentry_id,
                                "user_input_text": user_input.text or "",
                                "assistant_text": _answer,
                                "route": "external",
                            },
                        )
                    except Exception:  # noqa: BLE001 — never break routing on event fire
                        pass
                    return ConversationResult(
                        response=_resp,
                        conversation_id=user_input.conversation_id,
                    )
                except Exception as e:  # noqa: BLE001
                    _ext_routing_state["dispatched"] = "external→fallback"
                    _ext_routing_state["ext_status"] = f"fail:{type(e).__name__}"
                    _ext_routing_state["ext_latency_ms"] = int((time.time() - _ext_t0) * 1000)
                    _LOGGER.warning(
                        "external routing: ask_external failed (%s); "
                        "falling back to local agent",
                        type(e).__name__,
                    )
                    # fall through to local handler (will log after)
        except ImportError:
            # external_routing.py not deployed yet — local-only mode,
            # no logging available either.
            pass

        with (
            async_get_chat_session(self.hass, user_input.conversation_id) as session,
            async_get_chat_log(self.hass, session, user_input) as chat_log,
        ):
            result = await self._async_handle_message(user_input, chat_log)

        # ── Log the local-routed (or external→fallback) outcome ────
        # Extract the assistant's speech from the IntentResponse so the
        # analyzer can detect declines. Best-effort: HA's response shape
        # varies between text / SSML / plain — try a few paths.
        if _log_decision_fn is not None:
            try:
                _local_reply = ""
                try:
                    _speech = getattr(result.response, "speech", {}) or {}
                    _local_reply = (_speech.get("plain", {}) or {}).get("speech", "") or ""
                except Exception:  # noqa: BLE001
                    pass
                await _log_decision_fn(self.hass, {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "conv_id": result.conversation_id or user_input.conversation_id,
                    "text": user_input.text or "",
                    "text_len": len(user_input.text or ""),
                    "local_reply_chars": len(_local_reply),
                    "local_reply_snippet": _local_reply[:500],
                    **_ext_routing_state,
                })
            except Exception:  # noqa: BLE001 — logging must never break routing
                pass
        if dev and result.conversation_id:
            DEVICE_CONV_MEMORY[dev] = (time.time(), result.conversation_id)
        if room and result.conversation_id:
            # HA may surface the real conversation_id only in the result.
            bind(room, result.conversation_id)
        return result

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Call the API."""
        # Create LLM context
        llm_context = user_input.as_llm_context(DOMAIN)

        # Get exposed entities for function tools
        exposed_entities = self._get_exposed_entities()

        # Get function tools
        function_tools = self._get_function_tools()

        # Build custom prompt with exposed entities
        system_prompt = self._build_system_prompt(
            exposed_entities, llm_context, user_input
        )

        # Set system prompt in chat log
        chat_log.content[0] = conversation.SystemContent(content=system_prompt)

        # Call the LLM

        try:
            await self._async_handle_chat_log(
                chat_log,
                function_tools=function_tools,
                exposed_entities=exposed_entities,
                llm_context=llm_context,
            )
        except OpenAIError as err:
            _LOGGER.error(err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Sorry, I had a problem talking to OpenAI: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )
        except HomeAssistantError as err:
            # Always log the full error for debugging — the user-facing
            # message below is intentionally short for TTS. Earlier bug
            # (2026-05-18): a multi-entity tool call truncated mid-stream,
            # ParseArgumentsFailed was raised with the raw 642-char JSON
            # in its __str__, the previous f"Something went wrong: {err}"
            # path fed all of it to TTS = user heard "gibberish nonstop"
            # until they cancelled. _friendly_error_speech maps specific
            # error classes to short TTS-safe strings + caps any unknown
            # error's surfacing at 180 chars.
            _LOGGER.error("Error during conversation: %s", err, exc_info=True)
            speech = _friendly_error_speech(err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN, speech,
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )

        # Build response from chat log
        intent_response = intent.IntentResponse(language=user_input.language)

        # Get last assistant message
        last_content = chat_log.content[-1]
        if isinstance(last_content, conversation.AssistantContent):
            _assistant_text = last_content.content or ""
        else:
            _assistant_text = ""
        # Phase 4A.7: output-side ASR backstop. Even with RULE 0.5
        # in the prompt, the local model occasionally hallucinates
        # "Marcello"; apply the same substitution to the response so
        # the user never sees the forbidden spelling. Local path
        # variant of the same correction the external path applies.
        if _assistant_text:
            _corrected_text = _apply_ha_asr_correction(_assistant_text)
            if _corrected_text != _assistant_text:
                try:
                    with open("/config/asr_debug.log", "a") as _f:
                        _f.write(
                            f"{time.time():.3f} OUTPUT_APPLIED local "
                            f"{_assistant_text[:100]!r} → {_corrected_text[:100]!r}\n"
                        )
                except Exception:  # noqa: BLE001
                    pass
                _assistant_text = _corrected_text
        intent_response.async_set_speech(_assistant_text)

        # Fire conversation finished event with SLIM payload — only the
        # user text + assistant response. The full chat_log (with system
        # prompt, tool_calls, tool_results) routinely exceeded 32 KB on
        # tool-using turns, triggering recorder warnings and breaking
        # transcript delivery to the Home app over WS. See plan
        # Addendum 4 fix-up. The local-path message routing in
        # conversation.py uses chat_log.content directly anyway; the
        # event is only consumed by external subscribers (Home app for
        # transcript rendering) which only need user/assistant text.
        try:
            self.hass.bus.async_fire(
                EVENT_CONVERSATION_FINISHED,
                {
                    "conversation_id": user_input.conversation_id,
                    "agent_id": self.subentry.subentry_id,
                    "user_input_text": user_input.text or "",
                    "assistant_text": _assistant_text,
                    "route": "local",
                },
            )
        except Exception:  # noqa: BLE001 — never break the response on event fire
            pass

        # Compute continue_conversation, then force False if muted or
        # if this turn was a transient-unmute (Addendum 9). Belt-and-
        # suspenders with the early-exit gate; catches the rare case
        # where a turn was already in-flight when mute toggled.
        _cc = bool(
            last_content.content and (
                last_content.content.rstrip().endswith("?")
                or any(k in last_content.content.lower()
                       for k in ("which", "would you", "what room", "prefer"))
            )
        )
        if self._is_muted() or getattr(self, "_transient_this_turn", False):
            _cc = False
        # Always reset the transient flag so it doesn't leak across turns.
        self._transient_this_turn = False
        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=_cc,
        )

    def _build_system_prompt(
        self,
        exposed_entities: list[dict],
        llm_context: llm.LLMContext,
        user_input: ConversationInput,
    ) -> str:
        """Build system prompt with exposed entities and skills."""
        raw_prompt: str = self.subentry.data.get(CONF_PROMPT, DEFAULT_PROMPT)

        # Proactive room binding: if this conversation (or its device) was
        # tagged with a room, that room is the authoritative current area --
        # overriding the speaker device's physical area. See room_binding.py.
        bound_room = resolve(
            user_input.conversation_id, getattr(user_input, "device_id", None)
        )
        _LOGGER.debug(
            "hg-diag _build_system_prompt cid=%s dev=%s bound_room=%s",
            user_input.conversation_id,
            getattr(user_input, "device_id", None),
            bound_room,
        )

        result = template.Template(raw_prompt, self.hass).async_render(
            {
                "ha_name": self.hass.config.location_name,
                "exposed_entities": exposed_entities,
                "current_device_id": llm_context.device_id,
                "bound_area": bound_room,
                "user_input": user_input,
                "skills": self._get_enabled_skills(),
            },
            parse_result=False,
        )

        result = str(result)
        if bound_room:
            result = (
                f"{result}\n\n## Active Room Context (authoritative)\n"
                f"The user is in area '{bound_room}'. Treat '{bound_room}' as the "
                f"Current Area for this turn. Resolve every ambiguous location "
                f"reference ('the lights', 'them', 'it', 'here', 'this room') to "
                f"entities in '{bound_room}'. Only target a different area if the "
                f"user explicitly names one."
            )
        return result

    def _get_enabled_skills(self) -> list[Skill]:
        """Get enabled skills as list for template rendering."""
        enabled_skill_names = self.skills
        all_skills = self.skill_manager.get_all_skills()

        return [s for s in all_skills if s.name in enabled_skill_names]

    def _get_exposed_entities(self) -> list[dict[str, Any]]:
        return get_exposed_entities(self.hass)

    def _get_function_tools(self) -> list[dict[str, Any]]:
        """Get custom functions configuration."""
        try:
            function_tools_config = self.subentry.data.get(CONF_FUNCTION_TOOLS)
            function_tools: list[dict[str, Any]] | None = (
                yaml.safe_load(function_tools_config)
                if function_tools_config
                else DEFAULT_CONF_FUNCTION_TOOLS
            )
            if function_tools:
                for function_tool in function_tools:
                    if isinstance(function_tool, dict) and "function" in function_tool:
                        function_config = function_tool["function"]
                        if (
                            isinstance(function_config, dict)
                            and "type" in function_config
                        ):
                            function = get_function(function_config["type"])
                            function_tool["function"] = function.validate_schema(
                                function_config
                            )

            return function_tools or []
        except (InvalidFunction, FunctionNotFound) as e:
            raise e
        except Exception as e:
            raise FunctionLoadFailed() from e
