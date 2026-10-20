"""Owner entry: fixed TLS session controls. Never constructs a camera/model graph."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import ssl
import sys
from urllib.parse import urlsplit
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("operator_redirect_refused")


def protected(filename, maximum, secret=False):
    path = Path(filename)
    stat = path.lstat()
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or stat.st_size > maximum or sys.platform != "win32" and stat.st_mode & (0o077 if secret else 0o022):
        raise ValueError("operator_file_refused")
    return path.read_bytes()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("start", "stop", "status", "read-results"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--id", required=True, help="Retain this exact ID for a retry of the same operation")
    args = parser.parse_args(argv)
    config = json.loads(protected(args.config, 16384))
    keys = {"schema", "version", "upstream", "ca_file", "token_file", "session_ref", "configuration_digest", "timeout_s"}
    if not isinstance(config, dict) or set(config) != keys or config["schema"] != "home-live-operator-config-1" or config["version"] != 1 or not 0 < config["timeout_s"] <= 15:
        raise ValueError("operator_configuration_refused")
    origin = urlsplit(config["upstream"])
    if origin.scheme != "https" or origin.path not in {"", "/"} or origin.username or origin.password or origin.query or origin.fragment:
        raise ValueError("operator_fixed_tls_origin_required")
    token = protected(config["token_file"], 4096, secret=True).decode().strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("operator_credential_refused")
    context = ssl.create_default_context(cadata=protected(config["ca_file"], 65536).decode())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context), NoRedirect())
    command = {"schema": "home-live-session-command-1", "version": 1, "id": args.id, "operation": args.operation,
               "session_ref": config["session_ref"], "configuration_digest": config["configuration_digest"]}
    request = urllib.request.Request(config["upstream"].rstrip("/") + "/api/home-live-session/v1/" + args.operation,
        data=json.dumps(command, separators=(",", ":")).encode(), headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    # Mutations are never automatically retried. Status/lookup uses owner-retained ID.
    with opener.open(request, timeout=config["timeout_s"]) as response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("operator_response_bound")
        value = json.loads(raw)
    if (value.get("schema") not in {"home-live-session-status-1", "home-live-session-error-1"}
            or value.get("schema") == "home-live-session-status-1" and (value.get("session_ref") != config["session_ref"]
                or value.get("configuration_digest") != config["configuration_digest"])):
        raise ValueError("operator_response_refused")
    print(json.dumps(value, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"schema":"home-live-session-error-1","reason":"operator_unavailable_or_refused","physical_authority":false}')
        raise SystemExit(2)
