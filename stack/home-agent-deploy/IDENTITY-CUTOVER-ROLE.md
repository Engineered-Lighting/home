# Identity semantic-authority cutover roles

The deployment contains a dormant, fail-closed boundary for the future E4
semantic-authority cutover. It does not activate a cutover and does not make
the legacy Identity Store read-only.

The boundary is separate from the migration and finalizer credentials. The
historical `provision-roles.sh` path deliberately does not know either E4 role,
so a fresh database can reproduce the reviewed 0001–0013 catalog unchanged.
Only the additive, revision-0013 ceremony creates this pair:

- `home_agent_identity_cutover` is a `LOGIN`, `NOINHERIT`, `NOBYPASSRLS`,
  connection-limit-one role. Provisioning commits its 1970 expiry before
  catalog admission, terminates existing sessions with a bounded wait, and
  fails unless the zero-session state is proven.
- `home_agent_identity_cutover_kernel` is `NOLOGIN`, `NOINHERIT`,
  `NOBYPASSRLS`, and connection-limit-zero. Only `home_agent_owner` receives a
  non-inherited, SET-only membership for migration-time ownership management.

The role pair receives no Core, BFF, operator, service, model, encryption,
spool, ledger, media, Home Assistant, or legacy Identity Store credential.
Grant replay removes schema, table, column, sequence, function, type, default,
temporary-object, and database-creation authority. The login retains only an
unusable `CONNECT` privilege to `home_agent` while expired. Exact `pg_hba`
rules reject this role before the generic rule for every other database, so a
changed client URL cannot turn the disposable credential into a temporary
object or connection surface elsewhere in the cluster.

`home_agent_owner` is the PostgreSQL bootstrap/deployment superuser and remains
an explicit trusted-root boundary. Row-level security and the E4 ACLs constrain
application and cutover runtime roles; they do not claim to make evidence
tamper-proof against the database superuser.

The `identity-cutover` operator-profile service mounts only the isolated
database URL. Its fixed entrypoint exits with status 78 before opening that
file. It has no port, application network, writable filesystem, Linux
capability, or log output.

## Deliberate E4 catalog stop

Grant replay recognizes these future E4 control-object names:

- `operations.enforced_legacy_identity_writer_freezes`
- `operations.reviewed_identity_cutover_admissions`
- `operations.semantic_authority_promotions`
- `operations.commit_reviewed_identity_cutover(bytea,uuid)`

Before the ceremony, revisions through `0013_identity_finalizer_e3` have
neither role and none of these objects. After the ceremony, the exact expired
pair may exist but must own nothing. Partial roles or objects, wrong-revision
objects, stale grants, and a 0014 upgrade attempted without the ceremony all
fail closed.

The exact reviewed 0014 post-quarantine catalog digest is pinned as
`a96aeb68c7c5656988088ae74539760c6a811320849f01c122e02141f87eff27`.
If all four objects appear at `0014_identity_cutover_e4`, the hosted gate
recomputes and validates that digest, then must reach the deliberate
`identity cutover E4 activation contract is not installed` stop. The hosted
gate adds no positive grant and prints neither the raw PostgreSQL failure
output nor a digest-discovery marker. The digest describes the
post-quarantine state:
relation ownership and RLS flags, column and constraint shape, user triggers
and rewrite rules, normalized policy roles/commands/qualifiers/checks,
table/column/function ACLs, function ownership/configuration/body, and every
direct application-catalog grant to the two dormant roles. It also records
their effective access to every exact E4 dependency so a grant through
`PUBLIC` cannot disappear from the review. This pin admits only that dormant
catalog. The external legacy-writer-freeze ceremony remains a separate
operator review, and any later callable activation needs a distinct reviewed
activation manifest.

Production remains pinned to `0006a_worker_lease_arbitration` and
`record_only`.

## Existing installation preparation

Do not prepare or run this role on the live host yet. After a later deployment
review explicitly authorizes dormant role preparation, the additive helper can
create its independent secret pair without replacing existing secrets. The
database must already be at 0013:

```sh
cd /opt/home/home-github/stack
secrets_root="$(sudo sed -n 's/^HOME_AGENT_SECRETS_DIR=//p' \
  /srv/home-agent/config/home-agent.env)"
case "$secrets_root" in /*) ;; *) echo "invalid secrets root" >&2; exit 1;; esac
sudo sh home-agent-deploy/add-identity-cutover-role-secrets.sh "$secrets_root"
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml --profile operator run --rm \
  provision-identity-cutover-roles
sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
  -f home-agent-compose.yml run --rm grant-runtime
```

The helper refuses complete, partial, symlinked, or legacy shared layouts. It
generates a password distinct from every existing database role, atomically
publishes the root-only pair, materializes a two-secret root-only role
provisioner directory plus exactly one mode-0400 database URL for the dormant
service, and prints no secret. The dedicated provisioner rejects every
revision except 0013 and every partial E4 object/role state. Grant replay then
quarantines the expired pair before 0014 may be tested.

### Safe recovery after master publication

The master pair is intentionally preserved if runtime materialization or its
E4 preflight fails after the atomic rename. Do not delete it, run the ordinary
create command again, or hand-edit either file. Correct the reported
runtime/master-layout finding, then use the explicit recovery path:

```sh
sudo sh home-agent-deploy/add-identity-cutover-role-secrets.sh \
  "$secrets_root" --resume-existing
```

Resume accepts only the exact root-owned, mode-0700 two-file directory with
canonical mode-0600 password and URL files. It revalidates the credential
against every historical database role, rejects stale/symlinked/partial
publication state, and then runs the normal materializer and E4 preflight. It
does not invoke the random generator and never writes the E4 master pair. If
resume validation fails, stop and preserve the directory for review rather
than trying to reconstruct either value.

Pristine installations with neither the master pair nor an E4 runtime copy
remain valid. A runtime copy without that master pair, or an abandoned
`.identity-cutover.new.*` or `.identity-cutover.previous.*` publication, is
treated as orphaned authority and blocks materialization/preflight.

The GitHub-hosted gate exercises initial publication, re-materialization,
interrupted-publication recovery, exact owners/modes/file sets, secret-output
redaction, malformed/matching credentials, symlink and stale-path rejection,
and the rendered dormant Compose services in a disposable root-only tree. It
is guarded against execution outside GitHub-hosted Linux and is not a live
deployment procedure.
