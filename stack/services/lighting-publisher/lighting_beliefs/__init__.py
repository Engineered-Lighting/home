"""lighting_beliefs: the Living Lights belief publisher's pure-logic core.

This package holds the five Jev questions as data, the probability-only
reduction of System One answers, the house-level packet builder with its
allow-list, the leak guard that blocks a packet before it can leave the house,
and the egress gate that decides whether a call may be made at all.

No module here opens a socket. The network client (the TypeSafe SDK behind the
optional ``cloud`` extra) is wired in a later milestone and must go through
``EgressGate`` and ``leak_guard.assert_clean`` on every call.
"""
from .questions import QUESTION_IDS, build_questions, questions_as_dicts
from .reduce import reduce_answers
from .packet import PACKET_SCHEMA, build_packet, content_signature, validate_packet
from .leak_guard import LeakError, assert_clean, scan_packet
from .egress import EgressGate

__all__ = [
    "QUESTION_IDS", "build_questions", "questions_as_dicts",
    "reduce_answers",
    "PACKET_SCHEMA", "build_packet", "content_signature", "validate_packet",
    "LeakError", "assert_clean", "scan_packet",
    "EgressGate",
]
__version__ = "0.1.0"
