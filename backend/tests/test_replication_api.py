import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _app_client():
    from fastapi.testclient import TestClient
    from src.config.database import Base, engine
    from src.main import app
    import scripts.seed_demo as seed

    Base.metadata.create_all(engine)
    seed.seed()
    return TestClient(app)


class ReplicationApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = _app_client()

    def login(self, email):
        response = self.client.post('/auth/login', json={'email': email, 'password': 'Recovery2026!'})
        self.assertEqual(response.status_code, 200, response.text)
        return {'Authorization': 'Bearer ' + response.json()['access_token']}

    def case_id(self, headers, crash_after):
        cases = self.client.get('/crash-cases', headers=headers).json()
        return next(item for item in cases if item['crash_after'] == crash_after)['id']

    def make_replication(self, headers, crash_after=10):
        response = self.client.post('/replications', headers=headers, json={
            'crash_case_id': self.case_id(headers, crash_after), 'label': '测试双分库',
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def test_shards_advance_independently(self):
        op = self.login('operator@haili.local')
        rid = self.make_replication(op)
        first = self.client.post(f'/replications/{rid}/shards/a/advance', headers=op, json={'limit': 1}).json()
        self.assertEqual(first['to_count'], 1)
        self.assertEqual(first['ledger'], {'sample_A': 75, 'sample_B': 50})
        detail = self.client.get(f'/replications/{rid}', headers=op).json()
        self.assertEqual(detail['replicas']['b']['applied_count'], 0)
        second = self.client.post(f'/replications/{rid}/shards/b/advance', headers=op, json={}).json()
        self.assertEqual(second['to_count'], 2)
        self.assertTrue(second['ledger_matches_progress'])

    def test_lost_receipt_retry_is_idempotent(self):
        op = self.login('operator@haili.local')
        rid = self.make_replication(op)
        loss = self.client.post(f'/replications/{rid}/shards/b/advance', headers=op,
                                json={'simulate_receipt_loss': True}).json()
        self.assertFalse(loss['acknowledged'])
        self.assertEqual(loss['lost_receipt']['tx'], 'T1')
        retry = self.client.post(f'/replications/{rid}/shards/b/advance', headers=op,
                                 json={'receipt': 'T1'}).json()
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['to_count'], 1)
        self.assertEqual(retry['ledger'], {'sample_A': 75, 'sample_B': 50})
        again = self.client.post(f'/replications/{rid}/shards/b/advance', headers=op,
                                 json={'receipt': 'T1'})
        self.assertTrue(again.json()['duplicate'])
        detail = self.client.get(f'/replications/{rid}', headers=op).json()
        self.assertEqual(detail['replicas']['b']['applied_count'], 1)

    def test_reclaim_protects_slow_shard_and_retains_intact_logs(self):
        op = self.login('operator@haili.local')
        rid = self.make_replication(op)
        self.client.post(f'/replications/{rid}/shards/a/advance', headers=op, json={'limit': 1})
        self.client.post(f'/replications/{rid}/shards/b/advance', headers=op, json={'limit': 1})
        reclaim = self.client.post(f'/replications/{rid}/reclaim', headers=op, json={}).json()
        self.assertEqual(reclaim['truncated_lsn'], 4)
        self.assertTrue(reclaim['resume_safe'])
        self.assertEqual([row['lsn'] for row in reclaim['retained_records']], [5, 6, 7, 8, 9, 10])
        # 慢分库从原进度续接，不依赖已回收的前缀。
        resumed = self.client.post(f'/replications/{rid}/shards/b/advance', headers=op, json={}).json()
        self.assertEqual(resumed['to_count'], 2)
        self.assertEqual(resumed['ledger'], {'sample_A': 60, 'sample_B': 50})
        final = self.client.post(f'/replications/{rid}/reclaim', headers=op, json={}).json()
        self.assertEqual(final['truncated_lsn'], 4)  # a 仍落后，回收不再前进
        self.client.post(f'/replications/{rid}/shards/a/advance', headers=op, json={})
        done = self.client.post(f'/replications/{rid}/reclaim', headers=op, json={}).json()
        self.assertEqual(done['truncated_lsn'], 10)
        self.assertEqual(done['retained_records'], [])

    def test_dry_run_reclaim_does_not_change_position(self):
        op = self.login('operator@haili.local')
        rid = self.make_replication(op)
        self.client.post(f'/replications/{rid}/shards/a/advance', headers=op, json={})
        self.client.post(f'/replications/{rid}/shards/b/advance', headers=op, json={})
        preview = self.client.post(f'/replications/{rid}/reclaim', headers=op, json={'dry_run': True}).json()
        self.assertEqual(preview['reclaim']['candidate_lsn'], 10)
        self.assertEqual(preview['truncated_lsn'], 0)
        self.assertFalse(preview['recorded'])

    def test_viewer_is_read_only(self):
        viewer = self.login('viewer@haili.local')
        op = self.login('operator@haili.local')
        rid = self.make_replication(op)
        self.assertEqual(self.client.post(f'/replications/{rid}/shards/a/advance',
                                          headers=viewer, json={}).status_code, 403)
        self.assertEqual(self.client.post(f'/replications/{rid}/reclaim',
                                          headers=viewer, json={}).status_code, 403)
        self.assertEqual(self.client.get(f'/replications/{rid}', headers=viewer).status_code, 200)

    def test_reclaim_audit_and_retained_endpoint(self):
        admin = self.login('admin@haili.local')
        op = self.login('operator@haili.local')
        rid = self.make_replication(op)
        self.client.post(f'/replications/{rid}/shards/a/advance', headers=op, json={})
        self.client.post(f'/replications/{rid}/shards/b/advance', headers=op, json={})
        self.client.post(f'/replications/{rid}/reclaim', headers=op, json={})
        retained = self.client.get(f'/replications/{rid}/retained', headers=op).json()
        self.assertEqual(retained['truncated_lsn'], 10)
        self.assertEqual(retained['records'], [])
        actions = [row['action'] for row in self.client.get('/audits', headers=admin).json()]
        self.assertTrue(any(action.startswith(f'replication:reclaim:{rid}:10') for action in actions))


if __name__ == '__main__':
    unittest.main()
