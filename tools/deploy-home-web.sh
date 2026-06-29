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

git fetch origin "${branch}"
git pull --ff-only origin "${branch}"
npm run web:check

sudo "${systemctl_bin}" restart "${service}"
sleep 1
sudo "${systemctl_bin}" is-active --quiet "${service}"
sudo "${systemctl_bin}" status --no-pager --lines=20 "${service}"
