"""Constants for the Home Agent Edge integration."""

from __future__ import annotations

DOMAIN = "home_agent_edge"

CONF_ENDPOINT = "endpoint"
CONF_ENTITY_IDS = "entity_ids"
CONF_BLOCKED_ENTITY_IDS = "blocked_entity_ids"
CONF_BLOCKED_USER_IDS = "blocked_user_ids"
CONF_INCLUDE_CONVERSATION_TEXT = "include_conversation_text"
CONF_CLIENT_CERT = "client_cert"
CONF_CLIENT_KEY = "client_key"
CONF_CA_CERT = "ca_cert"
CONF_EDGE_TOKEN_PATH = "edge_token_path"
CONF_SPOOL_PATH = "spool_path"
CONF_SPOOL_KEY_PATH = "spool_key_path"
CONF_SPOOL_MAX_BYTES = "spool_max_bytes"
CONF_SPOOL_MAX_AGE_SECONDS = "spool_max_age_seconds"

DEFAULT_ENTITY_IDS = (
    "person.engineeredlighting",
    "device_tracker.iphone",
    "zone.home",
)
# Raw location observations must not live below Home Assistant backup roots.
# Operators that need replay across container replacement should mount this
# runtime directory from encrypted local storage and explicitly exclude it
# from every backup job.
DEFAULT_SPOOL_PATH = "/tmp/home_agent_edge/runtime.sqlite"
DEFAULT_SPOOL_KEY_PATH = "/config/.storage/home_agent_edge_spool.key"
DEFAULT_EDGE_TOKEN_PATH = "/config/secrets/home_agent_edge_token"
DEFAULT_SPOOL_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_SPOOL_MAX_AGE_SECONDS = 24 * 60 * 60
DELIVERY_INTERVAL_SECONDS = 5
MAINTENANCE_INTERVAL_SECONDS = 60
DELIVERY_BATCH_SIZE = 100

EVENT_CONVERSATION_FINISHED = "extended_openai_conversation.conversation.finished"
EVENT_GAP = "home_agent_edge.gap"
SCHEMA_VERSION = 1
SOURCE_NAME = "home_assistant"

DATA_RUNTIME = "runtime"
DATA_VIEW_REGISTERED = "whoami_view_registered"

WHOAMI_URL = "/api/home_agent_edge/whoami"
WHOAMI_NAME = "api:home_agent_edge:whoami"
