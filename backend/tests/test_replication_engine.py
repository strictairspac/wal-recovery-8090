import unittest

from src.services import replication_engine as engine

INITIAL = {'sample_A': 100, 'sample_B': 50}
RECORDS = [
    {'op': 'begin', 'tx': 'T1'},
    {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 75},
    {'op': 'commit', 'tx': 'T1'},
    {'op': 'checkpoint'},
    {'op': 'begin', 'tx': 'T2'},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 40},
    {'op': 'begin', 'tx': 'T3'},
    {'op': 'write', 'tx': 'T3', 'key': 'sample_A', 'value': 60},
    {'op': 'commit', 'tx': 'T3'},
    {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 35},
    {'op': 'commit', 'tx': 'T2'},
]


class BuildPlanTest(unittest.TestCase):
    def test_only_transactions_committed_before_crash(self):
        plan = engine.build_plan(INITIAL, RECORDS, 10)
        self.assertEqual([tx['tx'] for tx in plan['committed']], ['T1', 'T3'])
        self.assertEqual([tx['commit_lsn'] for tx in plan['committed']], [3, 9])
        self.assertEqual([tx['tx'] for tx in plan['excluded']], ['T2'])
        self.assertEqual(plan['excluded'][0]['reason'], 'active_at_crash')
        self.assertEqual(plan['excluded'][0]['commit_after_crash_lsn'], 11)

    def test_commit_order_not_begin_order(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        self.assertEqual([(tx['tx'], tx['begin_lsn'], tx['commit_lsn']) for tx in plan['committed']],
                         [('T1', 1, 3), ('T3', 7, 9), ('T2', 5, 11)])

    def test_aborted_transaction_excluded(self):
        records = [
            {'op': 'begin', 'tx': 'T1'},
            {'op': 'write', 'tx': 'T1', 'key': 'sample_A', 'value': 1},
            {'op': 'abort', 'tx': 'T1'},
            {'op': 'begin', 'tx': 'T2'},
            {'op': 'write', 'tx': 'T2', 'key': 'sample_B', 'value': 2},
            {'op': 'commit', 'tx': 'T2'},
        ]
        plan = engine.build_plan(INITIAL, records, 6)
        self.assertEqual([tx['tx'] for tx in plan['committed']], ['T2'])
        self.assertEqual(plan['excluded'][0]['reason'], 'aborted_before_crash')

    def test_crash_point_bounds(self):
        with self.assertRaises(ValueError):
            engine.build_plan(INITIAL, RECORDS, len(RECORDS) + 1)
        with self.assertRaises(ValueError):
            engine.build_plan(INITIAL, RECORDS, -1)


class LedgerTest(unittest.TestCase):
    def test_whole_transaction_by_commit_order(self):
        plan = engine.build_plan(INITIAL, RECORDS, 10)
        self.assertEqual(engine.expected_ledger(INITIAL, plan['committed'], 0), INITIAL)
        self.assertEqual(engine.expected_ledger(INITIAL, plan['committed'], 1), {'sample_A': 75, 'sample_B': 50})
        self.assertEqual(engine.expected_ledger(INITIAL, plan['committed'], 2), {'sample_A': 60, 'sample_B': 50})

    def test_commit_order_matches_lsn_order(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        for applied in range(len(plan['committed']) + 1):
            self.assertEqual(
                engine.expected_ledger(INITIAL, plan['committed'], applied),
                engine.lsn_order_ledger(INITIAL, plan['committed'], applied),
            )

    def test_progress_bounds(self):
        plan = engine.build_plan(INITIAL, RECORDS, 10)
        with self.assertRaises(ValueError):
            engine.expected_ledger(INITIAL, plan['committed'], 3)
        with self.assertRaises(ValueError):
            engine.expected_ledger(INITIAL, plan['committed'], -1)


class CutTest(unittest.TestCase):
    def test_no_reclaim_before_any_shard_applied(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        cut = engine.compute_cut(plan, [0, 0], 0)
        self.assertEqual(cut['truncated_lsn'], 0)
        self.assertFalse(cut['changed'])
        self.assertEqual(cut['reason'], 'slowest_replica_pending')

    def test_cut_stops_before_pending_transaction(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        cut = engine.compute_cut(plan, [3, 2], 0)
        self.assertEqual(cut['truncated_lsn'], 4)
        self.assertEqual(cut['boundary_tx']['tx'], 'T2')

    def test_cut_never_splits_transaction(self):
        plan = engine.build_plan(INITIAL, RECORDS, 9)
        cut = engine.compute_cut(plan, [1, 1], 0)
        self.assertEqual(cut['truncated_lsn'], 6)
        for tx in plan['transactions']:
            self.assertFalse(tx['begin_lsn'] <= cut['truncated_lsn'] < tx['end_lsn'])

    def test_full_reclaim_when_all_caught_up(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        cut = engine.compute_cut(plan, [3, 3], 0)
        self.assertEqual(cut['truncated_lsn'], 11)
        self.assertEqual(cut['reason'], 'prefix_consumed')

    def test_cut_never_moves_backwards(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        cut = engine.compute_cut(plan, [0, 0], 11)
        self.assertEqual(cut['truncated_lsn'], 11)
        self.assertFalse(cut['changed'])

    def test_every_reclaim_keeps_pending_logs_resumable(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        for a in range(4):
            for b in range(4):
                cut = engine.compute_cut(plan, [a, b], 0)
                for applied in (a, b):
                    state = engine.availability(plan['committed'], applied, cut['truncated_lsn'])
                    self.assertTrue(state['resume_safe'], (a, b, applied))

    def test_retained_records_are_an_intact_suffix(self):
        plan = engine.build_plan(INITIAL, RECORDS, 11)
        cut = engine.compute_cut(plan, [3, 2], 0)
        rows = engine.remaining_records(RECORDS, cut['truncated_lsn'], 11)
        self.assertEqual([row['lsn'] for row in rows], [5, 6, 7, 8, 9, 10, 11])


if __name__ == '__main__':
    unittest.main()
