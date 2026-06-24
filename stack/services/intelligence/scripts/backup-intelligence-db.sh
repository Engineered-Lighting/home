#!/usr/bin/env bash
set -Eeuo pipefail

DATA_DIR="${INTELLIGENCE_DATA_DIR_HOST:-/opt/home-ai-voice/intelligence-data}"
BACKUP_DIR="${INTELLIGENCE_BACKUP_DIR:-/opt/home-ai-voice/backups/intelligence}"
DB_PATH="${DATA_DIR}/intelligence.sqlite"

if [[ ! -f "${DB_PATH}" ]]; then
  echo "missing intelligence DB: ${DB_PATH}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

manifest="${work_dir}/manifest.txt"
{
  echo "created_utc=${stamp}"
  echo "source=${DB_PATH}"
  echo "host=$(hostname)"
} > "${manifest}"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "${DB_PATH}" ".backup '${work_dir}/intelligence.sqlite'"
  echo "method=sqlite_backup" >> "${manifest}"
else
  cp -a "${DB_PATH}" "${work_dir}/intelligence.sqlite"
  [[ -f "${DB_PATH}-wal" ]] && cp -a "${DB_PATH}-wal" "${work_dir}/intelligence.sqlite-wal"
  [[ -f "${DB_PATH}-shm" ]] && cp -a "${DB_PATH}-shm" "${work_dir}/intelligence.sqlite-shm"
  echo "method=file_copy_with_wal_if_present" >> "${manifest}"
fi

if command -v sha256sum >/dev/null 2>&1; then
  (cd "${work_dir}" && sha256sum intelligence.sqlite*) >> "${manifest}"
fi

archive="${BACKUP_DIR}/intelligence-${stamp}.tgz"
tar -C "${work_dir}" -czf "${archive}" .
echo "${archive}"
