#!/bin/sh
set -eu
umask 077

# This gate intentionally runs only on an ephemeral, GitHub-hosted Linux
# runner. It exercises the real root-only publication scripts without opening
# a Docker daemon connection or printing any generated credential.

[ "$(uname -s)" = "Linux" ] || {
  echo "identity cutover secret lifecycle gate requires Linux" >&2
  exit 77
}
[ "${GITHUB_ACTIONS:-}" = "true" ] &&
  [ "${HOME_AGENT_SECRET_LIFECYCLE_RUNNER_ENVIRONMENT:-}" = "github-hosted" ] || {
  echo "identity cutover secret lifecycle gate requires a GitHub-hosted runner" >&2
  exit 77
}
[ "$(id -u)" -eq 0 ] || {
  echo "identity cutover secret lifecycle gate must run as root" >&2
  exit 77
}
for command_name in cp docker find grep openssl python3 sha256sum stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "identity cutover secret lifecycle gate dependency is unavailable" >&2
    exit 77
  }
done
docker compose version >/dev/null 2>&1 || {
  echo "identity cutover secret lifecycle gate requires Docker Compose v2" >&2
  exit 77
}

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/../.." && pwd)"
workspace="$(
  mktemp -d "${RUNNER_TEMP:-/tmp}/home-agent-e4-secret-lifecycle.XXXXXX"
)"
case "$workspace" in
  "${RUNNER_TEMP:-/tmp}"/home-agent-e4-secret-lifecycle.*) ;;
  *) echo "unsafe lifecycle gate workspace" >&2; exit 70 ;;
esac
chmod 0700 "$workspace"
output_log="$workspace/command-output.log"
: > "$output_log"
chmod 0600 "$output_log"

cleanup() {
  case "$workspace" in
    "${RUNNER_TEMP:-/tmp}"/home-agent-e4-secret-lifecycle.*)
      rm -rf -- "$workspace"
      ;;
  esac
}
trap cleanup EXIT HUP INT TERM

fail() {
  echo "identity cutover secret lifecycle gate failed: $1" >&2
  exit 1
}

run_quiet() {
  label="$1"
  shift
  if "$@" >>"$output_log" 2>&1; then
    return 0
  else
    status=$?
  fi
  echo "identity cutover secret lifecycle gate command failed: $label ($status)" >&2
  exit 1
}

expect_status() {
  expected_status="$1"
  label="$2"
  shift 2
  set +e
  "$@" >>"$output_log" 2>&1
  actual_status=$?
  set -e
  [ "$actual_status" -eq "$expected_status" ] || {
    echo "identity cutover secret lifecycle gate rejection mismatch: $label" >&2
    exit 1
  }
}

clone_historical() {
  case_root="$workspace/$1"
  [ ! -e "$case_root" ] || fail "duplicate disposable case"
  cp -a "$historical_root" "$case_root"
}

assert_stat() {
  expected_stat="$1"
  target_path="$2"
  [ "$(stat -c '%u:%g:%a' "$target_path")" = "$expected_stat" ] ||
    fail "unexpected disposable secret ownership or mode"
}

assert_exact_names() {
  directory="$1"
  expected_names="$2"
  actual_names="$(
    find "$directory" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort
  )"
  [ "$actual_names" = "$expected_names" ] ||
    fail "unexpected disposable secret file set"
}

verify_cutover_layout() {
  verified_root="$1"
  cutover_master="$verified_root/master/identity-cutover"
  provision_runtime="$verified_root/runtime/provision-identity-cutover-roles"
  cutover_runtime="$verified_root/runtime/identity-cutover"

  [ -d "$cutover_master" ] && [ ! -L "$cutover_master" ] ||
    fail "missing disposable E4 master directory"
  assert_stat 0:0:700 "$cutover_master"
  assert_exact_names "$cutover_master" \
"database_url_identity_cutover
postgres_identity_cutover_password"
  for path in \
    "$cutover_master/database_url_identity_cutover" \
    "$cutover_master/postgres_identity_cutover_password"
  do
    [ -f "$path" ] && [ ! -L "$path" ] ||
      fail "unsafe disposable E4 master file"
    assert_stat 0:0:600 "$path"
  done

  [ -d "$provision_runtime" ] && [ ! -L "$provision_runtime" ] ||
    fail "missing disposable E4 provisioner runtime"
  assert_stat 0:0:700 "$provision_runtime"
  assert_exact_names "$provision_runtime" \
"postgres_identity_cutover_password
postgres_owner_password"
  assert_stat 0:0:400 "$provision_runtime/postgres_owner_password"
  assert_stat 0:0:400 \
    "$provision_runtime/postgres_identity_cutover_password"
  cmp -s "$provision_runtime/postgres_owner_password" \
    "$verified_root/master/postgres_owner_password" ||
    fail "disposable owner credential copy differs"
  cmp -s "$provision_runtime/postgres_identity_cutover_password" \
    "$cutover_master/postgres_identity_cutover_password" ||
    fail "disposable cutover credential copy differs"

  [ -d "$cutover_runtime" ] && [ ! -L "$cutover_runtime" ] ||
    fail "missing disposable E4 runtime"
  assert_stat 0:0:700 "$cutover_runtime"
  assert_exact_names "$cutover_runtime" "database_url"
  assert_stat 10001:10001:400 "$cutover_runtime/database_url"
  cmp -s "$cutover_runtime/database_url" \
    "$cutover_master/database_url_identity_cutover" ||
    fail "disposable cutover URL copy differs"

  [ ! -e "$verified_root/master/postgres_identity_cutover_password" ] &&
    [ ! -e "$verified_root/master/database_url_identity_cutover" ] ||
    fail "legacy flat E4 credential exists"
  [ ! -e \
    "$verified_root/runtime/provision-roles/postgres_identity_cutover_password" ] ||
    fail "historical provisioner received E4 credential"
}

master_fingerprint() {
  fingerprint_root="$1"
  {
    stat -c '%i:%u:%g:%a:%s' \
      "$fingerprint_root/master/identity-cutover/"*
    sha256sum "$fingerprint_root/master/identity-cutover/"*
  } | sha256sum | cut -d ' ' -f 1
}

write_cutover_pair() {
  write_root="$1"
  write_password="$2"
  install -d -m 0700 -o root -g root \
    "$write_root/master/identity-cutover"
  printf '%s\n' "$write_password" > \
    "$write_root/master/identity-cutover/postgres_identity_cutover_password"
  printf 'postgresql+psycopg://home_agent_identity_cutover:%s@postgres:5432/home_agent\n' \
    "$write_password" > \
    "$write_root/master/identity-cutover/database_url_identity_cutover"
  chmod 0600 "$write_root/master/identity-cutover/"*
  chown root:root "$write_root/master/identity-cutover/"*
}

historical_root="$workspace/historical"
run_quiet bootstrap-historical \
  sh "$script_dir/bootstrap-secrets.sh" "$historical_root"
[ ! -e "$historical_root/master/identity-cutover" ] ||
  fail "historical fixture unexpectedly contains E4"

# A historically pristine deployment remains valid without E4. Runtime
# credentials are never accepted unless their root-only master pair exists.
clone_historical pristine-absence
run_quiet pristine-rematerialize \
  sh "$script_dir/materialize-secrets.sh" "$case_root"
run_quiet pristine-preflight \
  sh "$script_dir/preflight-identity-cutover-roles.sh" "$case_root"

clone_historical orphaned-runtime
install -d -m 0700 -o root -g root \
  "$case_root/runtime/identity-cutover"
printf 'orphaned-runtime-credential\n' \
  >"$case_root/runtime/identity-cutover/database_url"
chmod 0400 "$case_root/runtime/identity-cutover/database_url"
chown 10001:10001 "$case_root/runtime/identity-cutover/database_url"
expect_status 78 orphaned-runtime-materialize \
  sh "$script_dir/materialize-secrets.sh" "$case_root"
expect_status 78 orphaned-runtime-preflight \
  sh "$script_dir/preflight-identity-cutover-roles.sh" "$case_root"

# Successful additive creation, exact publication, safe repeat materialization,
# and ordinary duplicate rejection.
clone_historical success
success_root="$case_root"
run_quiet additive-create \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$success_root"
verify_cutover_layout "$success_root"
success_fingerprint_before="$(master_fingerprint "$success_root")"
run_quiet rematerialize \
  sh "$script_dir/materialize-secrets.sh" "$success_root"
run_quiet recheck \
  sh "$script_dir/preflight-identity-cutover-roles.sh" "$success_root"
success_fingerprint_after="$(master_fingerprint "$success_root")"
[ "$success_fingerprint_before" = "$success_fingerprint_after" ] ||
  fail "re-materialization changed E4 master credentials"
expect_status 73 duplicate-create \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$success_root"

# A post-publication materialization failure is recoverable only through the
# explicit resume path. The fake openssl proves resume never regenerates a
# credential, while inode/content metadata proves it never overwrites one.
clone_historical resume
resume_root="$case_root"
mv "$resume_root/master/database_url_owner" \
  "$workspace/resume-database-url-owner"
expect_status 78 post-publication-failure \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$resume_root"
[ -d "$resume_root/master/identity-cutover" ] ||
  fail "published E4 master pair was not preserved"
resume_fingerprint_before="$(master_fingerprint "$resume_root")"
mv "$workspace/resume-database-url-owner" \
  "$resume_root/master/database_url_owner"
mkdir "$workspace/no-generator"
cat >"$workspace/no-generator/openssl" <<'EOF'
#!/bin/sh
exit 99
EOF
chmod 0700 "$workspace/no-generator/openssl"
run_quiet resume-existing \
  env PATH="$workspace/no-generator:$PATH" \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" \
  "$resume_root" --resume-existing
resume_fingerprint_after="$(master_fingerprint "$resume_root")"
[ "$resume_fingerprint_before" = "$resume_fingerprint_after" ] ||
  fail "resume overwrote or regenerated E4 master credentials"
verify_cutover_layout "$resume_root"

# Partial and symlinked publication targets always fail before a credential is
# generated or a runtime directory is modified.
clone_historical partial-target
install -d -m 0700 -o root -g root \
  "$case_root/master/identity-cutover"
printf '%064d\n' 0 > \
  "$case_root/master/identity-cutover/postgres_identity_cutover_password"
chmod 0600 \
  "$case_root/master/identity-cutover/postgres_identity_cutover_password"
expect_status 73 partial-target \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical symlink-target
ln -s "$workspace/absent-target" "$case_root/master/identity-cutover"
expect_status 73 symlink-target \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical symlink-root
mv "$case_root" "$workspace/symlink-root-real"
ln -s "$workspace/symlink-root-real" "$case_root"
expect_status 78 symlink-root \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical symlink-master
mv "$case_root/master" "$case_root/master-real"
ln -s "$case_root/master-real" "$case_root/master"
expect_status 78 symlink-master \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical symlink-runtime
mv "$case_root/runtime" "$case_root/runtime-real"
ln -s "$case_root/runtime-real" "$case_root/runtime"
expect_status 78 symlink-runtime \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical symlink-runtime-target
ln -s "$workspace/absent-runtime" "$case_root/runtime/identity-cutover"
expect_status 73 symlink-runtime-target \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"
[ ! -e "$case_root/master/identity-cutover" ] ||
  fail "runtime symlink rejection published an E4 master pair"

# Abandoned publication paths are never treated as safe retry state.
clone_historical stale-master-publication
install -d -m 0700 -o root -g root \
  "$case_root/master/.identity-cutover.new.stale"
expect_status 73 stale-master-publication \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical stale-master-previous-publication
install -d -m 0700 -o root -g root \
  "$case_root/master/.identity-cutover.previous.stale"
expect_status 73 stale-master-previous-publication \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical stale-runtime-publication
install -d -m 0700 -o root -g root \
  "$case_root/runtime/.identity-cutover.previous.stale"
expect_status 73 stale-runtime-publication \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

clone_historical stale-provisioner-publication
: >"$case_root/runtime/.provision-identity-cutover-roles.new.stale"
expect_status 73 stale-provisioner-publication \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"

# Existing historical credentials and resume candidates must be canonical,
# independent, root-only values.
clone_historical malformed-historical
printf 'not-a-canonical-password\n' \
  >"$case_root/master/postgres_owner_password"
chmod 0600 "$case_root/master/postgres_owner_password"
expect_status 78 malformed-historical \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" "$case_root"
[ ! -e "$case_root/master/identity-cutover" ] ||
  fail "malformed historical credential allowed E4 publication"

clone_historical malformed-resume
write_cutover_pair "$case_root" "not-a-canonical-password"
expect_status 78 malformed-resume \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" \
  "$case_root" --resume-existing

clone_historical mismatched-url-resume
write_cutover_pair "$case_root" \
  "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
printf 'postgresql+psycopg://wrong-role:invalid@postgres:5432/home_agent\n' \
  >"$case_root/master/identity-cutover/database_url_identity_cutover"
chmod 0600 \
  "$case_root/master/identity-cutover/database_url_identity_cutover"
expect_status 78 mismatched-url-resume \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" \
  "$case_root" --resume-existing

clone_historical matching-resume
matching_password="$(
  tr -d '\r\n' <"$case_root/master/postgres_owner_password"
)"
write_cutover_pair "$case_root" "$matching_password"
unset matching_password
expect_status 70 matching-resume \
  sh "$script_dir/add-identity-cutover-role-secrets.sh" \
  "$case_root" --resume-existing

# Compose interpolation is checked without pulling an image or contacting the
# daemon. The rendered operator services must point only to the disposable
# mode-0400 E4 files and remain inert.
mkdir "$workspace/compose-data" "$workspace/compose-runtime" \
  "$workspace/compose-ledger" "$workspace/compose-tls" \
  "$workspace/compose-pgbackrest-repository" \
  "$workspace/compose-pgbackrest-spool" \
  "$workspace/compose-session"
compose_json="$workspace/compose.json"
if ! env \
  HOME_AGENT_POLICY_DIGEST="$(
    sha256sum \
      "$repo_root/stack/home-agent-deploy/policy/home-agent-mvp-v1.json" |
      cut -d ' ' -f 1
  )" \
  HOME_AGENT_DATA_ROOT="$workspace/compose-data" \
  HOME_AGENT_RUNTIME_ROOT="$workspace/compose-runtime" \
  HOME_AGENT_ERASURE_LEDGER_ROOT="$workspace/compose-ledger" \
  HOME_AGENT_SECRETS_DIR="$success_root" \
  HOME_AGENT_TLS_DIR="$workspace/compose-tls" \
  HOME_AGENT_PGBACKREST_CONF="$workspace/compose-pgbackrest.conf" \
  HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT="$workspace/compose-pgbackrest-repository" \
  HOME_AGENT_PGBACKREST_SPOOL_ROOT="$workspace/compose-pgbackrest-spool" \
  HOME_AGENT_PGBACKREST_LOCK_FILE="$workspace/compose-repository.lock" \
  HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE="$workspace/compose-backup.lock" \
  HOME_AGENT_EXPECTED_DB_REVISION="0014_identity_cutover_e4" \
  HOME_AGENT_EDGE_BIND_ADDR="127.0.0.1" \
  HOME_AGENT_ALLOWED_ORIGINS="https://agent.invalid" \
  HOME_AGENT_HA_URL="https://home-assistant.invalid" \
  HOME_AGENT_OAUTH_CLIENT_ID="e4-lifecycle-fixture" \
  HOME_AGENT_OAUTH_REDIRECT_URI="https://agent.invalid/api/agent/auth/callback" \
  HOME_AGENT_NATIVE_PUBLIC_ORIGIN="https://native-agent.invalid" \
  HOME_AGENT_SESSION_ROOT="$workspace/compose-session" \
  docker compose --profile operator \
    -f "$repo_root/stack/home-agent-compose.yml" \
    config --format json >"$compose_json" 2>>"$output_log"
then
  fail "Docker Compose E4 rendering failed"
fi
if ! python3 - "$compose_json" "$success_root" >>"$output_log" 2>&1 <<'PY'
import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = Path(sys.argv[2])
services = document.get("services", {})
secrets = document.get("secrets", {})

required_files = {
    "postgres_owner_password_identity_cutover_provision":
        root / "runtime/provision-identity-cutover-roles/postgres_owner_password",
    "postgres_identity_cutover_password_identity_cutover_provision":
        root / "runtime/provision-identity-cutover-roles/postgres_identity_cutover_password",
    "database_url_identity_cutover_identity_cutover":
        root / "runtime/identity-cutover/database_url",
}
for name, expected in required_files.items():
    if Path(secrets.get(name, {}).get("file", "")) != expected:
        raise SystemExit("rendered E4 secret path mismatch")

for name in ("provision-identity-cutover-roles", "identity-cutover"):
    service = services.get(name)
    if not isinstance(service, dict) or service.get("profiles") != ["operator"]:
        raise SystemExit("rendered E4 operator service missing")
    if service.get("ports"):
        raise SystemExit("rendered E4 operator service exposes a port")
    if "postgres-net" not in service.get("networks", {}):
        raise SystemExit("rendered E4 operator service escaped postgres network")
    if service.get("read_only") is not True:
        raise SystemExit("rendered E4 operator service is writable")
    if service.get("cap_drop") != ["ALL"]:
        raise SystemExit("rendered E4 operator service retains capabilities")

provisioner = services["provision-identity-cutover-roles"]
provisioner_secrets = {
    item.get("source"): item.get("target")
    for item in provisioner.get("secrets", [])
}
if provisioner_secrets != {
    "postgres_owner_password_identity_cutover_provision":
        "postgres_owner_password",
    "postgres_identity_cutover_password_identity_cutover_provision":
        "postgres_identity_cutover_password",
}:
    raise SystemExit("rendered E4 provisioner secret set mismatch")
if provisioner.get("entrypoint") != [
    "/bin/sh",
    "/deploy/provision-identity-cutover-roles.sh",
]:
    raise SystemExit("rendered E4 provisioner entrypoint mismatch")

surface = services["identity-cutover"]
surface_secrets = {
    item.get("source"): item.get("target")
    for item in surface.get("secrets", [])
}
if surface_secrets != {
    "database_url_identity_cutover_identity_cutover":
        "database_url",
}:
    raise SystemExit("rendered E4 surface secret set mismatch")
rendered_command = " ".join(surface.get("command", []))
if "cutover is dormant" not in rendered_command or "exit 78" not in rendered_command:
    raise SystemExit("rendered E4 surface is not inert")
if surface.get("logging", {}).get("driver") != "none":
    raise SystemExit("rendered E4 surface logging is enabled")
PY
then
  fail "Docker Compose E4 contract validation failed"
fi

# Never stream captured command output. Search it against every disposable
# master value and fail with a generic message if any value escaped.
leak_marker="$workspace/secret-output-detected"
find "$workspace" -path '*/master/*' -type f -print |
while IFS= read -r secret_path; do
  secret_value="$(tr -d '\r\n' <"$secret_path")"
  if [ -n "$secret_value" ] &&
    grep -F -q -- "$secret_value" "$output_log"
  then
    : >"$leak_marker"
  fi
done
[ ! -e "$leak_marker" ] ||
  fail "a disposable secret value appeared in command output"

echo "identity cutover secret lifecycle gate passed"
