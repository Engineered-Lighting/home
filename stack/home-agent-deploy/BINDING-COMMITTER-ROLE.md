# Binding committer role upgrade

`home_agent_binding_committer` is independent from the proposal-staging
`home_agent_binding_operator`. Generate and materialize it before running
preflight for an image that contains E5c:

```sh
cd /opt/home/home-github/stack
secrets_root="$(sudo sed -n 's/^HOME_AGENT_SECRETS_DIR=//p' \
  /srv/home-agent/config/home-agent.env)"
case "$secrets_root" in /*) ;; *) echo "invalid configured secrets root" >&2; exit 1;; esac
sudo sh home-agent-deploy/add-binding-committer-role-secrets.sh "$secrets_root"
sudo sh home-agent-deploy/materialize-secrets.sh "$secrets_root"
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm provision-roles
```

The helper creates a distinct 64-character random password and database URL in
a root-only, atomically published master directory. Materialization exposes the
URL only to Core API and the password only to role provisioning. Preflight
fails if the committer password matches the operator, API, owner, or any other
database role password.

At revision `0016_principal_binding_e5b`, grant replay leaves the committer
without schema or function access. At
`0017_authenticated_binding_e5c`, the reviewed replay grants only identity
schema `USAGE` and `EXECUTE` on
`identity.commit_authenticated_principal_binding_e5b(...)`. It grants no table,
sequence, generic function, role membership, or schema-creation privilege.

Adding the secret or role does not activate binding. Keep the live deployment
at `0006a_worker_lease_arbitration` in `record_only` until the hosted E5c gate
has passed and rollout is separately approved.
