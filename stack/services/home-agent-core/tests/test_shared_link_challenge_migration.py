"""Frozen migration contract only; hosted PostgreSQL acceptance remains required."""
import importlib.util
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.shared_link_challenge_schema import SHARED_LINK_CHALLENGE_TABLES

PATH = Path(__file__).resolve().parents[1] / 'alembic/versions/0034_shared_link_challenges.py'


def migration():
    spec = importlib.util.spec_from_file_location('shared_link_challenge_migration', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(monkeypatch, operation):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, 'execute', statements.append)
    getattr(module, operation)()
    return module, statements


def test_frozen_ddl_matches_isolated_storage_without_runtime_import():
    module = migration()
    assert module.revision == '0034_shared_link_challenges_v1'
    assert module.down_revision == '0033_shared_auth_proof_v1'
    assert 'from app' not in PATH.read_text(encoding='utf-8')
    assert module.CREATE_STATEMENTS == tuple(str(CreateTable(t).compile(dialect=postgresql.dialect())).strip()
                                             for t in SHARED_LINK_CHALLENGE_TABLES)


def test_full_scope_proof_key_precedes_child_creation_and_does_not_rewrite_proofs(monkeypatch):
    module, statements = commands(monkeypatch, 'upgrade')
    assert statements[1] == ('ALTER TABLE identity.shared_auth_proofs ADD CONSTRAINT '
        'shared_proof_full_challenge_scope UNIQUE (proof_id, issuer_id, subject, challenge_commitment, '
        'session_commitment, registration_revision);')
    assert statements.index(module.CREATE_STATEMENTS[-1]) > 1
    assert not any(s.lstrip().startswith(('UPDATE ', 'DELETE ', 'TRUNCATE ', 'INSERT ')) for s in statements)


def test_upgrade_closes_every_new_table_without_runtime_grants(monkeypatch):
    module, statements = commands(monkeypatch, 'upgrade')
    for table in module.TABLE_NAMES:
        assert f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;' in statements
        assert f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY;' in statements
        assert f'REVOKE ALL ON TABLE {table} FROM PUBLIC;' in statements
    joined = '\n'.join(statements)
    assert joined.count('aclexplode(relation.relacl)') == 3
    assert 'acl.grantee <> relation.relowner' in joined
    assert 'GRANT ' not in joined and 'CREATE POLICY' not in joined


def test_both_directions_require_exact_owner_session_and_revision(monkeypatch):
    for direction in ('upgrade', 'downgrade'):
        module, statements = commands(monkeypatch, direction)
        guard = statements[0]
        assert "current_user <> 'home_agent_owner'" in guard
        assert "session_user <> 'home_agent_owner'" in guard
        assert '(SELECT count(*) FROM public.alembic_version) <> 1' in guard
        assert (module.down_revision if direction == 'upgrade' else module.revision) in guard


def test_downgrade_refuses_filtered_or_populated_history_before_dropping(monkeypatch):
    module, statements = commands(monkeypatch, 'downgrade')
    assert statements[1] == 'SET LOCAL row_security = off;'
    assert statements[2].startswith('LOCK TABLE ')
    assert 'identity.shared_auth_proofs IN ACCESS EXCLUSIVE MODE' in statements[2]
    for table in module.TABLE_NAMES:
        assert f'EXISTS (SELECT 1 FROM {table})' in statements[3]
    assert "ERRCODE = '55000'" in statements[3]
    assert statements[4:-1] == [f'DROP TABLE {table};' for table in reversed(module.TABLE_NAMES)]
    assert statements[-1].endswith('DROP CONSTRAINT shared_proof_full_challenge_scope;')
    assert not any('CASCADE' in s or 'DELETE FROM' in s for s in statements)
