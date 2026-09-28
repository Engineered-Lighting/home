"""Dormant issuer-bound proof outcome lookup; absence never permits replay.

Six predecessor wrappers are frozen and aligned without changing their logic.
No runtime caller grants or table privileges are added.
"""
from alembic import op

revision = "0045_shared_link_proof_lookup_v1"
down_revision = "0044_shared_link_reconcile_v1"
branch_labels = None
depends_on = None
LOOKUP_FUNCTION = "identity.inspect_shared_link_auth_proof_v1"
LOOKUP_SIGNATURE = "uuid,text,text,text,timestamptz,bigint"
KERNEL_ROLE = "home_agent_shared_link_proof_kernel"

WRAPPERS = ({'function': 'identity.issue_shared_link_ceremony_v1',
  'signature': 'uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text',
  'owner': 'home_agent_shared_link_issue_kernel',
  'original_revision': '0044_shared_link_reconcile_v1',
  'original_sql': '\n'
                  'CREATE FUNCTION identity.issue_shared_link_ceremony_v1(\n'
                  ' p_ceremony uuid,p_principal uuid,p_person uuid,p_binding uuid,p_echo_subject text,\n'
                  ' p_owner text,p_request text,p_echo_session text,p_victoria_session text,\n'
                  ' p_echo_child uuid,p_victoria_child uuid,p_echo_challenge text,p_victoria_challenge '
                  'text,\n'
                  ' p_key_id text,p_key_fingerprint text\n'
                  ') RETURNS TABLE(ceremony_id uuid,authorization_generation bigint,revision bigint,\n'
                  ' created_at timestamptz,expires_at timestamptz,echo_registration_revision '
                  'bigint,victoria_registration_revision bigint)\n'
                  'LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on\n'
                  'AS $kernel$\n'
                  'BEGIN\n'
                  " IF session_user<>'home_agent_shared_link_coordinator' OR "
                  "current_user<>'home_agent_shared_link_issue_kernel' OR\n"
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT '
                  'r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid=m.member WHERE r.rolname=current_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m\n'
                  '      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid\n'
                  '      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member\n'
                  '      WHERE target.rolname=current_user AND\n'
                  "        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR "
                  'NOT m.set_option)) THEN\n'
                  "   RAISE EXCEPTION 'shared_link_issuance_role_invalid' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  " IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR\n"
                  "    pg_catalog.current_setting('transaction_read_only')<>'off' OR "
                  'pg_catalog.pg_is_in_recovery() OR\n'
                  '    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN\n'
                  "   RAISE EXCEPTION 'shared_link_issuance_transaction_invalid' USING ERRCODE='25001';\n"
                  ' END IF;\n'
                  ' IF (SELECT count(*) FROM public.alembic_version)<>1 OR\n'
                  '    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE '
                  "a.version_num='0044_shared_link_reconcile_v1') THEN\n"
                  "   RAISE EXCEPTION 'shared_link_issuance_revision_invalid' USING ERRCODE='55000';\n"
                  ' END IF;\n'
                  ' RETURN QUERY SELECT issued.* FROM identity.begin_shared_link_v1(\n'
                  '   '
                  'p_ceremony,p_principal,p_person,p_binding,p_echo_subject,p_owner,p_request,p_echo_session,p_victoria_session,\n'
                  '   '
                  'p_echo_child,p_victoria_child,p_echo_challenge,p_victoria_challenge,p_key_id,p_key_fingerprint) '
                  'issued;\n'
                  'END\n'
                  '$kernel$;\n'},
 {'function': 'identity.resolve_shared_link_owner_v1',
  'signature': 'text',
  'owner': 'home_agent_shared_link_issue_kernel',
  'original_revision': '0044_shared_link_reconcile_v1',
  'original_sql': '\n'
                  'CREATE FUNCTION identity.resolve_shared_link_owner_v1(p_echo_subject text)\n'
                  'RETURNS TABLE(principal_id uuid,person_id uuid,legacy_binding_id uuid)\n'
                  'LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on\n'
                  'AS $lookup$\n'
                  'DECLARE anchor record;\n'
                  'BEGIN\n'
                  " IF session_user<>'home_agent_shared_link_coordinator' OR "
                  "current_user<>'home_agent_shared_link_issue_kernel' OR\n"
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT '
                  'r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid=m.member WHERE r.rolname=current_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m\n'
                  '      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid\n'
                  '      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member\n'
                  '      WHERE target.rolname=current_user AND\n'
                  "        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR "
                  'NOT m.set_option)) THEN\n'
                  "   RAISE EXCEPTION 'shared_link_issuance_role_invalid' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  " IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR\n"
                  "    pg_catalog.current_setting('transaction_read_only')<>'off' OR "
                  'pg_catalog.pg_is_in_recovery() OR\n'
                  '    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN\n'
                  "   RAISE EXCEPTION 'shared_link_issuance_transaction_invalid' USING ERRCODE='25001';\n"
                  ' END IF;\n'
                  ' IF (SELECT count(*) FROM public.alembic_version)<>1 OR\n'
                  '    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE '
                  "a.version_num='0044_shared_link_reconcile_v1') THEN\n"
                  "   RAISE EXCEPTION 'shared_link_issuance_revision_invalid' USING ERRCODE='55000';\n"
                  ' END IF;\n'
                  '\n'
                  ' IF p_echo_subject IS NULL OR pg_catalog.char_length(p_echo_subject) NOT BETWEEN 1 AND 64 '
                  'OR\n'
                  "    p_echo_subject<>pg_catalog.btrim(p_echo_subject) OR p_echo_subject ~ '[[:cntrl:]]' "
                  'OR\n'
                  "    p_echo_subject ~ '^[[:space:]]|[[:space:]]$' THEN\n"
                  "   RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  ' PERFORM privacy.lock_identity_semantic_write_fence();\n'
                  ' BEGIN\n'
                  '   SELECT b.principal_id,b.person_id,b.binding_id INTO STRICT anchor\n'
                  '     FROM identity.ha_user_bindings b\n'
                  '     JOIN identity.principals p ON p.principal_id=b.principal_id AND '
                  'p.person_id=b.person_id\n'
                  '     JOIN identity.people person ON person.person_id=b.person_id\n'
                  '    WHERE b.ha_user_id=p_echo_subject AND b.revoked_at IS NULL\n'
                  "      AND p.kind='ha_user' AND p.status='active' AND person.status='active';\n"
                  ' EXCEPTION WHEN NO_DATA_FOUND OR TOO_MANY_ROWS THEN\n'
                  "   RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE='42501';\n"
                  ' END;\n'
                  ' PERFORM identity.require_shared_link_owner_v1(\n'
                  '   p_echo_subject,anchor.principal_id,anchor.person_id,anchor.binding_id);\n'
                  ' RETURN QUERY SELECT anchor.principal_id,anchor.person_id,anchor.binding_id;\n'
                  'END\n'
                  '$lookup$;\n'},
 {'function': 'identity.inspect_shared_link_issuance_v1',
  'signature': 'uuid,uuid,uuid,uuid,text,text,text,text,text,uuid,uuid,text,text,text,text',
  'owner': 'home_agent_shared_link_issue_kernel',
  'original_revision': '0044_shared_link_reconcile_v1',
  'original_sql': '\n'
                  'CREATE FUNCTION identity.inspect_shared_link_issuance_v1(\n'
                  ' p_ceremony uuid,p_principal uuid,p_person uuid,p_binding uuid,p_echo_subject text,\n'
                  ' p_owner text,p_request text,p_echo_session text,p_victoria_session text,\n'
                  ' p_echo_child uuid,p_victoria_child uuid,p_echo_challenge text,p_victoria_challenge '
                  'text,\n'
                  ' p_key_id text,p_key_fingerprint text\n'
                  ') RETURNS TABLE(ceremony_id uuid,authorization_generation bigint,revision bigint,\n'
                  ' created_at timestamptz,expires_at timestamptz,echo_registration_revision '
                  'bigint,victoria_registration_revision bigint)\n'
                  'LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on\n'
                  'AS $inspect$\n'
                  'DECLARE prior record; generation record; echo_revision bigint; victoria_revision bigint;\n'
                  'BEGIN\n'
                  " IF session_user<>'home_agent_shared_link_coordinator' OR "
                  "current_user<>'home_agent_shared_link_issue_kernel' OR\n"
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT '
                  'r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid=m.member WHERE r.rolname=current_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m\n'
                  '      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid\n'
                  '      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member\n'
                  '      WHERE target.rolname=current_user AND\n'
                  "        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR "
                  'NOT m.set_option)) THEN\n'
                  "   RAISE EXCEPTION 'shared_link_issuance_role_invalid' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  " IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR\n"
                  "    pg_catalog.current_setting('transaction_read_only')<>'off' OR "
                  'pg_catalog.pg_is_in_recovery() OR\n'
                  '    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN\n'
                  "   RAISE EXCEPTION 'shared_link_issuance_transaction_invalid' USING ERRCODE='25001';\n"
                  ' END IF;\n'
                  ' IF (SELECT count(*) FROM public.alembic_version)<>1 OR\n'
                  '    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE '
                  "a.version_num='0044_shared_link_reconcile_v1') THEN\n"
                  "   RAISE EXCEPTION 'shared_link_issuance_revision_invalid' USING ERRCODE='55000';\n"
                  ' END IF;\n'
                  '\n'
                  ' IF p_ceremony IS NULL OR p_principal IS NULL OR p_person IS NULL OR p_binding IS NULL '
                  'OR\n'
                  '    p_echo_child IS NULL OR p_victoria_child IS NULL OR p_echo_child=p_victoria_child OR\n'
                  "    p_owner IS NULL OR p_owner !~ '^[0-9a-f]{64}$' OR\n"
                  "    p_request IS NULL OR p_request !~ '^[0-9a-f]{64}$' OR\n"
                  "    p_echo_challenge IS NULL OR p_echo_challenge !~ '^[0-9a-f]{64}$' OR\n"
                  "    p_victoria_challenge IS NULL OR p_victoria_challenge !~ '^[0-9a-f]{64}$' OR\n"
                  '    p_echo_challenge=p_victoria_challenge OR p_key_id IS NULL OR\n'
                  "    p_key_fingerprint IS NULL OR p_key_fingerprint !~ '^[0-9a-f]{64}$' THEN\n"
                  "   RAISE EXCEPTION 'shared_link_issuance_invalid' USING ERRCODE='22023';\n"
                  ' END IF;\n'
                  ' PERFORM '
                  "identity.require_shared_link_session_active_v1('home-assistant:echo',p_echo_session);\n"
                  ' PERFORM '
                  "identity.require_shared_link_session_active_v1('home-assistant:victoria',p_victoria_session);\n"
                  ' PERFORM '
                  'identity.require_shared_link_owner_v1(p_echo_subject,p_principal,p_person,p_binding);\n'
                  ' PERFORM 1 FROM privacy.shared_link_key_admission admitted\n'
                  "  WHERE admitted.scope='shared-link-v1' AND admitted.state='active'\n"
                  '    AND admitted.key_id=p_key_id AND admitted.key_fingerprint=p_key_fingerprint FOR '
                  'SHARE;\n'
                  " IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_key_not_admitted' USING ERRCODE='42501'; "
                  'END IF;\n'
                  ' SELECT issuer.registration_revision INTO echo_revision FROM identity.shared_issuers '
                  'issuer\n'
                  "  WHERE issuer.issuer_id='home-assistant:echo' AND issuer.state='active' FOR SHARE;\n"
                  " IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuer_unavailable' USING "
                  "ERRCODE='42501'; END IF;\n"
                  ' SELECT issuer.registration_revision INTO victoria_revision FROM identity.shared_issuers '
                  'issuer\n'
                  "  WHERE issuer.issuer_id='home-assistant:victoria' AND issuer.state='active' FOR SHARE;\n"
                  " IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuer_unavailable' USING "
                  "ERRCODE='42501'; END IF;\n"
                  ' BEGIN\n'
                  '   SELECT parent.* INTO STRICT prior FROM identity.shared_link_ceremonies parent\n'
                  '    WHERE parent.ceremony_id=p_ceremony OR\n'
                  '      (parent.owner_commitment=p_owner AND parent.request_commitment=p_request) FOR '
                  'SHARE;\n'
                  ' EXCEPTION WHEN NO_DATA_FOUND THEN\n'
                  '   -- Absence is unknown, never permission to dispatch again.\n'
                  '   RETURN;\n'
                  ' WHEN TOO_MANY_ROWS THEN\n'
                  "   RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505';\n"
                  ' END;\n'
                  ' SELECT counter.* INTO generation FROM privacy.shared_owner_generations counter\n'
                  '  WHERE counter.owner_commitment=p_owner FOR SHARE;\n'
                  " IF NOT FOUND OR generation.state<>'active' THEN\n"
                  "   RAISE EXCEPTION 'shared_link_owner_unavailable' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  ' IF prior.ceremony_id IS DISTINCT FROM p_ceremony OR prior.owner_commitment IS DISTINCT '
                  'FROM p_owner OR\n'
                  '    prior.request_commitment IS DISTINCT FROM p_request OR prior.principal_id IS DISTINCT '
                  'FROM p_principal OR\n'
                  '    prior.person_id IS DISTINCT FROM p_person OR prior.legacy_binding_id IS DISTINCT FROM '
                  'p_binding OR\n'
                  '    prior.initiating_session_commitment IS DISTINCT FROM p_echo_session OR '
                  "prior.state<>'pending' OR\n"
                  "    prior.purpose<>'link_echo_victoria' OR prior.revision<>1 OR prior.ended_at IS NOT "
                  'NULL OR\n'
                  '    prior.authorization_generation<>generation.authorization_generation OR\n'
                  "    prior.expires_at<>prior.created_at+interval '5 minutes' THEN\n"
                  "   RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505';\n"
                  ' END IF;\n'
                  ' PERFORM 1 FROM identity.shared_link_challenges child WHERE child.ceremony_id=p_ceremony\n'
                  "   AND child.issuer_id='home-assistant:echo' AND child.challenge_id=p_echo_child\n"
                  '   AND child.session_commitment=p_echo_session AND '
                  'child.challenge_commitment=p_echo_challenge\n'
                  '   AND child.registration_revision=echo_revision AND '
                  'child.authorization_generation=prior.authorization_generation\n'
                  '   AND child.created_at=prior.created_at AND child.expires_at=prior.expires_at FOR '
                  'SHARE;\n'
                  " IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505'; "
                  'END IF;\n'
                  ' PERFORM 1 FROM identity.shared_link_challenges child WHERE child.ceremony_id=p_ceremony\n'
                  "   AND child.issuer_id='home-assistant:victoria' AND child.challenge_id=p_victoria_child\n"
                  '   AND child.session_commitment=p_victoria_session AND '
                  'child.challenge_commitment=p_victoria_challenge\n'
                  '   AND child.registration_revision=victoria_revision AND '
                  'child.authorization_generation=prior.authorization_generation\n'
                  '   AND child.created_at=prior.created_at AND child.expires_at=prior.expires_at FOR '
                  'SHARE;\n'
                  " IF NOT FOUND THEN RAISE EXCEPTION 'shared_link_issuance_conflict' USING ERRCODE='23505'; "
                  'END IF;\n'
                  ' -- Original timestamps are historical evidence even when the lease has expired.\n'
                  ' RETURN QUERY SELECT prior.ceremony_id,prior.authorization_generation,prior.revision,\n'
                  '   prior.created_at,prior.expires_at,echo_revision,victoria_revision;\n'
                  'END\n'
                  '$inspect$;\n'},
 {'function': 'identity.issue_shared_link_auth_proof_v1',
  'signature': 'uuid,text,text,text,timestamptz,bigint',
  'owner': 'home_agent_shared_link_proof_kernel',
  'original_revision': '0044_shared_link_reconcile_v1',
  'original_sql': '\n'
                  'CREATE FUNCTION identity.issue_shared_link_auth_proof_v1(p_proof_id uuid,p_subject '
                  'text,p_session text,\n'
                  ' p_challenge text,p_authenticated_at timestamptz,p_registration_revision bigint)\n'
                  'RETURNS TABLE(proof_id uuid,issuer_id varchar,subject varchar,session_commitment '
                  'varchar,\n'
                  ' challenge_commitment varchar,authenticated_at timestamptz,issued_at timestamptz,\n'
                  ' expires_at timestamptz,registration_revision bigint)\n'
                  'LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on\n'
                  'AS $kernel$\n'
                  'DECLARE v_issuer text; parent_id uuid; parent_session text; sibling record; receipt '
                  'record;\n'
                  'BEGIN\n'
                  " v_issuer := CASE session_user WHEN 'home_agent_shared_echo_proof_ingress' THEN "
                  "'home-assistant:echo' WHEN 'home_agent_shared_victoria_proof_ingress' THEN "
                  "'home-assistant:victoria' ELSE NULL END;\n"
                  " IF v_issuer IS NULL OR current_user<>'home_agent_shared_link_proof_kernel' OR\n"
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT '
                  'r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid=m.member WHERE r.rolname=current_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m\n'
                  '      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid\n'
                  '      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member\n'
                  '      WHERE target.rolname=current_user AND\n'
                  "        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR "
                  'NOT m.set_option)) THEN\n'
                  "   RAISE EXCEPTION 'shared_link_proof_role_invalid' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  " IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR\n"
                  "    pg_catalog.current_setting('transaction_read_only')<>'off' OR "
                  'pg_catalog.pg_is_in_recovery() OR\n'
                  '    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN\n'
                  "   RAISE EXCEPTION 'shared_link_proof_transaction_invalid' USING ERRCODE='25001';\n"
                  ' END IF;\n'
                  ' IF (SELECT count(*) FROM public.alembic_version)<>1 OR\n'
                  '    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE '
                  "a.version_num='0044_shared_link_reconcile_v1') THEN\n"
                  "   RAISE EXCEPTION 'shared_link_proof_revision_invalid' USING ERRCODE='55000';\n"
                  ' END IF;\n'
                  ' -- Includes the semantic-write fence before any authority reads. Triggers alone\n'
                  ' -- do not cover an exact proof replay, which performs no insert or update.\n'
                  ' PERFORM identity.require_shared_link_session_active_v1(v_issuer,p_session);\n'
                  ' SELECT parent.ceremony_id,parent.initiating_session_commitment INTO '
                  'parent_id,parent_session\n'
                  ' FROM identity.shared_link_challenges child\n'
                  ' JOIN identity.shared_link_ceremonies parent ON parent.ceremony_id=child.ceremony_id\n'
                  ' WHERE child.issuer_id=v_issuer AND child.challenge_commitment=p_challenge\n'
                  "   AND child.session_commitment=p_session AND parent.state='pending';\n"
                  ' IF NOT FOUND THEN\n'
                  "   RAISE EXCEPTION 'shared_link_challenge_unavailable' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  ' PERFORM '
                  "identity.require_shared_link_session_active_v1('home-assistant:echo',parent_session);\n"
                  ' FOR sibling IN SELECT child.issuer_id,child.session_commitment\n'
                  '   FROM identity.shared_link_challenges child WHERE child.ceremony_id=parent_id\n'
                  ' LOOP\n'
                  '   PERFORM '
                  'identity.require_shared_link_session_active_v1(sibling.issuer_id,sibling.session_commitment);\n'
                  ' END LOOP;\n'
                  ' SELECT admitted.* INTO STRICT receipt FROM identity.associate_shared_auth_proof_v1(\n'
                  '   '
                  'v_issuer,p_proof_id,p_subject,p_session,p_challenge,p_authenticated_at,p_registration_revision) '
                  'admitted;\n'
                  ' -- A unique-index wait during insertion must not turn an expired proof into\n'
                  ' -- a successful call. Raise before returning any receipt, rolling back writes.\n'
                  ' IF receipt.expires_at<=pg_catalog.clock_timestamp() THEN\n'
                  "   RAISE EXCEPTION 'shared_link_challenge_expired' USING ERRCODE='22023';\n"
                  ' END IF;\n'
                  ' RETURN QUERY SELECT '
                  'receipt.proof_id,receipt.issuer_id,receipt.subject,receipt.session_commitment,\n'
                  '   '
                  'receipt.challenge_commitment,receipt.authenticated_at,receipt.issued_at,receipt.expires_at,receipt.registration_revision;\n'
                  'END\n'
                  '$kernel$;\n'},
 {'function': 'identity.confirm_shared_link_ceremony_v1',
  'signature': 'uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid',
  'owner': 'home_agent_shared_link_confirm_kernel',
  'original_revision': '0044_shared_link_reconcile_v1',
  'original_sql': '\n'
                  'CREATE FUNCTION identity.confirm_shared_link_ceremony_v1(\n'
                  ' p_ceremony uuid,p_echo_subject text,p_session text,p_expected_revision bigint,\n'
                  ' p_confirmation text,p_digest text,p_proposal uuid,p_receipt uuid,p_link '
                  'uuid,p_echo_binding uuid,p_victoria_binding uuid\n'
                  ') RETURNS TABLE(link_id uuid,authorization_generation bigint,revision bigint,confirmed_at '
                  'timestamptz)\n'
                  'LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on\n'
                  'AS $kernel$\n'
                  'BEGIN\n'
                  " IF session_user<>'home_agent_shared_link_coordinator' OR "
                  "current_user<>'home_agent_shared_link_confirm_kernel' OR\n"
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT '
                  'r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid=m.member WHERE r.rolname=current_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m\n'
                  '      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid\n'
                  '      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member\n'
                  '      WHERE target.rolname=current_user AND\n'
                  "        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR "
                  'NOT m.set_option)) THEN\n'
                  "   RAISE EXCEPTION 'shared_link_confirmation_role_invalid' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  " IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR\n"
                  "    pg_catalog.current_setting('transaction_read_only')<>'off' OR "
                  'pg_catalog.pg_is_in_recovery() OR\n'
                  '    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN\n'
                  "   RAISE EXCEPTION 'shared_link_confirmation_transaction_invalid' USING ERRCODE='25001';\n"
                  ' END IF;\n'
                  ' IF (SELECT count(*) FROM public.alembic_version)<>1 OR\n'
                  '    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE '
                  "a.version_num='0044_shared_link_reconcile_v1') THEN\n"
                  "   RAISE EXCEPTION 'shared_link_confirmation_revision_invalid' USING ERRCODE='55000';\n"
                  ' END IF;\n'
                  '\n'
                  ' RETURN QUERY SELECT confirmed.* FROM identity.confirm_shared_link_v1(\n'
                  '   p_ceremony,p_echo_subject,p_session,p_expected_revision,p_confirmation,p_digest,\n'
                  '   p_proposal,p_receipt,p_link,p_echo_binding,p_victoria_binding) confirmed;\n'
                  'END\n'
                  '$kernel$;\n'},
 {'function': 'identity.inspect_shared_link_confirmation_v1',
  'signature': 'uuid,text,text,bigint,text,text,uuid,uuid,uuid,uuid,uuid',
  'owner': 'home_agent_shared_link_confirm_kernel',
  'original_revision': '0044_shared_link_reconcile_v1',
  'original_sql': '\n'
                  'CREATE FUNCTION identity.inspect_shared_link_confirmation_v1(\n'
                  ' p_ceremony uuid,p_echo_subject text,p_session text,p_expected_revision bigint,\n'
                  ' p_confirmation text,p_digest text,p_proposal uuid,p_receipt uuid,p_link '
                  'uuid,p_echo_binding uuid,p_victoria_binding uuid\n'
                  ') RETURNS TABLE(link_id uuid,authorization_generation bigint,revision bigint,confirmed_at '
                  'timestamptz)\n'
                  'LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on\n'
                  'AS $kernel$\n'
                  'DECLARE parent record;\n'
                  'BEGIN\n'
                  " IF session_user<>'home_agent_shared_link_coordinator' OR "
                  "current_user<>'home_agent_shared_link_confirm_kernel' OR\n"
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT '
                  'r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user\n'
                  '      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit\n'
                  '      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r\n'
                  '      ON r.oid=m.member WHERE r.rolname=current_user) OR\n'
                  '    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m\n'
                  '      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid\n'
                  '      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member\n'
                  '      WHERE target.rolname=current_user AND\n'
                  "        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR "
                  'NOT m.set_option)) THEN\n'
                  "   RAISE EXCEPTION 'shared_link_confirmation_role_invalid' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  " IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR\n"
                  "    pg_catalog.current_setting('transaction_read_only')<>'off' OR "
                  'pg_catalog.pg_is_in_recovery() OR\n'
                  '    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN\n'
                  "   RAISE EXCEPTION 'shared_link_confirmation_transaction_invalid' USING ERRCODE='25001';\n"
                  ' END IF;\n'
                  ' IF (SELECT count(*) FROM public.alembic_version)<>1 OR\n'
                  '    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE '
                  "a.version_num='0044_shared_link_reconcile_v1') THEN\n"
                  "   RAISE EXCEPTION 'shared_link_confirmation_revision_invalid' USING ERRCODE='55000';\n"
                  ' END IF;\n'
                  '\n'
                  '\n'
                  ' PERFORM '
                  "identity.require_shared_link_session_active_v1('home-assistant:echo',p_session);\n"
                  ' SELECT ceremony.* INTO parent FROM identity.shared_link_ceremonies ceremony\n'
                  '   WHERE ceremony.ceremony_id=p_ceremony FOR UPDATE;\n'
                  ' IF NOT FOUND THEN\n'
                  '   -- Absence is unknown; never evidence that an earlier dispatch rolled back.\n'
                  '   RETURN;\n'
                  ' END IF;\n'
                  ' IF parent.initiating_session_commitment IS DISTINCT FROM p_session THEN\n'
                  "   RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  ' PERFORM identity.require_shared_link_owner_v1(\n'
                  '   p_echo_subject,parent.principal_id,parent.person_id,parent.legacy_binding_id);\n'
                  " IF parent.state='pending' THEN RETURN; END IF;\n"
                  " IF parent.state<>'consumed' THEN\n"
                  "   RAISE EXCEPTION 'shared_link_confirmation_unavailable' USING ERRCODE='42501';\n"
                  ' END IF;\n'
                  " -- Holding the parent row lock makes the helper's write-capable pending branch\n"
                  ' -- unreachable. Its consumed branch rechecks both proofs/sessions, registration,\n'
                  ' -- generation, live link, exact IDs/commitments and correction/privacy authority.\n'
                  ' RETURN QUERY SELECT historical.* FROM identity.confirm_shared_link_v1(\n'
                  '   p_ceremony,p_echo_subject,p_session,p_expected_revision,p_confirmation,p_digest,\n'
                  '   p_proposal,p_receipt,p_link,p_echo_binding,p_victoria_binding) historical;\n'
                  'END\n'
                  '$kernel$;\n'})

LOOKUP_BODY = """
CREATE FUNCTION identity.inspect_shared_link_auth_proof_v1(p_proof_id uuid,p_subject text,p_session text,
 p_challenge text,p_authenticated_at timestamptz,p_registration_revision bigint)
RETURNS TABLE(proof_id uuid,issuer_id varchar,subject varchar,session_commitment varchar,
 challenge_commitment varchar,authenticated_at timestamptz,issued_at timestamptz,
 expires_at timestamptz,registration_revision bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on
AS $kernel$
DECLARE v_issuer text; context record; sibling record; receipt record;
 echo_subject text; checked_at timestamptz; expiry timestamptz;
BEGIN
 v_issuer := CASE session_user WHEN 'home_agent_shared_echo_proof_ingress' THEN 'home-assistant:echo' WHEN 'home_agent_shared_victoria_proof_ingress' THEN 'home-assistant:victoria' ELSE NULL END;
 IF v_issuer IS NULL OR current_user<>'home_agent_shared_link_proof_kernel' OR
    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=current_user
      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin AND NOT r.rolinherit
      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR
    NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname=session_user
      AND NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolinherit
      AND NOT r.rolcreatedb AND NOT r.rolcreaterole AND NOT r.rolreplication) OR
    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r
      ON r.oid IN (m.member,m.roleid) WHERE r.rolname=session_user) OR
    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r
      ON r.oid=m.member WHERE r.rolname=current_user) OR
    EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
      JOIN pg_catalog.pg_roles target ON target.oid=m.roleid
      JOIN pg_catalog.pg_roles member_role ON member_role.oid=m.member
      WHERE target.rolname=current_user AND
        (member_role.rolname<>'home_agent_owner' OR m.admin_option OR m.inherit_option OR NOT m.set_option)) THEN
   RAISE EXCEPTION 'shared_link_proof_role_invalid' USING ERRCODE='42501';
 END IF;
 IF pg_catalog.current_setting('transaction_isolation')<>'serializable' OR
    pg_catalog.current_setting('transaction_read_only')<>'off' OR pg_catalog.pg_is_in_recovery() OR
    pg_catalog.pg_current_xact_id_if_assigned() IS NOT NULL THEN
   RAISE EXCEPTION 'shared_link_proof_transaction_invalid' USING ERRCODE='25001';
 END IF;
 IF (SELECT count(*) FROM public.alembic_version)<>1 OR
    NOT EXISTS (SELECT 1 FROM public.alembic_version a WHERE a.version_num='0045_shared_link_proof_lookup_v1') THEN
   RAISE EXCEPTION 'shared_link_proof_revision_invalid' USING ERRCODE='55000';
 END IF;

 IF p_proof_id IS NULL OR p_subject IS NULL OR pg_catalog.char_length(p_subject) NOT BETWEEN 1 AND 64 OR
    p_subject <> pg_catalog.btrim(p_subject) OR p_subject ~ '[[:cntrl:]]' OR
    p_subject ~ '^[[:space:]]|[[:space:]]$' OR
    p_session IS NULL OR p_session !~ '^[0-9a-f]{64}$' OR
    p_challenge IS NULL OR p_challenge !~ '^[0-9a-f]{64}$' OR
    p_authenticated_at IS NULL OR NOT pg_catalog.isfinite(p_authenticated_at) OR
    p_registration_revision IS NULL OR p_registration_revision <= 0 THEN
   RAISE EXCEPTION 'shared_link_proof_input_invalid' USING ERRCODE='22023';
 END IF;
 -- This helper obtains the privacy fence before any authority or proof reads.
 PERFORM identity.require_shared_link_session_active_v1(v_issuer,p_session);
 SELECT child.*,parent.principal_id,parent.person_id,parent.legacy_binding_id,
   parent.created_at AS parent_created_at,parent.expires_at AS parent_expires_at,
   parent.initiating_session_commitment AS parent_session
 INTO context FROM identity.shared_link_challenges child
 JOIN identity.shared_link_ceremonies parent ON parent.ceremony_id=child.ceremony_id
 JOIN privacy.shared_owner_generations generation ON generation.owner_commitment=parent.owner_commitment
 WHERE child.challenge_commitment=p_challenge AND child.issuer_id=v_issuer
   AND child.session_commitment=p_session AND child.registration_revision=p_registration_revision
   AND parent.state='pending' AND parent.purpose='link_echo_victoria'
   AND child.authorization_generation=parent.authorization_generation
   AND generation.authorization_generation=parent.authorization_generation AND generation.state='active'
 FOR UPDATE OF generation,parent,child;
 IF NOT FOUND THEN
   -- Absence is unknown and can never authorize another dispatch.
   RETURN;
 END IF;
 PERFORM 1 FROM identity.shared_issuers issuer
 WHERE issuer.issuer_id=v_issuer AND issuer.registration_revision=p_registration_revision
   AND issuer.state='active' FOR SHARE;
 IF NOT FOUND THEN
   RAISE EXCEPTION 'shared_link_challenge_unavailable' USING ERRCODE='42501';
 END IF;
 PERFORM identity.require_shared_link_session_active_v1('home-assistant:echo',context.parent_session);
 FOR sibling IN SELECT child.issuer_id,child.session_commitment
   FROM identity.shared_link_challenges child WHERE child.ceremony_id=context.ceremony_id
 LOOP
   PERFORM identity.require_shared_link_session_active_v1(sibling.issuer_id,sibling.session_commitment);
 END LOOP;
 SELECT binding.ha_user_id INTO echo_subject FROM identity.ha_user_bindings binding
 WHERE binding.binding_id=context.legacy_binding_id;
 PERFORM identity.require_shared_link_owner_v1(echo_subject,context.principal_id,context.person_id,context.legacy_binding_id);
 IF v_issuer='home-assistant:echo' AND p_subject IS DISTINCT FROM echo_subject THEN
   RAISE EXCEPTION 'shared_link_challenge_unavailable' USING ERRCODE='42501';
 END IF;
 -- No proof row lock: this kernel has no proof UPDATE privilege. The privacy
 -- fence and locked parent/generation/child serialize legitimate mutations.
 SELECT proof.* INTO receipt FROM identity.shared_auth_proofs proof
 WHERE proof.proof_id=p_proof_id OR proof.challenge_commitment=p_challenge;
 IF NOT FOUND THEN RETURN; END IF;
 -- Fresh wall time follows every potentially blocking authority read.
 checked_at := pg_catalog.clock_timestamp();
 expiry := LEAST(p_authenticated_at + interval '5 minutes',context.expires_at,context.parent_expires_at);
 IF p_authenticated_at<context.created_at OR p_authenticated_at<context.parent_created_at OR
    p_authenticated_at>checked_at OR expiry<=checked_at THEN
   RAISE EXCEPTION 'shared_link_challenge_expired' USING ERRCODE='22023';
 END IF;
 IF receipt.proof_id IS DISTINCT FROM p_proof_id OR receipt.issuer_id IS DISTINCT FROM v_issuer OR
    receipt.subject IS DISTINCT FROM p_subject OR receipt.session_commitment IS DISTINCT FROM p_session OR
    receipt.challenge_commitment IS DISTINCT FROM p_challenge OR
    receipt.authenticated_at IS DISTINCT FROM p_authenticated_at OR
    receipt.registration_revision IS DISTINCT FROM p_registration_revision OR
    receipt.consumed_at IS NOT NULL OR receipt.expires_at IS DISTINCT FROM expiry OR
    receipt.issued_at<p_authenticated_at OR receipt.issued_at>checked_at OR
    context.consumed_proof_id IS DISTINCT FROM p_proof_id OR
    context.consumed_subject IS DISTINCT FROM p_subject OR
    context.consumed_at IS DISTINCT FROM receipt.issued_at THEN
   RAISE EXCEPTION 'shared_link_proof_conflict' USING ERRCODE='23505';
 END IF;
 RETURN QUERY SELECT receipt.proof_id,receipt.issuer_id,receipt.subject,receipt.session_commitment,
   receipt.challenge_commitment,receipt.authenticated_at,receipt.issued_at,receipt.expires_at,receipt.registration_revision;
END
$kernel$;
"""
LOOKUP = dict(function=LOOKUP_FUNCTION,signature=LOOKUP_SIGNATURE,owner=KERNEL_ROLE,
              original_revision=revision,original_sql=LOOKUP_BODY)

def aligned_sql(wrapper):
    old = "a.version_num='" + wrapper['original_revision'] + "'"
    new = "a.version_num='" + revision + "'"
    original = wrapper['original_sql']
    if original.count(old) != 1:
        raise ValueError('frozen wrapper revision must occur exactly once')
    return original.replace(old, new)


def body_source(sql):
    # All frozen wrappers use a known dollar tag; no caller SQL is accepted.
    marker = next(tag for tag in ('$kernel$', '$lookup$', '$inspect$') if tag in sql)
    parts = sql.split(marker)
    if len(parts) != 3:
        raise ValueError('invalid frozen wrapper body')
    return parts[1]


def _guard(expected):
    op.execute(f"""DO $guard$ BEGIN
      IF current_user<>'home_agent_owner' OR session_user<>'home_agent_owner' OR
         (SELECT count(*) FROM public.alembic_version)<>1 OR
         NOT EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num='{expected}') THEN
        RAISE EXCEPTION 'shared_link_combined_migration_authority_invalid';
      END IF;
    END $guard$;""")


def _verify(wrapper, expected_sql):
    source = body_source(expected_sql)
    if '$expected_body$' in source:
        raise ValueError('invalid frozen SQL delimiter')
    signature = wrapper['function'] + '(' + wrapper['signature'] + ')'
    owner = wrapper['owner']
    op.execute(f"""DO $drift$ DECLARE target oid; BEGIN
      target := pg_catalog.to_regprocedure('{signature}');
      IF target IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE p.oid=target AND r.rolname='{owner}'
          AND p.prosrc=$expected_body${source}$expected_body$
          AND p.prosecdef AND p.prokind='f' AND p.proretset AND NOT p.proisstrict
          AND p.provolatile='v' AND p.proparallel='u' AND NOT p.proleakproof
          AND p.probin IS NULL AND l.lanname='plpgsql'
          AND p.proconfig @> ARRAY['search_path=pg_catalog','row_security=on']::text[]
          AND pg_catalog.cardinality(p.proconfig)=2
          AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
            WHERE a.grantee<>p.proowner)
      ) OR pg_catalog.has_schema_privilege('{owner}','identity','CREATE') THEN
        RAISE EXCEPTION 'shared_link_combined_wrapper_drift' USING ERRCODE='55000';
      END IF;
    END $drift$;""")


def _replace(wrapper, sql):
    # CREATE OR REPLACE retains owner and existing ACL rather than resetting to
    # installation-time default privileges. Only the role that owns it acts.
    owner = wrapper['owner']
    replacement = sql.replace('CREATE FUNCTION ', 'CREATE OR REPLACE FUNCTION ', 1)
    if replacement == sql:
        raise ValueError('invalid frozen function definition')
    op.execute(f'GRANT CREATE ON SCHEMA identity TO {owner};')
    op.execute(f'SET LOCAL ROLE {owner};')
    op.execute(replacement)
    op.execute('RESET ROLE;')
    op.execute(f'REVOKE CREATE ON SCHEMA identity FROM {owner};')



def upgrade():
    _guard(down_revision)
    for wrapper in WRAPPERS:
        _verify(wrapper, wrapper['original_sql'])
    op.execute(f"""DO $absent$ BEGIN
      IF pg_catalog.to_regprocedure('{LOOKUP_FUNCTION}({LOOKUP_SIGNATURE})') IS NOT NULL THEN
        RAISE EXCEPTION 'shared_link_proof_lookup_collision' USING ERRCODE='55000';
      END IF;
    END $absent$;""")
    for wrapper in WRAPPERS:
        _replace(wrapper, aligned_sql(wrapper))
    op.execute(f'GRANT CREATE ON SCHEMA identity TO {KERNEL_ROLE};')
    op.execute(f'SET LOCAL ROLE {KERNEL_ROLE};')
    op.execute(LOOKUP_BODY)
    op.execute(f'REVOKE ALL ON FUNCTION {LOOKUP_FUNCTION}({LOOKUP_SIGNATURE}) FROM PUBLIC;')
    op.execute(f"""DO $acl$ DECLARE recipient record; BEGIN
      FOR recipient IN SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p
        CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) a
        JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
        WHERE p.oid='{LOOKUP_FUNCTION}({LOOKUP_SIGNATURE})'::regprocedure AND a.grantee<>p.proowner
      LOOP EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {LOOKUP_FUNCTION}({LOOKUP_SIGNATURE}) FROM %I',recipient.rolname); END LOOP;
    END $acl$;""")
    op.execute('RESET ROLE;')
    op.execute(f'REVOKE CREATE ON SCHEMA identity FROM {KERNEL_ROLE};')
    for wrapper in WRAPPERS:
        _verify(wrapper, aligned_sql(wrapper))
    _verify(LOOKUP, LOOKUP_BODY)


def downgrade():
    _guard(revision)
    for wrapper in WRAPPERS:
        _verify(wrapper, aligned_sql(wrapper))
    _verify(LOOKUP, LOOKUP_BODY)
    op.execute(f'SET LOCAL ROLE {KERNEL_ROLE};')
    op.execute(f'DROP FUNCTION {LOOKUP_FUNCTION}({LOOKUP_SIGNATURE});')
    op.execute('RESET ROLE;')
    for wrapper in WRAPPERS:
        _replace(wrapper, wrapper['original_sql'])
    for wrapper in WRAPPERS:
        _verify(wrapper, wrapper['original_sql'])
