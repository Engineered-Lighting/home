"""Tools for Extended OpenAI Conversation."""

from __future__ import annotations

from ..exceptions import FunctionNotFound
from .base import Function
from .disabled import DisabledModelActionFunction
from .bash import BashFunction
from .composite import CompositeFunction
from .file import EditFileFunction, ReadFileFunction, WriteFileFunction
from .frigate import FrigateFunction
from .gesture_training import GestureTrainingFunction
from .living_lights import LivingLightsFunction
from .native import NativeFunction
from .recap import RecapFunction
from .registry import RegistryFunction
from .script import ScriptFunction
from .sqlite import SqliteFunction
from .template import TemplateFunction
from .web import RestFunction, ScrapeFunction
from .world_state import WorldStateFunction

__all__ = [
    "BashFunction",
    "CompositeFunction",
    "DisabledModelActionFunction",
    "EditFileFunction",
    "FrigateFunction",
    "GestureTrainingFunction",
    "Function",
    "LivingLightsFunction",
    "NativeFunction",
    "ReadFileFunction",
    "RecapFunction",
    "RegistryFunction",
    "RestFunction",
    "ScrapeFunction",
    "ScriptFunction",
    "SqliteFunction",
    "TemplateFunction",
    "WorldStateFunction",
    "WriteFileFunction",
    "get_function",
]

_DISABLED_ACTION = DisabledModelActionFunction()

FUNCTIONS: dict[str, Function] = {
    "native": NativeFunction(),
    "script": _DISABLED_ACTION,
    "template": TemplateFunction(),
    "rest": _DISABLED_ACTION,
    "scrape": ScrapeFunction(),
    "composite": _DISABLED_ACTION,
    "sqlite": SqliteFunction(),
    "bash": _DISABLED_ACTION,
    "read_file": ReadFileFunction(),
    "write_file": _DISABLED_ACTION,
    "edit_file": _DISABLED_ACTION,
    "world_state": WorldStateFunction(),
    "registry": RegistryFunction(),
    "frigate": FrigateFunction(),
    "gesture_training": _DISABLED_ACTION,
    "recap": RecapFunction(),
    "living_lights": LivingLightsFunction(),
}


def get_function(function_type: str) -> Function:
    """Get function by function_config."""
    function = FUNCTIONS.get(function_type)
    if function is None:
        raise FunctionNotFound(function_type)
    return function
