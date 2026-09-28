"""Offline lookup containment checks, not PostgreSQL runtime acceptance."""
import importlib.util
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / 'alembic/versions'
PATH = DIRECTORY / '0045_shared_link_proof_lookup.py'


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


def test_six_frozen_wrappers_match_previous_revision_exactly():
    m=load(PATH); prior=load(DIRECTORY/'0044_shared_link_confirmation_lookup.py')
    assert len(m.WRAPPERS)==6
    for actual,old in zip(m.WRAPPERS,prior.WRAPPERS+(prior.LOOKUP,)):
        assert actual=={**old,'original_revision':prior.revision,'original_sql':prior.aligned_sql(old)}
        assert m.aligned_sql(actual).replace("a.version_num='"+m.revision+"'","a.version_num='"+prior.revision+"'")==actual['original_sql']
    for forbidden in ('runpy','importlib','read_text(','from app','__import__'):
        assert forbidden not in PATH.read_text()


def test_lookup_has_same_issuer_derived_transaction_boundary():
    m=load(PATH); prior=load(DIRECTORY/'0041_shared_link_proof_kernel.py')
    assert m.revision=='0045_shared_link_proof_lookup_v1'
    assert m.down_revision=='0044_shared_link_reconcile_v1'
    assert len(m.revision)<=32
    assert m.LOOKUP_FUNCTION=='identity.inspect_shared_link_auth_proof_v1'
    assert m.LOOKUP_SIGNATURE==prior.SIGNATURE
    guard=prior.BODY.split('BEGIN\n',1)[1].split(' -- Includes',1)[0]
    assert guard.replace(prior.revision,m.revision) in m.LOOKUP_BODY
    assert 'p_issuer text' not in m.LOOKUP_BODY


def test_lookup_fences_and_locks_without_write_capable_helper():
    body=load(PATH).LOOKUP_BODY
    assert body.index('require_shared_link_session_active_v1(v_issuer,p_session)') < body.index('SELECT child.*')
    assert 'FOR UPDATE OF generation,parent,child;' in body
    assert "AND issuer.state='active' FOR SHARE;" in body
    assert "parent.state='pending' AND parent.purpose='link_echo_victoria'" in body
    assert 'child.authorization_generation=parent.authorization_generation' in body
    assert "generation.authorization_generation=parent.authorization_generation AND generation.state='active'" in body
    assert "require_shared_link_session_active_v1('home-assistant:echo',context.parent_session)" in body
    assert 'require_shared_link_session_active_v1(sibling.issuer_id,sibling.session_commitment)' in body
    assert 'require_shared_link_owner_v1(echo_subject,context.principal_id,context.person_id,context.legacy_binding_id)' in body
    assert "v_issuer='home-assistant:echo' AND p_subject IS DISTINCT FROM echo_subject" in body
    for forbidden in ('INSERT INTO','UPDATE identity.','DELETE FROM','associate_shared_auth_proof_v1','issue_shared_link_auth_proof_v1','FOR UPDATE OF proof'):
        assert forbidden not in body


def test_proof_lookup_checks_full_replay_and_freshness_after_authority_locks():
    body=load(PATH).LOOKUP_BODY
    for column,argument in [('proof_id','p_proof_id'),('issuer_id','v_issuer'),('subject','p_subject'),
        ('session_commitment','p_session'),('challenge_commitment','p_challenge'),
        ('authenticated_at','p_authenticated_at'),('registration_revision','p_registration_revision')]:
        assert f'receipt.{column} IS DISTINCT FROM {argument}' in body
    for check in ('receipt.consumed_at IS NOT NULL','receipt.expires_at IS DISTINCT FROM expiry',
        'context.consumed_proof_id IS DISTINCT FROM p_proof_id','context.consumed_subject IS DISTINCT FROM p_subject',
        'context.consumed_at IS DISTINCT FROM receipt.issued_at','receipt.issued_at<p_authenticated_at',
        'receipt.issued_at>checked_at','p_authenticated_at<context.created_at',
        'p_authenticated_at<context.parent_created_at','p_authenticated_at>checked_at OR expiry<=checked_at'):
        assert check in body
    assert "LEAST(p_authenticated_at + interval '5 minutes',context.expires_at,context.parent_expires_at)" in body
    assert body.index('FOR UPDATE OF') < body.index('FOR SHARE;') < body.index('require_shared_link_owner_v1(') < body.index('checked_at :=')
    assert 'IF NOT FOUND THEN RETURN; END IF;' in body
    assert 'Absence is unknown' in body


def test_drift_and_collision_checks_precede_every_mutation(monkeypatch):
    m,sql=commands(monkeypatch,'upgrade')
    firstgrant=next(i for i,s in enumerate(sql) if s.startswith('GRANT'))
    assert firstgrant==8
    assert all('shared_link_combined_wrapper_drift' in s for s in sql[1:7])
    assert 'shared_link_proof_lookup_collision' in sql[7]
    for s in sql[1:7]:
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


def test_downgrade_verifies_all_seven_then_restores_0044(monkeypatch):
    m,sql=commands(monkeypatch,'downgrade')
    assert f"version_num='{m.revision}'" in sql[0]
    assert all('shared_link_combined_wrapper_drift' in s for s in sql[1:8])
    assert sql[8:11]==[f'SET LOCAL ROLE {m.KERNEL_ROLE};',f'DROP FUNCTION {m.LOOKUP_FUNCTION}({m.LOOKUP_SIGNATURE});','RESET ROLE;']
    for i,w in enumerate(m.WRAPPERS):
        assert sql[13+i*5]==w['original_sql'].replace('CREATE FUNCTION ','CREATE OR REPLACE FUNCTION ',1)
        assert m.body_source(w['original_sql']) in sql[-6+i]
    assert not any('DROP TABLE' in s or 'CASCADE' in s or 'ALTER ROLE' in s for s in sql)
