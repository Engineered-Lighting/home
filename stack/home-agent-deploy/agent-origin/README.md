# Dedicated Home Agent web origin

This deployment is a separate process, container, network edge, and Compose
project from `web-gateway`. Its immutable image contains and serves exactly
four built files below `/home-agent/` and an explicit allowlist of browser BFF
routes below `/api/agent/`. It has no legacy UI, Basic-auth route, generic
proxy, native bearer route, health URL, websocket upgrade, HA bearer, or Core
credential.

The origin has no Docker host port and joins only `home-agent_api-net`, an
IPv4-only `internal: true` network with no external gateway. Tailscale Serve connects
from the host directly to the origin's reviewed static address. The network's
dynamic range is disjoint from that address, preventing restart drift and
allocator collision.

The process has no per-request logger. OAuth callback query strings pass to
the BFF but are never interpolated into startup, error, access, or application
logs. Docker's bounded `local` log contains only fixed lifecycle messages.

## Locked network contract

The production defaults are:

```text
API subnet:       172.23.0.0/16
Gateway:          172.23.0.1
Dynamic range:    172.23.128.0/17
Agent origin:     172.23.0.10
Serve target:     http://172.23.0.10:8096
```

The origin and gateway are outside the dynamic range. Changing any value
requires a reviewed update to both environment files, the preflight evidence,
and the Serve target. Never select an address from `IPRange`.

Run the hermetic tests before touching the live network:

```sh
cd stack/services/home-agent-origin
npm run check
npm test
cd ../../../stack/home-agent-deploy/agent-origin
python3 -m unittest -v test_network_contract.py
```

Render both Compose projects and verify the origin has `ipv4_address` but no
`ports`, and the base `api-net` has `internal: true` plus exact IPAM:

```sh
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f ../../home-agent-compose.yml config >/tmp/home-agent-base.rendered.yml
docker compose --env-file home-agent-origin.env -f compose.yml config \
  >/tmp/home-agent-origin.rendered.yml
```

## Immutable hosted image acquisition

For the BFF and browser origin, Ubuntu is an import-only deployment host. Never
run `docker build`, `docker buildx`, `docker pull`, Compose `build`, or Compose
`pull` for either image there. The only deployable source is the main-branch
`deployable-images` job in
`.github/workflows/home-agent-web-boundary.yml`, running on GitHub-hosted
Ubuntu. A pull-request or feature-branch gate result is test evidence, not a
deployable image.

On a trusted, networked administration workstation, select one successful
main-branch workflow run and its exact 40-character source commit. Use a current
GitHub CLI with `gh attestation verify` support. Download only the artifact whose
name embeds that commit; do not use a wildcard or a "latest" artifact:

```sh
umask 077
export REPO=Engineered-Lighting/home
export WORKFLOW=.github/workflows/home-agent-web-boundary.yml
export RUN_ID=REPLACE_WITH_REVIEWED_RUN_ID
export SOURCE_COMMIT=REPLACE_WITH_40_CHARACTER_MAIN_COMMIT
export ARTIFACT="home-agent-web-images-${SOURCE_COMMIT}"
export BUNDLE_DIR="$PWD/verified-${ARTIFACT}"

test "$(printf %s "$SOURCE_COMMIT" | wc -c)" -eq 40
case "$SOURCE_COMMIT" in
  *[!0-9a-f]*|"") echo "source commit must be 40 lowercase hex characters" >&2; exit 1 ;;
esac
test "$(gh run view "$RUN_ID" --repo "$REPO" \
  --json workflowName --jq .workflowName)" = "home agent web boundary gate"
test "$(gh run view "$RUN_ID" --repo "$REPO" \
  --json conclusion --jq .conclusion)" = success
test "$(gh run view "$RUN_ID" --repo "$REPO" \
  --json headBranch --jq .headBranch)" = main
test "$(gh run view "$RUN_ID" --repo "$REPO" \
  --json headSha --jq .headSha)" = "$SOURCE_COMMIT"
case "$(gh run view "$RUN_ID" --repo "$REPO" --json event --jq .event)" in
  push|workflow_dispatch) ;;
  *) echo "reviewed run is not a deployable main event" >&2; exit 1 ;;
esac

install -d -m 0700 "$BUNDLE_DIR"
test -z "$(find "$BUNDLE_DIR" -mindepth 1 -print -quit)"
gh run download "$RUN_ID" --repo "$REPO" \
  --name "$ARTIFACT" --dir "$BUNDLE_DIR"
```

Require exactly the five workflow outputs. Then verify **all four attested
subjects** against the downloaded bundle. Repository identity alone is
insufficient: verification is also pinned to the signer workflow, `main`, the
selected source commit, and a non-self-hosted runner.

```sh
(
  cd "$BUNDLE_DIR"
  expected_files="$(
    printf '%s\n' \
      SHA256SUMS \
      home-agent-bff-linux-amd64.tar.gz \
      home-agent-origin-linux-amd64.tar.gz \
      manifest.json \
      provenance.sigstore.json
  )"
  actual_files="$(
    find . -mindepth 1 -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort
  )"
  test "$actual_files" = "$(printf '%s\n' "$expected_files" | LC_ALL=C sort)"
  test -z "$(find . -mindepth 1 -maxdepth 1 ! -type f -print -quit)"

  for subject in \
    home-agent-bff-linux-amd64.tar.gz \
    home-agent-origin-linux-amd64.tar.gz \
    manifest.json \
    SHA256SUMS
  do
    gh attestation verify "$subject" \
      --bundle provenance.sigstore.json \
      --repo "$REPO" \
      --signer-workflow "$REPO/$WORKFLOW" \
      --source-ref refs/heads/main \
      --source-digest "$SOURCE_COMMIT" \
      --deny-self-hosted-runners
  done

  sha256sum --strict --check SHA256SUMS
)
```

Validate the manifest as an exact schema, not as a collection of optional
hints. This also binds each archive checksum to its expected service and
requires immutable Docker image IDs:

```sh
(
  cd "$BUNDLE_DIR"
  python3 - "$REPO" "$SOURCE_COMMIT" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import sys

repo, commit = sys.argv[1:]

def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate manifest key: {key}")
        result[key] = value
    return result

manifest = json.loads(
    Path("manifest.json").read_text(encoding="utf-8"),
    object_pairs_hook=no_duplicate_keys,
)
if set(manifest) != {
    "schema_version", "repository", "source_ref", "source_commit", "platform",
    "base_image", "images",
}:
    raise SystemExit("unexpected manifest top-level fields")
expected_header = {
    "schema_version": 1,
    "repository": repo,
    "source_ref": "refs/heads/main",
    "source_commit": commit,
    "platform": "linux/amd64",
}
for key, value in expected_header.items():
    if manifest.get(key) != value:
        raise SystemExit(f"manifest {key} mismatch")
expected_base = {
    "reference": "node:24-alpine",
    "index_digest":
        "sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd",
    "linux_amd64_digest":
        "sha256:4ba75f835bb8802193e4c114572113d4b26f95f6f094f4b5229d2a77773e0afc",
}
if manifest["base_image"] != expected_base:
    raise SystemExit("manifest base image mismatch")
expected_images = {
    "bff": {
        "source_tag": f"engineered-lighting/home-agent-bff:ci-{commit}",
        "deployment_tag": "engineered-lighting/home-agent-bff:local",
        "archive": "home-agent-bff-linux-amd64.tar.gz",
    },
    "origin": {
        "source_tag": f"engineered-lighting/home-agent-origin:ci-{commit}",
        "deployment_tag": "engineered-lighting/home-agent-origin:local",
        "archive": "home-agent-origin-linux-amd64.tar.gz",
    },
}
if set(manifest["images"]) != set(expected_images):
    raise SystemExit("manifest image set mismatch")
for name, expected in expected_images.items():
    image = manifest["images"][name]
    if set(image) != {
        "source_tag", "deployment_tag", "image_id", "archive",
        "archive_sha256",
    }:
        raise SystemExit(f"{name} manifest fields mismatch")
    for key, value in expected.items():
        if image.get(key) != value:
            raise SystemExit(f"{name} {key} mismatch")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image["image_id"]):
        raise SystemExit(f"{name} image ID is not immutable")
    digest = hashlib.sha256(Path(image["archive"]).read_bytes()).hexdigest()
    if digest != image["archive_sha256"]:
        raise SystemExit(f"{name} archive digest mismatch")
PY
)
chmod -R a-w "$BUNDLE_DIR"
```

Retain that complete read-only directory as deployment and rollback evidence.
Transfer the exact directory to Ubuntu through the approved administrative
channel. Do not `docker load`, start, unpack, or otherwise execute either image
archive on the workstation.

On Ubuntu, place the bundle under a root-controlled directory on the encrypted
volume, repeat `sha256sum --strict --check SHA256SUMS`, and rerun the exact
manifest validator above before touching image tags. First archive any current
deployment images:

```sh
export REPO=Engineered-Lighting/home
export SOURCE_COMMIT=REPLACE_WITH_THE_SAME_40_CHARACTER_MAIN_COMMIT
export BUNDLE_DIR="/srv/home-agent/image-bundles/${SOURCE_COMMIT}"
export ROLLBACK_DIR="/srv/home-agent/image-rollback/$(date -u +%Y%m%dT%H%M%SZ)-before-${SOURCE_COMMIT}"
export BASE_ENV=/srv/home-agent/config/home-agent.env
export ORIGIN_ENV=REPLACE_WITH_ABSOLUTE_PATH_TO_PRIVATE_HOME_AGENT_ORIGIN_ENV

case "$ORIGIN_ENV" in
  /*) ;;
  *) echo "ORIGIN_ENV must be an absolute private configuration path" >&2; exit 1 ;;
esac

cd "$BUNDLE_DIR"
sha256sum --strict --check SHA256SUMS
sudo install -d -m 0700 "$ROLLBACK_DIR"
archive_image() {
  image="$1"
  name="$2"
  if sudo docker image inspect "$image" >/dev/null 2>&1; then
    sudo docker image inspect "$image" --format '{{.Id}}' \
      | sudo tee "$ROLLBACK_DIR/${name}.image-id" >/dev/null
    sudo docker image save "$image" | gzip -n -9 \
      | sudo tee "$ROLLBACK_DIR/${name}.tar.gz" >/dev/null
  else
    printf '%s\n' absent | sudo tee "$ROLLBACK_DIR/${name}.absent" >/dev/null
  fi
}
archive_image engineered-lighting/home-agent-bff:local bff
archive_image engineered-lighting/home-agent-origin:local origin
sudo chmod -R go-rwx "$ROLLBACK_DIR"
```

Load the two verified archives without pulling. Compare each loaded top-level
source tag to the manifest image ID. Only after **both** comparisons succeed
may the mutable deployment tags be moved:

```sh
BFF_SOURCE="engineered-lighting/home-agent-bff:ci-${SOURCE_COMMIT}"
ORIGIN_SOURCE="engineered-lighting/home-agent-origin:ci-${SOURCE_COMMIT}"
BFF_EXPECTED_ID="$(
  python3 -c 'import json; print(json.load(open("manifest.json"))["images"]["bff"]["image_id"])'
)"
ORIGIN_EXPECTED_ID="$(
  python3 -c 'import json; print(json.load(open("manifest.json"))["images"]["origin"]["image_id"])'
)"

sudo docker image load --input home-agent-bff-linux-amd64.tar.gz
sudo docker image load --input home-agent-origin-linux-amd64.tar.gz
BFF_ACTUAL_ID="$(sudo docker image inspect "$BFF_SOURCE" --format '{{.Id}}')"
ORIGIN_ACTUAL_ID="$(sudo docker image inspect "$ORIGIN_SOURCE" --format '{{.Id}}')"
test "$BFF_ACTUAL_ID" = "$BFF_EXPECTED_ID"
test "$ORIGIN_ACTUAL_ID" = "$ORIGIN_EXPECTED_ID"

sudo docker image tag "$BFF_SOURCE" engineered-lighting/home-agent-bff:local
sudo docker image tag "$ORIGIN_SOURCE" engineered-lighting/home-agent-origin:local
test "$(sudo docker image inspect engineered-lighting/home-agent-bff:local \
  --format '{{.Id}}')" = "$BFF_EXPECTED_ID"
test "$(sudo docker image inspect engineered-lighting/home-agent-origin:local \
  --format '{{.Id}}')" = "$ORIGIN_EXPECTED_ID"

printf 'Set HOME_AGENT_BFF_IMAGE_ID=%s\n' "$BFF_EXPECTED_ID"
printf 'Set HOME_AGENT_WEB_IMAGE_ID=%s\n' "$ORIGIN_EXPECTED_ID"
sudoedit "$BASE_ENV"
sudoedit "$ORIGIN_ENV"
```

Set `HOME_AGENT_BFF_IMAGE_ID` to the printed, verified BFF ID in the base
environment and `HOME_AGENT_WEB_IMAGE_ID` to the printed, verified origin ID
before running network preflight. A `:local`, `:main`, or any other mutable tag
by itself is never provenance or deployment approval. If any attestation,
checksum, manifest, load, or ID comparison fails, do not tag or start either
image; preserve the bundle and failure output for review.

## One-time network pinning

An existing auto-allocated `home-agent_api-net` must be recreated once to add
the disjoint `IPRange`. This operation may touch only the stateless `core-api`,
`bff`, and `origin` containers. PostgreSQL, ingest, worker, edge ingress, and
the BFF session directory remain running or preserved.

Before the maintenance window:

1. Copy `home-agent-origin.env.example` to a private `home-agent-origin.env`
   and set the exact production HTTPS origin and verified
   `HOME_AGENT_WEB_IMAGE_ID`.
2. Add the three `HOME_AGENT_API_*` values from `home-agent.env.example` to
   `/srv/home-agent/config/home-agent.env`, and set
   `HOME_AGENT_BFF_IMAGE_ID` to the verified BFF image ID.
3. Confirm the BFF session bind remains `/srv/home-agent/sessions`.
4. Run the read-only candidate preflight. It rejects overlap with host routes,
   Tailscale routes, and every other Docker network before network removal:

   ```sh
   python3 network_contract.py \
     --base-env /srv/home-agent/config/home-agent.env \
     --origin-env home-agent-origin.env --candidate-only
   ```

5. Record `docker compose ps` and `docker network inspect home-agent_api-net`.
6. Confirm no container outside `core-api`, `bff`, and `origin` is attached to
   `home-agent_api-net`.

Then recreate only this network and its three consumers:

```sh
# Stop/remove the separate origin first so the external network can be removed.
docker compose --env-file home-agent-origin.env -f compose.yml down

cd ../..
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml stop bff core-api
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml rm -f bff core-api
docker network rm home-agent_api-net

# Recreate only the stateless API boundary. --no-deps leaves PostgreSQL and
# every ingest/worker/edge container untouched.
docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml up -d --no-deps --no-build --pull never \
  core-api bff
```

Wait for both containers to become healthy. The network preflight must now
pass before starting the origin. The quarantined Ubuntu host must already have
the reviewed `engineered-lighting/home-agent-origin:local` image imported from
an external, attested build; do not build or pull it during this procedure:

```sh
cd home-agent-deploy/agent-origin
python3 network_contract.py \
  --base-env /srv/home-agent/config/home-agent.env \
  --origin-env home-agent-origin.env
docker compose --env-file home-agent-origin.env -f compose.yml \
  up -d --no-build --pull never
python3 network_contract.py \
  --base-env /srv/home-agent/config/home-agent.env \
  --origin-env home-agent-origin.env --require-origin
```

The origin preflight fails if the network is not internal, IPAM differs, the
reviewed address is dynamic/occupied, or the Serve target differs by even its
scheme, address, port, path, query, or fragment.

Every candidate and live preflight requires both immutable image IDs to be
exact lowercase SHA-256 digests, plus an exact private origin setting and base
BFF allowed origin/client ID/callback. Candidate mode still checks the running
BFF's Compose identity, exact reviewed `:local` image reference, and three OAuth
values, but deliberately does not compare its top-level Docker image ID with
the newly reviewed BFF ID because the old BFF is still running at that point.
After recreation, normal and `--require-origin` preflight require the running
BFF's top-level Docker image ID to equal `HOME_AGENT_BFF_IMAGE_ID`. Every mode
reports only a fixed pass/fail reason; it never prints environment contents or
credentials.

## Pre-exposure validation

Obtain the target from the validated contract rather than retyping it:

```sh
target=$(python3 network_contract.py \
  --base-env /srv/home-agent/config/home-agent.env \
  --origin-env home-agent-origin.env --require-origin --print-serve-target)
test "$target" = "http://172.23.0.10:8096"
```

From the Ubuntu host, the reviewed target must serve the Agent bundle when the
exact public Host is supplied:

```sh
curl --fail-with-body -H 'Host: home-app.example.ts.net:8443' \
  "$target/home-agent/index.html" >/dev/null
```

Verify `/`, `/healthz`, `/proxy/ha/api/states`, `/native-oauth-client`,
`/api/agent/native/v1/snapshot`, and a query-bearing static asset all return
404. A mismatched Host must return 404 and a mismatched Origin must return 403.

Prove the container can reach the BFF but has no LAN or Internet route:

```sh
docker compose --env-file home-agent-origin.env -f compose.yml exec -T origin \
  node -e "fetch('http://bff:8097/healthz').then(r=>{if(!r.ok)process.exit(1)})"
docker compose --env-file home-agent-origin.env -f compose.yml exec -T origin \
  npm run verify-network
```

The verifier first rejects any IPv4 default route, independently proves the
BFF health endpoint is reachable, and then requires both external probes to
fail specifically with `ENETUNREACH` or `EHOSTUNREACH`. Connection success,
`ECONNREFUSED`, `ECONNRESET`, timeout, or any suppressed/unknown error fails the
gate.

The `--require-origin` preflight additionally inspects the exact Compose
container. It requires one network attachment, no host `PortBindings` or
published port, no IPv6 address, expected Compose labels/image/user, a read-only
root, all capabilities dropped, `no-new-privileges`, no runtime mounts, and the
reviewed memory/PID limits.

From a separate LAN host, both the Ubuntu LAN address on port 8096 and the
Docker-internal address on port 8096 must be unreachable. For example, on
Windows both commands must report `TcpTestSucceeded : False`:

```powershell
Test-NetConnection 192.168.0.100 -Port 8096
Test-NetConnection 172.23.0.10 -Port 8096
```

## Tailscale Serve exposure

Tailscale 1.98.3's `serve get-config --all` does **not** capture the node-level
TCP handlers used here; it may return only a version object. It is not a valid
backup or rollback mechanism. Save the actual baseline from `status --json`:

```sh
sudo install -d -m 0700 /srv/home-agent/config/tailscale
sudo tailscale serve status --json \
  | sudo install -m 0600 -o root -g root /dev/stdin \
      /srv/home-agent/config/tailscale/pre-agent-origin.status.json
sudo tailscale serve status
```

Before proceeding, the human-readable and JSON status must show exactly the
existing tailnet-only handlers:

```text
443   -> 127.0.0.1:5181 (TLS terminated TCP)
10000 -> 192.168.0.125:8123 (TLS terminated TCP)
```

Use the preflight-produced target and add only the scoped HTTPS listener:

```sh
sudo tailscale serve --bg --https=8443 "$target"
sudo tailscale serve status --json
sudo tailscale serve status
```

The result must retain 443 and 10000 byte-for-byte and add only tailnet-only
8443 targeting `http://172.23.0.10:8096`. Never use Funnel or `serve reset`.

From a tailnet client, repeat all positive and negative path tests. Send a
deliberately invalid callback canary without an OAuth cookie and confirm its
code/state occur in neither origin logs nor `tailscaled` journal output. Do not
use a real authorization code in diagnostics.

Only after those checks pass may a browser begin one supervised HA OAuth flow.
Verify the callback URL disappears through the no-referrer redirect, cookies
are `Secure`, `HttpOnly`, `SameSite=Strict`, and no callback query appears in
container, Tailscale, browser console, metrics, or crash output.

## Rollback

Remove only the Agent listener; never reset all Serve state:

```sh
sudo tailscale serve --https=8443 off
sudo tailscale serve status --json
docker compose --env-file home-agent-origin.env -f compose.yml down
```

The status must match the saved baseline for ports 443 and 10000. If either
legacy handler is missing, reconstruct it explicitly; never use `reset`:

```sh
sudo tailscale serve --bg --tls-terminated-tcp=443 tcp://127.0.0.1:5181
sudo tailscale serve --bg --tls-terminated-tcp=10000 tcp://192.168.0.125:8123
sudo tailscale serve status
```

The pinned internal network may remain: Core and BFF operate normally on it,
and the session bind is preserved. If network recreation fails before they are
healthy, leave 8443 off, correct the IPAM/environment contract, and repeat only
the targeted `core-api`/`bff` recreation. Do not restart PostgreSQL, ingest,
worker, edge, or legacy device-control services as part of this rollback.
