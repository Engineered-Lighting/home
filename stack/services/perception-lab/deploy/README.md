# First real recording deployment

This package is prepared locally, not installed or authorized. It contains the
controller source, direct dependency pins and an unrendered service template.
It does not contain credentials, models, media or the Home frontend release.

1. Approve the Victoria-only installation and bounded recording trial. Preserve
   the existing camera service/venv. Choose the separate release, virtualenv and
   NVMe recording paths; install requirements into that new environment only.
2. Review resource limits for this trial and replace every service-template token.
   Limits are deliberately not inferred from available-memory snapshots. Render
   a root-owned system service; it has no automatic restart or boot enablement.
3. Provision protected settings and capture JSON as described below. Existing
   camera RTSP reader credentials stay private. Validate codec using a separately
   approved bounded stream probe; do not invent the codec or validation receipt.
4. Start only the Lab service, verify authenticated capabilities, then admit one
   120-second maximum session. Recording must require an explicit API/UI action.
   Confirm existing Frigate/camera performance before and during the short trial.
5. Stop the recording, verify process termination and saved media, replay and add
   an interval label through Home. Keep the recording until explicitly deleted.

Settings: `site_id=victoria`, one camera with `modalities=["rgb"]`, dedicated
hashed token grants (`read`, `record`, `review`; optionally `export`), NVMe
storage_root, proposed 64 GiB Lab quota, a reviewed free-space reserve, and
`max_session_seconds=120` for this first trial. Do not grant run/prepare.

Capture JSON: schema_version=1, capture_mode=encoded_only, absolute settings_file,
the existing loopback RTSP source URL/id/camera_id, verified H.264 codec and
codec_validation_id, and technical/health_resource/workload_authorization gate
references. Omit camera_credential_file and receiver_clock. No JPEG, depth, HA,
cloud reasoning or specialist model is needed for this trial.

## Home connection is a separate required piece

The controller listens on Jetson loopback 8788. A provisioned HTTPS ingress and
gateway-owned token are needed for the existing Home gateway's Victoria registry;
there is no verified Victoria HTTPS ingress yet. The gateway also needs the Lab
frontend/server release. Do not overwrite the dirty deployed Home checkout.

The latest read-only check found home-web-gateway active, but the LA host was
degraded. AGENTS.md requires passing its maintenance health checks before a
gateway restart. This package does not authorize a restart or lift that gate.
Do not deploy the ordinary pull/reset-based web script over unrelated changes.

Rollback: stop only the new Lab controller after confirming its recorder exits.
Remove/disable only its new ingress/registry entry if provisioned. Leave camera,
MediaMTX, Frigate and saved Lab data unchanged. Do not restore old databases over
current labels/deletion records. No model or production service is evicted.

## Private host values

The units here are templates. `perception-lab-victoria.service` and
`perception-lab.service.in` use `@JETSON_USER@` (the Jetson service account)
and `@JETSON_HOME@` (its home directory). The real values are not in the repo.
Keep them in the private deploy env on the Jetson, beside the other private
files, as `JETSON_USER=...` and `JETSON_HOME=...`. Render the unit with:

    sed -e "s#@JETSON_USER@#$JETSON_USER#g" -e "s#@JETSON_HOME@#$JETSON_HOME#g" \
      perception-lab-victoria.service > "$JETSON_HOME/perception-lab/incoming/perception-lab-victoria.service"

`activate-victoria-recording.sh` reads `JETSON_USER` (and `JETSON_HOME`, which
defaults to `/home/$JETSON_USER`) from its environment and refuses a unit that
still has template tokens. The Lab code reads `JETSON_HOME` from the service
environment (`host_home.py`; `home_shared` and `den-live-runtime` read the same
variable). Without it every Jetson path falls back to the placeholder
`/home/jetson-user`, so the rendered unit must also carry
`Environment=JETSON_HOME=...` or an `EnvironmentFile=` that sets it.
