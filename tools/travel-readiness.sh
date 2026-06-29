#!/usr/bin/env bash
set -u

repo_dir="${HOME_WEB_REPO:-${HOME}/code/home}"
ai_host="${HOME_TRAVEL_AI_HOST:-engineeredlightingserver1.taild52a15.ts.net}"
ha_host="${HOME_TRAVEL_HA_HOST:-homeassistant}"
gateway_url="${HOME_TRAVEL_GATEWAY_URL:-http://127.0.0.1:5181/healthz}"
json_only=0

if [[ "${1:-}" == "--json" ]]; then
  json_only=1
fi

blockers=0
degraded=0
reachable=0
total=0
json_items=()
human_items=()

json_escape() {
  local s="${1:-}"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/}"
  printf '%s' "${s}"
}

add_result() {
  local name="$1"
  local severity="$2"
  local ok="$3"
  local detail="$4"
  local command="$5"
  total=$((total + 1))
  if [[ "${ok}" == "true" ]]; then
    reachable=$((reachable + 1))
  elif [[ "${severity}" == "blocker" ]]; then
    blockers=$((blockers + 1))
  else
    degraded=$((degraded + 1))
  fi
  human_items+=("${ok} ${severity} ${name} - ${detail}")
  json_items+=("{\"name\":\"$(json_escape "${name}")\",\"severity\":\"$(json_escape "${severity}")\",\"ok\":${ok},\"detail\":\"$(json_escape "${detail}")\",\"command\":\"$(json_escape "${command}")\"}")
}

check_command() {
  local name="$1"
  local severity="$2"
  shift 2
  local cmd=("$@")
  if "${cmd[@]}" >/tmp/home-travel-check.out 2>&1; then
    add_result "${name}" "${severity}" true "ok" "${cmd[*]}"
  else
    local detail
    detail="$(tr '\n' ' ' </tmp/home-travel-check.out | cut -c1-220)"
    add_result "${name}" "${severity}" false "${detail:-failed}" "${cmd[*]}"
  fi
}

check_systemd() {
  local service="$1"
  local severity="$2"
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "${service}"; then
    add_result "systemd ${service}" "${severity}" true "active" "systemctl status ${service} --no-pager"
  else
    add_result "systemd ${service}" "${severity}" false "not active" "systemctl status ${service} --no-pager"
  fi
}

check_http() {
  local name="$1"
  local severity="$2"
  local url="$3"
  local ok_statuses="$4"
  local status
  status="$(curl -k -sS -o /tmp/home-travel-http.out -w '%{http_code}' --max-time 5 "${url}" 2>/tmp/home-travel-http.err || true)"
  if [[ " ${ok_statuses} " == *" ${status} "* ]]; then
    add_result "${name}" "${severity}" true "HTTP ${status}" "curl -i ${url}"
  else
    local err
    err="$(tr '\n' ' ' </tmp/home-travel-http.err | cut -c1-160)"
    add_result "${name}" "${severity}" false "HTTP ${status:-000} ${err}" "curl -i ${url}"
  fi
}

check_disk() {
  local path="$1"
  local usage
  usage="$(df -Pk "${path}" 2>/dev/null | awk 'NR==2 { gsub(/%/, "", $5); print $5 }')"
  if [[ -z "${usage}" ]]; then
    add_result "disk ${path}" degraded false "could not read disk usage" "df -h ${path}"
  elif (( usage >= 97 )); then
    add_result "disk ${path}" blocker false "${usage}% full" "df -h ${path}"
  elif (( usage >= 90 )); then
    add_result "disk ${path}" degraded false "${usage}% full" "df -h ${path}"
  else
    add_result "disk ${path}" degraded true "${usage}% full" "df -h ${path}"
  fi
}

check_repo_clean() {
  if [[ ! -d "${repo_dir}/.git" ]]; then
    add_result "repo checkout" degraded false "missing ${repo_dir}" "ls -la ${repo_dir}"
    return
  fi
  local dirty
  dirty="$(cd "${repo_dir}" && git status --short)"
  if [[ -n "${dirty}" ]]; then
    add_result "repo cleanliness" degraded false "uncommitted changes present" "cd ${repo_dir} && git status --short"
  else
    add_result "repo cleanliness" degraded true "clean" "cd ${repo_dir} && git status --short"
  fi
}

if command -v tailscale >/dev/null 2>&1; then
  check_command "tailscale online" blocker tailscale status
else
  add_result "tailscale online" blocker false "tailscale command not found" "tailscale status"
fi

check_systemd home-web-gateway blocker
check_systemd home-apartment-assets degraded
check_systemd hav-stack-supervisor degraded

check_http "web gateway" blocker "${gateway_url}" "200 401"
check_http "Home Assistant API" blocker "http://${ha_host}:8123/api/" "200 401"
check_http "Frigate stats" degraded "http://${ha_host}:5000/api/stats" "200 401"
check_http "vLLM models" degraded "http://${ai_host}:8000/v1/models" "200 401"
check_http "vision" degraded "http://${ai_host}:8091/healthz" "200"
check_http "metrics" degraded "http://${ai_host}:8092/healthz" "200"
check_http "supervisor" degraded "http://${ai_host}:8093/healthz" "200"
check_http "S2S bridge" degraded "http://${ai_host}:8094/healthz" "200"
check_http "intelligence" degraded "http://${ai_host}:8095/healthz" "200"
check_http "tracker" degraded "http://${ai_host}:8098/healthz" "200"
check_http "video labeler" degraded "http://${ai_host}:8099/healthz" "200"
check_http "Apartment assets" degraded "http://${ai_host}:5190/healthz" "200"

check_disk "${repo_dir}"
check_repo_clean

status="ready"
if (( blockers > 0 )); then
  status="blocked"
elif (( degraded > 0 )); then
  status="degraded"
fi

json="{\"status\":\"${status}\",\"generatedAt\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"reachable\":${reachable},\"total\":${total},\"blockers\":${blockers},\"degraded\":${degraded},\"checks\":["
for i in "${!json_items[@]}"; do
  if (( i > 0 )); then json+=","; fi
  json+="${json_items[$i]}"
done
json+="]}"

if (( json_only == 1 )); then
  printf '%s\n' "${json}"
else
  printf 'travel readiness: %s (%s/%s reachable, %s blockers, %s degraded)\n' "${status}" "${reachable}" "${total}" "${blockers}" "${degraded}"
  for item in "${human_items[@]}"; do
    printf '%s\n' "${item}"
  done
  printf '\njson:\n%s\n' "${json}"
fi

if [[ "${status}" == "blocked" ]]; then
  exit 1
fi
exit 0
