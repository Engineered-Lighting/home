"""Fixed operator activation for validated preference permissions.

Run only in a reviewed maintenance window after backup and migration. This
does not provision owner links, source grants, listeners, or runtime secrets.
It preserves the commissioned legacy grants instead of replaying the older
generic grants script against a schema that script does not support.
"""
import argparse
import importlib.util
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

FUNCTION="identity.resolve_personal_preference_authority_v1(text,text,text,boolean)"
TRIGGER_FUNCTION="ingest.guard_personal_preference_lineage_v1()"
READER="home_agent_personal_memory_reader"
REVISION="0047_personal_pref_authority_v1"
COLUMNS=("link_id","parent_artifact_id","child_artifact_id","relation")


def _migration():
    path=Path(__file__).resolve().parents[1]/"alembic/versions/0047_personal_preference_authority.py"
    spec=importlib.util.spec_from_file_location("_preference_permission_contract",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_permissions(connection, *, enabled):
    if type(enabled) is not bool:
        raise ValueError("explicit activation mode required")
    state=connection.execute(text("SELECT current_user,session_user,current_setting('transaction_isolation'),"
        "(SELECT array_agg(version_num::text) FROM public.alembic_version)" )).one()
    if tuple(state)!=("home_agent_owner","home_agent_owner","serializable",[REVISION]):
        raise ValueError("exact migrated owner transaction required")
    migration=_migration()
    expected=((FUNCTION,READER,True,migration.BODY.split("$kernel$")[1]),
        (TRIGGER_FUNCTION,"home_agent_owner",False,migration.LINEAGE_GUARD.split("$lineage$")[1]))
    for signature,owner,definer,body in expected:
        row=connection.execute(text("""SELECT r.rolname,p.prosecdef,p.prosrc,p.proconfig,
          EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee<>p.proowner AND (a.grantee<>(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='home_agent_api')
              OR a.privilege_type<>'EXECUTE' OR a.is_grantable)) AS unexpected_acl
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
          WHERE p.oid=pg_catalog.to_regprocedure(:signature)"""),{"signature":signature}).one_or_none()
        if row is None or tuple(row)!=(owner,definer,body,["search_path=pg_catalog","row_security=on"],False):
            raise ValueError("preference permission function drift")
    guarded=connection.execute(text("""SELECT count(*) FROM pg_catalog.pg_trigger
      WHERE tgrelid='ingest.artifact_links'::regclass AND tgname='personal_preference_lineage_guard'
        AND tgfoid='ingest.guard_personal_preference_lineage_v1()'::regprocedure
        AND tgtype=7 AND tgenabled='O' AND NOT tgisinternal AND tgnargs=0 AND tgqual IS NULL""")).scalar_one()
    if guarded!=1:
        raise ValueError("preference lineage guard unavailable")
    broad=connection.execute(text("""SELECT
      has_table_privilege('home_agent_api','ingest.artifact_links','INSERT,UPDATE,DELETE,TRUNCATE'),
      EXISTS(SELECT 1 FROM information_schema.column_privileges
        WHERE grantee='home_agent_api' AND table_schema='ingest' AND table_name='artifact_links'
          AND privilege_type IN ('INSERT','UPDATE')
          AND (privilege_type<>'INSERT' OR column_name NOT IN ('link_id','parent_artifact_id','child_artifact_id','relation')))""")).one()
    if any(broad):
        raise ValueError("unexpected existing lineage write privileges")
    # The owner is allowed to SET this non-login role but does not inherit it.
    connection.execute(text(f"SET LOCAL ROLE {READER}"))
    connection.execute(text(f"{'GRANT' if enabled else 'REVOKE'} EXECUTE ON FUNCTION {FUNCTION} "
        f"{'TO' if enabled else 'FROM'} home_agent_api"))
    connection.execute(text("RESET ROLE"))
    connection.execute(text(f"{'GRANT' if enabled else 'REVOKE'} INSERT ({','.join(COLUMNS)}) "
        f"ON ingest.artifact_links {'TO' if enabled else 'FROM'} home_agent_api"))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=("activate","deactivate"))
    parser.add_argument("--database-url-file",required=True)
    args=parser.parse_args()
    path=Path(args.database_url_file)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>4096:
        raise ValueError("mounted owner database credential required")
    url=make_url(path.read_text(encoding="utf-8").strip())
    if url.username!="home_agent_owner" or url.drivername not in ("postgresql","postgresql+psycopg") or url.query:
        raise ValueError("fixed owner database URL required")
    engine=create_engine(url.set(drivername="postgresql+psycopg"),isolation_level="SERIALIZABLE",
        pool_size=1,max_overflow=0,hide_parameters=True,
        connect_args={"connect_timeout":5,"options":"-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.begin() as connection:
            apply_permissions(connection,enabled=args.mode=="activate")
    finally:
        engine.dispose()
    print("Preference API permissions "+("active" if args.mode=="activate" else "inactive"))


if __name__=="__main__":
    try: main()
    except Exception:
        print("Preference permission operation failed",file=sys.stderr)
        raise SystemExit(78)
