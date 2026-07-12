# Binding operator role upgrade

Existing deployments must add the isolated binding-operator secret pair before
running preflight or replacing Core. On the Home Agent host, run:

```sh
cd /opt/home/home-github/stack
secrets_root="$(sudo sed -n 's/^HOME_AGENT_SECRETS_DIR=//p' \
  /srv/home-agent/config/home-agent.env)"
case "$secrets_root" in /*) ;; *) echo "invalid configured secrets root" >&2; exit 1;; esac
sudo sh home-agent-deploy/add-binding-operator-role-secrets.sh "$secrets_root"
sudo sh home-agent-deploy/materialize-secrets.sh "$secrets_root"
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm provision-roles
```

The helper publishes both root-only master files atomically and refuses to
overwrite a complete or partial set. It never prints either secret. Keep the
deployment in `record_only`; provisioning this role does not authorize binding
or advance rollout mode.

After migration `0005_principal_binding_proposals` commits, never restart or
roll back to a pre-0005 Core image. Its startup contract does not understand the
new authority graph. Application rollback is read-only/degraded until a
compatible image is restored; the migration itself refuses to discard any
binding workflow or confirmed binding rows.
