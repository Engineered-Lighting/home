"""Subscribe to extended_openai_conversation.identity_mutation events via HA WS.
Listen for 15s, then report what was received."""
import asyncio
import json
import os
import sys

try:
    import websockets  # type: ignore
except ImportError:
    print("ERROR: install websockets first: py -3 -m pip install websockets")
    sys.exit(1)


async def main():
    token = os.environ.get("HA_TOKEN")
    if not token:
        print("ERROR: set HA_TOKEN env var")
        sys.exit(1)
    url = "ws://homeassistant.local:8123/api/websocket"
    received: list[dict] = []
    async with websockets.connect(url) as ws:
        # Auth handshake
        hello = json.loads(await ws.recv())
        assert hello["type"] == "auth_required", hello
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "auth_ok", resp
        print(f"AUTHED (HA version {resp.get('ha_version')})")
        # Subscribe
        await ws.send(json.dumps({
            "id": 1, "type": "subscribe_events",
            "event_type": "extended_openai_conversation.identity_mutation",
        }))
        sub_ok = json.loads(await ws.recv())
        assert sub_ok.get("success") is True, sub_ok
        print(f"SUBSCRIBED to identity_mutation; listening 15s...")
        try:
            deadline = asyncio.get_event_loop().time() + 15
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "event":
                    data = msg["event"]["data"]
                    print(f"EVENT: {json.dumps(data)}")
                    received.append(data)
        finally:
            pass
    print(f"DONE: received {len(received)} events")
    sys.exit(0 if received else 2)


if __name__ == "__main__":
    asyncio.run(main())
