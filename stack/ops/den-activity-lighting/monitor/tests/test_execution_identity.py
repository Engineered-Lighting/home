from den_ops.deployment import execution_identity


def unit(command='/venv/python -m uvicorn worker:create_app', pid=123, start='[Mon 2026-10-19 01:00:00 UTC]'):
    return '{ path=/venv/python ; argv[]='+command+' ; ignore_errors=no ; start_time='+start+' ; stop_time=[n/a] ; pid='+str(pid)+' ; code=(null) ; status=0/0 }'


def test_worker_roll_metadata_is_not_a_command_change():
    assert execution_identity(unit()) == execution_identity(unit(pid=456, start='[Wed 2026-10-21 01:00:00 UTC]'))


def test_changed_executable_arguments_or_error_policy_remain_changes():
    original = execution_identity(unit())
    assert original != execution_identity(unit(command='/venv/python -m uvicorn wrong:create_app'))
    assert original != execution_identity(unit().replace('path=/venv/python', 'path=/other/python'))
    assert original != execution_identity(unit().replace('ignore_errors=no', 'ignore_errors=yes'))


def test_multiple_commands_and_unknown_serialization_are_refused():
    assert execution_identity(unit()+' ; '+unit()) is None
    assert execution_identity('{ path=/venv/python ; argv[]=worker }') is None
    assert execution_identity(None) is None
    assert execution_identity('') is None


def test_plain_manifest_fixture_commands_keep_their_identity():
    assert execution_identity('python -m test') == 'python -m test'
