import itertools
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import stat
from home_shared.chrony_publisher import daemon_identity
from home_shared.chrony_clock import ChronyClockConfig
from home_shared.chrony_publisher import ChronyPublisher
from home_shared.ntp_clock import CameraHostClockEvidence
from home_shared.host_clock_service import _Point
from test_host_clock_service import config


class Store:
    def __init__(self, config):
        self.value=None
    def _atomic(self, name, value, check):
        check(); self.value=value
    def _clear(self):
        self.value=None
    def close(self):
        pass


class PublisherTests(unittest.TestCase):
    def test_daemon_identity_uses_provisioned_unit_and_rejects_other_clock(self):
        executable='/opt/victoria-clock/chrony-4.2/usr/sbin/chronyd'
        report=('ActiveState=active\nMainPID=123\nExecMainStartTimestampMonotonic=456\n'
                'FragmentPath=/etc/systemd/system/victoria-clock-pilot.service\n'
                'ExecStart={ path='+executable+' ; argv[]='+executable+
                ' -U -u systemd-timesync -d -R -f /opt/victoria-clock/chrony-pilot.conf ; }\n')
        info=SimpleNamespace(st_mode=stat.S_IFREG|0o755,st_uid=0)
        for changed,original,accepted in ((report,'inactive',True),
                                          (report,'active',False),
                                          (report.replace('MainPID=123','MainPID=0'),'inactive',False),
                                          (report.replace(' -R -f ',' -f '),'inactive',False)):
            with self.subTest(original=original,accepted=accepted):
                with patch('home_shared.chrony_publisher.subprocess.run',side_effect=[
                        SimpleNamespace(stdout=changed),SimpleNamespace(stdout=original)]), \
                        patch('home_shared.chrony_publisher.Path.lstat',return_value=info):
                    if accepted:
                        self.assertEqual(daemon_identity(),('123','456'))
                    else:
                        with self.assertRaises(ValueError): daemon_identity()

    def make(self, **kwargs):
        cfg=config()
        policy=ChronyClockConfig(cfg.ntp.server_ip,4096,cfg.ntp.local_clock_quantization_ns,
                                cfg.ntp.drift_slew_ppm,.5,cfg.ntp.valid_for_seconds,cfg.ntp.max_uncertainty_seconds)
        evidence=CameraHostClockEvidence('boot','chrony:test',1000,1000000,1000000000,.1)
        ticks=itertools.count(2000,1000)
        def point():
            tick=next(ticks)
            return _Point(tick,tick+1000,tick+999000,tick+10)
        defaults=dict(_store=Store,_sample=lambda _: evidence,_identity=lambda: ('pid','start'),
                      _point=point,_boot=lambda:'boot')
        defaults.update(kwargs)
        return ChronyPublisher(cfg,policy,**defaults)

    def test_original_receipt_and_one_shot(self):
        publisher=self.make()
        value=publisher.publish_once()
        self.assertEqual(value['checked_monotonic_ns'],1000)
        self.assertEqual(publisher._store.value,value)
        with self.assertRaises(ValueError): publisher.publish_once()

    def test_expiry_and_daemon_change_clear_receipt(self):
        identities=iter((('pid','old'),('pid','new')))
        for changes in ({'_point':lambda:_Point(2000000000,2000001000,2000999000,2000000010)},
                        {'_identity':lambda:next(identities)}, {'_boot':lambda:'other'}):
            with self.subTest(changes=changes):
                publisher=self.make(**changes)
                with self.assertRaises(ValueError): publisher.publish_once()
                self.assertIsNone(publisher._store.value)

    def test_close_cannot_release_lock_during_query(self):
        entered, release = threading.Event(), threading.Event()
        def query(_):
            entered.set()
            release.wait(2)
            return CameraHostClockEvidence('boot','chrony:test',1000,1000000,1000000000,.1)
        publisher=self.make(_sample=query)
        failures=[]
        def publish():
            try: publisher.publish_once()
            except Exception as error: failures.append(error)
        worker=threading.Thread(target=publish)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(RuntimeError): publisher.close()
            with self.assertRaises(RuntimeError): publisher.publish_once()
        finally:
            release.set();worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures,[])
        publisher.close()


if __name__ == '__main__':
    unittest.main()
