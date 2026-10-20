"""The fixed managed child propagates failure independently of its exit code."""
import unittest
import test_runtime as runtime


class BlockingRunnerTests(unittest.TestCase):
    fixture_factory=staticmethod(runtime.ActualOrdinaryReplayTests.fixture_factory)
    setUp=runtime.ActualOrdinaryReplayTests.setUp

    def test_lost_read_authority_is_visible_and_cannot_report_a_settled_drain(self):
        self.assertEqual(self.driver.status()['status'],'ready')
        self.journal.grants=()
        self.driver.cycle(self.now+.1)
        status=self.driver.status()
        self.assertEqual(status['status'],'failed',status)
        self.assertIsInstance(status['reason'],str)
        self.assertEqual(self.driver.drain(deadline_ms=200,through_cursor=1)['status'],'unconfirmed')
        self.assertIsNone(self.journal.db.execute('SELECT checkpoint FROM workers').fetchone())


if __name__=='__main__':unittest.main()
