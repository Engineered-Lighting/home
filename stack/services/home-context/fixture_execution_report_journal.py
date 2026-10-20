"""Committed read-only hardware status in the existing Home journal.

This observer stream has its own cursor namespace in the SAME database. It
neither inserts unknown publications into config7 nor creates decision authority.
Only fixed authenticated simulated ingress is implemented; no browser publisher.
"""
from copy import deepcopy
import hashlib
import hmac
import json
import uuid

from fixture_execution_validation import (CAP_FIELDS, PIN_NAMES, FixtureAuthorityDenied, record_bytes,
                                         canonical_bytes, require, sha256, u64)

BASE_FIELDS=("fixture_id","device_id","boot_generation","session_id","context_digest","pins")
CURRENT_FIELDS={"eligible","not_after_home_us","clock_qualification_not_after_home_us"}
MAP_FIELDS={"revision_id","device_anchor_us","home_anchor_us","uncertainty_us","drift_ppm","valid_until_home_us"}
REPORT_FIELDS={"schema","version","report_id","scope","evidence_source","category","sampled_device_us",
               "first_received_home_us","original_hex","original_sha256","physical_truth"}
_UNSET=object()


def original_json(raw):
    def pairs(values):
        result={}
        for key,value in values:
            require(key not in result,"duplicate original report key")
            result[key]=value
        return result
    return json.loads(raw.decode("utf-8"),object_pairs_hook=pairs,
                      parse_constant=lambda _:require(False,"nonfinite original report"))


class FixtureReportJournal:
    def __init__(self, journal, scope_binding, *, native_validator, channel_verifier,
                 current_authority, clock_mapping, clock_home_us, credential_sha256,
                 create=False, capacity=4096, max_age_us=3_000_000, clock_error_us=0):
        require(all(callable(x) for x in (native_validator,channel_verifier,current_authority,clock_mapping,clock_home_us)),
                "explicit observer capabilities required")
        require(type(scope_binding) is dict and set(scope_binding)==set(BASE_FIELDS),"fixed observer session binding")
        require(type(capacity) is int and 1<=capacity<=65536 and type(max_age_us) is int and 0<max_age_us<=3_000_000,
                "bounded observer capacity/age")
        require(type(clock_error_us) is int and 0<=clock_error_us<=250_000,"qualified observer timestamp error bound")
        require(type(scope_binding["fixture_id"]) is str and scope_binding["fixture_id"].isascii()
                and 0<len(scope_binding["fixture_id"])<=128,"fixed fixture identity")
        require(type(scope_binding["device_id"]) is str and len(scope_binding["device_id"])==32
                and all(c in "0123456789abcdef" for c in scope_binding["device_id"]),"fixed device identity")
        for key in ("boot_generation","session_id"):require(u64(scope_binding[key])>0,"nonzero observer generation")
        sha256(scope_binding["context_digest"])
        require(type(scope_binding["pins"]) is dict and set(scope_binding["pins"])==set(PIN_NAMES),"exact observer pins")
        for pin in scope_binding["pins"].values():sha256(pin)
        sha256(credential_sha256)
        self.journal=journal;self.binding=deepcopy(scope_binding);self.native_validator=native_validator
        self.channel_verifier=channel_verifier;self.current_authority=current_authority;self.clock_mapping=clock_mapping
        self.clock=clock_home_us;self.credential_sha256=credential_sha256;self.capacity=capacity;self.max_age_us=max_age_us
        self.clock_error_us=clock_error_us
        marker=canonical_bytes({"binding":self.binding,"capacity":capacity,"max_age_us":max_age_us,
                                "clock_error_us":clock_error_us,"lease_policy":"monotonic-durable-1"}).decode()
        with journal.lock:
            exists=journal.db.execute("SELECT 1 FROM sqlite_master WHERE name='fixture_observer_meta'").fetchone()
            require(exists is not None or create is True,"observer absent; no automatic commissioning")
            if exists is None:
                journal.db.execute("CREATE TABLE fixture_observer_meta(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT NOT NULL,blocked INTEGER NOT NULL,last_clock TEXT NOT NULL)")
                journal.db.execute("INSERT INTO fixture_observer_meta VALUES(1,?,0,'0')",(marker,))
                journal.db.execute("CREATE TABLE fixture_observer_reports(cursor INTEGER PRIMARY KEY AUTOINCREMENT,report_id TEXT UNIQUE NOT NULL,identity_sha TEXT NOT NULL,body TEXT NOT NULL)")
                journal.db.execute("CREATE TABLE fixture_observer_lease_state(cursor INTEGER PRIMARY KEY,lease TEXT)")
            require(journal.db.execute("SELECT config FROM fixture_observer_meta WHERE id=1").fetchone()[0]==marker,
                    "observer context/config changed; explicit new scope required")

    def _ready(self):
        require(not getattr(self.journal,"_recorder_failed",False) and callable(self.journal._recorder),"observer recorder unavailable")
        require(self.journal.db.execute("SELECT blocked FROM fixture_observer_meta WHERE id=1").fetchone()[0]==0,
                "observer durable outcome unconfirmed")

    def _now(self):
        now=self.clock();require(type(now) is int and 0<now<2**64,"qualified observer Home clock required")
        old=int(self.journal.db.execute("SELECT last_clock FROM fixture_observer_meta WHERE id=1").fetchone()[0])
        if now<old:
            self.journal.db.execute("UPDATE fixture_observer_meta SET blocked=1 WHERE id=1")
            raise FixtureAuthorityDenied("observer clock rollback")
        self.journal.db.execute("UPDATE fixture_observer_meta SET last_clock=? WHERE id=1",(str(now),))
        return now

    def _original(self,report,admitted):
        require(type(report) is dict and set(report)==REPORT_FIELDS and report.get("schema")=="home-fixture-execution-report-envelope-1"
                and type(report.get("version")) is int and report["version"]==1,"typed report envelope required")
        require(type(report["report_id"]) is str and report["report_id"].isascii() and 0<len(report["report_id"])<=128,
                "report identity required")
        require(all(report["scope"].get(k)==self.binding[k] for k in BASE_FIELDS),"observer boot/session/profile mismatch")
        raw=bytes.fromhex(report["original_hex"])
        require(len(raw)<=16384 and hashlib.sha256(raw).hexdigest()==report["original_sha256"],"report original bytes mismatch")
        native=original_json(raw)
        category=self.native_validator(native,report["scope"],admitted=admitted)
        require(report["category"]==category and report["sampled_device_us"]==native["sampled_device_us"],
                "native category/sample identity mismatch")
        require(report["evidence_source"]=="simulated" and report["physical_truth"]=="unqualified","observer provenance mismatch")
        require(self.channel_verifier(deepcopy(report),deepcopy(admitted)) is True,"unverified original report channel")
        g=admitted["grant_body"]
        expected={"fixture_id":g["fixture_id"],"device_id":g["device_id"],"context_digest":g["context_digest"],
                  "pins":g["pins"],**{key:g[key] for key in ("source_generation","authority_generation","ownership_generation")},
                  "domain_generations":admitted["plan"]["domain_generations"]}
        require(all(report["scope"][key]==value for key,value in expected.items()),"report original authority scope mismatch")
        require(u64(g["original_available_home_us"])<=u64(report["first_received_home_us"]),"report precedes original admission")
        row=self.journal.db.execute("SELECT body,receipt FROM fixture_execution_grants WHERE grant_id=?",(g["grant_id"],)).fetchone()
        require(row is not None and canonical_bytes(g).decode()==row[0],"report original grant not in existing journal")
        original=json.loads(row[1])
        require(original["wire_sha256"]==admitted["wire_sha256"] and original["plan"]==admitted["plan"],"report admission changed")
        return native

    def _lease(self,report,admitted,native,now,*,mapping_override=_UNSET):
        # A missing/expired authority still permits historical retention. It
        # cannot supply a current status or refresh an old observation.
        try:
            revoked=self.journal.db.execute("SELECT revoked FROM fixture_execution_grants WHERE grant_id=?",
                                            (admitted["grant_body"]["grant_id"],)).fetchone()
            require(revoked is not None and revoked[0]==0,"report original authority revoked")
            current=self.current_authority(deepcopy(report),deepcopy(admitted))
            require(type(current) is dict and set(current)==CURRENT_FIELDS and current["eligible"] is True,
                    "current observer authority unavailable")
            mapping=self.clock_mapping() if mapping_override is _UNSET else mapping_override
            require(type(mapping) is dict and set(mapping)==MAP_FIELDS,"qualified mapping required")
            require(type(mapping["revision_id"]) is int and mapping["revision_id"]==admitted["plan"]["clock_mapping_revision"],
                    "report mapping revision mismatch")
            require(type(mapping["drift_ppm"]) is int and 0<=mapping["drift_ppm"]<=1_000_000,"mapping drift bound")
            for key in MAP_FIELDS-{"revision_id","drift_ppm"}:u64(mapping[key])
            sample=u64(native["sampled_device_us"] if native["sampled_device_us"] is not None else native["reported_device_us"])
            anchor=u64(mapping["device_anchor_us"]);require(sample>=anchor,"report predates qualified mapping")
            delta=sample-anchor
            error=u64(mapping["uncertainty_us"])+(delta*mapping["drift_ppm"]+999999)//1_000_000
            center=u64(mapping["home_anchor_us"])+delta
            require(error<=center and center+error<2**64,"mapped report time overflow")
            earliest,latest=center-error,center+error
            first=u64(report["first_received_home_us"])
            require(latest<=first-self.clock_error_us and first<=now,"report sample/receipt/commit causal order")
            caps=admitted["grant_body"]["caps"]
            end=min(earliest+self.max_age_us,u64(admitted["plan"]["support_expiry_home_us"]),
                    *(u64(caps[key]) for key in CAP_FIELDS),u64(current["not_after_home_us"]),
                    u64(current["clock_qualification_not_after_home_us"]),u64(mapping["valid_until_home_us"]))
            require(now+self.clock_error_us<end,"report display lease expired")
            return {"report_id":report["report_id"],"sampled_home_us":str(earliest),"valid_until_home_us":str(end),
                    "clock_qualification_not_after_home_us":str(min(u64(current["clock_qualification_not_after_home_us"]),
                                                                 u64(mapping["valid_until_home_us"])))}
        except (ValueError,KeyError,TypeError,OverflowError):
            return None

    def commit_report(self, report, admitted, credential):
        require(type(credential) is str and hmac.compare_digest(hashlib.sha256(credential.encode()).hexdigest(),self.credential_sha256),
                "observer ingress credential denied")
        report,admitted=deepcopy(report),deepcopy(admitted)
        identity=hashlib.sha256(record_bytes({"report":report,"admission_sha256":admitted["wire_sha256"]})).hexdigest()
        with self.journal.lock:
            self._ready()
            prior=self.journal.db.execute("SELECT identity_sha,body FROM fixture_observer_reports WHERE report_id=?",(report["report_id"],)).fetchone()
            if prior:
                require(hmac.compare_digest(prior[0],identity),"changed immutable report retry")
                return json.loads(prior[1])
            require(self.journal.db.execute("SELECT COUNT(*) FROM fixture_observer_reports").fetchone()[0]<self.capacity,
                    "observer capacity exhausted")
            native=self._original(report,admitted);now=self._now()
            require(u64(report["first_received_home_us"])<=now,"report first receipt is in the future")
            try:mapping=deepcopy(self.clock_mapping())
            except (ValueError,KeyError,TypeError,OverflowError):mapping=None
            lease=self._lease(report,admitted,native,now,mapping_override=mapping)
            epoch="fixture-report:"+str(self.journal._meta("epoch"))
            last=self.journal.db.execute("SELECT MAX(cursor) FROM fixture_observer_reports").fetchone()[0] or 0
            cursor=last+1
            commit={"report_id":report["report_id"],"report_sha256":hashlib.sha256(canonical_bytes(report)).hexdigest(),
                    "stream_epoch":epoch,"cursor":cursor,"committed_home_us":str(now)}
            result={"schema":"home-fixture-execution-inspection-1","version":1,"report":report,"scope":report["scope"],
                    "commit":commit,"lease":lease,"nowHomeUs":str(now+self.clock_error_us),"physical_permission":False}
            tx_id=str(uuid.uuid4())
            data={"tx_id":tx_id,"inspection":result,"admission_wire_sha256":admitted["wire_sha256"],
                  "clock_mapping":mapping,"clock_error_us":self.clock_error_us}
            try:
                self.journal.db.execute("UPDATE fixture_observer_meta SET blocked=1 WHERE id=1")
                self.journal._recorder({"kind":"fixture_report_prepare","data":data})
                self.journal.db.execute("BEGIN IMMEDIATE")
                self.journal.db.execute("INSERT INTO fixture_observer_reports VALUES(?,?,?,?)",(cursor,report["report_id"],identity,record_bytes(result).decode()))
                self.journal.db.execute("INSERT INTO fixture_observer_lease_state VALUES(?,?)",
                                        (cursor,record_bytes(lease).decode() if lease is not None else None))
                self.journal.db.execute("COMMIT")
                self.journal._recorder({"kind":"fixture_report_commit","data":{"tx_id":tx_id,"commit":commit}})
                require(not self.journal._recorder_failed,"independent recorder failure")
                self.journal.db.execute("UPDATE fixture_observer_meta SET blocked=0 WHERE id=1")
            except BaseException:
                if self.journal.db.in_transaction:self.journal.db.execute("ROLLBACK")
                self.journal._recorder_failed=True
                raise
            return deepcopy(result)

    def inspection(self):
        with self.journal.lock:
            self._ready();now=self._now()
            row=self.journal.db.execute("SELECT body FROM fixture_observer_reports ORDER BY cursor DESC LIMIT 1").fetchone()
            require(row is not None,"no committed fixture report")
            result=json.loads(row[0]);result["nowHomeUs"]=str(now+self.clock_error_us)
            # Read current authority using the original admitted body, never a
            # newly minted grant or a redated copy of the report.
            native=original_json(bytes.fromhex(result["report"]["original_hex"]))
            original=self.journal.db.execute("SELECT receipt FROM fixture_execution_grants WHERE json_extract(receipt,'$.wire_sha256')=?",
                (native["revision_sha256"],)).fetchone()
            require(original is not None,"committed report lost its original admission")
            candidate=self._lease(result["report"],json.loads(original[0]),native,now)
            lease_row=self.journal.db.execute("SELECT lease FROM fixture_observer_lease_state WHERE cursor=?",
                                              (result["commit"]["cursor"],)).fetchone()
            require(lease_row is not None,"committed report lost its durable lease state")
            old=json.loads(lease_row[0]) if lease_row[0] is not None else None
            if old is None or candidate is None:
                result["lease"]=None
            else:
                old["valid_until_home_us"]=str(min(u64(old["valid_until_home_us"]),u64(candidate["valid_until_home_us"])))
                old["clock_qualification_not_after_home_us"]=str(min(u64(old["clock_qualification_not_after_home_us"]),
                    u64(candidate["clock_qualification_not_after_home_us"])))
                result["lease"]=old
            # Currentness may only decrease, even across reconnects, Return or
            # process reconstruction. Preserve original report/commit bytes;
            # keep the monotonic lease state in its own durable row.
            next_raw=record_bytes(result["lease"]).decode() if result["lease"] is not None else None
            if next_raw!=lease_row[0]:
                tx_id=str(uuid.uuid4())
                change={"tx_id":tx_id,"report_id":result["report"]["report_id"],"cursor":result["commit"]["cursor"],
                        "prior_lease":json.loads(lease_row[0]) if lease_row[0] is not None else None,
                        "next_lease":deepcopy(result["lease"]),"available_home_us":str(now),
                        "cause":"current_authority_or_clock"}
                try:
                    self.journal.db.execute("UPDATE fixture_observer_meta SET blocked=1 WHERE id=1")
                    self.journal._recorder({"kind":"fixture_report_lease_prepare","data":change})
                    self.journal.db.execute("UPDATE fixture_observer_lease_state SET lease=? WHERE cursor=?",
                                            (next_raw,result["commit"]["cursor"]))
                    self.journal._recorder({"kind":"fixture_report_lease_commit","data":{
                        "tx_id":tx_id,"report_id":change["report_id"],"cursor":change["cursor"]}})
                    require(not self.journal._recorder_failed,"independent recorder failure")
                    self.journal.db.execute("UPDATE fixture_observer_meta SET blocked=0 WHERE id=1")
                except BaseException:
                    self.journal._recorder_failed=True
                    raise
            return result
