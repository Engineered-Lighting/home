"""Offline lookup containment checks, not PostgreSQL runtime acceptance."""
import importlib.util
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / 'alembic/versions'
PATH = DIRECTORY / '0044_shared_link_confirmation_lookup.py'


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem,path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commands(monkeypatch,operation):
    module=load(PATH); sql=[]
    monkeypatch.setattr(module.op,'execute',sql.append)
    getattr(module,operation)()
    return module,sql


def test_five_frozen_wrappers_match_combined_revision_exactly():
    m=load(PATH); prior=load(DIRECTORY/'0043_shared_link_combined_revision.py')
    assert len(m.WRAPPERS)==5
    for actual,old in zip(m.WRAPPERS,prior.WRAPPERS):
        assert actual=={**old,'original_revision':prior.revision,'original_sql':prior.aligned_sql(old)}
        assert m.aligned_sql(actual).replace("a.version_num='"+m.revision+"'","a.version_num='"+prior.revision+"'")==actual['original_sql']
    for forbidden in ('runpy','importlib','read_text(','from app','__import__'):
        assert forbidden not in PATH.read_text()


def test_lookup_signature_uses_identical_authority_boundary():
    m=load(PATH)
    assert m.revision=='0044_shared_link_reconcile_v1'
    assert m.down_revision=='0043_shared_link_combined_v1'
    assert m.LOOKUP_FUNCTION=='identity.inspect_shared_link_confirmation_v1'
    assert m.LOOKUP_SIGNATURE=='uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid'
    confirm=load(DIRECTORY/'0042_shared_link_confirmation_kernel.py')
    original_guard=confirm.BODY.split('BEGIN\n',1)[1].split(' RETURN QUERY',1)[0]
    assert original_guard.replace(confirm.revision,m.revision) in m.LOOKUP_BODY
    assert 'RETURNS TABLE(link_id uuid,authorization_generation bigint,revision bigint,confirmed_at timestamptz)' in m.LOOKUP_BODY


def test_lookup_fences_and_locks_before_consumed_only_delegation():
    body=load(PATH).LOOKUP_BODY
    assert body.index("require_shared_link_session_active_v1('home-assistant:echo',p_session)") < body.index('SELECT ceremony.*')
    assert 'WHERE ceremony.ceremony_id=p_ceremony FOR UPDATE;' in body
    assert "IF parent.state='pending' THEN RETURN; END IF;" in body
    assert "IF parent.state<>'consumed' THEN" in body
    assert body.index('FOR UPDATE;') < body.index("IF parent.state<>'consumed'") < body.index('RETURN QUERY')
    assert 'parent.initiating_session_commitment IS DISTINCT FROM p_session' in body
    assert 'p_echo_subject,parent.principal_id,parent.person_id,parent.legacy_binding_id' in body
    assert 'FROM identity.confirm_shared_link_v1(' in body
    assert 'p_ceremony,p_echo_subject,p_session,p_expected_revision,p_confirmation,p_digest,' in body
    assert 'p_proposal,p_receipt,p_link,p_echo_binding,p_victoria_binding' in body
    for forbidden in ('INSERT INTO','UPDATE identity.','DELETE FROM','begin_shared_link_v1'):
        assert forbidden not in body


def test_missing_lookup_is_unknown_without_confirmation_call():
    body=load(PATH).LOOKUP_BODY
    missing=body.split('IF NOT FOUND THEN',1)[1].split('END IF;',1)[0]
    assert 'RETURN;' in missing and 'confirm_shared_link_v1' not in missing
    assert 'Absence is unknown' in missing


def test_drift_and_collision_checks_precede_every_mutation(monkeypatch):
    m,sql=commands(monkeypatch,'upgrade')
    firstgrant=next(i for i,s in enumerate(sql) if s.startswith('GRANT'))
    assert firstgrant==7
    assert all('shared_link_combined_wrapper_drift' in s for s in sql[1:6])
    assert 'shared_link_confirmation_lookup_collision' in sql[6]
    for s in sql[1:6]:
        for fragment in ('p.prosrc=$expected_body$', 'p.prosecdef', "l.lanname='plpgsql'", 'cardinality(p.proconfig)=2',
                         'a.grantee<>p.proowner','has_schema_privilege(',"'identity','CREATE'"):
            assert fragment in s


def test_new_lookup_owned_by_kernel_acl_closed_and_no_caller_grants(monkeypatch):
    m,sql=commands(monkeypatch,'upgrade')
    index=sql.index(m.LOOKUP_BODY)
    assert sql[index-2:index]==[f'GRANT CREATE ON SCHEMA identity TO {m.KERNEL_ROLE};',f'SET LOCAL ROLE {m.KERNEL_ROLE};']
    assert sql[index+1]==f'REVOKE ALL ON FUNCTION {m.LOOKUP_FUNCTION}({m.LOOKUP_SIGNATURE}) FROM PUBLIC;'
    assert 'aclexplode(p.proacl)' in sql[index+2] and 'a.grantee<>p.proowner' in sql[index+2]
    assert sql[index+3:index+5]==['RESET ROLE;',f'REVOKE CREATE ON SCHEMA identity FROM {m.KERNEL_ROLE};']
    assert not any('GRANT EXECUTE' in s or 'ALTER ROLE' in s or 'CREATE ROLE' in s for s in sql)
    assert m.body_source(m.LOOKUP_BODY) in sql[-1]


def test_downgrade_verifies_all_six_then_drops_lookup_and_restores_0043(monkeypatch):
    m,sql=commands(monkeypatch,'downgrade')
    assert f"version_num='{m.revision}'" in sql[0]
    assert all('shared_link_combined_wrapper_drift' in s for s in sql[1:7])
    assert sql[7:10]==[f'SET LOCAL ROLE {m.KERNEL_ROLE};',f'DROP FUNCTION {m.LOOKUP_FUNCTION}({m.LOOKUP_SIGNATURE});','RESET ROLE;']
    for i,w in enumerate(m.WRAPPERS):
        assert sql[12+i*5]==w['original_sql'].replace('CREATE FUNCTION ','CREATE OR REPLACE FUNCTION ',1)
        assert m.body_source(w['original_sql']) in sql[-5+i]
    assert not any('DROP TABLE' in s or 'CASCADE' in s or 'ALTER ROLE' in s for s in sql)
