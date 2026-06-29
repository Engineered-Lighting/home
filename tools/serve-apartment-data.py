#!/usr/bin/env python3
"""Dev server for the heavy apartment assets (app/data/apartment) with CORS,
so the browser-preview app (a different origin) can load the FULL point
cloud / splat / mesh instead of the bundled sim fixtures.

  py -3 tools/serve-apartment-data.py [port]      (default 5175)

Then in the app:  localStorage.setItem('apartment3d.assetsBase',
'http://localhost:5175') and reload. The Tauri app never needs this —
it reads app/data via the asset protocol.
"""
import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app" / "data" / "apartment"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5175


class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    print(f"serving {ROOT} on :{PORT} (CORS *)")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), CORSHandler).serve_forever()
