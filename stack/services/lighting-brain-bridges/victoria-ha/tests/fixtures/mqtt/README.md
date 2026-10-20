# Synthetic Frigate MQTT timelines

Each file is a list of steps replayed against a fake clock (seconds from the
start of the run). They follow Frigate's MQTT behaviour (0.14 and later):

- `frigate/available` is retained (`online`/`offline`, with `offline` as the
  last will);
- `frigate/<camera>/person` and `frigate/<zone>/person` carry the current count,
  are published only on change and are not retained;
- `frigate/stats` arrives every `mqtt.stats_interval` (60 s by default);
- `frigate/<camera>/detect/state` is retained `ON`/`OFF`.

Step forms:

- `{"at": 0, "connect": true}`: the bridge's MQTT connection opens (or closes);
- `{"at": 5, "topic": "...", "payload": "1", "retain": false}`: one message;
- `{"every": [1, 600, 60], "topic": "frigate/stats", "payload": "@stats"}`: a
  message repeated from 1 s to 600 s every 60 s. `@stats` is
  `../frigate_stats_den_oak.json`.

None of this was read from a live system; replace or extend it with redacted
recordings once the owner approves a capture.
