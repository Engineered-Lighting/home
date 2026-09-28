"""Register the two fixed preference sources during reviewed owner maintenance.

This creates no person link, grant, login, credential, or runtime listener.
Existing registrations must match exactly; revocation is never undone.
"""
import argparse
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REVISION = "0047_personal_pref_authority_v1"
SOURCE = "core.personal-preferences.v1"


def register_sources(connection):
    state = connection.execute(text("SELECT current_user,session_user,current_setting('transaction_isolation'),"
        "(SELECT array_agg(version_num::text) FROM public.alembic_version)")).one()
    if tuple(state) != ("home_agent_owner", "home_agent_owner", "serializable", [REVISION]):
        raise ValueError("exact migrated owner transaction required")
    for table in ("shared_issuers", "shared_sources"):
        safe = connection.execute(text("""SELECT c.relrowsecurity AND c.relforcerowsecurity
          AND r.rolname='home_agent_owner' FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
          WHERE c.oid=to_regclass(:table)"""), {"table": "identity."+table}).scalar_one_or_none()
        if safe is not True:
            raise ValueError("registration ownership or row security differs")
    connection.execute(text("LOCK TABLE identity.shared_issuers,identity.shared_sources IN SHARE ROW EXCLUSIVE MODE"))
    missing_issuers, missing_sources = [], []
    for site in ("echo", "victoria"):
        issuer = "home-assistant:"+site
        params = {"site": site, "issuer": issuer, "source": SOURCE}
        rows = connection.execute(text("SELECT issuer_id,site_id,registration_revision,state FROM identity.shared_issuers "
            "WHERE issuer_id=:issuer OR site_id=:site"), params).all()
        if not rows:
            missing_issuers.append(params)
        elif [tuple(row) for row in rows] != [(issuer, site, 1, "active")]:
            raise ValueError("existing issuer registration requires review")
        for capability in ("memory.read", "personal_memory.write"):
            source_params = {**params, "capability": capability}
            row = connection.execute(text("SELECT issuer_id,registration_revision,state FROM identity.shared_sources "
                "WHERE site_id=:site AND source_id=:source AND capability=:capability"), source_params).one_or_none()
            if row is None:
                missing_sources.append(source_params)
            elif tuple(row) != (issuer, 1, "active"):
                raise ValueError("existing source registration requires review")
    # Validate every existing row before any insertion; the caller still owns
    # the atomic transaction and must roll back on any failure.
    for params in missing_issuers:
        connection.execute(text("INSERT INTO identity.shared_issuers (issuer_id,site_id,registration_revision,state) "
            "VALUES (:issuer,:site,1,'active')"), params)
    for params in missing_sources:
        connection.execute(text("INSERT INTO identity.shared_sources "
            "(site_id,source_id,capability,issuer_id,registration_revision,state) "
            "VALUES (:site,:source,:capability,:issuer,1,'active')"), params)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-file", required=True)
    args = parser.parse_args()
    path = Path(args.database_url_file)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("mounted owner credential required")
    url = make_url(path.read_text(encoding="utf-8").strip())
    if url.username != "home_agent_owner" or url.drivername not in ("postgresql", "postgresql+psycopg") or url.query:
        raise ValueError("fixed owner database URL required")
    engine = create_engine(url.set(drivername="postgresql+psycopg"), isolation_level="SERIALIZABLE",
        pool_size=1, max_overflow=0, hide_parameters=True,
        connect_args={"connect_timeout":5,"options":"-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.begin() as connection:
            register_sources(connection)
    finally:
        engine.dispose()
    print("Fixed preference source registrations verified; no personal grants created")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Preference source registration failed; inspect through owner maintenance", file=sys.stderr)
        raise SystemExit(78) from None
