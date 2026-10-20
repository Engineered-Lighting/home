"""Additive hardware-authority archive, joined to the unchanged worker archive.

The existing v1 worker reader continues to refuse unknown records. This channel
retains new grant bodies and references its exact parent manifest; a combined
receipt must bind BOTH closed archives before hardware replay is complete.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from archive import Recorder, ArchiveRefusal
from journal import digest

KINDS = frozenset({"fixture_grant_prepare", "fixture_grant_commit", "fixture_grant_revocation",
                   "fixture_report_prepare", "fixture_report_commit",
                   "fixture_report_lease_prepare", "fixture_report_lease_commit"})


class FixtureAuthorityRecorder(Recorder):
    def __init__(self, filename, *, parent_manifest_sha256, source_pins, **limits):
        if (type(parent_manifest_sha256) is not str or len(parent_manifest_sha256) != 64
                or type(source_pins) is not dict or not source_pins):
            raise ArchiveRefusal("fixture_parent_manifest_and_source_pins_required")
        manifest = {"schema": "home-live-session-manifest-1", "version": 1,
                    "extension": {"schema": "home-fixture-authority-archive-1", "version": 1,
                                  "parent_manifest_sha256": parent_manifest_sha256,
                                  "source_pins": deepcopy(source_pins), "physical_permission": False}}
        super().__init__(filename, manifest, **limits)

    def record(self, record, *, record_id=None):
        if type(record) is dict and set(record) == {"kind", "data"} and record["kind"] in KINDS:
            if record_id is not None:
                raise ArchiveRefusal("fixture_identity_is_original_transaction_not_caller_alias")
            with self.lock:
                return self._append(record)
        if type(record) is dict and record.get("kind") == "session_end":
            return super().record(record, record_id=record_id)
        raise ArchiveRefusal("fixture_authority_record_kind_refused")


def multiplex_recorders(worker_recorder, fixture_recorder):
    """Fixed process composition; a caller cannot select an archive or route."""
    def record(body):
        return fixture_recorder.record(body) if body.get("kind") in KINDS else worker_recorder(body)
    return record


def replay_fixture_authority(filename, *, parent_manifest_sha256, source_pins,encode_plan=None,encode_context=None,context=None):
    """Replay recorded grants, report commits and monotonic display withdrawals.

    This verifies causal originals; worker/native behavior is recomputed by
    their own pinned replay consumers. No new grant or report is synthesized.
    """
    from fixture_reports import parse_original, validate_native_event
    from fixture_execution_validation import canonical_bytes, record_bytes, require, u64, PROJECTION, CAP_FIELDS
    try:
        path=Path(filename)
        require(callable(encode_plan) and callable(encode_context),"pinned canonical replay codec required")
        require(type(context)is dict and all(k in context for k in ("principal_id","ownership_generation","pins","boot_generation","session_id")),
                "pinned original session context required")
        context=deepcopy(context)
        require(path.is_file() and path.stat().st_size<=64*1024*1024,"authority archive bound")
        entries=[parse_original(line,max_bytes=1024*1024) for line in path.read_bytes().splitlines()]
        require(2<=len(entries)<=16384,"authority archive record bound")
        previous=None;pending={};grants={};reports={};revoked=set();last_cursor=0;ended=False;changes=0
        for seq,entry in enumerate(entries):
            require(type(entry)is dict and set(entry)=={"schema","version","seq","previous_digest","record","digest"}
                    and entry["schema"]=="home-live-session-record-1" and type(entry["version"])is int and entry["version"]==1
                    and type(entry["seq"])is int and entry["seq"]==seq and entry["previous_digest"]==previous,
                    "authority archive order incomplete")
            body={k:v for k,v in entry.items() if k!="digest"}
            require(digest(body)==entry["digest"],"authority archive hash mismatch");previous=entry["digest"]
            record=entry["record"]
            require(type(record)is dict and set(record)=={"kind","data"} and not ended,"authority record fields")
            kind,data=record["kind"],record["data"]
            if seq==0:
                require(kind=="session_manifest","authority manifest missing")
                ext=data["manifest"]["extension"]
                require(ext=={"schema":"home-fixture-authority-archive-1","version":1,
                    "parent_manifest_sha256":parent_manifest_sha256,"source_pins":source_pins,"physical_permission":False},
                    "authority manifest pins mismatch")
                continue
            if kind=="session_end":
                require(seq==len(entries)-1 and data.get("status")=="complete" and not pending,"authority archive incomplete")
                ended=True;continue
            require(kind in KINDS,"unknown authority record")
            if kind.endswith("_prepare"):
                tx=data["tx_id"];require(tx not in pending,"duplicate authority transaction")
                if kind=="fixture_grant_prepare":
                    r=data["receipt"];g=r["grant_body"];wire=bytes.fromhex(r["wire_hex"])
                    require(hashlib.sha256(canonical_bytes(g)).hexdigest()==r["grant_body_sha256"]
                        and hashlib.sha256(wire).hexdigest()==r["wire_sha256"]
                        and len(wire)>=40 and wire[-40:-32]==u64(g["grant_id"]).to_bytes(8,"little")
                        and wire[-32:]==bytes.fromhex(r["grant_body_sha256"])
                        and hashlib.sha256(bytes.fromhex(r["context_hex"])).hexdigest()==g["context_digest"]
                        and g["command_content_hex"]==(b"C12-CONTENT-V1"+wire[:-40]).hex()
                        and hashlib.sha256(bytes.fromhex(g["command_content_hex"])).hexdigest()==g["command_content_sha256"],
                        "authority original grant/wire mismatch")
                    plan=r["plan"]
                    require(encode_plan(deepcopy(plan))==wire and encode_context(deepcopy(context))==bytes.fromhex(r["context_hex"])
                        and g["schema"]=="home-fixture-execution-grant-1" and type(g["version"])is int and g["version"]==1
                        and plan["context_digest"]==g["context_digest"]
                        and g["session_principal_id"]==context["principal_id"]
                        and g["ownership_generation"]==context["ownership_generation"]
                        and all(g["pins"][k]==v for k,v in context["pins"].items())
                        and all(type(plan[k]) is type(g[k]) and plan[k]==g[k] for k in PROJECTION)
                        and all(plan[k]==g["caps"][k] for k in CAP_FIELDS)
                        and plan["grant_id"]==g["grant_id"] and plan["grant_body_sha256"]==r["grant_body_sha256"],
                        "authority canonical plan projection mismatch")
                elif kind=="fixture_report_prepare":
                    i=data["inspection"];r=i["report"];wire=data["admission_wire_sha256"]
                    require(wire in grants,"report original grant missing")
                    raw=bytes.fromhex(r["original_hex"])
                    require(hashlib.sha256(raw).hexdigest()==r["original_sha256"],"report original hash mismatch")
                    native=parse_original(raw);category=validate_native_event(native,r["scope"],admitted=grants[wire])
                    require(category==r["category"] and native["sampled_device_us"]==r["sampled_device_us"]
                        and i["commit"]["report_sha256"]==hashlib.sha256(canonical_bytes(r)).hexdigest(),
                        "report original identity mismatch")
                    require(i["commit"]["cursor"]==last_cursor+1,"report cursor incomplete")
                    c=i["commit"];first=u64(r["first_received_home_us"]);committed=u64(c["committed_home_us"])
                    require(c["report_id"]==r["report_id"] and r["scope"]==i["scope"]
                        and r["scope"]["boot_generation"]==context["boot_generation"] and r["scope"]["session_id"]==context["session_id"]
                        and u64(grants[wire]["grant_body"]["original_available_home_us"])<=first<=committed<=u64(i["nowHomeUs"]),
                        "report commit clock/scope mismatch")
                    lease=i["lease"]
                    if lease is not None:
                        require(set(lease)=={"report_id","sampled_home_us","valid_until_home_us","clock_qualification_not_after_home_us"}
                            and lease["report_id"]==r["report_id"],"initial report lease fields")
                        mapping=data["clock_mapping"];clock_error=data["clock_error_us"]
                        require(type(mapping)is dict and set(mapping)=={"revision_id","device_anchor_us","home_anchor_us","uncertainty_us","drift_ppm","valid_until_home_us"}
                            and type(clock_error)is int and 0<=clock_error<=250000
                            and type(mapping["revision_id"])is int and mapping["revision_id"]==grants[wire]["plan"]["clock_mapping_revision"]
                            and type(mapping["drift_ppm"])is int and 0<=mapping["drift_ppm"]<=1000000,"initial report mapping invalid")
                        sample=u64(native["sampled_device_us"] if native["sampled_device_us"] is not None else native["reported_device_us"])
                        delta=sample-u64(mapping["device_anchor_us"]);require(delta>=0,"initial sample predates mapping")
                        error=u64(mapping["uncertainty_us"])+(delta*mapping["drift_ppm"]+999999)//1000000
                        center=u64(mapping["home_anchor_us"])+delta;earliest=center-error
                        end=u64(lease["valid_until_home_us"]);clock_cap=u64(lease["clock_qualification_not_after_home_us"])
                        require(earliest>=0 and center+error<2**64 and u64(lease["sampled_home_us"])==earliest
                            and center+error<=first-clock_error and committed+clock_error<end
                            and end<=min(earliest+3000000,clock_cap,u64(mapping["valid_until_home_us"]),
                                u64(grants[wire]["grant_body"]["support_expiry_home_us"]),
                                *(u64(grants[wire]["grant_body"]["caps"][k]) for k in CAP_FIELDS))
                            and clock_cap<=u64(mapping["valid_until_home_us"]),"initial report lease outside original bounds")
                else:
                    require(set(data)=={"tx_id","report_id","cursor","prior_lease","next_lease","available_home_us","cause"}
                        and data["cause"]=="current_authority_or_clock","lease transition fields")
                    require(data["cursor"] in reports,"lease report missing")
                    r=reports[data["cursor"]];old,new=data["prior_lease"],data["next_lease"]
                    require(r["report_id"]==data["report_id"] and record_bytes(r["lease"])==record_bytes(old)
                        and old is not None and record_bytes(old)!=record_bytes(new),"lease predecessor mismatch")
                    u64(data["available_home_us"])
                    require(u64(data["available_home_us"])>=u64(r["last_available_home_us"]),"lease transition clock order")
                    if new is not None:
                        require(set(new)==set(old) and new["report_id"]==old["report_id"]
                            and new["sampled_home_us"]==old["sampled_home_us"]
                            and u64(new["valid_until_home_us"])<=u64(old["valid_until_home_us"])
                            and u64(new["clock_qualification_not_after_home_us"])<=u64(old["clock_qualification_not_after_home_us"]),
                            "lease extended during replay")
                pending[tx]=(kind,deepcopy(data));continue
            if kind=="fixture_grant_revocation":
                original=next((r for r in grants.values() if r["grant_body"]["grant_id"]==data["grant_id"]),None)
                require(original is not None,"revoked grant original missing")
                require(u64(data["available_home_us"])>=u64(original["grant_body"]["admitted_home_us"]),"revocation clock order")
                revoked.add(data["grant_id"])
                continue
            tx=data["tx_id"];require(tx in pending,"authority prepare missing")
            prepared_kind,p=pending.pop(tx)
            require(kind==prepared_kind.replace("_prepare","_commit"),"authority commit kind mismatch")
            if kind=="fixture_grant_commit":
                r=p["receipt"]
                require(data=={"tx_id":tx,"request_id":p["request_id"],"grant_body_sha256":r["grant_body_sha256"]}
                    and r["wire_sha256"] not in grants,"grant commit mismatch")
                grants[r["wire_sha256"]]=r
            elif kind=="fixture_report_commit":
                i=p["inspection"];c=i["commit"]
                require(data=={"tx_id":tx,"commit":c},"report commit mismatch")
                last_cursor=c["cursor"];reports[last_cursor]={"report_id":i["report"]["report_id"],"lease":deepcopy(i["lease"]),
                    "grant_id":grants[p["admission_wire_sha256"]]["grant_body"]["grant_id"],"last_available_home_us":c["committed_home_us"]}
            else:
                require(data=={"tx_id":tx,"report_id":p["report_id"],"cursor":p["cursor"]},"lease commit mismatch")
                reports[p["cursor"]]["lease"]=deepcopy(p["next_lease"])
                reports[p["cursor"]]["last_available_home_us"]=p["available_home_us"];changes+=1
        require(ended and not pending,"authority archive missing finalization")
        for row in reports.values():row.update(current=False,grant_revoked=row["grant_id"] in revoked)
        return {"status":"causal_archive_verified","grants":len(grants),"reports":len(reports),"lease_transitions":changes,
                "final_report_leases":reports,"lease_authority":"historical_recorded_ceilings_only","current":False,
                "last_digest":previous,"physical_verified":False,"firmware_recomputed":False}
    except (ValueError,KeyError,TypeError,OverflowError,OSError) as error:
        return {"status":"incomplete","reason":str(error),"physical_verified":False,"firmware_recomputed":False}
