# Voice opt-in and scoped retirement

Voice defaults off. Typed HA/Qwen chat, vision, and lighting do not depend on
the retired voice services. Ordinary `stack.sh up` clears inherited Compose
profiles and starts the default services; the four voice services require
`VOICE_ENABLED=1`, which adds only the `voice` profile. The existing `s2s`
profile remains separate and dormant. Profiles do not stop existing containers.

The supervisor reports `capabilities.voice=false`, skips voice network probes
and excludes disabled services from readiness, while retaining their actual
container state. It rejects their restart requests. Its systemd EnvironmentFile
must contain `VOICE_ENABLED=0` (the default if absent); merely editing that file
does not update an already running supervisor. The older video-labeler recovery
also defaults off: a historical GPU-exclusive ledger cannot revive any of the
four retired containers. Skipped restores remain recorded as
`restore_result=skipped_retired` in `units_json` with the original names.

The app requires the boolean `window.HG_VOICE_ENABLED=true` from trusted startup
configuration to opt in. Missing/false/string values keep the mic, S2S entry,
voice commands and proactive speech disabled. Saved voice preferences cannot
enable the capability. Keep this startup setting absent for normal operation;
rollback requires deliberately adding it before the app initializes. This flag
does not enable the separate S2S model/bridge services.

HA morning announcements additionally require
`input_boolean.homeai_voice_enabled=on`. The package declares `initial: false`,
so speech remains off after HA restarts until explicitly enabled again. Morning
energize and the app return-home scene use independent lighting paths.

## Operator transaction

Before changing the live host, follow the repository host-health policy, compare
the installed Compose/source/caller configuration with this checkout, and make
private backups of the deployed files. Confirm HA's selected Assist pipeline,
satellite/wake-word callers, morning announcement automation, gesture speech
feedback and any other installed announcements are disabled or deliberately
redirected. This checkout cannot establish their current HA enablement. Apply
and verify the caller gates before stopping voice. Coordinate the window with
users of typed Qwen and Den; do not stop or restart their services.

Run the helper locally on the intended Docker host first without `--apply`:

```sh
python3 stack/services/supervisor/retire_voice.py
```

It reads only daemon identity plus the four targets' IDs, names, running state
and restart policy. After verifying that daemon ID against host preflight,
choose a private receipt path whose parent exists. The receipt is a durable
metadata backup, created exclusively with mode 0600 before the first mutation:

```sh
python3 stack/services/supervisor/retire_voice.py --apply \
  --expected-daemon-id REVIEWED_ID --receipt /private/path/voice-baseline.json
```

Only these targets can change: `hav-wyoming-parakeet`, `hav-kokoro-tts`,
`hav-chatterbox-tts`, `hav-wyoming-kokoro`. The helper pins their inspected IDs,
sets `restart=no`, stops running targets with a 30-second grace, and verifies
that they are stopped and non-restarting. It never runs Compose down or touches
Qwen/Den. A partial failure leaves the receipt and completed operations; it does
not automatically start models. Inspect and resolve a partial result before
proceeding. Retain the receipt, deployed-file backups and legacy ledger history.

Rollback is also a dry run unless `--apply` is supplied:

```sh
python3 stack/services/supervisor/retire_voice.py --rollback \
  --expected-daemon-id REVIEWED_ID --receipt /private/path/voice-baseline.json
```

After host/resource checks and explicit restoration of the intended capability,
add `--apply` to restore recorded restart policies and start only targets that
were running in that receipt. Replacement IDs or names cause refusal. Restore
the deployed config backups and explicitly set `VOICE_ENABLED=1` for supervisor,
manual stack commands, and the legacy labeler if its restore behavior is wanted;
then enable only the intended app/HA callers after voice health is verified.
Do not replay a finished legacy ledger: it remains historical evidence.
