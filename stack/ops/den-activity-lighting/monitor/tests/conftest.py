import copy
import hashlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MONITOR = os.path.dirname(HERE)
sys.path.insert(0, MONITOR)

from den_ops.hostio import check_readonly  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
CONFIG = os.path.join(MONITOR, "config")


def fixture_text(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


def fixture_json(name):
    return json.loads(fixture_text(name))


def write_tree(root, tree):
    """{dir: {file: text}} -> files under root."""
    for directory, files in tree.items():
        if directory.startswith("_"):
            continue
        os.makedirs(os.path.join(root, directory), exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(root, directory, name), "w", encoding="utf-8") as out:
                out.write(text)


class FakeHost:
    """The machine, replayed: canned command output, files and HTTP replies.
    Every command still goes through the real read-only allow-list."""

    def __init__(self, now=1_800_000_000.0):
        self.clock = now
        self.files = {}
        self.commands = {}          # argv tuple prefix -> (code, text)
        self.first_lines = {}
        self.responses = {}         # (method, url) -> (status, doc) or callable(body) -> (status, doc)
        self.ran = []
        self.calls = []

    def now(self):
        return self.clock

    def monotonic(self):
        return self.clock

    def file_sha256(self, path):
        value = self.read_text(path)
        return hashlib.sha256(value.encode()).hexdigest()

    def advance(self, seconds):
        self.clock += seconds

    def read_text(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def run(self, argv):
        check_readonly(argv)
        argv = tuple(argv)
        self.ran.append(argv)
        best = None
        for prefix, result in self.commands.items():
            if argv[: len(prefix)] == prefix and (best is None or len(prefix) > len(best[0])):
                best = (prefix, result)
        return best[1] if best else (127, "")

    def first_line(self, argv, timeout_s=5.0):
        check_readonly(argv)
        self.ran.append(tuple(argv))
        return self.first_lines.get(tuple(argv))

    def http(self, method, url, *, token=None, body=None, ca_file=None, timeout_s=10.0, allow_plain=False):
        self.calls.append({"method": method, "url": url, "token": token, "body": copy.deepcopy(body)})
        reply = self.responses.get((method, url), (0, None))
        return reply(body) if callable(reply) else reply

    def posts(self, url_part):
        return [c for c in self.calls if c["method"] == "POST" and url_part in c["url"]]


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def private(tmp_path):
    """Write a private (0600) file and return its path."""
    def make(name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)
        return str(path)
    return make
