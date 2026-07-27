#!/bin/sh
set -eu
umask 077

usage="usage: add-identity-cutover-role-secrets.sh <secrets-root> [--resume-existing]"
secrets_root="${1:?$usage}"
resume_existing=0
case "${2:-}" in
  "") ;;
  --resume-existing) resume_existing=1 ;;
  *) echo "$usage" >&2; exit 64 ;;
esac
[ "$#" -le 2 ] || {
  echo "$usage" >&2
  exit 64
}
master_root="$secrets_root/master"
runtime_root="$secrets_root/runtime"
target="$master_root/identity-cutover"
temporary="$master_root/.identity-cutover.new.$$"
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || {
  echo "add-identity-cutover-role-secrets.sh must run as root" >&2
  exit 77
}
case "$secrets_root" in
  /*) ;;
  *) echo "secrets root must be absolute" >&2; exit 78 ;;
esac
[ -d "$secrets_root" ] && [ ! -L "$secrets_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$secrets_root")" = "0:0:700" ] || {
  echo "existing secret root is absent or unsafe" >&2
  exit 78
}
[ -d "$master_root" ] && [ ! -L "$master_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$master_root")" = "0:0:700" ] || {
  echo "existing master secret directory is absent or unsafe" >&2
  exit 78
}
[ -d "$runtime_root" ] && [ ! -L "$runtime_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$runtime_root")" = "0:0:700" ] || {
  echo "existing runtime secret directory is absent or unsafe" >&2
  exit 78
}

stale_master="$(
  find "$master_root" -mindepth 1 -maxdepth 1 \
    \( -name '.identity-cutover.new.*' \
       -o -name '.identity-cutover.previous.*' \) \
    -print -quit
)"
[ -z "$stale_master" ] || {
  echo "stale identity cutover master publication exists" >&2
  exit 73
}
stale_runtime="$(
  find "$runtime_root" -mindepth 1 -maxdepth 1 \
    \( -name '.identity-cutover.new.*' \
       -o -name '.identity-cutover.previous.*' \
       -o -name '.provision-identity-cutover-roles.new.*' \
       -o -name '.provision-identity-cutover-roles.previous.*' \) \
    -print -quit
)"
[ -z "$stale_runtime" ] || {
  echo "stale identity cutover runtime publication exists" >&2
  exit 73
}
unset stale_master stale_runtime

for path in \
  "$master_root/postgres_identity_cutover_password" \
  "$master_root/database_url_identity_cutover"
do
  [ ! -e "$path" ] && [ ! -L "$path" ] || {
    echo "identity cutover secret set already exists or is partial; refusing to overwrite" >&2
    exit 73
  }
done

validate_existing_role_passwords() {
  candidate="$1"
  for other_path in \
    "$master_root/postgres_owner_password" \
    "$master_root/postgres_api_password" \
    "$master_root/postgres_ingest_password" \
    "$master_root/postgres_worker_password" \
    "$master_root/postgres_erasure_password" \
    "$master_root/postgres_backup_password" \
    "$master_root/binding-operator/postgres_binding_operator_password" \
    "$master_root/rollout/postgres_rollout_password" \
    "$master_root/identity-migration/postgres_identity_migration_password" \
    "$master_root/identity-finalizer/postgres_identity_finalizer_password"
  do
    [ -f "$other_path" ] && [ ! -L "$other_path" ] &&
      [ "$(stat -c '%u:%g:%a' "$other_path")" = "0:0:600" ] || {
      echo "existing database role secret is absent or unsafe: $other_path" >&2
      exit 78
    }
    other_password="$(tr -d '\r\n' < "$other_path")"
    case "$other_password" in
      *[!0-9a-f]*|'')
        echo "existing database role secret is malformed: $other_path" >&2
        exit 78
        ;;
    esac
    [ "${#other_password}" -eq 64 ] &&
      [ "$(wc -c < "$other_path")" -eq 65 ] &&
      [ "$(wc -l < "$other_path")" -eq 1 ] &&
      [ "$(tr -cd '\r' < "$other_path" | wc -c)" -eq 0 ] &&
      [ -z "$(tail -c 1 "$other_path")" ] || {
      echo "existing database role secret is malformed: $other_path" >&2
      exit 78
    }
    [ -z "$candidate" ] || [ "$candidate" != "$other_password" ] || {
      echo "identity cutover password must differ from every database role" >&2
      exit 70
    }
  done
  unset other_password other_path candidate
}

validate_published_cutover_pair() {
  [ -d "$target" ] && [ ! -L "$target" ] &&
    [ "$(stat -c '%u:%g:%a' "$target")" = "0:0:700" ] || {
    echo "published identity cutover master secret directory is unsafe" >&2
    exit 78
  }
  [ "$(find "$target" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
    echo "published identity cutover master secret set is incomplete" >&2
    exit 78
  }
  for name in postgres_identity_cutover_password database_url_identity_cutover
  do
    path="$target/$name"
    [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] &&
      [ "$(stat -c '%u:%g:%a' "$path")" = "0:0:600" ] || {
      echo "published identity cutover master secret is unsafe: $path" >&2
      exit 78
    }
  done
  cutover_password="$(
    tr -d '\r\n' < "$target/postgres_identity_cutover_password"
  )"
  case "$cutover_password" in
    *[!0-9a-f]*|'')
      echo "published identity cutover password is malformed" >&2
      exit 78
      ;;
  esac
  [ "${#cutover_password}" -eq 64 ] &&
    [ "$(wc -c < "$target/postgres_identity_cutover_password")" -eq 65 ] &&
    [ "$(wc -l < "$target/postgres_identity_cutover_password")" -eq 1 ] &&
    [ "$(tr -cd '\r' < "$target/postgres_identity_cutover_password" | wc -c)" -eq 0 ] &&
    [ -z "$(tail -c 1 "$target/postgres_identity_cutover_password")" ] || {
    echo "published identity cutover password is malformed" >&2
    exit 78
  }
  cutover_url="$(
    tr -d '\r\n' < "$target/database_url_identity_cutover"
  )"
  expected_url="postgresql+psycopg://home_agent_identity_cutover:${cutover_password}@postgres:5432/home_agent"
  [ "$cutover_url" = "$expected_url" ] &&
    [ "$(wc -c < "$target/database_url_identity_cutover")" -eq $((${#expected_url} + 1)) ] &&
    [ "$(wc -l < "$target/database_url_identity_cutover")" -eq 1 ] &&
    [ "$(tr -cd '\r' < "$target/database_url_identity_cutover" | wc -c)" -eq 0 ] &&
    [ -z "$(tail -c 1 "$target/database_url_identity_cutover")" ] || {
    echo "published identity cutover database URL is malformed" >&2
    exit 78
  }
  validate_existing_role_passwords "$cutover_password"
  unset cutover_password cutover_url expected_url name path
}

for service in provision-identity-cutover-roles identity-cutover; do
  service_path="$runtime_root/$service"
  if [ -e "$service_path" ] || [ -L "$service_path" ]; then
    [ "$resume_existing" -eq 1 ] &&
      [ -d "$service_path" ] && [ ! -L "$service_path" ] &&
      [ "$(stat -c '%u:%g:%a' "$service_path")" = "0:0:700" ] || {
      echo "identity cutover runtime target already exists or is unsafe" >&2
      exit 73
    }
  fi
done
unset service service_path

if [ "$resume_existing" -eq 1 ]; then
  validate_published_cutover_pair
else
  [ ! -e "$target" ] && [ ! -L "$target" ] || {
    echo "identity cutover secret set already exists or is partial; refusing to overwrite" >&2
    exit 73
  }
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] || {
    echo "temporary identity cutover secret path already exists" >&2
    exit 73
  }
  validate_existing_role_passwords ""

  cleanup() {
    if [ -d "$temporary" ] && [ ! -L "$temporary" ]; then
      find "$temporary" -mindepth 1 -maxdepth 1 -type f -delete
      rmdir "$temporary" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT HUP INT TERM

  install -d -m 0700 -o root -g root "$temporary"
  password="$(openssl rand -hex 32)"
  case "$password" in
    *[!0-9a-f]*|'')
      echo "generated identity cutover password is invalid" >&2
      exit 70
      ;;
  esac
  [ "${#password}" -eq 64 ] || {
    echo "generated identity cutover password has the wrong length" >&2
    exit 70
  }
  validate_existing_role_passwords "$password"

  printf '%s\n' "$password" > "$temporary/postgres_identity_cutover_password"
  printf 'postgresql+psycopg://home_agent_identity_cutover:%s@postgres:5432/home_agent\n' \
    "$password" > "$temporary/database_url_identity_cutover"
  chmod 0600 "$temporary/postgres_identity_cutover_password" \
    "$temporary/database_url_identity_cutover"
  chown root:root "$temporary/postgres_identity_cutover_password" \
    "$temporary/database_url_identity_cutover"
  sync -f "$temporary/postgres_identity_cutover_password"
  sync -f "$temporary/database_url_identity_cutover"
  sync -f "$temporary"
  mv -T --no-clobber "$temporary" "$target"
  [ -d "$target" ] && [ ! -e "$temporary" ] || {
    echo "identity cutover secret set publication raced or failed" >&2
    exit 73
  }
  sync -f "$master_root"
  trap - EXIT HUP INT TERM
  unset password
fi

if ! sh "$script_dir/materialize-secrets.sh" "$secrets_root"; then
  echo "identity cutover master secret set is preserved; repair runtime materialization and retry with --resume-existing" >&2
  exit 78
fi
if ! sh "$script_dir/preflight-identity-cutover-roles.sh" "$secrets_root"; then
  echo "identity cutover master secret set is preserved; repair preflight findings and retry with --resume-existing" >&2
  exit 78
fi
if [ "$resume_existing" -eq 1 ]; then
  echo "resumed the existing isolated identity cutover role secret set"
else
  echo "created the isolated identity cutover role secret set"
fi
