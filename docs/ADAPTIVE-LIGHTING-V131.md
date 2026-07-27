# Adaptive Lighting v1.31 Safe Adoption

This runbook keeps the current Home contract intact:

- Living Lights owns brightness, on/off, occupancy, ramping, and Travel Mode.
- Adaptive Lighting owns color temperature only.
- Travel Mode must remain the highest-priority guard against accidental light activation.

## Phase 0 - Preflight

Before updating HACS:

1. Take a Home Assistant snapshot and export it outside HA.
2. Record the live Home Assistant Core version.
3. Record the live Adaptive Lighting version from HACS.
4. Record these entity states:
   - `switch.home_adaptive_lighting_home`
   - `switch.adaptive_lighting_adapt_brightness_home`
   - `switch.adaptive_lighting_adapt_color_home`
   - `switch.adaptive_lighting_sleep_mode_home`
   - `input_boolean.living_lights_travel_mode`
5. Confirm every light is in the expected state before the update.

Useful read-only checks:

```bash
ssh hav-ubuntu 'cd ~/code/home && tools/travel-readiness.sh'
```

From a workstation with an HA token:

```bash
python tools/probe-ct-convergence.py
python tools/probe-lights-drawer.py
```

## Phase 1 - Update Only

Update Adaptive Lighting to `v1.31.0` in HACS.

Do not change YAML behavior in the same step.

If HACS asks for a restart, restart Home Assistant and wait for it to settle. Then verify:

- `switch.home_adaptive_lighting_home` is available.
- `switch.adaptive_lighting_adapt_brightness_home` is off.
- `switch.adaptive_lighting_adapt_color_home` is on.
- `/lights` shows the Adaptive Lighting diagnostic as healthy.
- Travel Mode can turn on, force known lighting outputs off, and remain on after app reload.

## Phase 2 - Observe

Watch normal lighting behavior for at least one Adaptive Lighting interval.

Required checks:

- With Travel Mode on and all lights off, no lights turn on during an AL interval.
- Living Lights brightness/on/off behavior still works.
- Color temperature still converges toward the AL target.
- The activity feed does not spam repeated manual-adjusted messages during ordinary pilot ticks.

If anything misbehaves, stop here and roll back.

## Phase 3 - Optional Canary

Only run this after Phase 1 is stable.

Use one isolated canary light. Prefer `light.office` only if it can be removed from the main `home` Adaptive Lighting profile.

Rules:

- Do not put the same bulb in both AL profiles.
- Keep `detect_non_ha_changes: false`.
- Keep brightness adaptation off for the canary profile.
- Keep color adaptation on.
- Use `take_over_control: true` and `take_over_control_mode: pause_changed`.
- Rebuild the canary Living Lights pilot without color-temperature injection:

```bash
python tools/build-living-lights-actuators.py --zone office --omit-ct-zone office --apply
```

The resulting pilot writes brightness only. Adaptive Lighting owns color for that canary.

## Rollback

Rollback order:

1. Turn off the canary Adaptive Lighting profile if one exists.
2. Re-add any canary bulb to the main `home` AL profile if it was removed.
3. Regenerate the canary pilot without `--omit-ct-zone`.
4. Restart or reload Home Assistant as required.
5. If the HACS update itself caused instability, downgrade Adaptive Lighting or restore the HA snapshot.
6. Verify Travel Mode and all Living Lights pilots before leaving the system unattended.

## Out Of Scope

The MindRoom content in the `v1.31.0` release announcement is unrelated to this lighting adoption plan.
