"""Offline frozen-body/drift contracts; hosted PostgreSQL acceptance required."""
import importlib.util
import ast
from pathlib import Path
from alembic.runtime.migration import MigrationContext

DIRECTORY = Path(__file__).resolve().parents[1] / 'alembic/versions'
PATH = DIRECTORY / '0043_shared_link_combined_revision.py'


def test_shared_link_combined_revision_ids_fit_actual_alembic_version_column():
    limit = MigrationContext.configure(dialect_name="postgresql")._version.c.version_num.type.length
    assert limit == 32
    for path in DIRECTORY.glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "revision" for t in node.targets):
                revision_id = ast.literal_eval(node.value)
                assert isinstance(revision_id, str) and len(revision_id) <= limit, (path.name, revision_id, limit)


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def commands(monkeypatch, operation):
    m = load(PATH)
    sql = []
    monkeypatch.setattr(m.op, 'execute', sql.append)
    getattr(m, operation)()
    return m, sql


def test_frozen_bodies_exactly_match_reviewed_predecessors():
    m = load(PATH)
    issue = load(DIRECTORY / '0040_shared_link_issuance_kernel.py')
    proof = load(DIRECTORY / '0041_shared_link_proof_kernel.py')
    confirm = load(DIRECTORY / '0042_shared_link_confirmation_kernel.py')
    expected = [
        (issue,issue.FUNCTION,issue.SIGNATURE,issue.BODY),
        (issue,issue.LOOKUP_FUNCTION,issue.LOOKUP_SIGNATURE,issue.LOOKUP_BODY),
        (issue,issue.RECONCILE_FUNCTION,issue.RECONCILE_SIGNATURE,issue.RECONCILE_BODY),
        (proof,proof.FUNCTION,proof.SIGNATURE,proof.BODY),
        (confirm,confirm.FUNCTION,confirm.SIGNATURE,confirm.BODY),
    ]
    assert len(m.WRAPPERS) == len(expected) == 5
    for frozen, (original, function, signature, body) in zip(m.WRAPPERS, expected):
        assert frozen == dict(function=function,signature=signature,owner=original.KERNEL_ROLE,
                              original_revision=original.revision,original_sql=body)
    source = PATH.read_text()
    for forbidden in ('importlib', 'runpy', 'read_text(', 'from app', '__import__'):
        assert forbidden not in source


def test_only_exact_revision_literal_changes():
    m = load(PATH)
    assert m.revision == '0043_shared_link_combined_v1'
    assert m.down_revision == '0042_shared_link_confirm_krnl_v1'
    for wrapper in m.WRAPPERS:
        updated = m.aligned_sql(wrapper)
        assert updated.replace("a.version_num='"+m.revision+"'", "a.version_num='"+wrapper['original_revision']+"'") == wrapper['original_sql']
        assert updated.count("a.version_num='"+m.revision+"'") == 1
        assert m.body_source(updated).startswith('\n')
        assert 'SECURITY DEFINER' in updated


def test_complete_drift_check_precedes_temporary_grants(monkeypatch):
    m, sql = commands(monkeypatch, 'upgrade')
    assert 'shared_link_combined_migration_authority_invalid' in sql[0]
    first_grant = next(i for i,s in enumerate(sql) if s.startswith('GRANT '))
    assert first_grant == 6
    for statement in sql[1:6]:
        for fragment in ('to_regprocedure(', 'p.prosrc=$expected_body$', 'p.prosecdef', "p.prokind='f'",
                         'p.proretset AND NOT p.proisstrict', "p.provolatile='v'", "p.proparallel='u'",
                         'NOT p.proleakproof', 'p.probin IS NULL', "l.lanname='plpgsql'",
                         "ARRAY['search_path=pg_catalog','row_security=on']", 'cardinality(p.proconfig)=2',
                         "acldefault('f',p.proowner)", 'a.grantee<>p.proowner', 'has_schema_privilege(',
                         "'identity','CREATE'", 'shared_link_combined_wrapper_drift'):
            assert fragment in statement
    assert sum('shared_link_combined_wrapper_drift' in s for s in sql) == 10


def test_replacement_uses_existing_owner_and_preserves_acl(monkeypatch):
    m, sql = commands(monkeypatch, 'upgrade')
    for index, wrapper in enumerate(m.WRAPPERS):
        offset = 6 + 5*index
        owner = wrapper['owner']
        assert sql[offset:offset+5] == [
            f'GRANT CREATE ON SCHEMA identity TO {owner};',
            f'SET LOCAL ROLE {owner};',
            m.aligned_sql(wrapper).replace('CREATE FUNCTION ', 'CREATE OR REPLACE FUNCTION ', 1),
            'RESET ROLE;', f'REVOKE CREATE ON SCHEMA identity FROM {owner};',
        ]
    assert not any('GRANT EXECUTE' in s or 'ALTER ROLE' in s or 'CREATE ROLE' in s or 'ALTER FUNCTION' in s for s in sql)
    assert not any(s.startswith('DROP ') for s in sql)


def test_downgrade_restores_each_original_boundary_and_preserves_records(monkeypatch):
    m, sql = commands(monkeypatch, 'downgrade')
    assert f"version_num='{m.revision}'" in sql[0]
    for index, wrapper in enumerate(m.WRAPPERS):
        assert m.body_source(m.aligned_sql(wrapper)) in sql[1+index]
        assert sql[8+5*index] == wrapper['original_sql'].replace('CREATE FUNCTION ', 'CREATE OR REPLACE FUNCTION ', 1)
        assert m.body_source(wrapper['original_sql']) in sql[-5+index]
    for statement in sql:
        if statement.lstrip().startswith('CREATE OR REPLACE FUNCTION'):
            continue  # Existing frozen function bodies are not executed here.
        for forbidden in ('DROP TABLE', 'DELETE FROM', 'TRUNCATE', 'CASCADE', 'GRANT EXECUTE', 'ALTER ROLE'):
            assert forbidden not in statement


def test_invalid_freeze_cannot_silently_skip_revision_guard():
    import pytest
    m = load(PATH)
    with pytest.raises(ValueError, match='exactly once'):
        m.aligned_sql(dict(original_revision='missing',original_sql=m.WRAPPERS[0]['original_sql']))
