"""Authored desk proposals through the actual allocator, without admission.

This is a diagnostic projection of private candidate shapes into existing
public-source delivery fixtures. It commissions no camera, part bounds, source,
association, room or fixture; no Journal, model, image or host is opened.

Run from the checkout root with ``python -B -P
tools/den-live-runtime/test_desk_enclosure_feasibility.py -v``. Safe-path mode
avoids the sibling operator.py shadowing Python's standard-library operator.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
ASSESSMENT = ROOT / "tools" / "den-assessment"
sys.path.insert(0, str(ASSESSMENT))
from desk_enclosure import digest, evaluate_desk_enclosure

spec = importlib.util.spec_from_file_location(
    "authored_desk_enclosure_feasibility_inputs", ASSESSMENT / "test_desk_enclosure.py")
authored = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authored)


# Reuse the existing allocator and authored delivery fixture. The only changed
# variable between blocked and feasible cases is the supplied part geometry.
# Bootstrap history is resolved with the actual existing attributed clearance
# mechanism; no checkpoint or achieved state is injected.
ALLOCATE = r"""
'use strict';
const fs=require('node:fs');
const F=require('./tools/fixtures/home-live-delivery.cjs');
const {L,Delivery,clone,pin}=F;
const request=JSON.parse(fs.readFileSync(0,'utf8')),proposal=request.proposal;
const work=proposal.work,offset=work.center_m.map((v,n)=>v-[0,0,1][n]);
const policy={...F.policy(),schema:'home-live-task-delivery-policy-4',version:4,brightness_transition_s:2};
policy.fixture.origin_m=[work.center_m[0],work.center_m[1]-2,work.center_m[2]+2];
policy.fixture.initial={pan_deg:90,tilt_deg:45};
policy.room.usable_bounds=F.aabb([work.center_m[0],work.center_m[1],1.5],[5,5,2]);
policy.modeled_emission={assessment_ref:pin('authored-emission'),range_m:12,valid_until:200};
policy.observation_coverage={assessment_ref:pin('authored-observation'),method_ref:pin('authored-observation-method'),
  qualification_ref:pin('authored-observation-qualification'),applicability_ref:pin('authored-observation-applicability'),
  bounds:clone(policy.room.usable_bounds),valid_until:200};
const lifecycle={schema:'home-live-person-lifecycle-policy-1',version:1,continuity_methods:[],negative_methods:[],absence_methods:[],
  operator_clearance:{policy_ref:pin('authored-clearance'),applicability_ref:pin('authored-clearance-applicability'),
    procedure_ref:pin('authored-clearance-procedure'),room_assessment_ref:clone(policy.room.assessment_ref),max_age_s:5}};
const ref=(p,cursor)=>({stream_epoch:'delivery-stream',cursor,producer_id:'delivery-producer',id:p.id,payload_digest:L.digest(p),component_id:null});
function projectedGeometry(){
  const g=F.geometry(proposal.observed_at);
  const authoredParts=clone(g.parts);
  function shifted(shape){
    const s=clone(shape);
    if(s.center_m)s.center_m=s.center_m.map((v,n)=>v+offset[n]);
    if(s.items)s.items=s.items.map(shifted);
    return s;
  }
  const named=(part_id,shape)=>({part_id,...clone(shape)});
  const full=proposal.parts;
  const shapes=request.independent_distinct_parts?
    Object.fromEntries(Object.entries(authoredParts).map(([name,p])=>[name,shifted(p.shape)])):
    {body:{kind:'opaque_primitives',items:[named('torso',full.body)]},head:clone(full.head),
      hand_left:clone(full.hand_left),hand_right:clone(full.hand_right),
      protection:{kind:'protected_primitives',policy_ref:clone(policy.protection.policy_ref),
        items:[named('authored-person-protection',full.protection)]}};
  shapes.work=clone(work);
  g.id='authored-desk-projection';g.available_at=proposal.available_at;g.valid_until=proposal.valid_until;
  g.clock.uncertainty_s=.001;
  for(const [name,shape] of Object.entries(shapes)){
    const p=F.part(shape,proposal.observed_at);p.valid_until=proposal.valid_until;
    p.uncertainty.bound_at_observation_m=.01;
    p.uncertainty.components_m.estimator=.01;
    p.uncertainty.age={origin_at:proposal.observed_at,max_age_s:proposal.valid_until-proposal.observed_at,
      max_speed_m_s:.01,max_accel_m_s2:.01};
    g.parts[name]=p;
  }
  return g;
}
const geometry=projectedGeometry();
function current(t,empty=false){
  const i=F.input(t),g=clone(geometry),r=ref(g,10);
  i.geometries=empty?[]:[g];i.geometry_refs=empty?[]:[r];i.goal.geometry_refs=empty?[]:[clone(r)];
  if(!empty){i.goal.valid_until=proposal.valid_until;i.scene.valid_until=proposal.valid_until;}
  return {...i,facts:[],lifecycles:[],publication_refs:empty?{}:{[g.id]:r},pending_clearance_intents:[],intent_refs:{}};
}
const d=Delivery.createDelivery({identity:F.identity,policy,lifecycle_policy:lifecycle,max_output_age_s:3});
const first=d.step(current(98,true)),at=99;
const value={...clone(lifecycle.operator_clearance),inspection_at:at,hazard_refs:first.person_hazards.items.map(h=>clone(h.hazard_ref))};
delete value.max_age_s;
const clear={schema:'home-live-intent-event-2',version:2,id:'intent:authored-clearance',...F.identity,
  manifest_ref:pin('authored-manifest'),actor_ref:'authored-owner',origin:'human_declaration',truth_role:'runtime_intent',
  kind:'clear_person_hazards',target:null,set_at:at,available_at:at,valid_until:at+5,reply_to:null,value};
const clearance=d.step({...current(at,true),pending_clearance_intents:[clear],intent_refs:{[clear.id]:ref(clear,99)}});
if(clearance.intent_results[0]?.status!=='applied')throw Error('authored bootstrap not reconciled');
const start=d.step(current(100.5)),later=d.step(current(101.5));
process.stdout.write(JSON.stringify({classification:'authored_candidate_feasibility_only',operational_admission:false,
  allocator_version:d.snapshot().version,policy_digest:L.digest(policy),parts:Object.keys(geometry.parts).sort(),
  work_shape:geometry.parts.work.shape,head_shape:geometry.parts.head.shape,
  start:{allocation:start.allocation,execution:start.execution},later:{allocation:later.allocation,execution:later.execution}}));
"""


def proposal():
    """A fictional work patch inside the independently authored reach envelope."""
    candidate, assessment, anchor, binding = authored.inputs()
    survey = deepcopy(candidate["original_survey"])
    measure = authored.fixture.measurement
    survey["work_patch"]["outer_corners"] = [measure(1.3, 1.05, .75), measure(1.7, 1.45, .75)]
    candidate = authored.fixture.guide.prepare_candidate(survey, now=lambda: 90)
    assessment["candidate_digest"] = binding["candidate_digest"] = candidate["digest"]
    assessment["enclosure"] = deepcopy(candidate["seated_person_envelope"])
    binding["assessment_ref"]["digest"] = digest(assessment)
    return evaluate_desk_enclosure(candidate, binding=binding, assessment=assessment,
                                   anchor=anchor, now=100.5, hazards=[])


class DeskEnclosureFeasibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if cls.node is None:
            raise RuntimeError("existing Node runtime required; no install or false pass")

    def allocate(self, result, *, distinct=False):
        self.assertEqual(result["status"], "eligible_for_admission_review")
        self.assertIsNone(result["operational_geometry"])
        for key in ("journal_authority", "engagement_authority", "beam_authority", "physical_authority"):
            self.assertFalse(result[key])
        run = subprocess.run([self.node, "--max-old-space-size=128", "-e", ALLOCATE], cwd=ROOT,
            input=json.dumps({"proposal": result["geometry_proposal"], "independent_distinct_parts": distinct}),
            text=True, capture_output=True, timeout=8)
        self.assertEqual(run.returncode, 0, run.stderr[:4000])
        self.assertLess(len(run.stdout), 32768)
        checked = json.loads(run.stdout)
        self.assertEqual(checked["classification"], "authored_candidate_feasibility_only")
        self.assertIs(checked["operational_admission"], False)
        self.assertEqual(checked["allocator_version"], "home-live-task-delivery-4.0.0")
        self.assertTrue({"work", "body", "head", "hand_left", "hand_right", "protection"} <= set(checked["parts"]))
        return checked

    def test_full_reach_box_as_head_and_protection_blocks_the_actual_allocator(self):
        result = proposal()
        parts = result["geometry_proposal"]["parts"]
        self.assertTrue(all(parts[name] == parts["protection"] for name in parts))
        checked = self.allocate(result)
        for phase in ("start", "later"):
            self.assertEqual(checked[phase]["allocation"]["status"], "unavailable")
            self.assertEqual(checked[phase]["allocation"]["reason"], "protected_volume_intersection")
            self.assertEqual(checked[phase]["execution"]["status"], "unavailable")

    def test_independently_distinct_authored_parts_can_be_feasible_without_policy_relaxation(self):
        result = proposal()
        original = deepcopy(result)
        blocked = self.allocate(result)
        checked = self.allocate(result, distinct=True)
        self.assertEqual(checked["policy_digest"], blocked["policy_digest"])
        self.assertEqual(checked["work_shape"], blocked["work_shape"])
        self.assertNotEqual(checked["head_shape"], blocked["head_shape"])
        self.assertEqual(checked["start"]["allocation"]["status"], "allocated")
        self.assertEqual(checked["later"]["execution"]["status"], "virtual")
        self.assertGreater(checked["later"]["execution"]["readback"][0]["brightness_fraction"], 0)
        self.assertLessEqual(checked["later"]["allocation"]["valid_until"], result["geometry_proposal"]["valid_until"])
        self.assertEqual(result, original, "diagnostic projection must not upgrade or alter the candidate")


if __name__ == "__main__":
    unittest.main()
