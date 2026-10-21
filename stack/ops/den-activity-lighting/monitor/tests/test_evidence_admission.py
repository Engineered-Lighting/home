"""Adversarial admission tests: fake hosts/HTTP only; never exercise live faults."""
import copy
import hashlib
import json
import os
import pytest
from conftest import FakeHost, CONFIG, fixture_json, fixture_text, write_tree
from den_ops import deployment, monitor as mon, signals as sig, soak
from den_ops.__main__ import main
from test_monitor import Jetson, JOURNAL
from test_soak import make_samples, configs, PASSING_CHECKLIST, manifest_fixture, START, qwen_workload


def criterion(rows, cid):
    return next(row for row in rows if row["id"] == cid)


@pytest.mark.parametrize("failure", ["journal_denied", "journal_malformed", "boot_missing", "boot_malformed", "units_denied", "units_malformed", "baseline_host", "thermal_required"])
def test_missing_safety_evidence_latches_immediately(tmp_path, private, failure):
    j = Jetson(tmp_path, private)
    if failure == "journal_denied": j.host.commands[JOURNAL] = (1, "permission denied")
    if failure == "journal_malformed": j.host.commands[JOURNAL] = (0, "not JSON")
    if failure == "boot_missing": j.host.files.pop("/proc/sys/kernel/random/boot_id")
    if failure == "boot_malformed": j.host.files["/proc/sys/kernel/random/boot_id"] = "garbage"
    if failure == "units_denied": j.host.commands[("systemctl", "--user")] = (1, "denied")
    if failure == "units_malformed": j.host.commands[("systemctl",)] = (0, "unexpected output")
    if failure == "baseline_host":
        path = j.cfg["sources"]["failed_units"]["baseline"]
        doc = json.loads(open(path, encoding="utf8").read()); doc["host"] = "wrong-host"
        with open(path, "w", encoding="utf8") as out: json.dump(doc, out)
    if failure == "thermal_required": os.remove(os.path.join(j.root, "thermal_zone1", "temp"))
    state, status, report = j.check()
    assert status["level"] == 3 and state["latched"]
    assert report["evidence"]["ok"] is False
    assert j.loop_state == "stopped"
    assert not report["faults"]  # evidence blindness is not fabricated hardware fault evidence


def test_optional_unsupported_sensor_has_reviewed_exclusion(tmp_path, private):
    j = Jetson(tmp_path, private)
    write_tree(j.root, {"thermal_zone2": {"type": "cv0-thermal"}})
    zones = j.cfg["sources"]["thermal_zones"]
    zones.update(exclude_types=["cv0-thermal"], exclusion_reasons={"cv0-thermal": "Reviewed unsupported channel; CPU/GPU remain required"})
    assert j.check()[1]["level"] == 0
    del zones["exclusion_reasons"]
    assert j.check()[1]["level"] == 3


def test_validate_nonzero_and_dry_run_does_not_stop(tmp_path, private, monkeypatch, capsys):
    j = Jetson(tmp_path, private)
    j.host.commands[JOURNAL] = (1, "permission denied")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(j.cfg), encoding="utf8")
    monkeypatch.setattr("den_ops.__main__.Host", lambda: j.host)
    assert main(["--config", str(config_path), "validate"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert j.loop_state == "running" and not j.host.posts(JOURNAL[0])


@pytest.mark.parametrize("mutation,cid", [
    (lambda rows: rows[5]["signals"].pop("temp.hwmon.nvme.composite"), "temperatures"),
    (lambda rows: rows[5].update(evidence={"ok":False,"errors":[{"source":"journal"}]}), "evidence"),
    (lambda rows: rows[5].update(run_id="another-run"), "identity"),
    (lambda rows: rows[5].update(monotonic_at=rows[4]["monotonic_at"]), "duration"),
    (lambda rows: rows[5].update(manifest_sha256="0"*64), "identity"),
])
def test_corrupt_or_missing_sample_evidence_cannot_pass(mutation, cid):
    cfg, monitor = configs(); rows = make_samples(); mutation(rows)
    assert criterion(soak.evaluate(cfg, monitor, rows, checklist=PASSING_CHECKLIST), cid)["result"] == "fail"


def test_short_sampling_hole_fails_despite_95_percent_count():
    cfg, monitor = configs(); rows = make_samples(); del rows[20:22]
    assert criterion(soak.evaluate(cfg, monitor, rows), "duration")["result"] == "fail"


@pytest.mark.parametrize("rows", [[], [{"id":"empty_off","result":"pass","optional":False}], [{"id":"unknown","result":"pass","optional":True}]])
def test_partial_checklist_is_never_a_pass(rows):
    assert soak._checklist_criterion({"schema":soak.CHECKLIST_SCHEMA,"results":rows})["result"] == "fail"


def test_checklist_wrong_run_is_rejected():
    cfg, monitor = configs(); checklist = copy.deepcopy(PASSING_CHECKLIST); checklist["run_id"]="wrong"
    assert criterion(soak.evaluate(cfg, monitor, make_samples(), checklist=checklist), "mvp_checklist")["result"] == "fail"


def test_absent_occupied_inputs_are_not_a_pass():
    cfg, monitor = configs(); rows=make_samples()
    for row in rows: row["brain"]["present"]=False
    assert criterion(soak.evaluate(cfg, monitor, rows), "brain_inputs")["result"] == "fail"


def test_roll_epoch_reset_or_unexercised_stack_is_incomplete():
    profile=soak.load_soak_config(os.path.join(CONFIG,'soak.jetson.example.json'))['loop']['qwen_profile']
    cfg={"warmup_s":0, 'loop':{'qwen_profile':profile}}
    rows=[{"at":i*60,"loop":{"session_id":"s","producer_epoch":"a" if i<35 else "b","rolls":i//4,"state":"running","occupancy_age_s":0.,
                               'qwen_workload':qwen_workload(profile,i)}} for i in range(70)]
    assert soak._roll_exposure(cfg, rows)["result"] == "pending"
    for row in rows: row["loop"]["producer_epoch"]="a"
    assert soak._roll_exposure(cfg, rows)["result"] == "pass"


def prepare_pair(tmp_path, monkeypatch):
    """Real samplers/collectors and CLI, with deterministic host/HTTP boundaries."""
    pairs = {}
    hosts={}
    manifest={"schema":deployment.SCHEMA,"deployment_id":"test-live-release","hosts":hosts}
    monkeypatch.setattr(sig, "read_disks", lambda paths: {"disk."+name+".free_gib":500. for name in paths})
    for label in ("la", "jetson"):
        cfg=soak.load_soak_config(os.path.join(CONFIG,"soak."+label+".example.json"))
        monitor=mon.load_config(os.path.join(CONFIG,"monitor."+label+".example.json"))
        root=tmp_path/label; root.mkdir()
        def private(name, doc):
            p=root/name; p.write_text(json.dumps(doc), encoding="utf8"); p.chmod(0o600); return str(p)
        monitor["sources"]["failed_units"]["baseline"]=private("units.json", {"schema":"den-ops-failed-units-baseline-1","host":cfg["host"],"units":{"system":[],"user":[]}})
        monitor["sources"]["faults"]["baseline"]=private("faults.json", {"schema":"den-ops-fault-baseline-1","host":cfg["host"],"ignore":[]})
        thermal=root/"thermal"
        if label=="jetson":
            write_tree(str(thermal), {"thermal_zone"+str(i):{"type":kind,"temp":"50000"} for i,kind in enumerate(monitor["sources"]["thermal_zones"]["required_types"])})
        else:
            write_tree(str(thermal), fixture_json("sysfs.json")["la_thermal"])
            hwmon=root/"hwmon"; write_tree(str(hwmon), fixture_json("sysfs.json")["la_hwmon"])
            monitor["sources"]["hwmon"]["root"]=str(hwmon)
        monitor["sources"]["thermal_zones"]["root"]=str(thermal)
        host=FakeHost(now=START)
        host.files["/proc/meminfo"]=fixture_text("meminfo."+label+".txt")
        host.files["/proc/sys/kernel/random/boot_id"]="00000000-0000-0000-0000-000000000001"
        host.commands[JOURNAL]=(0,'{"__CURSOR":"a","__REALTIME_TIMESTAMP":"1800000000000000","MESSAGE":"Linux version"}')
        host.commands[("systemctl",)]=(0,""); host.commands[("systemctl","--user")]=(0,"")
        host.commands[("nvidia-smi",)]=(0,fixture_text("nvidia-smi.normal.csv"))
        host.first_lines[("tegrastats","--interval","1000")]=fixture_text("tegrastats.orin.txt").splitlines()[0]
        unit_inventory=[]
        for unit in cfg["units"]:
            prefix=("systemctl",)+(("--user",) if unit["scope"]=="user" else ())+("show",unit["name"])
            host.commands[prefix]=(0,"ActiveState=active\nNRestarts=0\nMemoryCurrent=44040192\nMemoryMax=268435456\nWorkingDirectory="+str(root)+"\nExecStart=python -m service\n")
            unit_inventory.append({"name":unit["name"],"scope":unit["scope"],"working_directory":str(root),"exec_start":"python -m service"})
        if label=="la":
            def brain_reply(body, host=host):
                state=fixture_json("brain-state.json"); state["generated_at"]=host.now()
                return 200,state
            host.responses[("GET",cfg["brain"]["state_url"])]=brain_reply
            host.responses[("GET",cfg["brain"]["healthz_url"])]=(200,{"ok":True})
        else:
            token=root/"loop.token"; token.write_text("t"*40); token.chmod(0o600)
            cfg["loop"]["token_file"]=str(token)
            host.responses[("GET",cfg["loop"]["status_url"])]=lambda body,host=host,profile=cfg['loop']['qwen_profile']:(200,{"state":"running","last_occupied_age_s":0.,"deep_stack":{"session_id":"s","producer_epoch":"epoch","rolls":int((host.now()-START)//280), 'qwen_workload':qwen_workload(profile,int(host.now()-START))}})
        artifacts=[]
        for component in deployment.COMPONENTS[cfg["host"]]:
            source=str(root/(component+".py")); host.files[source]="source bytes"
            artifacts.append({"path":source,"sha256":hashlib.sha256(b"source bytes").hexdigest(),"component":component})
        hosts[cfg["host"]]={"components":{component:"release-"+label for component in deployment.COMPONENTS[cfg["host"]]},"monitor_config_sha256":deployment.digest(monitor),"soak_config_sha256":deployment.digest(cfg),"artifacts":artifacts,"units":unit_inventory}
        def sleep(seconds, host=host,cfg=cfg):
            host.advance(seconds)
            host.files[cfg["monitor_status"]]=json.dumps({"checked_at":host.now(),"level":0,"latched":False,"step":"normal"})
        sleep(0)
        pairs[label]=(cfg,monitor,host,sleep)
    for cfg,_,_,_ in pairs.values(): cfg["_deployment"]=manifest
    return pairs,manifest


def test_real_sampler_two_host_end_to_end_and_cli_reconstruction(tmp_path, monkeypatch, capsys):
    pairs,manifest=prepare_pair(tmp_path,monkeypatch); receipts=[]
    for label,(cfg,monitor,host,sleep) in pairs.items():
        out=tmp_path/(label+"-attempt")
        path=soak.run(cfg,monitor,host,str(out),sleep=sleep,log=lambda value:None)
        rows=soak.read_samples(path)
        assert len(rows)==191
        original=json.loads((out/("soak-"+cfg["host"]+".json")).read_text())
        assert criterion(original["criteria"],"attempt_complete")["result"]=="pass"
        with pytest.raises(FileExistsError): soak.run(cfg,monitor,host,str(out),sleep=sleep)
        if label=="la":
            checklist=copy.deepcopy(PASSING_CHECKLIST)
            checklist.update(manifest_sha256=deployment.digest(manifest),run_id=rows[0]["run_id"],started_at=host.now()+1,ended_at=host.now()+600)
            cpath=out/"chosen-checklist.json"; cpath.write_text(json.dumps(checklist))
            mpath=tmp_path/"deployment.json"; mpath.write_text(json.dumps(manifest))
            cfgpath=tmp_path/"soak.json"; cfgpath.write_text(json.dumps({k:v for k,v in cfg.items() if k != "_deployment"}))
            monpath=tmp_path/"monitor.json"; monpath.write_text(json.dumps(monitor))
            monkeypatch.setattr("den_ops.__main__.Host",lambda:host)
            assert main(["--config",str(monpath),"soak","evaluate","--soak-config",str(cfgpath),"--out",str(out),"--deployment-manifest",str(mpath)])==1
            assert "INCOMPLETE" in capsys.readouterr().out
            assert main(["--config",str(monpath),"soak","evaluate","--soak-config",str(cfgpath),"--out",str(out),"--deployment-manifest",str(mpath),"--checklist",str(cpath)])==0
            capsys.readouterr()
        receipts.append(json.loads((out/("soak-"+cfg["host"]+".json")).read_text()))
    assert soak.combine(receipts,manifest=manifest)["verdict"]=="pass"
    assert soak.combine(receipts,release="unrelated-label",manifest=manifest)["verdict"]=="fail"
    assert soak.combine([receipts[0],receipts[0]],manifest=manifest)["verdict"]=="fail"
    for mutate in (lambda d:d["criteria"][0].update(result="banana"), lambda d:d.update(verdict="fail"), lambda d:d.update(started_at=float("nan")), lambda d:d.update(started_at="invalid"), lambda d:d.pop("run_id"), lambda d:d.update(occupied_window={"started_at":float("nan"),"ended_at":1})):
        malformed=copy.deepcopy(receipts); mutate(malformed[0])
        assert soak.combine(malformed,manifest=manifest)["verdict"]=="fail"
    legacy=copy.deepcopy(receipts); legacy[1]["schema"]="den-ops-soak-receipt-1"
    assert soak.combine(legacy,manifest=manifest)["verdict"]=="fail"
    roll_only=copy.deepcopy(receipts); roll_only[1]['schema']='den-ops-soak-receipt-2'
    assert soak.combine(roll_only,manifest=manifest)['verdict']=='fail'
    occupancy_unverified=copy.deepcopy(receipts); occupancy_unverified[1]['schema']='den-ops-soak-receipt-3'
    assert soak.combine(occupancy_unverified,manifest=manifest)['verdict']=='fail'


def test_sampler_preserves_partial_failure_and_rejects_config_drift(tmp_path,monkeypatch):
    pairs,manifest=prepare_pair(tmp_path,monkeypatch)
    cfg,monitor,host,sleep=pairs["la"]
    host.commands[JOURNAL]=(1,"permission denied")
    out=tmp_path/"failed-attempt"
    soak.run(cfg,monitor,host,str(out),sleep=sleep,log=lambda value:None)
    receipt=json.loads((out/"soak-la-host.json").read_text())
    assert receipt["verdict"]=="fail" and receipt["samples"]==1
    assert criterion(receipt["criteria"],"attempt_complete")["result"]=="fail"
    monitor["rules"][0]["levels"][0]+=1
    with pytest.raises(ValueError,match="configuration digest mismatch"):
        soak.run(cfg,monitor,host,str(tmp_path/"drift"),sleep=sleep)


def test_pass_rows_without_observation_evidence_are_rejected():
    checklist=copy.deepcopy(PASSING_CHECKLIST)
    checklist["results"][0].pop("evidence")
    assert soak._checklist_criterion(checklist)["result"] == "fail"


def test_sofa_check_uses_fresh_near_position_without_frigate_zones():
    state=fixture_json("brain-state.json")
    state["occupancy"]["zones"]={"sofa":False,"desk":False}
    state["inputs"].update(at_sofa=True,position_age_s=10)
    predicate=next(step[2] for step in soak.CHECKLIST if step[0]=="sofa_ambient")
    assert predicate(state)
    for change in ({"position_age_s":10.01},{"position_age_s":None},{"position_age_s":float("nan")},{"position_age_s":True},{"at_sofa":False}):
        candidate=copy.deepcopy(state); candidate["inputs"].update(change)
        assert not predicate(candidate)
    missing=copy.deepcopy(state); missing["inputs"].pop("position_age_s")
    assert not predicate(missing)
    state["occupancy"]["present"]=False
    assert not predicate(state)
