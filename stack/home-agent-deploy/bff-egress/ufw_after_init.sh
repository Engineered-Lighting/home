#!/bin/sh
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH LANG=C LC_ALL=C
unset CDPATH ENV LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH PYTHONSTARTUP

helper=/usr/local/libexec/home-agent-bff-egress/firewall_contract.py
environment=/srv/home-agent/config/home-agent.env

case "${1:-}" in
  start|stop|flush-all)
    # Keep the first-hop bridge guard present across boot, stop, reload, and
    # flush lifecycle boundaries. This action uses only root-owned static
    # configuration; Docker, DNS, and Tailscale need not be online yet.
    exec /usr/bin/python3 -I "$helper" guard --env "$environment"
    ;;
  status)
    exit 0
    ;;
  *)
    echo "unsupported Home Agent UFW lifecycle action" >&2
    exit 64
    ;;
esac
