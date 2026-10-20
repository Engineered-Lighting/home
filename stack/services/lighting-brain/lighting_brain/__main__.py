"""python -m lighting_brain  (configuration: README.md, "Configuration")."""
from __future__ import annotations

import logging
import os
import sys

from .brain import Brain
from .geometry import load_geometry
from .server import load_tokens, serve
from .settings import load_settings


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("lighting_brain")
    geometry_path = os.environ.get("LIGHTING_BRAIN_GEOMETRY")
    if not geometry_path:
        log.error("LIGHTING_BRAIN_GEOMETRY must point at the private geometry export")
        return 2
    geometry = load_geometry(geometry_path)
    settings = load_settings(os.environ.get("LIGHTING_BRAIN_SETTINGS"))
    producers = load_tokens(os.environ.get("LIGHTING_BRAIN_TOKENS"),
                            os.environ.get("LIGHTING_BRAIN_TOKENS_JSON"))
    if not producers:
        log.error("no producer tokens: set LIGHTING_BRAIN_TOKENS (file) or LIGHTING_BRAIN_TOKENS_JSON")
        return 2
    if geometry.synthetic:
        log.warning("geometry is SYNTHETIC (example file): spot aims are not the real den")
    host = os.environ.get("LIGHTING_BRAIN_BIND", "127.0.0.1")
    port = int(os.environ.get("LIGHTING_BRAIN_PORT", "8101"))
    brain = Brain(geometry, settings)
    server = serve(brain, producers, host, port)
    log.info("lighting brain %s/%s on %s:%d (settings %s, %d producers)", geometry.home, geometry.room,
             host, port, settings.settings_version, len(producers))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        brain.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
