import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from home_shared.adapter import Frame
from home_shared.on_demand import OnDemandRefresh
from home_shared.submissions import SubmissionJournal


class OnDemandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = SubmissionJournal(Path(self.directory.name)/'journal.sqlite', device_id='den-adapter',
            gateway_identity='ingress', max_records=100, free_space_floor_bytes=0)
        self.addCleanup(self.journal.close)
        self.calls, self.published = [], []
        self.fail = False
        def submit(payload):
            self.calls.append(payload)
            if self.fail:
                raise TimeoutError('unknown delivery')
            keys=('site_id','device_id','camera_id','room_id','producer_session','producer_sequence',
                  'occupancy_generation','capture_session','frame_id')
            observation={k:payload[k] for k in keys}
            observation.update(observed_at=1000,valid_until=1180,configuration_revision='config',
                               model_revision='model',description='Empty room.',uncertainty_seconds=.1)
            return dict(version=1,site_id='victoria',request_id=payload['request_id'],status='terminal',
                        outcome='completed',observation=observation)
        gateway=SimpleNamespace(gateway_identity='ingress',submit=submit,
            config=SimpleNamespace(device_id='den-adapter',configuration_revision='config',cameras={'den_oak':'den'}))
        self.backend=OnDemandRefresh(camera=lambda:(b'jpeg','header'),gateway=gateway,journal=self.journal,
            caption_writer=lambda value,**kw:self.published.append(value),model_revision='model',
            configuration_revision='config',clock=lambda:1001)
        self.frame=patch('home_shared.on_demand.camera_frame',return_value=Frame('capture','frame',1000,0,.1,'map',b'jpeg'))
        self.frame.start()
        self.addCleanup(self.frame.stop)
        self.namespace='a'*64
        self.rid='12345678-1234-1234-1234-123456789abc'

    async def test_explicit_empty_room_without_mqtt_and_duplicate_id_no_replay(self):
        receipt=await self.backend.accept(self.namespace,self.rid)
        self.assertEqual(receipt['state'],'queued')
        await self.backend.task
        self.assertEqual(len(self.published),1)
        self.assertEqual(self.published[0]['request_id'],self.rid)
        again=await self.backend.accept(self.namespace,self.rid)
        self.assertEqual(again['state'],'terminal')
        self.assertEqual(len(self.calls),1)
        self.assertIsNone(await self.backend.status('b'*64,self.rid))

    async def test_uncertain_delivery_retains_reservation_and_blocks_new_work(self):
        self.fail=True
        await self.backend.accept(self.namespace,self.rid)
        await self.backend.task
        self.assertIsNotNone(self.journal.outstanding())
        self.assertEqual(self.published,[])
        with self.assertRaises(ValueError):
            await self.backend.accept(self.namespace,'22345678-1234-1234-1234-123456789abc')
        await self.backend.accept(self.namespace,self.rid)
        self.assertEqual(len(self.calls),1)

    async def test_camera_failure_never_dispatches_or_publishes_old_caption(self):
        def unavailable():
            raise ConnectionError('camera unavailable')
        self.backend.camera=unavailable
        await self.backend.accept(self.namespace,self.rid)
        await self.backend.task
        self.assertEqual(self.calls,[])
        self.assertEqual(self.published,[])
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual((await self.backend.status(self.namespace,self.rid))['outcome'],'client_not_sent')

    async def test_recovery_queries_original_id_without_replaying_or_publishing(self):
        self.fail=True
        await self.backend.accept(self.namespace,self.rid)
        await self.backend.task
        lookups=[]
        def status(request_id):
            lookups.append(request_id)
            return dict(version=1,site_id='victoria',request_id=request_id,status='terminal',outcome='completed')
        self.backend.gateway.status=status
        await self.backend.reconcile()
        self.assertEqual(lookups,[self.rid])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.published,[])
        self.assertIsNone(self.journal.outstanding())

    async def test_restart_keeps_completed_request_cooldown(self):
        await self.backend.accept(self.namespace,self.rid)
        await self.backend.task
        await self.backend.close()
        self.journal.close()
        reopened=SubmissionJournal(Path(self.directory.name)/'journal.sqlite',device_id='den-adapter',
            gateway_identity='ingress',max_records=100,free_space_floor_bytes=0)
        self.addCleanup(reopened.close)
        backend=OnDemandRefresh(camera=self.backend.camera,gateway=self.backend.gateway,journal=reopened,
            caption_writer=self.backend.caption_writer,model_revision='model',configuration_revision='config',clock=lambda:1002)
        with self.assertRaises(ValueError):
            await backend.accept(self.namespace,'22345678-1234-1234-1234-123456789abc')
        self.assertEqual(len(self.calls),1)

    async def test_exact_image_namespace_expiry_and_shutdown(self):
        await self.backend.accept(self.namespace,self.rid)
        await self.backend.task
        value=await self.backend.image(self.namespace,self.rid)
        self.assertEqual(value['image_base64'],self.calls[0]['image_base64'])
        self.assertEqual(value['frame_id'],self.published[0]['frame_id'])
        self.assertIsNone(await self.backend.image('b'*64,self.rid))
        self.assertIsNone(await self.backend.image(self.namespace,'22345678-1234-1234-1234-123456789abc'))
        self.backend.clock=lambda:1180
        self.assertIsNone(await self.backend.image(self.namespace,self.rid))
        self.assertIsNone(self.backend.snapshot)
        await self.backend.close()
        self.assertIsNone(self.backend.snapshot_timer)

    async def test_unknown_execution_never_exposes_image(self):
        self.fail=True
        await self.backend.accept(self.namespace,self.rid)
        await self.backend.task
        self.assertIsNone(await self.backend.image(self.namespace,self.rid))
