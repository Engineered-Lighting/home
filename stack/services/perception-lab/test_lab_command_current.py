"""Actual source5 currentness remains local while Journal is held."""
import unittest
from unittest.mock import patch
import lab_stereo_mount_source as mounted
from test_lab_existing_api_mount_source import ExistingApiMountedSourceTests


class LabCommandCurrentTests(unittest.TestCase):
    def test_current_guard_has_no_health_probe_and_refuses_retired_tap(self):
        fixture=ExistingApiMountedSourceTests();self.addCleanup(fixture.doCleanups);fixture.setUp()
        source=fixture.source
        with patch.object(mounted,'read_private_record',side_effect=fixture.read_record):
            composition=mounted.stereo_runtime_composition(source.producer,fixture.config,
                store=source.f.store,preflight=lambda:True,control_clock=source.control_clock,
                qualification_authority=fixture.authority,incumbent_runtime=source.f.runtime,
                clock=lambda:source.f.now)
        composition.monotonic=source.composition.monotonic
        source.composition=composition;source.capability=composition.desk_capability
        source.anchor=source.capability.anchor_source;source.media=source.capability.media_archive
        graph=source.create()
        with patch.object(composition,'preflight',side_effect=AssertionError('health_probe_under_Journal')):
            with graph.journal.lock:
                self.assertTrue(composition.command_current(graph))
                with patch.object(source.f.runtime,'existing_api_assessment',None),self.assertRaises(ValueError):
                    composition.command_current(graph)


if __name__=='__main__':unittest.main()
