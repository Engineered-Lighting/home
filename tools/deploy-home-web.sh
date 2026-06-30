#!/usr/bin/env bash
set -euo pipefail

repo_dir="${HOME_WEB_REPO:-${HOME}/code/home}"
branch="${HOME_WEB_BRANCH:-main}"
service="${HOME_WEB_SERVICE:-home-web-gateway}"
systemctl_bin="${SYSTEMCTL_BIN:-$(command -v systemctl)}"

if [[ ! -d "${repo_dir}/.git" ]]; then
  echo "Home repo checkout not found: ${repo_dir}" >&2
  exit 1
fi

cd "${repo_dir}"

if [[ -n "$(git status --short)" ]]; then
  echo "Refusing to deploy because ${repo_dir} has uncommitted changes:" >&2
  git status --short >&2
  exit 1
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${current_branch}" != "${branch}" ]]; then
  git checkout "${branch}"
fi

previous_commit="$(git rev-parse HEAD)"

rollback() {
  local reason="$1"
  echo "Deploy failed: ${reason}" >&2
  echo "Previous commit: ${previous_commit}" >&2
  echo "Manual rollback command:" >&2
  echo "  cd ${repo_dir} && git reset --hard ${previous_commit} && sudo systemctl restart ${service}" >&2

  if [[ "${HOME_WEB_NO_AUTO_ROLLBACK:-0}" == "1" ]]; then
    echo "HOME_WEB_NO_AUTO_ROLLBACK=1 set; leaving checkout at $(git rev-parse HEAD)." >&2
    return 1
  fi

  if [[ -z "${previous_commit}" ]]; then
    echo "No previous commit recorded; cannot auto-rollback." >&2
    return 1
  fi

  echo "Rolling back ${repo_dir} to ${previous_commit}..." >&2
  git reset --hard "${previous_commit}" >&2
  echo "Restarting ${service} after rollback..." >&2
  sudo "${systemctl_bin}" restart "${service}" >&2 || true
  sleep 1
  if sudo "${systemctl_bin}" is-active --quiet "${service}"; then
    sudo "${systemctl_bin}" status --no-pager --lines=20 "${service}" >&2 || true
    echo "Rollback completed." >&2
  else
    echo "Rollback checkout completed, but ${service} is still not active." >&2
    sudo "${systemctl_bin}" status --no-pager --lines=40 "${service}" >&2 || true
    return 1
  fi
}

git fetch origin "${branch}"
git pull --ff-only origin "${branch}"
new_commit="$(git rev-parse HEAD)"

if ! npm run web:check; then
  rollback "web config check failed"
  exit 1
fi

if command -v python3 >/dev/null 2>&1 && [[ -f "${repo_dir}/app/data/apartment/apartment.ply" ]]; then
  if ! python3 tools/make-apartment-mobile-splat.py --quiet; then
    rollback "mobile apartment photo asset generation failed"
    exit 1
  fi
fi

if ! tools/check-home-web-assets.sh; then
  rollback "apartment asset check failed"
  exit 1
fi

if ! sudo "${systemctl_bin}" restart "${service}"; then
  rollback "service restart failed"
  exit 1
fi
sleep 1
if ! sudo "${systemctl_bin}" is-active --quiet "${service}"; then
  rollback "service did not become active"
  exit 1
fi
sudo "${systemctl_bin}" status --no-pager --lines=20 "${service}"
echo "Deployed ${service}: ${previous_commit} -> ${new_commit}"
