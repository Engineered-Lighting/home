"""Actual assessment-install/pin methods with thread barriers, no native I/O.

This isolates the after-ACK/before-reserve race. It does not claim a real
recorder ACK, accepted room commit, compiler qualification or dispatch.
"""
from copy import deepcopy
from types import SimpleNamespace
import threading
import unittest

from room_command_projection import RETAINED_TASK_PROFILE1
from room_fixture_command_join import RoomCommandStateResolver, RoomRetainedTaskAssessment
import test_fixture_joint_execution as joint
from test_fixture_retained_angle_execution import retained_request


class RetainedTaskAssessmentFenceTests(unittest.TestCase):
    def resolver(self):
        # Only metadata locking methods are exercised here; the real resolver
        # constructor/source/native checks belong to the combined task gate.
        resolver=object.__new__(RoomCommandStateResolver)
        resolver.projector=SimpleNamespace(task_profile=deepcopy(RETAINED_TASK_PROFILE1))
        resolver.task_compiler=object()
        resolver.task_assessment=None
        resolver._task_assessment_lock=threading.RLock()
        resolver._task_assessment_pending=None
        first=RoomRetainedTaskAssessment(retained_request(joint.joint_fixture()))
        replacement=first.original();replacement['assessment']['minimum_work_range_m']=.9
        resolver.install_task_assessment(first)
        return resolver,first,RoomRetainedTaskAssessment(replacement)

    def test_original_swap_after_ack_before_reservation_is_rejected(self):
        resolver,first,replacement=self.resolver()
        ack,attempted,release=threading.Event(),threading.Event(),threading.Event()
        rows,errors=[],[]
        def worker():
            pending=resolver._pin_task_assessment('task-1')
            try:
                rows.append(resolver._task_assessment_original())
                ack.set()
                if not release.wait(2):raise AssertionError('test_barrier_timeout')
                pending['reservation_attempted']=True
                rows.append(resolver._task_assessment_original())
            except BaseException as error:errors.append(error)
            finally:resolver._finish_task_assessment(pending)
        thread=threading.Thread(target=worker);thread.start()
        try:
            self.assertTrue(ack.wait(2))
            with self.assertRaisesRegex(ValueError,'retained_task_assessment_pending'):
                resolver.install_task_assessment(replacement)
            attempted.set()
            self.assertIs(resolver.task_assessment,first)
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors,errors)
        self.assertTrue(attempted.is_set())
        self.assertEqual(rows,[first.original(),first.original()])
        resolver.install_task_assessment(replacement)
        self.assertIs(resolver.task_assessment,replacement)

    def test_unknown_reserved_outcome_keeps_original_pinned_until_explicit_settlement(self):
        resolver,first,replacement=self.resolver()
        pending=resolver._pin_task_assessment('task-1');pending['reservation_attempted']=True
        for operation in (lambda:resolver.install_task_assessment(replacement),
                          lambda:resolver._pin_task_assessment('task-2')):
            with self.assertRaisesRegex(ValueError,'retained_task_assessment_pending'):operation()
        self.assertEqual(resolver._task_assessment_original(),first.original())
        # Mutation through an accidental private reference is detected too;
        # pinning never turns a changed body into the acknowledged original.
        first.request['assessment']['minimum_work_range_m']=1
        with self.assertRaisesRegex(ValueError,'original_changed'):resolver._task_assessment_original()
        resolver._finish_task_assessment(pending)
        resolver.install_task_assessment(replacement)
        self.assertEqual(resolver._task_assessment_original(),replacement.original())


if __name__=='__main__':unittest.main()
