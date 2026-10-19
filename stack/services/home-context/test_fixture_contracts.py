"""Additive schema examples and independent literal byte-layout controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import unittest

from schema_registry import SchemaRegistry
from fixture_execution_validation import canonical_bytes
from fixture_profile import validate_profile, FixtureProfileRefusal

ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/"contracts/home-fixture-execution"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.validator=SchemaRegistry.__new__(SchemaRegistry)
        self.validator.documents={}
        self.validator.profile="legacy"

    def valid(self,name,value):
        path=BASE/"schemas"/(name+".schema.json")
        schema=json.loads(path.read_text())
        return self.validator._valid(value,schema,schema,path)

    def test_generated_pins_match_current_bytes(self):
        bundle=json.loads((BASE/"schemas/DIGESTS.json").read_text())
        for name,expected in bundle["files"].items():
            self.assertEqual(hashlib.sha256((BASE/"schemas"/name).read_bytes()).hexdigest(),expected)

    def test_literal_c_g_w_graph_does_not_depend_on_the_python_encoder(self):
        c=(BASE/"examples/C-content.bin").read_bytes()
        w=(BASE/"examples/W-plan.bin").read_bytes()
        g=json.loads((BASE/"examples/G-grant.json").read_text())
        p=json.loads((BASE/"examples/plan-fields.json").read_text())
        self.assertEqual(len(w),409)
        self.assertEqual(c,b"C12-CONTENT-V1"+w[:-40])
        self.assertEqual(hashlib.sha256(c).hexdigest(),"322fcfa09edc9bb0c32cf24e029913b4e68624671c5e3ab4e2097a7f4f1dba0a")
        self.assertEqual(hashlib.sha256(canonical_bytes(g)).hexdigest(),"ec555f972216c99f191a34dc02ed444de4a61cd1416da9ff67dc722ea0bfbf43")
        self.assertEqual(hashlib.sha256(w).hexdigest(),"299c1998f2322338c97668e14c96987ae82abc27021bf9098a52dfb6b7c767f4")
        self.assertEqual(w[-32:].hex(),hashlib.sha256(canonical_bytes(g)).hexdigest())
        self.assertEqual(struct.unpack_from("<Q",w,369)[0],100)
        self.assertEqual(struct.unpack_from("<2i",w,307),(1000,-2000))
        self.assertTrue(self.valid("home-fixture-execution-grant-1",g))
        envelope={"schema":"home-fixture-execution-plan-1","version":1,"wire_plan":p,
                  "wire_sha256":hashlib.sha256(w).hexdigest(),"physical_permission":False}
        self.assertTrue(self.valid(envelope["schema"],envelope))

    def test_changed_version_unknown_fields_and_signed_target_limits_reject(self):
        g=json.loads((BASE/"examples/G-grant.json").read_text())
        for field,value in (("version",True),("version",2),("schema","home-c12-grant-draft-1"),("producer_url","https://invalid.example")):
            mutated={**g,field:value}
            self.assertFalse(self.valid("home-fixture-execution-grant-1",mutated))
        p=json.loads((BASE/"examples/plan-fields.json").read_text())
        envelope={"schema":"home-fixture-execution-plan-1","version":1,"wire_plan":p,
                  "wire_sha256":"0"*64,"physical_permission":False}
        for targets in ([2**31,0],[True,0],[-2**31-1,0]):
            bad=deepcopy(envelope);bad["wire_plan"]["target_centideg"]=targets
            self.assertFalse(self.valid(envelope["schema"],bad))

    def test_source_inventory_schema_cannot_be_renamed_into_commissioning(self):
        raw=json.loads((BASE/"examples/shipping-source-inventory.json").read_text())
        self.assertFalse(raw["production_permission"])
        self.assertIsNone(raw["motors"]["calibration"])
        for schema in (raw["schema"],"home-fixture-commissioning-profile-1"):
            bad={**raw,"schema":schema}
            with self.assertRaises(FixtureProfileRefusal):validate_profile(bad)


if __name__=="__main__":unittest.main()
