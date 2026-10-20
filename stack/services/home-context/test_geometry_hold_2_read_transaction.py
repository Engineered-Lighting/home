"""Actual saved consumer cuts/originals inside atomic G2 reconstruction."""
import unittest

from journal import JournalError
from test_geometry_hold_2_carrier_diagnostic import CarrierHost


class AtomicReadTests(unittest.TestCase):
    def setUp(self):
        self.host = CarrierHost(views=True)
        self.addCleanup(self.host.close)
        self.host.accept(self.host.prepare_bootstrap())
        self.host.accept(self.host.prepare_boundary(0))
        self.reader = next(grant for grant in self.host.j.grants if grant.operations == frozenset({"read"}))
        self.profile = self.host.j.dynamic
        self.scope = (self.profile.identity["site_id"], self.profile.identity["room_id"])
        self.cut = self.profile.read_cut(self.reader, *self.scope, consumer_id=self.reader.expected_consumer_id)
        self.assertTrue(self.cut["snapshot"]["items"], "cut must contain actual admitted originals")
        self.cursor = self.cut["snapshot"]["items"][0]["cursor"]

    def test_saved_cut_reconstruction_reuses_held_transaction(self):
        result = self.host.j._transaction(lambda: self.profile.read_cut(self.reader, *self.scope,
            consumer_id=self.reader.expected_consumer_id, cut_id=self.cut["cut_id"]))
        self.assertEqual(result, self.cut)

    def test_original_reconstruction_preserves_genuine_consumer_FIRST(self):
        original = self.profile.lookup_original(self.reader, *self.scope, cursor=self.cursor,
            consumer_id=self.reader.expected_consumer_id)
        self.assertIsNotNone(original["receipt"])
        result = self.host.j._transaction(lambda: self.profile.lookup_original(self.reader, *self.scope,
            cursor=self.cursor, consumer_id=self.reader.expected_consumer_id))
        self.assertEqual(result, original)

    def test_new_cut_delivery_changes_rollback_with_outer_admission(self):
        before = list(self.host.j.db.iterdump())
        self.host.clock.advance(.01)
        created = []
        def abort():
            created.append(self.profile.read_cut(self.reader, *self.scope,
                consumer_id=self.reader.expected_consumer_id))
            raise JournalError("fixture_abort", "rollback the scoped fictional reconstruction", 409)
        with self.assertRaises(JournalError) as caught:
            self.host.j._transaction(abort)
        self.assertEqual(caught.exception.code, "fixture_abort")
        self.assertEqual(created[0]["as_of_t"], .01)
        self.assertNotEqual(created[0]["cut_id"], self.cut["cut_id"])
        self.assertEqual(list(self.host.j.db.iterdump()), before)
        with self.assertRaises(JournalError):
            self.profile.read_cut(self.reader, *self.scope, consumer_id=self.reader.expected_consumer_id,
                cut_id=created[0]["cut_id"])


if __name__ == "__main__":
    unittest.main()
