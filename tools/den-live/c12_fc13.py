"""Pure encoder for the C12 v2-a FC13 contract (contract_version 1)."""

import hashlib
import json
import math
import struct


FRAME = "c12-fixture-joints-1"
ENDPOINT = 18
CLUSTER = 0xFC13
MOVE = 0x00
KEEP_LIT = 0x01
STOP = 0x02
RESUME = 0x03
DEFAULT_VALID_MS = 1000


class Refusal(ValueError):
    """The request has no admissible FC13 representation."""


def _integer(value, low, high, field):
    if type(value) is not int or not low <= value <= high:
        raise Refusal("invalid_" + field)
    return value


def _number(value, low, high, field):
    if type(value) not in (int, float) or not low <= value <= high:
        raise Refusal("invalid_" + field)
    return value


def _pair(values, field):
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise Refusal("invalid_" + field)
    return values


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip():
        raise Refusal("missing_" + field)
    return value


def _target_and_route(intent, profile, current_joints):
    if profile.get("frame") != FRAME or intent.get("frame", FRAME) != FRAME:
        raise Refusal("unsupported_joint_frame")
    bounds = profile.get("range_cdeg")
    if not isinstance(bounds, dict):
        raise Refusal("invalid_range_cdeg")
    inset = _integer(profile.get("host_inset_cdeg"), 0, 2000, "host_inset_cdeg")
    limits = []
    for axis, maximum in (("pan", 18000), ("tilt", 10000)):
        low, high = _pair(bounds.get(axis), axis + "_range")
        _integer(low, -maximum, maximum, axis + "_min")
        _integer(high, -maximum, maximum, axis + "_max")
        if low + inset >= high - inset:
            raise Refusal("empty_inset_range")
        limits.append((low + inset, high - inset))
    if current_joints is None:
        raise Refusal("unknown_start_joints")
    if isinstance(current_joints, dict):
        if current_joints.get("frame", FRAME) != FRAME:
            raise Refusal("unsupported_joint_frame")
        current_joints = (current_joints.get("pan_cdeg"), current_joints.get("tilt_cdeg"))
    start = _pair(current_joints, "current_joints")
    target = (intent.get("pan_cdeg"), intent.get("tilt_cdeg"))
    for axis, origin, end, (low, high) in zip(("pan", "tilt"), start, target, limits):
        _integer(origin, low, high, "current_" + axis + "_cdeg")
        _integer(end, low, high, axis + "_cdeg")
        if abs(end - origin) > 18000:
            raise Refusal("travel_exceeds_180_degrees")
    return target, tuple(start) != target


def _velocity(intent, profile, mode, moving):
    pan, tilt = _pair(intent.get("velocity_cdeg_s", [0, 0]), "velocity_cdeg_s")
    _integer(pan, -32768, 32767, "pan_velocity_cdeg_s")
    _integer(tilt, -32768, 32767, "tilt_velocity_cdeg_s")
    if pan or tilt:
        if not moving or mode == 3 or profile.get("terminal_velocity") is not True:
            raise Refusal("terminal_velocity_unsupported")
        max_pan, max_tilt = _pair(profile.get("max_velocity_cdeg_s"), "max_velocity_cdeg_s")
        _integer(max_pan, 1, 32767, "max_pan_velocity_cdeg_s")
        _integer(max_tilt, 1, 32767, "max_tilt_velocity_cdeg_s")
        if abs(pan) > max_pan or abs(tilt) > max_tilt:
            raise Refusal("terminal_velocity_out_of_range")
    return pan, tilt


def _move(intent, profile, seq, source, current_joints, remaining_ms):
    mode = _integer(profile.get("motion_mode"), 0, 3, "motion_mode")
    if mode == 0:
        raise Refusal("lights_only_profile")
    (pan, tilt), moving = _target_and_route(intent, profile, current_joints)
    vel_pan, vel_tilt = _velocity(intent, profile, mode, moving)

    level = _number(intent.get("level"), 0, 1, "level")
    spot_level = math.floor(level * 254 + 0.5)
    kelvin = _number(intent.get("cct_k"), 1, 1_000_000, "cct_k")
    spot_mired = math.floor(1_000_000 / kelvin + 0.5)
    _integer(spot_mired, 154, 556, "spot_mired")

    valid_ms = _integer(intent.get("valid_for_ms", 1500), 0, 65535, "valid_for_ms")
    if remaining_ms is not None:
        if type(remaining_ms) is not int or remaining_ms <= 0:
            raise Refusal("command_expired")
        if valid_ms == 0 and remaining_ms < DEFAULT_VALID_MS:
            raise Refusal("command_expired")
        if valid_ms:
            valid_ms = min(valid_ms, remaining_ms)
    valid_ms = min(valid_ms, 1500)

    permitted_lit = intent.get("lit_during_move", False)
    if type(permitted_lit) is not bool:
        raise Refusal("invalid_lit_during_move")
    style = intent.get("motion_style")
    if style is None:
        if intent.get("level_during_move") is not None:
            style = "dim"
        else:
            style = "lit" if permitted_lit else "dark"
    if style not in ("lit", "dim", "dark"):
        raise Refusal("unsupported_motion_style")
    lit = (moving and spot_level > 0 and style == "lit" and permitted_lit
           and mode != 3 and profile.get("lit_motion") is True)
    flags = 0x08
    if lit:
        flags |= 0x01
    if source == "hand":
        flags |= 0x02
    if vel_pan or vel_tilt:
        flags |= 0x04
    payload = struct.pack("<HBhhBHhhH", seq, flags, pan, tilt, spot_level, spot_mired,
                          vel_pan, vel_tilt, valid_ms)
    return payload, {
        "source": source, "pan_cdeg": pan, "tilt_cdeg": tilt, "spot_level": spot_level,
        "spot_mired": spot_mired, "flags": flags, "velocity_cdeg_s": [vel_pan, vel_tilt],
        "valid_ms": valid_ms, "level": spot_level / 254, "cct_k": 1_000_000 / spot_mired,
        "lit_during_move": lit, "requested_motion_style": style,
        "motion_style": ("lit" if lit else "dark") if moving else "stationary",
        "fade_supported": False, "dim_supported": False,
    }


def encode(intent, profile, *, seq, current_joints=None, token=None, remaining_ms=None):
    """Return the exact client command and its effective fields. No transport or mutable state."""
    if not isinstance(intent, dict) or not isinstance(profile, dict):
        raise Refusal("invalid_encoder_input")
    _integer(seq, 0, 65535, "seq")
    fixture_id = _identifier(intent.get("fixture_id"), "fixture_id")
    if profile.get("fixture_id", fixture_id) != fixture_id:
        raise Refusal("fixture_profile_mismatch")
    if "profile_sha256" in intent:
        canonical = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != intent["profile_sha256"]:
            raise Refusal("fixture_profile_mismatch")
    action = intent.get("action")
    source = intent.get("source", "coordinator")
    if source not in ("coordinator", "hand"):
        raise Refusal("invalid_source")
    effective = {"source": source}
    if action == "aim_move":
        _identifier(intent.get("decision_id"), "decision_id")
        payload, effective = _move(intent, profile, seq, source, current_joints, remaining_ms)
        command_id = MOVE
    elif action == "keep_lit":
        _identifier(intent.get("decision_id"), "decision_id")
        if source != "coordinator":
            raise Refusal("keep_lit_requires_coordinator")
        _integer(token, 0, 65535, "token")
        payload = struct.pack("<HH", seq, token)
        effective["token"] = token
        command_id = KEEP_LIT
    elif action in ("stop", "resume"):
        payload = struct.pack("<H", seq)
        command_id = STOP if action == "stop" else RESUME
    else:
        raise Refusal("unsupported_action")
    return {"endpoint": ENDPOINT, "cluster": CLUSTER, "command_id": command_id,
            "payload": payload, "effective": effective}
