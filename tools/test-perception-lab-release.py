"""Release provenance tests; no host operations or retained camera media."""
import hashlib
import importlib.util
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('lab_release_builder',ROOT/'tools/build-perception-lab-release.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

def test_committed_release_is_sealed_repeatable_and_non_overwriting(tmp_path):
    repo=tmp_path/'source';repo.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
    git('init','-q')
    for name,text in {
        'stack/services/perception-lab/managed_detector_app.py':'pass\n',
        'stack/services/perception-lab/activity_reading.py':'SCHEMA="fixture"\n',
        'stack/services/perception-lab/test_fixture.py':'excluded\n',
        'tools/la-detector-trial/lifecycle.py':'pass\n',
    }.items():
        path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    git('add','.')
    git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','authored fixture')
    revision=git('rev-parse','HEAD').decode().strip()
    first,second=tmp_path/'a.tar',tmp_path/'b.tar'
    manifest=builder.build(repo,revision,first)
    # An unstaged edit cannot replace the committed deployment source.
    (repo/'stack/services/perception-lab/activity_reading.py').write_text('unreviewed\n')
    assert builder.build(repo,revision,second)==manifest
    assert first.read_bytes()==second.read_bytes()
    assert manifest['source_ref']==revision and manifest['deployment_authorized'] is False
    with tarfile.open(first) as archive:
        assert 'service/test_fixture.py' not in archive.getnames()
        for name,digest in manifest['files'].items():
            member=archive.getmember(name)
            assert member.isfile() and member.mtime==0 and not member.linkname
            assert hashlib.sha256(archive.extractfile(member).read()).hexdigest()==digest
    with pytest.raises(FileExistsError):builder.build(repo,revision,first)
