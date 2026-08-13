#!/bin/sh
set -eu

# Docker Compose implements file-backed secrets as bind mounts, so the source
# file's numeric owner must match the unprivileged container user. Keep the
# generated master set root-only and expose a distinct mode-0400 copy to each
# service. Compose mounts individual files, never these directories.

secrets_root="${1:?usage: materialize-secrets.sh <secrets-root>}"
master_root="$secrets_root/master"
runtime_root="$secrets_root/runtime"

[ "$(id -u)" -eq 0 ] || {
  echo "materialize-secrets.sh must run as root" >&2
  exit 77
}
case "$secrets_root" in
  /*) ;;
  *) echo "secrets root must be absolute" >&2; exit 78 ;;
esac
[ -d "$secrets_root" ] && [ ! -L "$secrets_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$secrets_root")" = "0:0:700" ] || {
  echo "root-only secret directory is absent or unsafe" >&2
  exit 78
}
[ -d "$master_root" ] && [ ! -L "$master_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$master_root")" = "0:0:700" ] || {
  echo "root-only master secret directory is absent or unsafe" >&2
  exit 78
}

umask 077
if [ -e "$runtime_root" ] || [ -L "$runtime_root" ]; then
  [ -d "$runtime_root" ] && [ ! -L "$runtime_root" ] &&
    [ "$(stat -c '%u:%g:%a' "$runtime_root")" = "0:0:700" ] || {
    echo "runtime secret directory is unsafe" >&2
    exit 78
  }
else
  install -d -m 0700 -o root -g root "$runtime_root"
fi

stale_cutover_master="$(
  find "$master_root" -mindepth 1 -maxdepth 1 \
    \( -name '.identity-cutover.new.*' \
       -o -name '.identity-cutover.previous.*' \) \
    -print -quit
)"
[ -z "$stale_cutover_master" ] || {
  echo "stale identity cutover master publication exists" >&2
  exit 73
}

identity_cutover_master="$master_root/identity-cutover"
identity_cutover_enabled=0
if [ -e "$identity_cutover_master" ] || [ -L "$identity_cutover_master" ]; then
  [ -d "$identity_cutover_master" ] && [ ! -L "$identity_cutover_master" ] || {
    echo "identity cutover master path is not a safe directory" >&2
    exit 78
  }
  identity_cutover_enabled=1
else
  orphaned_cutover_runtime="$(
    find "$runtime_root" -mindepth 1 -maxdepth 1 \
      \( -name 'identity-cutover' \
         -o -name 'provision-identity-cutover-roles' \
         -o -name '.identity-cutover.new.*' \
         -o -name '.identity-cutover.previous.*' \
         -o -name '.provision-identity-cutover-roles.new.*' \
         -o -name '.provision-identity-cutover-roles.previous.*' \) \
      -print -quit
  )"
  [ -z "$orphaned_cutover_runtime" ] || {
    echo "orphaned identity cutover runtime exists without its master" >&2
    exit 78
  }
fi
unset orphaned_cutover_runtime stale_cutover_master

install_secret() {
  service="$1"
  source_name="$2"
  target_name="$3"
  owner_uid="$4"
  owner_gid="$5"
  source_path="$master_root/$source_name"
  service_dir="$runtime_root/$service"
  target_path="$service_dir/$target_name"
  temporary_path="$service_dir/.$target_name.tmp.$$"

  [ -s "$source_path" ] || {
    echo "missing master secret: $source_path" >&2
    exit 78
  }
  install -d -m 0700 -o root -g root "$service_dir"
  install -m 0400 -o "$owner_uid" -g "$owner_gid" \
    "$source_path" "$temporary_path"
  mv -f "$temporary_path" "$target_path"
}

# Dormant operator roles receive exact one-file runtime directories. Identity
# migration previously received two bearer-token copies, so atomic replacement
# also ensures those files cannot survive rematerialization. Only ordinary
# files are accepted in a retired directory; symlinks and nesting fail closed.
install_exact_single_secret_service() {
  service="$1"
  source_name="$2"
  target_name="$3"
  owner_uid="$4"
  owner_gid="$5"
  service_label="$6"
  source_path="$master_root/$source_name"
  service_dir="$runtime_root/$service"
  temporary_dir="$runtime_root/.$service.new.$$"
  previous_dir="$runtime_root/.$service.previous.$$"

  [ -s "$source_path" ] && [ ! -L "$source_path" ] || {
    echo "missing or unsafe master secret: $source_path" >&2
    exit 78
  }
  stale_publication="$(
    find "$runtime_root" -mindepth 1 -maxdepth 1 \
      \( -name ".$service.new.*" -o -name ".$service.previous.*" \) \
      -print -quit
  )"
  [ -z "$stale_publication" ] || {
    echo "$service_label stale runtime publication exists" >&2
    exit 73
  }
  [ ! -e "$temporary_dir" ] && [ ! -L "$temporary_dir" ] &&
    [ ! -e "$previous_dir" ] && [ ! -L "$previous_dir" ] || {
    echo "$service_label runtime publication path already exists" >&2
    exit 73
  }
  install -d -m 0700 -o root -g root "$temporary_dir"
  if ! install -m 0400 -o "$owner_uid" -g "$owner_gid" \
    "$source_path" "$temporary_dir/$target_name"; then
    rm -f "$temporary_dir/$target_name"
    rmdir "$temporary_dir" 2>/dev/null || true
    exit 78
  fi

  if [ -e "$service_dir" ] || [ -L "$service_dir" ]; then
    [ -d "$service_dir" ] && [ ! -L "$service_dir" ] || {
      rm -f "$temporary_dir/$target_name"
      rmdir "$temporary_dir"
      echo "$service_label runtime path is not a safe directory" >&2
      exit 78
    }
    unsafe_entry="$(find "$service_dir" -mindepth 1 -maxdepth 1 ! -type f -print -quit)"
    [ -z "$unsafe_entry" ] || {
      rm -f "$temporary_dir/$target_name"
      rmdir "$temporary_dir"
      echo "$service_label runtime directory contains an unsafe entry" >&2
      exit 78
    }
    if ! mv -T --no-clobber "$service_dir" "$previous_dir"; then
      rm -f "$temporary_dir/$target_name"
      rmdir "$temporary_dir"
      echo "$service_label runtime retirement failed" >&2
      exit 73
    fi
  fi

  if ! mv -T --no-clobber "$temporary_dir" "$service_dir"; then
    [ ! -d "$previous_dir" ] || mv -T --no-clobber "$previous_dir" "$service_dir"
    rm -f "$temporary_dir/$target_name"
    rmdir "$temporary_dir" 2>/dev/null || true
    echo "$service_label runtime publication failed" >&2
    exit 73
  fi
  if [ -d "$previous_dir" ]; then
    find "$previous_dir" -mindepth 1 -maxdepth 1 -type f -delete
    rmdir "$previous_dir"
  fi
}

install_exact_secret_pair_service() {
  service="$1"
  first_source="$2"
  first_target="$3"
  second_source="$4"
  second_target="$5"
  owner_uid="$6"
  owner_gid="$7"
  service_label="$8"
  service_dir="$runtime_root/$service"
  temporary_dir="$runtime_root/.$service.new.$$"
  previous_dir="$runtime_root/.$service.previous.$$"

  for source_name in "$first_source" "$second_source"; do
    source_path="$master_root/$source_name"
    [ -s "$source_path" ] && [ ! -L "$source_path" ] || {
      echo "missing or unsafe master secret: $source_path" >&2
      exit 78
    }
  done
  stale_publication="$(
    find "$runtime_root" -mindepth 1 -maxdepth 1 \
      \( -name ".$service.new.*" -o -name ".$service.previous.*" \) \
      -print -quit
  )"
  [ -z "$stale_publication" ] || {
    echo "$service_label stale runtime publication exists" >&2
    exit 73
  }
  [ ! -e "$temporary_dir" ] && [ ! -L "$temporary_dir" ] &&
    [ ! -e "$previous_dir" ] && [ ! -L "$previous_dir" ] || {
    echo "$service_label runtime publication path already exists" >&2
    exit 73
  }
  install -d -m 0700 -o root -g root "$temporary_dir"
  if ! install -m 0400 -o "$owner_uid" -g "$owner_gid" \
       "$master_root/$first_source" "$temporary_dir/$first_target" ||
     ! install -m 0400 -o "$owner_uid" -g "$owner_gid" \
       "$master_root/$second_source" "$temporary_dir/$second_target"; then
    rm -f "$temporary_dir/$first_target" "$temporary_dir/$second_target"
    rmdir "$temporary_dir" 2>/dev/null || true
    exit 78
  fi

  if [ -e "$service_dir" ] || [ -L "$service_dir" ]; then
    [ -d "$service_dir" ] && [ ! -L "$service_dir" ] || {
      rm -f "$temporary_dir/$first_target" "$temporary_dir/$second_target"
      rmdir "$temporary_dir"
      echo "$service_label runtime path is not a safe directory" >&2
      exit 78
    }
    unsafe_entry="$(find "$service_dir" -mindepth 1 -maxdepth 1 ! -type f -print -quit)"
    [ -z "$unsafe_entry" ] || {
      rm -f "$temporary_dir/$first_target" "$temporary_dir/$second_target"
      rmdir "$temporary_dir"
      echo "$service_label runtime directory contains an unsafe entry" >&2
      exit 78
    }
    if ! mv -T --no-clobber "$service_dir" "$previous_dir"; then
      rm -f "$temporary_dir/$first_target" "$temporary_dir/$second_target"
      rmdir "$temporary_dir"
      echo "$service_label runtime retirement failed" >&2
      exit 73
    fi
  fi

  if ! mv -T --no-clobber "$temporary_dir" "$service_dir"; then
    [ ! -d "$previous_dir" ] ||
      mv -T --no-clobber "$previous_dir" "$service_dir"
    rm -f "$temporary_dir/$first_target" "$temporary_dir/$second_target"
    rmdir "$temporary_dir" 2>/dev/null || true
    echo "$service_label runtime publication failed" >&2
    exit 73
  fi
  if [ -d "$previous_dir" ]; then
    find "$previous_dir" -mindepth 1 -maxdepth 1 -type f -delete
    rmdir "$previous_dir"
  fi
}

# The official postgres entrypoint re-executes as UID/GID 999 before reading
# POSTGRES_PASSWORD_FILE, so this copy must be readable only by that account.
install_secret postgres postgres_owner_password postgres_owner_password 999 999

# Root-started bootstrap jobs.
for name in \
  postgres_owner_password \
  postgres_api_password \
  postgres_ingest_password \
  postgres_worker_password \
  postgres_erasure_password \
  postgres_backup_password
do
  install_secret provision-roles "$name" "$name" 0 0
done
install_secret provision-roles rollout/postgres_rollout_password \
  postgres_rollout_password 0 0
install_secret provision-roles binding-operator/postgres_binding_operator_password \
  postgres_binding_operator_password 0 0
install_secret provision-roles binding-committer/postgres_binding_committer_password \
  postgres_binding_committer_password 0 0
install_secret provision-roles identity-migration/postgres_identity_migration_password \
  postgres_identity_migration_password 0 0
identity_finalizer_master="$master_root/identity-finalizer"
if [ -e "$identity_finalizer_master" ] || [ -L "$identity_finalizer_master" ]; then
  [ -d "$identity_finalizer_master" ] && [ ! -L "$identity_finalizer_master" ] || {
    echo "identity finalizer master path is not a safe directory" >&2
    exit 78
  }
  install_secret provision-roles identity-finalizer/postgres_identity_finalizer_password \
    postgres_identity_finalizer_password 0 0
fi
if [ "$identity_cutover_enabled" -eq 1 ]; then
  install_exact_secret_pair_service provision-identity-cutover-roles \
    postgres_owner_password postgres_owner_password \
    identity-cutover/postgres_identity_cutover_password \
    postgres_identity_cutover_password 0 0 \
    "identity cutover role provisioner"
fi
install_secret grant-runtime postgres_owner_password postgres_owner_password 0 0
install_secret grant-phase3-activation \
  postgres_owner_password postgres_owner_password 0 0
install_secret identity-role-activation \
  postgres_owner_password postgres_owner_password 0 0
install_secret provision-identity-binding-kernel-role \
  postgres_owner_password postgres_owner_password 0 0
install_secret provision-parent-relationship-kernel-role \
  postgres_owner_password postgres_owner_password 0 0

# pgBackRest authenticates as a dedicated non-superuser over the shared Unix
# socket. It never receives the bootstrap owner password.
install_secret backup-gate postgres_backup_password postgres_backup_password 999 999

# Core runs as the fixed homeagent UID/GID 10001.
install_secret migrate database_url_owner database_url 10001 10001
install_secret core-api database_url_api database_url 10001 10001
install_secret core-api binding-operator/database_url_binding_operator \
  operator_database_url 10001 10001
install_secret core-api binding-committer/database_url_binding_committer \
  binding_commit_database_url 10001 10001
install_secret core-api knowledge_encryption_key knowledge_encryption_key 10001 10001
install_secret core-api service_token service_token 10001 10001
install_secret core-api operator_token operator_token 10001 10001
install_secret core-api bootstrap_token bootstrap_token 10001 10001
install_secret core-ingest database_url_ingest database_url 10001 10001
install_secret core-ingest runtime_spool_key runtime_spool_key 10001 10001
install_secret core-ingest knowledge_encryption_key knowledge_encryption_key 10001 10001
install_secret core-ingest edge_token edge_token 10001 10001
install_secret core-worker database_url_worker database_url 10001 10001
install_secret core-worker runtime_spool_key runtime_spool_key 10001 10001
install_secret core-worker erasure_ledger_key erasure_ledger_key 10001 10001
install_secret ledger-init erasure_ledger_key erasure_ledger_key 10001 10001
install_secret restore-replay database_url_erasure database_url 10001 10001
install_secret restore-replay erasure_ledger_key erasure_ledger_key 10001 10001
install_secret rollout-authorize rollout/database_url_rollout database_url 10001 10001

# The official Node Alpine image's node account is fixed at UID/GID 1000.
install_secret bff service_token service_token 1000 1000
install_secret bff session_encryption_key session_encryption_key 1000 1000
install_secret bff native_installations.json native_installations 1000 1000

# Dormant reviewed-migration profile. The persisted URL targets a login that
# provisioning always expires, so merely starting the profile cannot connect.
install_exact_single_secret_service identity-migration \
  identity-migration/database_url_identity_migration database_url 10001 10001 \
  "identity migration"
if [ -e "$identity_finalizer_master" ] || [ -L "$identity_finalizer_master" ]; then
  install_exact_single_secret_service identity-finalizer \
    identity-finalizer/database_url_identity_finalizer database_url 10001 10001 \
    "identity finalizer"
fi
if [ "$identity_cutover_enabled" -eq 1 ]; then
  install_exact_single_secret_service identity-cutover \
    identity-cutover/database_url_identity_cutover database_url 10001 10001 \
    "identity cutover"
fi

echo "materialized least-privilege Home Agent service secrets"
