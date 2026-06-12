"""Pass-2 strict JSON contract + tolerant extraction.

The activity enum is deliberately ACTIVE_SET + a literal "other" (plan
red-team: the full 23-class enum invites hallucinated rare classes that
poison rare-class triage). "other" is NOT a storable value -- the prelabel
job maps it to 'unknown' and preserves activity_other_hint as evidence.
Posture has no staged activation: the full 14-value enum is legal.

parse_pass2() tolerates fenced/inline JSON in prose and raises ValueError
with a message suitable for feeding back to the model on retry.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from .. import ontology

VLM_ACTIVITY_VALUES = tuple(ontology.ACTIVE_SET) + ("other",)
VLM_POSTURE_VALUES = tuple(ontology.POSTURE)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class Pass2Uncertainty(BaseModel):
    activity: float = Field(ge=0.0, le=1.0)
    posture: float = Field(ge=0.0, le=1.0)


class Pass2Result(BaseModel):
    activity_primary: str
    activity_other_hint: Optional[str] = None
    posture: str
    quality_flags: List[str] = []
    uncertainty: Pass2Uncertainty
    evidence: str
    evidence_timestamps: List[float] = []

    @field_validator("activity_primary")
    @classmethod
    def _activity_in_enum(cls, v):
        if v not in VLM_ACTIVITY_VALUES:
            raise ValueError(
                f"'{v}' is not allowed; activity_primary must be one of"
                f" {list(VLM_ACTIVITY_VALUES)}")
        return v

    @field_validator("posture")
    @classmethod
    def _posture_in_enum(cls, v):
        if v not in VLM_POSTURE_VALUES:
            raise ValueError(
                f"'{v}' is not allowed; posture must be one of"
                f" {list(VLM_POSTURE_VALUES)}")
        return v

    @field_validator("quality_flags")
    @classmethod
    def _flags_subset(cls, v):
        bad = [f for f in v if f not in ontology.QUALITY_FLAGS]
        if bad:
            raise ValueError(
                f"unknown quality_flags {bad}; allowed:"
                f" {list(ontology.QUALITY_FLAGS)}")
        return list(dict.fromkeys(v))  # dedupe, order kept


def extract_json(text) -> dict:
    """First JSON object found in a model reply: tries the raw text, then a
    fenced ``` block, then a balanced-object scan from each '{'. Raises
    ValueError when no object parses."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty response, expected a JSON object")
    candidates = [text.strip()]
    m = _FENCE_RE.search(text)
    if m:
        candidates.insert(0, m.group(1).strip())
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        idx = text.find("{", idx + 1)
    raise ValueError("no JSON object found in response")


def parse_pass2(text) -> Pass2Result:
    """Model reply text -> validated Pass2Result. Raises ValueError carrying
    the json/pydantic problem (appended to the retry prompt)."""
    data = extract_json(text)
    try:
        return Pass2Result.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"schema validation failed: {e}") from e
