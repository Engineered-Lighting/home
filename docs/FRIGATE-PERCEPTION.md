# Frigate Perception Bridge

The Home app can consume Frigate review/event metadata as a passive perception
feed. This is read-only context for the UI and natural `/look` routing.

## Flow

1. Frigate publishes MQTT messages on `frigate/reviews` and `frigate/events`.
2. Home Assistant package `homeai_frigate_perception.yaml` forwards those
   payloads as `homeai_perception` events when
   `input_boolean.homeai_frigate_perception_enabled` is on.
3. `app/src/home-frigate-perception.js` normalizes the payload into a
   `HomePerceptionEvent`.
4. The Home app renders one passive perception card and uses fresh events only
   as camera-ranking hints for natural visual prompts.

## Safety Rules

- Perception events never execute Home Assistant services.
- Perception events never append assistant responses directly.
- Driveway events always set `identity_allowed: false`.
- Stale perception events can rank historical context only; `/look` must fetch
  fresh frames before answering the user.
- Travel Mode remains the final blocker for all lighting output.

## Testing

Run:

```powershell
npm run test:frigate-perception
npm run test:natural-look
npm run test:chat-dedupe
```

Before enabling semantic triggers broadly, capture real MQTT payloads from the
live Frigate instance and add redacted fixtures to the tests.
