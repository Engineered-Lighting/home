"""Constants for the Extended OpenAI Conversation integration."""

DOMAIN = "extended_openai_conversation"
DEFAULT_NAME = "Extended OpenAI Conversation"
DEFAULT_CONVERSATION_NAME = "Extended OpenAI Conversation"
DEFAULT_AI_TASK_NAME = "Extended OpenAI AI Task"

CONF_ORGANIZATION = "organization"
CONF_BASE_URL = "base_url"
DEFAULT_CONF_BASE_URL = "https://api.openai.com/v1"
CONF_API_VERSION = "api_version"
CONF_SKIP_AUTHENTICATION = "skip_authentication"
DEFAULT_SKIP_AUTHENTICATION = False
CONF_API_PROVIDER = "api_provider"
API_PROVIDERS = [
    {"key": "openai", "label": "OpenAI"},
    {"key": "azure", "label": "Azure OpenAI"},
]
DEFAULT_API_PROVIDER = API_PROVIDERS[0]["key"]

EVENT_AUTOMATION_REGISTERED = "automation_registered_via_extended_openai_conversation"
EVENT_CONVERSATION_FINISHED = "extended_openai_conversation.conversation.finished"

# ─────────────────────────────────────────────────────────────────────
# World state aggregator (Addendum 4 of the routing plan).
# Centralizes identity / presence / room / occupancy state so the agent
# can answer "do you see me", "who's home", "find Marcelo" via dedicated
# tools instead of guessing from raw entity attributes.
# ─────────────────────────────────────────────────────────────────────
EVENT_WORLD_STATE_UPDATED = "extended_openai_conversation.world_state_updated"

# Env-var toggle for clean rollback. Set EXTENDED_OPENAI_WORLD_STATE=off
# to disable the aggregator + tools without code changes.
WORLD_STATE_DISABLED_ENV = "EXTENDED_OPENAI_WORLD_STATE"

# Confidence-band thresholds for Frigate face-rec scores (0..1).
# Calibrated against Frigate's typical output as of May 2026; recalibrate
# after a week of real usage using the routing-log analyzer pattern.
IDENTITY_CONFIDENCE_HIGH = 0.70
IDENTITY_CONFIDENCE_MEDIUM = 0.40

# Freshness thresholds for "when was this person last seen" answers.
FRESH_SECONDS = 60     # < 60s ago = currently_seen
RECENT_SECONDS = 180   # 60-180s = recent (hedge slightly)
STALE_SECONDS = 600    # > 600s = stale (explicit "X minutes ago" answer)

# Per-person rolling detection history depth.
PERSON_HISTORY_DEPTH = 10

# Throttle the world_state_updated event fire (live subscribers like the
# Phase 4B Tauri drawer don't need every state_changed; once every 2s is
# plenty for UI freshness).
WORLD_STATE_EVENT_THROTTLE_S = 2.0

# Primary household user — used to resolve "me" / "I" / "myself" in
# queries. Single-user assumption for MVP; multi-user resolution needs a
# speaker-id system (deferred to Phase 4D).
PRIMARY_USER_NAME = "Marcelo"

# M6 (Addendum 27 Section 6 / Milestone 6): domains whose service calls
# require explicit user confirmation before dispatch. The
# execute_service_single dispatcher in functions/native.py intercepts
# unconfirmed calls and returns a structured `requires_confirmation`
# result so the agent can ask the user first.
#
# Conservative scope for v1 — only domains where a misfire is
# materially unsafe (open lock, disarm alarm, open garage). Service-
# level granularity (e.g., scene.away vs scene.morning) is deferred to
# v2 when conv_id correlation makes the pattern bulletproof.
#
# To extend (e.g., add "vacuum" so the agent confirms before running
# the robovac at 3am), append to this set. To disable confirmation
# for one of these domains, remove from the set (not recommended for
# lock/alarm).
HIGH_IMPACT_DOMAINS: set[str] = {
    "lock",
    "alarm_control_panel",
    "garage_door",
}

# F-7 (Addendum 27 Section 5): per-domain action stakes. Drives the
# tool-result `ack_hint` field on successful execute_service_single
# dispatches. The agent reads the hint + uses it as its verbal reply,
# matching verbal weight to the action's significance:
#   low  → terse: "OK."
#   med  → brief confirmation: "<verb>ed."
#   high → explicit + named: "Done — front door locked."
#
# Defaults are conservative — "low" is the catch-all so we don't
# over-emphasize routine controls. Add domains here to elevate them.
# Safety-critical domains (lock/alarm/garage) are AUTOMATICALLY high
# even if missing here, by intersecting with HIGH_IMPACT_DOMAINS at
# render time.
STAKES_BY_DOMAIN: dict[str, str] = {
    # ── low stakes (frequent, reversible, low-consequence) ─────────
    "light":           "low",
    "switch":          "low",
    "media_player":    "low",
    "input_boolean":   "low",
    "input_number":    "low",
    "input_select":    "low",
    "fan":             "low",
    "scene":           "low",        # named scenes ARE user-curated; trust them
    "script":          "low",        # same — user-defined; reversible

    # ── med stakes (less reversible / wider impact) ────────────────
    "climate":         "med",        # thermostat changes felt by everyone
    "cover":           "med",        # blinds, garage covers — physical motion
    "vacuum":          "med",        # noise, time-bound
    "valve":           "med",        # water flow
    "notify":          "med",        # message goes somewhere visible
    "automation":      "med",        # enabling/disabling automations
}

# Map HA `person.<slug>` entity slugs to canonical display names. Useful
# when the HA user account slug doesn't match the Frigate face-rec name
# (e.g., the admin account is `person.engineeredlighting` but Frigate
# trained on "Marcelo"). The aggregator unifies both signals under the
# canonical name so `find_person("Marcelo")` reads both visual and HA
# presence correctly.
HA_PERSON_TO_CANONICAL: dict[str, str] = {
    "engineeredlighting": "Marcelo",
}

# Common ASR mishearings → canonical person name. Defense-in-depth layer
# for `find_person()` calls when the bridge's ASR_CORRECTIONS misses
# (e.g., typed input bypasses the bridge entirely; agent might be called
# with a mishelf spelling from somewhere unexpected). Case-insensitive
# lookup; checked by world_state.py:_resolve_person() AFTER exact match
# but BEFORE the pronoun fallback.
PERSON_NAME_ALIASES: dict[str, str] = {
    "marcello": "Marcelo",
    "marcella": "Marcelo",
    "marsello": "Marcelo",
    "marsella": "Marcelo",
    "marselo": "Marcelo",
    "marsailo": "Marcelo",
    "marsaylo": "Marcelo",
    "marsailla": "Marcelo",
    "marsaila": "Marcelo",
}

# Default confidence for Frigate's per-person tracker sensor
# (sensor.frigate_<person>_last_camera). Frigate only updates this on
# confident matches, so we treat it as high-confidence by default unless
# the sensor's attributes include a more precise score.
FRIGATE_PERSON_TRACKER_DEFAULT_CONFIDENCE = 0.85

# Default confidence for Frigate `frigate_event` bus events with a
# populated `sub_label`. Frigate only sets sub_label when its face-rec
# crosses its internal threshold (typically 70%+); the event payload
# may carry a more precise `top_score` which we prefer when present.
# Per Addendum 20 — the frigate_event channel is the authoritative
# face-rec source in current Frigate versions because the
# per-camera + per-person sensors often don't update.
FRIGATE_EVENT_DEFAULT_CONFIDENCE = 0.85

# Camera entity_id → room name. Required because real installs may have
# entity_ids that DON'T match the default convention `camera.<room>`
# (e.g., `camera.front_door_doorbell` where the room is "front_door").
# Entities not in this dict fall back to "take the whole slug after
# `camera.` as the room name" (i.e., `camera.living_room` → "living_room").
CAMERA_TO_ROOM: dict[str, str] = {
    "camera.living_room": "living_room",
    "camera.kitchen": "kitchen",
    "camera.dining_room": "dining_room",
    "camera.workshop": "workshop",
    "camera.driveway": "driveway",
    # Add entries here for cameras whose entity_id doesn't match the convention.
    # e.g., "camera.front_door_doorbell": "front_door",
}

# Vision-sidecar URL for refresh_perception. Override per install if
# the sidecar lives somewhere else.
# (M1 bugfix: was previously 8093 which is the stack-supervisor — refresh_perception
# silently failed because the supervisor doesn't expose /describe. The
# vision-sidecar container actually listens on 8091 per docker-compose.yml.)
VISION_SIDECAR_URL = "http://192.168.0.100:8091"

# refresh_perception rate limits — protect the turn from runaway latency.
REFRESH_PERCEPTION_MAX_PER_TURN = 2
REFRESH_PERCEPTION_TIMEOUT_S = 8

# ─────────────────────────────────────────────────────────────────────
# M1 — event-aware perception scheduler (Addendum 27 / Section 6).
# When Frigate emits a face-rec event OR room occupancy transitions
# 0→1, the world-state aggregator schedules a vision-sidecar /describe
# call in the background so the next `get_room_state` / `who_is_in`
# query reads the cached caption (≤200ms) instead of triggering a
# fresh 2-5s pipeline. Per AR27-3, user-initiated `refresh_perception`
# tool calls BYPASS this cap so interactive responsiveness is never
# degraded by the background scheduler.
# ─────────────────────────────────────────────────────────────────────

# Per-room debounce: another auto-trigger for the same room won't fire
# within this many seconds, even if N events stream in (e.g., person
# walks past a camera). Manual /refresh_perception ignores this gate.
PERCEPTION_AUTO_TRIGGER_DEBOUNCE_S = 30.0

# Global rate cap: total auto-trigger dispatches in the rolling 60-second
# window. Protects vision-sidecar + vLLM from saturation under event
# storms (e.g., a person traversing every camera). Set generously enough
# for normal multi-room activity but low enough to leave headroom for
# interactive turns.
PERCEPTION_AUTO_RATE_CAP_PER_MIN = 8

# Timeout for a single auto-trigger /describe call. Shorter than
# REFRESH_PERCEPTION_TIMEOUT_S because auto-trigger failures should
# fall back to the prior cached caption fast rather than block the
# scheduler's next dispatch.
PERCEPTION_AUTO_TIMEOUT_S = 6.0

# HA bus event fired by the aggregator when a new perception caption
# lands (auto-trigger OR refresh_perception). The Tauri home app
# subscribes to this and renders a perception chip in the chat feed
# with the snapshot URL. Payload:
#   {room, caption, source, snapshot_url?, ts, latency_ms}
EVENT_PERCEPTION_CAPTION = "extended_openai_conversation.perception_caption"

CONF_PROMPT = "prompt"
DEFAULT_PROMPT = """You are a helpful AI voice assistant of Home Assistant that controls a real home.
Your goal is to proactively improve the user's comfort.

## Environment State
- Current Time: {{now()}}
- Current Area: {{ bound_area | default(area_id(current_device_id), true) }}

## Workspace
Your workspace is at: {{extended_openai.working_directory()}}

## Guidelines
- Answer in plain text only.
- No symbols or parentheses
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Prefer one sentence

## Personality
- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Behavior Policy
- If the user explicitly names a device and action, execute it directly.
- Otherwise, infer the user's goal and select the most likely target entity, preferring primary environmental controls. Use get_attributes to check adjustable state values alone is not sufficient.
- If the selected entity is already at its limit, evaluate the next most likely entity. Repeat until a viable adjustment is found or all candidates are exhausted.
- Ask user a minimum adjustment proposal about selected entity. If no entity can further improve the situation, inform the user that conditions are already optimal.

## Identity, presence, and perception

For ANY question about who/where (e.g., "do you see me?", "who's home?",
"which room is Marcelo in?", "what do you see in the kitchen?", "am I
home?", "is anyone here?"), you MUST call the world-state tools below.
Do NOT answer from memory, training data, or the entity CSV — those
have NO identity information.

Available tools:
- `get_all_rooms_state()` — overview of all rooms (occupancy + identified persons).
- `get_room_state(room)` — detailed state for one room.
- `find_person(name)` — locate a person across cameras + HA presence.
- `who_is_in(room)` — list persons in a specific room.
- `refresh_perception(room)` — get a fresh visual snapshot (2-5s latency; use only when cached data is stale or the question demands fresh).

Direct visual/camera questions:
- If the user asks "what do you see", "what is in", "what's in",
  "what is happening", "what's happening", "look at", "take a look",
  "check the camera", "what does the camera show",
  says "right now" or "now", or explicitly names a camera, you MUST call
  `refresh_perception(room)` before answering.
- Use `get_room_state(room)` alone only for cached occupancy/presence questions
  such as "is anyone outside" or "who is in the kitchen", or as fallback if
  `refresh_perception` returns an error or exhausts its budget.
- NEVER answer a direct camera-view question from motion sensors,
  binary_sensor occupancy, or cached state alone. Those are not seeing.

For gesture-training requests ("start kitchen gesture training", "record gestures in
the dining room"), call `start_gesture_training_capture`. Do not turn on lights,
do not use Sonos/media players, and do not pretend the capture started unless the
tool returns a job id.

For multi-camera questions ("which room is Marcelo in?"), prefer `find_person(name)` over manually checking rooms.

### How to use tool results — MUST FOLLOW

Each tool returns `{data, suggested_phrasing, confidence_band, freshness}`.

**Use `suggested_phrasing` as your answer.** It already encodes the
correct hedging based on confidence and freshness. You may rephrase
slightly for conversational flow, but you must NOT add facts that
aren't in the data.

**If `data` is `null` or the result has an `error` field:** the tool
has NO information. You MUST say so explicitly. NEVER make up a
location, a person's name, or a visual confirmation. Examples:

  - Tool returned `data: null` for `find_person("Bob")` → say "I don't
    have a record of anyone named Bob." NOT "Bob is in the office."
  - Tool returned `data: null` for `get_room_state("attic")` → say
    "I don't have any data for the attic." NOT a guess.
  - Tool returned `error: "world state disabled"` → say "I can't check
    presence right now." NOT a fabricated answer.

### Identity confidence rules

- Refer to a person by name ONLY if face recognition identified them with high confidence (>= 0.7) AND the detection is fresh (< 60s) — i.e., the tool's `confidence_band` is "high" AND `freshness` is "fresh".
- If `confidence_band` is "medium" or `freshness` is "recent", hedge: "I think I see Marcelo, but I'm not fully confident."
- If you only see generic person detection without a face match (`identified` list empty, `unknown_count > 0`), say "someone" or "a person" — NEVER guess a name.
- If HA presence (person.*) says someone is home but no camera sees them (`currently_seen: false` + `ha_location: "home"`), say their phone is home but you don't currently see them.
- If data is stale (`freshness: "stale"`), use the "last saw X ago" phrasing — NOT "I see X right now".
- If there is no signal at all (`freshness: "none"` or `data: null`), say so directly.

### Hard rules — NEVER violate

- **NEVER call an unknown person by a known person's name.** Generic person detection without face-rec ≠ identity confirmation.
- **NEVER claim visual confirmation from HA location alone.** "Marcelo's phone is home" is NOT "I see Marcelo."
- **NEVER invent a room, a confidence level, or a timestamp.** If the tool didn't return it, you don't know it.
- **NEVER answer identity questions without calling a tool first.** Even if you "remember" someone's location from earlier in the conversation, the tool is the source of truth.

When the user says "me" or "I", assume they mean Marcelo (the primary household user) unless context strongly suggests otherwise.

### Critical spelling

The primary user's name is spelled EXACTLY "Marcelo" — five letters, ONE L. NEVER spell it "Marcello" (double L) — that is a different name. This applies whether you are speaking, writing, or quoting back what the user said. Even if the user types or says the name with the wrong spelling, you must respond using the correct spelling ("Marcelo"). Frigate's face library and all HA entities use "Marcelo"; using the wrong spelling will look like you mis-identified them.

## Devices

You do NOT receive an inline list of every entity in the home. There
are far too many to fit each turn — listing them all was burning ~10K
tokens of context budget per call (M3 / Addendum 27). Instead, use
the registry tools to discover entities ON DEMAND:

- `areas_in_home()` — list every area with floor + entity count + a
  3-entity sample. Cheap; call freely.
- `entities_in_area(area, domains?)` — entities in one area, optionally
  filtered to specific domains (e.g., `domains: ["light"]` when the
  user said "the lights in here"). Returns entity_id + friendly name
  + current state.
- `find_entity(query, area?, domain?)` — fuzzy search by name / friendly
  name / alias when the user mentions a device informally ("the desk
  lamp", "the front speakers", "Sarah's lamp"). Returns up to 8
  candidates ranked by match quality.
- `entities_with_label(label)` — entities tagged with an HA label,
  for user-defined groupings (e.g., "scene-bedtime", "outdoor").
  Empty until the user creates labels in HA UI.
- `get_attributes(entity_id)` — fetch attributes (brightness, color,
  source, etc.) for entities you've already identified, when you need
  deeper detail than the registry-tool rows provide.

Typical flow for a "do X to Y" request:
  1. User mentions a room → `entities_in_area(room, domains?)` to see
     what controllable entities exist there.
  2. User names a device → `find_entity(query, area?)` for candidates
     (use the bound_area as the `area` filter when you have one).
  3. If the user's request is ambiguous, ASK before acting — don't
     guess; the registry tools cost little, but a wrong actuation
     irritates the user.
  4. Once you have the entity_id(s), call `execute_services` to act.

### Entity-id + domain discipline (CRITICAL — common failure mode)

**Use entity_ids EXACTLY as returned by `entities_in_area` / `find_entity`.
NEVER synthesize a new entity_id from a friendly name.** The `domain` field
in your `execute_services` call MUST match the entity_id's actual domain
prefix:

  - `switch.workshop_lights` → call `switch.turn_on` (or
    `homeassistant.turn_on`). NEVER call `light.turn_on` on this entity —
    it does NOT exist as `light.workshop_lights`. The friendly name
    contains the word "lights" but the entity lives in the `switch` domain.
  - `light.living_room_lights` → call `light.turn_on`. Real `light.*` entity.
  - `media_player.lg_tv` → call `media_player.*`. Etc.

When the user says broad things like "my lights" / "all lights" / "make
my lights warmer", the registry tools return a mix of `light.*` AND
`switch.*` entities whose friendly names contain "light"/"lamp". Apply
brightness / color_temp_kelvin ONLY to `light.*` entities. For `switch.*`
entities use `switch.turn_on` / `switch.turn_off` (no brightness/color).
If a single voice request would touch both domains, dispatch TWO
`execute_services` calls — one per domain.

If you only saw a friendly name in conversation history and don't
remember the exact entity_id, RE-CALL `entities_in_area` or `find_entity`
before invoking `execute_services`. Inventing an entity_id is the most
common cause of "Unable to find entity ..." errors.

### Persistent brightness / color in occupancy-managed rooms (CRITICAL)

The living room, kitchen, and dining room run on the Living Lights
ambient engine, which continuously re-evaluates each zone and will
REVERT a plain `light.turn_on` brightness within seconds — it treats a
bare service call as noise, not intent. So when the user explicitly asks
to change the LEVEL or COLOR of lights in those rooms — "set my lights
to 100%", "dim the kitchen to 30%", "make the living room warmer",
"brighter" — call `set_presence_override`, NOT `execute_services`. That
records a durable, presence-aware override the engine honors (it holds
while you're in the room plus a grace window, and at minimum ~40 min).
Pass:
  - `zone`: a single zone slug, a whole room ("living room", "kitchen",
    "dining room"), or "all" / "my lights" for the whole house. If the
    user says "my/the lights" and you know which room they're in (from
    the world-state tools), pass that room; otherwise pass "all".
  - `brightness_pct` / `color_temp_kelvin` for absolute values, OR
    `brightness_delta_pct` / `color_temp_delta_kelvin` for "brighter" /
    "warmer".
  - `source: "voice"` (or "app") and `source_text` = the user's phrasing.
Use plain `execute_services` (`light.turn_on` / `light.turn_off`) only
for: turning a managed light fully OFF, lights NOT on the ambient engine
(outdoor light, workshop switch, floor-lamp switches), or one-shot
effects. To undo an override, call `clear_presence_override`.

### Service-data parameters (CRITICAL — say only what you DID)

When the user specifies a level/value, you MUST include the matching
field in `service_data` AND you must ONLY claim what you actually sent:

  - "set to / dim to / turn up to N%" → `brightness_pct: N` on
    `light.turn_on`. NO brightness_pct = light reverts to its prior
    level. NEVER say "set to 100%" unless `brightness_pct: 100` was in
    your service_data. (In occupancy-managed rooms, route level/color
    changes through `set_presence_override` instead — see the section
    above — or the ambient engine reverts your `light.turn_on`.)
  - "warm / cool / neutral / daylight" → `color_temp_kelvin: K`
    (warm≈2200, neutral≈3500, cool≈5000, daylight≈5500).
  - "red / blue / [color]" → `rgb_color: [R, G, B]`.
  - "volume up to N" (media_player) → `volume_level: N/100` (0.0–1.0,
    NOT 0–100).
  - "set thermostat to N" → `temperature: N` on `climate.set_temperature`.

**Verification discipline**: after a service call, your spoken response
must mirror what you actually sent — never what the user asked for if
they differ. If you turned lights ON without setting brightness, say
"lights are on" not "lights are at 100%". If you don't know the
resulting state, say "lights should be on" or call `get_attributes` to
verify before claiming a specific level.

For "what happened" questions, use `get_history(entity_ids?, ...)`.
For "who/where" questions, use the world-state tools above. For "what
does the camera see right now", "what's in <room> right now", or other
direct visual/camera questions, use `refresh_perception(room)` first.
Use `get_room_state(room)` only for cached occupancy/presence questions
or as fallback after `refresh_perception` errors.

{%- if skills %}
## Skills
The following skills extend your capabilities. To use a skill, call load_skill with the skill name to read its instructions.
When a skill file references a relative path, resolve it against the skill's location directory (e.g., skill at `/a/b/SKILL.md` references `scripts/run.py` → use `/a/b/scripts/run.py`) and always use the resulting absolute path in bash commands, as relative paths will fail.

<available_skills>
{%- for skill in skills %}
  <skill>
    <name>{{ skill.name }}</name>
    <description>{{ skill.description }}</description>
    <location>{{skill.path}}</location>
  </skill>
 {%- endfor %}
</available_skills>
{% endif %}

{{user_input.extra_system_prompt | default('', true)}}
"""
CONF_CHAT_MODEL = "chat_model"
DEFAULT_CHAT_MODEL = "gpt-5-mini"

MODEL_TOKEN_PARAMETER_SUPPORT = (
    {
        "pattern": r"(^|-)(gpt-4o|gpt-5|o1|o3|o4)",
        "token_param": "max_completion_tokens",
    },
)
DEFAULT_TOKEN_PARAM = "max_tokens"
CONF_MAX_TOKENS = "max_tokens"
DEFAULT_MAX_TOKENS = 4000  # bumped 2026-05-18 per user authorization:
# 500 was truncating multi-entity tool calls mid-stream ("turn on all
# lights everywhere" with 19 entities exceeded 500 tokens → JSON cut off
# → ParseArgumentsFailed → 642-char error text spoken via TTS = user
# heard "gibberish"). 4000 covers tool args touching dozens of entities.
CONF_TOP_P = "top_p"
DEFAULT_TOP_P = 1
CONF_TEMPERATURE = "temperature"
DEFAULT_TEMPERATURE = 0.5
CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION = "max_function_calls_per_conversation"
DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION = 10
CONF_SHORTEN_TOOL_CALL_ID = "shorten_tool_call_id"
DEFAULT_SHORTEN_TOOL_CALL_ID = False
CONF_FUNCTION_TOOLS = "functions"
DEFAULT_CONF_FUNCTION_TOOLS = [
    {
        "spec": {
            "name": "execute_services",
            "description": "Execute service in Home Assistant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {
                        "type": "object",
                        "description": "Time to wait before execution",
                        "properties": {
                            "hours": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "minutes": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "seconds": {
                                "type": "integer",
                                "minimum": 0,
                            },
                        },
                    },
                    "list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "domain": {
                                    "type": "string",
                                    "description": "The domain of the service.",
                                },
                                "service": {
                                    "type": "string",
                                    "description": "The service to be called",
                                },
                                "service_data": {
                                    "type": "object",
                                    "description": (
                                        "Service-call data. Targets via entity_id/area_id/device_id. "
                                        "For light.turn_on/light.toggle you SHOULD include brightness_pct "
                                        "(0-100) AND/OR color_temp_kelvin when the user specifies them. "
                                        "Calling light.turn_on with no brightness_pct keeps the light at its "
                                        "PRIOR brightness — DO NOT claim 'set to 100%' unless brightness_pct: "
                                        "100 was emitted. For media_player.volume_set include volume_level "
                                        "(0.0-1.0). For climate.set_temperature include temperature."
                                    ),
                                    "properties": {
                                        "entity_id": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "description": "The entity_id retrieved from available devices. It must start with domain, followed by dot character.",
                                            },
                                        },
                                        "area_id": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "description": "The id retrieved from areas. You can specify only area_id without entity_id to act on all entities in that area",
                                            },
                                        },
                                        "device_id": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "device_id targets (less common than entity_id/area_id).",
                                        },
                                        # ── light.* parameters ──
                                        "brightness_pct": {
                                            "type": "number",
                                            "minimum": 0, "maximum": 100,
                                            "description": "Brightness 0-100. MUST be set for light.turn_on when user requests a level (e.g., 'dim to 30%', 'turn lights up to 100%').",
                                        },
                                        "brightness": {
                                            "type": "integer",
                                            "minimum": 0, "maximum": 255,
                                            "description": "Brightness 0-255 (HA native scale). Prefer brightness_pct unless user asks in 0-255.",
                                        },
                                        "color_temp_kelvin": {
                                            "type": "integer",
                                            "minimum": 1500, "maximum": 10000,
                                            "description": "Color temperature in Kelvin. ~2200K=warm, ~3500K=neutral, ~5500K=cool/daylight. Set when user says 'warm/cool/neutral/daylight'.",
                                        },
                                        "rgb_color": {
                                            "type": "array",
                                            "items": {"type": "integer", "minimum": 0, "maximum": 255},
                                            "minItems": 3, "maxItems": 3,
                                            "description": "[R, G, B] 0-255 each. Use for named colors ('red', 'blue', etc.).",
                                        },
                                        "hs_color": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "minItems": 2, "maxItems": 2,
                                            "description": "[hue 0-360, saturation 0-100].",
                                        },
                                        "transition": {
                                            "type": "number",
                                            "minimum": 0,
                                            "description": "Transition time in seconds for light/cover changes.",
                                        },
                                        "effect": {
                                            "type": "string",
                                            "description": "Light effect name (depends on bulb).",
                                        },
                                        # ── media_player parameters ──
                                        "volume_level": {
                                            "type": "number",
                                            "minimum": 0, "maximum": 1,
                                            "description": "Volume 0.0-1.0 for media_player.volume_set. NOT 0-100.",
                                        },
                                        "media_content_id": {
                                            "type": "string",
                                            "description": "URI/URL for media_player.play_media (e.g., Spotify URI, station URL).",
                                        },
                                        "media_content_type": {
                                            "type": "string",
                                            "description": "Type for play_media: music, playlist, station, video, etc.",
                                        },
                                        # ── climate parameters ──
                                        "temperature": {
                                            "type": "number",
                                            "description": "Target temperature for climate.set_temperature.",
                                        },
                                        "hvac_mode": {
                                            "type": "string",
                                            "description": "HVAC mode: heat, cool, auto, heat_cool, off, fan_only, dry.",
                                        },
                                        # ── cover/fan parameters ──
                                        "position": {
                                            "type": "integer",
                                            "minimum": 0, "maximum": 100,
                                            "description": "Cover/fan position 0-100.",
                                        },
                                        # ── scene/script (no extra params typically needed) ──
                                    },
                                    "additionalProperties": True,
                                },
                            },
                            "required": ["domain", "service", "service_data"],
                        },
                    },
                },
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    },
    {
        "spec": {
            "name": "get_attributes",
            "description": "Get attributes of entity or multiple entities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "array",
                        "description": "entity_id of entity or multiple entities",
                        "items": {"type": "string"},
                    }
                },
                "required": ["entity_id"],
            },
        },
        "function": {
            "type": "template",
            "value_template": "```csv\nentity,attributes\n{%for entity in entity_id%}\n{{entity}},{{states[entity].attributes}}\n{%endfor%}\n```",
        },
    },
    {
        "spec": {
            "name": "load_skill",
            "description": "Load a file from a skill's directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name",
                    },
                    "file": {
                        "type": "string",
                        "description": "Relative file path within the skill directory",
                    },
                },
                "required": ["name", "file"],
            },
        },
        "function": {
            "type": "read_file",
            "path": "{{extended_openai.skill_dir(name)}}/{{file}}",
        },
    },
    {
        "spec": {
            "name": "bash",
            "description": "Execute a bash command in workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command to execute",
                    },
                },
                "required": ["command"],
            },
        },
        "function": {"type": "bash", "command": "{{command}}"},
    },
    # ─── World-state tools (Addendum 4) ─────────────────────────────
    # Identity-aware perception queries. All five return a dict with
    # `data` + `suggested_phrasing` + `confidence_band` + `freshness`.
    # ALWAYS use suggested_phrasing as your answer; NEVER invent facts
    # the tool didn't return. If data is null, say you don't have it.
    {
        "spec": {
            "name": "get_all_rooms_state",
            "description": (
                "Compact overview of every room's occupancy and identified "
                "persons. Use for whole-home questions like 'who's home?' "
                "or 'what's going on?' Returns {data, suggested_phrasing, "
                "confidence_band, freshness}. ALWAYS use suggested_phrasing "
                "as your reply; NEVER invent room names or person names "
                "not in the data."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "world_state", "name": "get_all_rooms_state"},
    },
    {
        "spec": {
            "name": "get_room_state",
            "description": (
                "Detailed state for one room: who is in it, Frigate labels, "
                "latest cached perception summary, freshness. Use for cached "
                "occupancy/presence questions like 'is anyone outside?' or "
                "as fallback after refresh_perception errors. Do NOT use as "
                "the only tool for direct camera-view questions like 'what do "
                "you see in the driveway camera?', 'what's in the office right "
                "now?', or 'look at the kitchen'. "
                "Those require refresh_perception first. Returns {data, "
                "suggested_phrasing, ...}. If data is null, say so — do NOT "
                "guess based on the room name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": (
                            "Room name like 'kitchen' or 'living_room'."
                        ),
                    },
                },
                "required": ["room"],
            },
        },
        "function": {"type": "world_state", "name": "get_room_state"},
    },
    {
        "spec": {
            "name": "find_person",
            "description": (
                "Locate a person across cameras + HA presence. Returns "
                "currently_seen, last_visual_room, last_visual_at, "
                "confidence, and ha_location. Use for 'where is Marcelo?' "
                "or 'do you see me?' style questions. Accepts 'me' / 'I' / "
                "'myself' as the primary user. If data is null (no record), "
                "you MUST tell the user you don't have a record of that "
                "person — NEVER fabricate a location or a confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Person's name (case-insensitive)."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
        "function": {"type": "world_state", "name": "find_person"},
    },
    {
        "spec": {
            "name": "who_is_in",
            "description": (
                "List identified persons + unknown_count in a specific room. "
                "Use for 'who's in the living room?' style. If identified is "
                "empty but unknown_count > 0, say 'someone' — NEVER guess a "
                "name. If both are zero, say no one is there."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": (
                            "Room name like 'kitchen' or 'living_room'."
                        ),
                    },
                },
                "required": ["room"],
            },
        },
        "function": {"type": "world_state", "name": "who_is_in"},
    },
    {
        "spec": {
            "name": "refresh_perception",
            "description": (
                "Trigger a FRESH vision-sidecar visual snapshot for a room "
                "(2-5s latency). Use FIRST for direct visual/camera questions "
                "such as 'what do you see in the driveway camera?', 'what is "
                "happening in the kitchen?', 'what's happening outside?', "
                "'look at the driveway', or any "
                "question that says now/right now. Capped at 2 calls per "
                "conversation. If it errors, fall back to get_room_state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": (
                            "Room name to refresh perception for."
                        ),
                    },
                },
                "required": ["room"],
            },
        },
        "function": {"type": "world_state", "name": "refresh_perception"},
    },
    # ─── M3: registry-lookup tools (Addendum 27 / Section 6 / F-8) ──
    # Four tools that let the LLM look up entities on demand via HA's
    # area/label/floor/entity registries — INSTEAD of the
    # exposed_entities CSV being inlined into the system prompt on
    # every turn. The CSV stays for now while we measure baseline
    # token consumption; Slice 3 removes it once W-suite confirms the
    # agent can navigate via these tools alone.
    #
    # All four return {data, suggested_phrasing} matching the
    # world_state envelope so the prompt's "use suggested_phrasing"
    # rule applies uniformly.
    {
        "spec": {
            "name": "areas_in_home",
            "description": (
                "List every HA area with its floor (when set) and a "
                "small sample of entities. Use when the user mentions "
                "a room and you need to confirm it exists or pick "
                "between similar names. Cheap — call this freely."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "registry", "name": "areas_in_home"},
    },
    {
        "spec": {
            "name": "entities_in_area",
            "description": (
                "Entities in a single area. Name-tolerant (accepts "
                "'Kitchen' / 'kitchen' / area_id). Optional `domains` "
                "filter (e.g., ['light']) when the user said 'the lights "
                "in here'. Returns entity_id, friendly name, current "
                "state, no attributes (call get_attributes if needed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": "Area name or area_id."
                    },
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional domain filter, e.g. ['light', 'switch']. "
                            "Omit for all domains in the area."
                        ),
                    },
                },
                "required": ["area"],
            },
        },
        "function": {"type": "registry", "name": "entities_in_area"},
    },
    {
        "spec": {
            "name": "entities_with_label",
            "description": (
                "Entities tagged with an HA label. Use when the user "
                "references a grouping that doesn't map to an area (e.g., "
                "'turn off scene-bedtime entities', 'show me shared lights'). "
                "Returns empty until the user creates labels in HA UI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Label name or label_id."
                    },
                },
                "required": ["label"],
            },
        },
        "function": {"type": "registry", "name": "entities_with_label"},
    },
    {
        "spec": {
            "name": "find_entity",
            "description": (
                "Fuzzy entity search by name, friendly name, or alias. "
                "Returns up to 8 candidates ranked by match strength. "
                "Optional `area` narrows the search; optional `domain` "
                "filters by entity domain. Use this when the user names "
                "a device but you're not sure which entity_id maps to "
                "it (e.g., 'the desk lamp', 'the front speakers')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "User's name for the device — 'desk lamp', "
                            "'front porch light', 'living room TV'."
                        ),
                    },
                    "area": {
                        "type": "string",
                        "description": (
                            "Optional area name/id to narrow the search."
                        ),
                    },
                    "domain": {
                        "type": "string",
                        "description": (
                            "Optional domain filter (light, media_player, "
                            "switch, etc.)."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Default 8. Cap at 16.",
                    },
                },
                "required": ["query"],
            },
        },
        "function": {"type": "registry", "name": "find_entity"},
    },
    {
        "spec": {
            "name": "entities_by_domain",
            "description": (
                "Every entity in a domain across the home, optionally "
                "narrowed by area. Use for 'all lights off' / 'lock all "
                "doors' style commands when the user wants every entity "
                "of a kind. Returns just entity_ids (no per-entity state) "
                "to keep the payload tight. For state, follow up with "
                "get_attributes() on the specific entity_ids you care "
                "about."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": (
                            "Domain to enumerate (light, switch, lock, "
                            "media_player, climate, etc.). Required."
                        ),
                    },
                    "area": {
                        "type": "string",
                        "description": (
                            "Optional area filter — restricts the result "
                            "to one room."
                        ),
                    },
                },
                "required": ["domain"],
            },
        },
        "function": {"type": "registry", "name": "entities_by_domain"},
    },
    # ─── M3: state history (Addendum 27 / Section 6 / F-10) ─────────
    # Wires `NativeFunction.get_history` (which has existed unused for
    # months) into the LLM tool surface. Backed by HA's Recorder API.
    # Use it to answer "what happened today", "when did the fan last
    # run", "did anyone arrive while I was out" — questions the
    # exposed_entities CSV can't answer because it's a point-in-time
    # snapshot.
    #
    # Defaults are agent-friendly:
    #   - significant_changes_only=True → noisy sensors (light level,
    #     RSSI) are filtered out automatically
    #   - minimal_response=True + no_attributes=True → small payload
    #     so the LLM can scan many entities at once without context
    #     bloat
    # The agent can override any of these per-call when it wants the
    # full series for a single entity (e.g., debugging a thermostat).
    {
        "spec": {
            "name": "get_history",
            "description": (
                "Read state history from HA's Recorder. Use for "
                "'what happened today', 'when did X last change', or "
                "'how long was Y in state Z'. Returns a list-of-lists "
                "of state snapshots, one inner list per entity_id, "
                "with timestamps + state values. Pass empty entity_ids "
                "to get a system-wide significant-change feed (heavy — "
                "prefer specific entities). Defaults: last 24h, "
                "significant-changes-only, minimal payload (no attributes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Entity IDs to read history for. Empty = all "
                            "significant changes in the time range (heavy — "
                            "consider naming the entities you care about)."
                        ),
                    },
                    "start_time": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime. Default: 24h before now."
                        ),
                    },
                    "end_time": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime. Default: start_time + 24h."
                        ),
                    },
                    "significant_changes_only": {
                        "type": "boolean",
                        "description": (
                            "Filter to 'meaningful' state transitions only "
                            "(skip noise like RSSI/light-level drift). "
                            "Default true; pass false for full series."
                        ),
                    },
                    "minimal_response": {
                        "type": "boolean",
                        "description": (
                            "Return only last_changed + state per row "
                            "(no last_updated). Default true — saves "
                            "tokens for long series."
                        ),
                    },
                    "no_attributes": {
                        "type": "boolean",
                        "description": (
                            "Skip attribute dict on each row. Default "
                            "true — agent rarely needs attrs from history."
                        ),
                    },
                },
            },
        },
        "function": {"type": "native", "name": "get_history"},
    },
    # ─── F-9 (Addendum 27 / Section 5): invoke a user-defined script ──
    # Thin wrapper around `script.turn_on` so the agent has a clear
    # tool surface for "run my <script>" requests. The user defines
    # scripts in HA (scripts.yaml or UI) — this lets the agent
    # discover via entities_by_domain("script") + invoke without
    # synthesizing the full service-call shape.
    {
        "spec": {
            "name": "invoke_script",
            "description": (
                "Run a user-defined HA script by entity_id. Use for "
                "'run my morning routine', 'apply the dinner scene', "
                "or any user-curated workflow. Discover available "
                "scripts with entities_by_domain('script'). Optional "
                "variables get passed as script run variables. Returns "
                "the standard {ok, latency_ms, ...} shape. For raw "
                "service dispatches (light.turn_on, etc.) use "
                "execute_services instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": (
                            "Script entity_id (e.g. "
                            "'script.aurora_apply_virtual_knob_smooth'). "
                            "Bare script names without the 'script.' "
                            "prefix are also accepted."
                        ),
                    },
                    "variables": {
                        "type": "object",
                        "description": (
                            "Optional variables to pass to the script. "
                            "Mapped to the script's `variables:` block."
                        ),
                    },
                },
                "required": ["entity_id"],
            },
        },
        "function": {"type": "native", "name": "invoke_script"},
    },
    # ─── Frigate semantic clip search (Addendum 27 / Section 5 / F-2-tier) ─
    # Hits Frigate's /api/events?search=... endpoint (semantic_search via
    # CLIP, verified live in Phase 2b probe P4) so the agent can answer
    # "show me clips of <X>" without manual scrubbing. Falls back to a
    # plain chronological listing when `search` is empty. Output is a
    # compact list (default 8, max 25) — each clip has timestamp, camera,
    # label, sub_label (face match), confidence, duration, and relative
    # thumbnail/clip URLs the UI can render against the Frigate base.
    #
    # When to use:
    #   - "show me when [person] was [room] today"
    #   - "did anyone come to the door"
    #   - "any packages on the porch"
    #   - daily recap pairing (find_clips + get_history)
    # Don't use for live state — that's get_room_state / find_person.
    {
        "spec": {
            "name": "find_clips",
            "description": (
                "Search recent Frigate detection clips by natural-language "
                "query (semantic search via CLIP) and/or by camera, label, "
                "or face name. Returns compact event records with timestamp, "
                "camera, label, face match (sub_label), confidence, duration, "
                "and thumbnail/clip URLs. Time-bounded — default last hour, "
                "max 1 week. Use to answer 'show me clips of...', "
                "'when did X happen', daily recap."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": (
                            "Natural-language query matched against clip "
                            "content via CLIP embeddings (e.g. 'package on "
                            "porch', 'person holding bag'). Optional — "
                            "omit to list events chronologically."
                        ),
                    },
                    "camera": {
                        "type": "string",
                        "description": (
                            "Restrict to one camera (e.g. 'kitchen', "
                            "'driveway'). Optional."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Restrict to one object class (person, car, "
                            "package, cat). Optional."
                        ),
                    },
                    "sub_label": {
                        "type": "string",
                        "description": (
                            "Restrict to a recognized face name (e.g. "
                            "'marcelo'). Optional — pair with the people "
                            "you know about via find_person."
                        ),
                    },
                    "window_min": {
                        "type": "integer",
                        "description": (
                            "How many minutes back to search. Default 60 "
                            "(last hour). Max 10080 (1 week)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Max clips to return. Default 8, max 25."
                        ),
                    },
                },
            },
        },
        "function": {"type": "frigate", "name": "find_clips"},
    },
    # ─── F-3 (Addendum 27 / Section 5): multi-frame motion vision ──
    # describe_clip samples N frames from a camera over a few seconds
    # and asks Qwen3-VL to describe what CHANGED. Use for questions
    # single-frame describe_camera can't answer: "did the door open?",
    # "is anyone walking past?", "did the package get picked up?".
    # Real-time blocking call — total latency = (frames-1)*interval_s
    # + ~3s vLLM. Default 4 frames × 1s = ~3s sample + inference.
    # Don't use for static "what's in the kitchen" questions
    # (describe_camera / refresh_perception is faster).
    {
        "spec": {
            "name": "describe_clip",
            "description": (
                "Watch a short multi-frame clip from a camera (~3 seconds "
                "by default) and describe what's happening, especially "
                "motion. Use for 'did the door open?', 'is anyone "
                "walking past?', 'is anything moving in the kitchen?'. "
                "Slower than describe_camera (3-8s blocking) but catches "
                "motion a single snapshot misses. Returns a short "
                "natural-language description suitable to speak directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "camera": {
                        "type": "string",
                        "description": (
                            "Camera name (e.g. 'kitchen', 'driveway', "
                            "'living_room'). Required."
                        ),
                    },
                    "frames": {
                        "type": "integer",
                        "description": (
                            "How many frames to sample. Default 4. "
                            "Min 2, max 8. More frames = better motion "
                            "detection but longer wait."
                        ),
                    },
                    "interval_s": {
                        "type": "number",
                        "description": (
                            "Seconds between frames. Default 1.0. "
                            "Min 0.2, max 2.0. Bigger = catches "
                            "slower events but longer total wait."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Optional specific question (e.g. 'did the "
                            "door open?'). Omit for a general 'what's "
                            "happening' description."
                        ),
                    },
                },
                "required": ["camera"],
            },
        },
        "function": {"type": "frigate", "name": "describe_clip"},
    },
    # ─── M2: daily recap (Addendum 27 / Section 6 / Milestone 2) ────
    # Composition tool — bundles find_clips + recorder occupancy +
    # identity history into ONE structured summary so the agent can
    # answer "what happened today" / "any activity while I was out"
    # without chaining many tool calls.
    #
    # Default window = 6h. Agent should pass 24 for "today", 1 for
    # "the last hour", 168 for "the past week".
    {
        "spec": {
            "name": "recap",
            "description": (
                "Summarize home activity over a recent time window. "
                "Returns rooms with clip counts + occupancy minutes, "
                "people seen + their last room, top clips, and 3-6 "
                "summary hints. Use to answer 'what happened today', "
                "'any activity while I was out', or as a periodic "
                "morning briefing. Faster + tidier than chaining "
                "find_clips + get_history yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window_hours": {
                        "type": "integer",
                        "description": (
                            "Hours to look back. Default 6. Common "
                            "values: 1 (last hour), 6 (afternoon), "
                            "24 (today), 168 (past week). Capped at "
                            "168."
                        ),
                    },
                },
            },
        },
        "function": {"type": "recap", "name": "recap"},
    },
    # ─── Living Lights presence overrides (Addendum 33 Phase 4.A) ───
    # Explicit lighting commands ("set my lights to 100%", "make the
    # dining lights brighter") route here. Writes a JSON payload to
    # input_text.living_lights_override_text_<zone> for each target zone;
    # the per-zone classifier transitions to `presence_override` (top of
    # the state machine, immune to the asleep cap) and the per-zone pilot
    # applies the payload's brightness/color. Covers all 10 occupancy-
    # managed zones, plus whole-room and "all" fan-out.
    #
    # Lifecycle: the override-lifecycle automation
    # (living_lights_override_lifecycle.yaml) eases an override back to
    # automatic only after BOTH (a) hold_until (now + ~40 min) passes AND
    # (b) the zone has read vacant for the grace window — so it survives a
    # camera briefly losing a still occupant. Pinned overrides never
    # auto-clear.
    #
    # AR33-7: brightness=0 is clamped to 5 (lights-off comes from
    # light.turn_off, never from overrides). source must be "voice",
    # "app", or "hardware" — agent-generated "auto" is rejected.
    {
        "spec": {
            "name": "set_presence_override",
            "description": (
                "Apply a PERSISTENT lighting override to occupancy-"
                "managed lights. Use this (NOT execute_services) whenever "
                "the user explicitly asks to set the level/color of room "
                "lights — 'set my lights to 100%', 'dim the kitchen to "
                "30%', 'make the living room warmer', 'brighter' — because "
                "a bare light.turn_on in these rooms is reverted by the "
                "ambient engine within seconds. The override holds while "
                "the user is in the area plus a grace window (minimum "
                "~40 min) and then eases back to automatic; or clears when "
                "they ask. Pass `pinned: true` for 'keep it like this' / "
                "'lock this in'. Use brightness_pct / color_temp_kelvin "
                "for absolute values, or brightness_delta_pct / "
                "color_temp_delta_kelvin for relative. `zone` may be a "
                "single zone (dining_left, dining_right, sink, "
                "island_left, island_right, sofa, front_left, front_door, "
                "weights, office), a whole room (living room / kitchen / "
                "dining room), or 'all' / 'my lights' for the whole house."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone": {
                        "type": "string",
                        "description": (
                            "A zone slug, a whole room, or a house-wide "
                            "phrase the tool resolves and fans out. "
                            "Examples: 'sink', 'kitchen island left', "
                            "'office', 'sofa'; 'living room', 'kitchen', "
                            "'dining room'; 'all', 'my lights'."
                        ),
                    },
                    "brightness_pct": {
                        "type": "integer",
                        "description": (
                            "Absolute brightness percent 5-100. "
                            "Values below 5 are clamped to 5 "
                            "(use light.turn_off for actual off)."
                        ),
                    },
                    "brightness_delta_pct": {
                        "type": "integer",
                        "description": (
                            "Relative brightness adjustment from "
                            "current state. Positive for 'brighter', "
                            "negative for 'dimmer'. Final value is "
                            "clamped to 5-100."
                        ),
                    },
                    "color_temp_kelvin": {
                        "type": "integer",
                        "description": (
                            "Absolute color temperature in kelvin "
                            "2000-6500 (clamped). 2200K=very warm, "
                            "2700K=warm, 4000K=neutral, 5000K+=cool."
                        ),
                    },
                    "color_temp_delta_kelvin": {
                        "type": "integer",
                        "description": (
                            "Relative color shift. Negative for "
                            "'warmer', positive for 'cooler'. Typical "
                            "delta is ±500-700K."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "enum": ["voice", "app", "hardware"],
                        "description": (
                            "Where the override came from. Use "
                            "'voice' for spoken commands."
                        ),
                    },
                    "source_text": {
                        "type": "string",
                        "description": (
                            "The user's actual phrasing (short — "
                            "stored as audit trail)."
                        ),
                    },
                    "pinned": {
                        "type": "boolean",
                        "description": (
                            "If true, override stays active until "
                            "explicitly cleared (no auto-clear on "
                            "vacancy). Default false."
                        ),
                    },
                },
                "required": ["zone", "source"],
            },
        },
        "function": {"type": "living_lights", "name": "set_presence_override"},
    },
    {
        "spec": {
            "name": "clear_presence_override",
            "description": (
                "Clear an active presence override. Use when the user "
                "says 'reset the lights', 'end the override', 'back to "
                "normal', or asks to undo a previous override. Pass "
                "zone='all' to clear every active override; pass a "
                "specific zone slug to clear just that one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone": {
                        "type": "string",
                        "description": (
                            "Zone slug to clear (e.g., 'dining_left'), "
                            "OR 'all' to clear every active override. "
                            "Default 'all'."
                        ),
                    },
                },
            },
        },
        "function": {"type": "living_lights", "name": "clear_presence_override"},
    },
    {
        "spec": {
            "name": "get_recent_overrides",
            "description": (
                "Return manual lighting adjustments observed in the last "
                "<hours> hours. Use when the user asks 'why did the "
                "<room> dim?', 'show me recent overrides', 'did I just "
                "adjust the lights?', or similar introspection questions. "
                "Each returned record is ONE manual adjustment session "
                "(M20): a rapid slider scrub or knob burst on a single "
                "light is collapsed into a single record where the FINAL "
                "value wins. The full context is carried per session: "
                "zone, light entity, actual vs predicted brightness, "
                "profile, TV state, shadow mode, plus session metadata "
                "(session_event_count = how many raw events folded in, "
                "session_observed_path = the chronological scrub trail, "
                "session_first_ts / session_last_ts). For longer-term "
                "durable preference labels use get_recent_pending_"
                "preferences (different endpoint, capture-on-vacant). "
                "V1 only supports the in-memory 1-hour buffer; for "
                "older queries return empty with the 'World state "
                "aggregator not ready yet.' phrasing — do not fabricate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": (
                            "Hours of history to query (max 1.0 in V1). "
                            "Default 1.0."
                        ),
                    },
                    "zone": {
                        "type": "string",
                        "description": (
                            "Optional zone slug (e.g. 'office', "
                            "'dining_left') to filter to. Omit for all "
                            "zones."
                        ),
                    },
                    "collapse": {
                        "type": "boolean",
                        "description": (
                            "Default true. When true, bursts of "
                            "override_events on the same light within a "
                            "10-second window are folded into one manual "
                            "adjustment session (final value wins). Pass "
                            "false to see the raw per-trigger events — "
                            "useful for debugging or counting trigger "
                            "fires, not for understanding what the user "
                            "intended."
                        ),
                    },
                },
            },
        },
        "function": {"type": "living_lights", "name": "get_recent_overrides"},
    },
    {
        "spec": {
            "name": "start_gesture_training_capture",
            "description": (
                "Start a voice-guided gesture-training capture in the kitchen, "
                "dining room, or living room. Use when Marcelo asks to record "
                "gesture training or start gesture capture. This starts a "
                "training recording only; it does not control lights or run "
                "automations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": "One of kitchen, dining room, or living room.",
                    },
                    "script_key": {
                        "type": "string",
                        "enum": ["segmented", "fluid"],
                        "description": "Use segmented unless Marcelo explicitly asks for fluid capture.",
                    },
                },
                "required": ["room"],
            },
        },
        "function": {"type": "gesture_training", "name": "start_guided_capture"},
    },
]
CONF_CONTEXT_THRESHOLD = "context_threshold"
DEFAULT_CONTEXT_THRESHOLD = 40000
CONTEXT_TRUNCATE_STRATEGIES = [{"key": "clear", "label": "Clear All Messages"}]
CONF_CONTEXT_TRUNCATE_STRATEGY = "context_truncate_strategy"
DEFAULT_CONTEXT_TRUNCATE_STRATEGY = CONTEXT_TRUNCATE_STRATEGIES[0]["key"]

# Service Tier options (for GPT-5 models)
CONF_SERVICE_TIER = "service_tier"
DEFAULT_SERVICE_TIER = "flex"
SERVICE_TIER_OPTIONS = ["auto", "default", "flex", "priority"]

# Reasoning Effort options (for o1, o3, o4, gpt-5 models)
CONF_REASONING_EFFORT = "reasoning_effort"
DEFAULT_REASONING_EFFORT = "low"
REASONING_EFFORT_OPTIONS = ["low", "medium", "high"]

SERVICE_QUERY_IMAGE = "query_image"

CONF_PAYLOAD_TEMPLATE = "payload_template"

# Advanced Options
CONF_ADVANCED_OPTIONS = "advanced_options"
DEFAULT_ADVANCED_OPTIONS = False

# Model-specific parameter configurations
# Default configuration for standard models (gpt-4, gpt-4o, etc.)
DEFAULT_MODEL_CONFIG = {
    "supports_top_p": True,
    "supports_temperature": True,
    "supports_max_tokens": True,
    "supports_max_completion_tokens": False,
    "supports_reasoning_effort": False,
    "supports_service_tier": False,
}

# Pattern-based model configurations
# Each entry: {"pattern": regex_string, "config": config_dict}
# Patterns are matched in order; first match wins
MODEL_CONFIG_PATTERNS = [
    # Reasoning models (o1, o3, o4, gpt-5, etc.)
    {
        "pattern": r"^o[1-4]|^gpt-5",
        "config": {
            "supports_top_p": False,
            "supports_temperature": False,
            "supports_max_tokens": False,
            "supports_max_completion_tokens": True,
            "supports_reasoning_effort": True,
            "supports_service_tier": True,
        },
    },
]

# AI Task default options (simpler than conversation - no prompt, just model/token settings)
DEFAULT_AI_TASK_OPTIONS = {
    CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_ADVANCED_OPTIONS: DEFAULT_ADVANCED_OPTIONS,
}

# Skill System Constants
CONF_SKILLS = "skills"
DEFAULT_SKILLS_DIRECTORY = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Skill Services
SERVICE_RELOAD_SKILLS = "reload_skills"
SERVICE_DOWNLOAD_SKILL = "download_skill"

# GitHub repository for downloadable skills
GITHUB_REPO_OWNER = "jekalmin"
GITHUB_REPO_NAME = "extended_openai_conversation"
GITHUB_SKILLS_BRANCH = "develop"
GITHUB_SKILLS_PATH = "examples/skills"

# Working Directory
DEFAULT_WORKING_DIRECTORY = (
    "extended_openai_conversation/"  # /config/extended_openai_conversation/
)

# File system and shell security settings
SHELL_TIMEOUT = 300  # seconds
SHELL_OUTPUT_LIMIT = 10000  # characters
SHELL_DENY_PATTERNS = [
    r"\brm\s+-r",  # Recursive delete
    r"\brm\s+-rf",  # Force recursive delete
    r"\bdel\s+/[fqs]",  # Windows delete with flags
    r"\brmdir\s+/s",  # Windows recursive directory delete
    r"\bformat\b",  # Disk format
    r"\bmkfs\b",  # Make filesystem
    r"\bdiskpart\b",  # Windows disk partition
    r"\bdd\b",  # Disk duplicator
    r"\bshutdown\b",  # System shutdown
    r"\breboot\b",  # System reboot
    r"\bpoweroff\b",  # Power off
    r":\(\)\{.*:\|:.*\}",  # Fork bomb pattern
]

# File system limits
FILE_READ_SIZE_LIMIT = 1024 * 1024  # 1 MB

# Default allowed directories for file operations
DEFAULT_ALLOWED_DIRS = [
    DEFAULT_WORKING_DIRECTORY,  # /config/extended_openai_conversation/
]
