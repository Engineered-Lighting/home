"""Explicit combined-profile admission, through existing public boundaries."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from journal import Journal, JournalError
from schema_registry import SchemaRegistry, LIVE_PROFILE


class CombinedDeclarationSchemaTests(unittest.TestCase):
    def test_explicit_declaration_schema_does_not_reinterpret_historical_intent(self):
        registry=SchemaRegistry([],profile=LIVE_PROFILE)
        scope=dict(site_id='authored-site',room_id='authored-room',session_id='authored-session',domain_epoch='authored-domain')
        proposal='clarification:'+'a'*64
        reply=dict(schema='home-live-intent-event-3',version=3,id='authored-activity-reply',**scope,
            manifest_ref={'id':'authored-manifest','digest':'b'*64},actor_ref='authored-owner',
            origin='human_declaration',truth_role='runtime_intent',kind='activity_reply',
            target={'person_ref':{**scope,'track_epoch':'authored-track','pid':1},
                'subject_ref':{'subject_id':'authored-material','subject_epoch':'authored-material-epoch'},
                'episode_id':None,'scene_id':None,'pending_ref':proposal},
            set_at=10,available_at=10,valid_until=40,reply_to=proposal,value={'answer':'using_material'})
        registry.validate(reply)
        historical=deepcopy(reply)
        historical.update(schema='home-live-intent-event-2',version=2)
        with self.assertRaises(JournalError):registry.validate(historical)

    def test_historical_commissioning_cannot_expand_into_activity_reply_authority(self):
        from live_conservative_test_helpers import conservative_fixture
        from schema_registry import schema_digest
        configuration,grants,_=conservative_fixture()
        human=next(grant for grant in grants if 'intent' in grant['operations'])
        human['payload_types'].append('home-live-intent-event-3')
        human['schema_pins']['home-live-intent-event-3']=schema_digest('home-live-intent-event-3',profile=LIVE_PROFILE)
        human['revisions'].append('3.0')
        with tempfile.TemporaryDirectory(prefix='authored-future-grant-',dir=Path(__file__).parent) as directory:
            ledger=Path(directory)/'must-not-open.sqlite'
            try:
                journal=Journal(ledger,grants=grants,profile=LIVE_PROFILE,live_config=configuration)
            except ValueError:
                pass
            else:
                journal.close()
                self.fail('historical profile admitted future declaration authority')
            self.assertFalse(ledger.exists())


if __name__=='__main__':unittest.main()
