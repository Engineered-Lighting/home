#!/bin/bash
# VRAM audit helper — runs on Ubuntu AI box.
# Maps each VRAM-holding PID to its parent container (if any) and command.
set -u
echo "=== nvidia-smi compute apps ==="
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader

echo ""
echo "=== PID → command + container ==="
for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do
  cmd=$(ps -o cmd= -p "$pid" 2>/dev/null | head -c 200)
  printf "PID %s\n  cmd: %s\n" "$pid" "$cmd"
  # Find the container PID (host-side) for each running container
  matched=""
  for cid in $(docker ps -q 2>/dev/null); do
    cpid=$(docker inspect --format '{{.State.Pid}}' "$cid" 2>/dev/null)
    cname=$(docker inspect --format '{{.Name}}' "$cid" 2>/dev/null | sed 's/^\///')
    # The compute-apps PID is the host-namespace PID of the process inside the container.
    # The container's main PID is its PID 1; child PIDs share the same cgroup.
    if [ -n "$cpid" ]; then
      # Check if this PID belongs to that container's pid-cgroup
      if grep -q "/docker/$cid" /proc/$pid/cgroup 2>/dev/null; then
        matched="$cname"
        break
      fi
    fi
  done
  if [ -n "$matched" ]; then
    printf "  container: %s\n" "$matched"
  else
    printf "  container: (host process, not in a container)\n"
  fi
  printf "\n"
done

echo "=== Containers + their main service ==="
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'

echo ""
echo "=== Stopped containers (potentially uninstalled) ==="
docker ps -a --filter status=exited --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | head -20
