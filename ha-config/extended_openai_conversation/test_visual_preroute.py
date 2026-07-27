#!/usr/bin/env python3
"""Focused regression tests for deterministic live-camera prerouting."""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


# conversation.py imports Home Assistant, which is unavailable in the
# workstation/HAOS standalone test environment.  Execute its pure routing
# block directly so these fixtures exercise the production definitions.
source = (Path(__file__).parent / "conversation.py").read_text(encoding="utf-8")
start = source.find("_VISUAL_QUERY_PUNCTUATION_TRANSLATION =")
end = source.find("# Module-import sentinel", start)
if start < 0 or end < 0:
    raise RuntimeError(f"could not locate visual routing block ({start=}, {end=})")

namespace: dict[str, Any] = {
    "Any": Any,
    "re": re,
    "unicodedata": unicodedata,
}
exec(source[start:end], namespace, namespace)
route = namespace["_should_preroute_grounded_look"]


def _assert_route(prompt: str, expected_room: str) -> None:
    result = route(prompt)
    assert result is not None, f"expected a live-camera route for {prompt!r}"
    assert result["room"] == expected_room, result
    assert result["question"] == prompt, "the original user wording must be preserved"


def main() -> int:
    # Exact production regression: iOS/Safari supplied a curly apostrophe.
    _assert_route("What\u2019s on my coffee table", "living_room")
    _assert_route("What's on my coffee table", "living_room")

    # Comparable harmless typography should not disable deterministic routing.
    _assert_route("What\u2019s on my coffee\u2011table?", "living_room")

    # Generic definitions, historical/memory requests, and user-supplied media
    # are not requests to inspect a current home camera.
    false_positives = (
        "What is a coffee table?",
        "What is the history of my coffee table?",
        "Do you remember what\u2019s on my coffee table?",
        "What\u2019s usually on my coffee table?",
        "What\u2019s on my coffee table in this photo?",
        "What\u2019s on my coffee table according to your memory?",
        "What\u2019s on the periodic table?",
        "What\u2019s on my carpet?",
    )
    for prompt in false_positives:
        assert route(prompt) is None, f"must not inspect a live camera for {prompt!r}"

    print("PASS: 3 live-camera variants")
    print(f"PASS: {len(false_positives)} non-live false-positive guards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
