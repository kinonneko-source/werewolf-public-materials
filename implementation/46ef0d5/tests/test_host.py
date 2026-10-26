import json
import tempfile
import unittest
import uuid
from pathlib import Path
from werewolf.engine import Engine, RuleError
from werewolf.host import KrowHost


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'host.db'
        self.now = 1000
        self.engine = Engine(self.path, clock=lambda: self.now)
        self.bindings = {str(uuid.uuid4()): p for p in 'ABCDEF'}
        self.host_id = str(uuid.uuid4())
        self.config = dict(mode='verification', label='本组验证局', authorization='task:authorized',
                           host_id=self.host_id, channel_id='team-channel', bindings=self.bindings,
                           routes={uid: seat for uid, seat in self.bindings.items()}, relay=None)
        self.roster = [dict(worker_id=uid, worker_name=seat) for uid, seat in self.bindings.items()]
        self.game = 'VERIFY-independent'
        self.engine.create(self.game, list('ABCDEF'), 'create', test=False, host=self.config)
        self.host = KrowHost(self.engine, self.game)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def message(self, actor, action, mid='msg1', phase=None):
        sender = next(k for k, v in self.bindings.items() if v == actor)
        return dict(from_worker_id=sender, to_worker_id=self.host_id, source_trust='first_party',
                    from_kind='worker', id=mid, body=json.dumps(dict(game_id=self.game,
                    phase_id=phase or self.engine.state(self.game)['phase_id'], action=action)))

    def expected(self):
        return self.engine.expected(self.engine.state(self.game))

    def test_persist_binding_random_roles_and_test_flags(self):
        self.assertFalse(self.engine.public(self.game)['test'])
        self.assertTrue(all(not x['body']['test'] for x in self.engine.outbox(self.game)))
        for kwargs in [dict(seed=1), dict(host=None)]:
            data = dict(test=False, host=self.config)
            data.update(kwargs)
            with self.assertRaises(RuleError):
                self.engine.create('VERIFY-other', list('ABCDEF'), 'x', **data)
        self.engine.close()
        self.engine = Engine(self.path, clock=lambda: self.now)
        self.assertEqual(self.engine.state(self.game)['host'], self.config)
        self.assertEqual(len(self.engine.audit(self.game)), 1)

    def test_identity_spoof_wrong_recipient_game_phase_no_mutation(self):
        actor = self.expected()[0]
        m = self.message(actor, {'type': 'pass'})
        for field, value in [('from_worker_id', str(uuid.uuid4())), ('to_worker_id', str(uuid.uuid4())),
                             ('source_trust', 'external'), ('from_kind', 'owner')]:
            bad = dict(m, **{field: value})
            before = self.engine.state(self.game)
            with self.assertRaises(RuleError): self.host.ingest_mail(bad)
            self.assertEqual(before, self.engine.state(self.game))
        for patch in [dict(game_id='VERIFY-other'), dict(phase_id='old'), dict(sender=self.host_id), dict(version=1)]:
            body = json.loads(m['body']); body.update(patch)
            with self.assertRaises(RuleError): self.host.ingest_mail(dict(m, body=json.dumps(body)))

    def test_ingest_duplicate_and_late(self):
        actor = self.expected()[0]
        msg = self.message(actor, {'type': 'pass'})
        self.host.ingest_mail(msg)
        with self.assertRaises(RuleError): self.host.ingest_mail(msg)
        for i, actor in enumerate(self.expected()[1:]):
            self.host.ingest_mail(self.message(actor, {'type': 'pass'}, f'next{i}'))
        self.host.tick('advance')
        with self.assertRaises(RuleError): self.host.ingest_mail(dict(msg, id='late'))

    def test_hall_requires_channel_and_secret_action_stays_private(self):
        actor = self.expected()[0]
        m = self.message(actor, {'type': 'pass'})
        hall = dict(worker_id=m['from_worker_id'], id=1, body=m['body'])
        with self.assertRaises(RuleError): self.host.ingest_hall(hall, 'other')
        with self.assertRaises(RuleError): self.host.ingest_hall(hall, 'team-channel')

    def test_dispatch_private_receipts_and_no_resend(self):
        roles = [x for x in self.engine.outbox(self.game) if x['body']['type'] == 'role']
        for row in roles:
            call = self.host.dispatch(row['id'], self.roster)
            sender = call['arguments']['to_worker']
            self.assertEqual(sender, row['recipient'])
            self.assertEqual(json.loads(call['arguments']['body']), row['body'])
            self.host.receipt(row['id'], dict(success=True, message_id='platform'+row['id'], delivered_to=row['recipient']))
            with self.assertRaises(RuleError): self.host.dispatch(row['id'], self.roster)
        public = self.engine.outbox(self.game, 'public')[0]
        call = self.host.dispatch(public['id'], self.roster)
        self.assertNotIn('role', json.loads(call['arguments']['body']))
        self.host.receipt(public['id'], dict(message_id=9, channel_id='wrong'))
        self.assertEqual(self.engine.outbox(self.game, 'public')[0]['status'], 'unknown')

    def test_restart_unknown_and_timeout(self):
        row = self.engine.outbox(self.game)[0]
        self.host.dispatch(row['id'], self.roster)
        self.engine.close()
        self.engine = Engine(self.path, clock=lambda: self.now)
        self.host = KrowHost(self.engine, self.game)
        self.assertEqual(self.host.recover()['unknown'], [row['id']])
        with self.assertRaises(RuleError): self.host.dispatch(row['id'], self.roster)
        self.now += 61
        with self.assertRaises(RuleError):
            self.host.ingest_mail(self.message(self.expected()[0], {'type': 'pass'}))
        self.assertTrue(self.host.tick('timeout')['advanced'])
        self.assertEqual(self.engine.state(self.game)['last_absent'], [p for p in 'ABCDEF' if self.engine.state(self.game)['roles'][p] == 'wolf'])


    def test_player_host_requires_relay_and_only_relay_can_confirm(self):
        config = dict(self.config, host_id=next(iter(self.bindings)))
        with self.assertRaises(RuleError):
            self.engine.create('VERIFY-no-relay', list('ABCDEF'), 'no-relay', test=False, host=config)
        relay_id = str(uuid.uuid4())
        config['relay'] = dict(worker_id=relay_id, worker_name='leader')
        game = 'VERIFY-relay'
        self.engine.create(game, list('ABCDEF'), 'relay-create', test=False, host=config)
        host = KrowHost(self.engine, game)
        roster = self.roster + [dict(worker_id=relay_id, worker_name='leader')]
        seat = self.bindings[config['host_id']]
        row = next(r for r in self.engine.outbox(game) if r['recipient'] == seat and r['body']['type'] == 'role')
        call = host.dispatch(row['id'], roster)
        self.assertEqual(call['arguments']['to_worker'], 'leader')
        packet = json.loads(call['arguments']['body'])['werewolf_relay']
        self.assertEqual(packet['body'], row['body'])
        host.receipt(row['id'], dict(success=True, message_id='firsthop', delivered_to='leader'))
        self.assertEqual(next(r for r in self.engine.outbox(game) if r['id'] == row['id'])['status'], 'unknown')
        msg = dict(id='relay-proof', from_kind='worker', source_trust='first_party', from_worker_id=relay_id,
                   to_worker_id=config['host_id'], body=json.dumps({'werewolf_receipt': dict(game_id=game,
                   message_id=row['id'], result=dict(success=True, message_id='finalhop', delivered_to=seat))}))
        with self.assertRaises(RuleError): host.relay_receipt(dict(msg, from_worker_id=config['host_id']))
        host.relay_receipt(msg)
        self.assertEqual(next(r for r in self.engine.outbox(game) if r['id'] == row['id'])['status'], 'sent')
        self.assertTrue(host.relay_receipt(msg)['already_confirmed'])
        self.assertEqual(host.recover()['unknown'], [])

    def test_relay_input_preserves_original_sender_game_and_dedup(self):
        relay_id = str(uuid.uuid4())
        actor = 'A'
        uid = next(uid for uid, seat in self.bindings.items() if seat == actor)
        config = dict(self.config, host_id=uid, relay=dict(worker_id=relay_id, worker_name='leader'))
        game = 'VERIFY-relay-input'
        self.engine.create(game, list('ABCDEF'), 'create-relay-input', test=False, host=config)
        host = KrowHost(self.engine, game)
        # Public discussion reached only via real deterministic deadline ticks.
        for i in range(3):
            self.now += 61
            host.tick('phase'+str(i))
        self.assertEqual(self.engine.state(game)['phase'], 'discussion')
        original = dict(id='original-input', from_worker_id=uid, to_worker_id=relay_id,
                        source_trust='first_party', from_kind='worker', body=json.dumps(dict(game_id=game,
                        phase_id=self.engine.state(game)['phase_id'], action=dict(type='speak', text='test'))))
        msg = dict(id='forwarded', from_worker_id=relay_id, to_worker_id=uid, from_kind='worker',
                   source_trust='first_party', body=json.dumps({'werewolf_input': original}))
        bad = dict(original, from_worker_id=relay_id)
        with self.assertRaises(RuleError): host.ingest_relay(dict(msg, body=json.dumps({'werewolf_input': bad})))
        host.ingest_relay(msg)
        with self.assertRaises(RuleError): host.ingest_relay(dict(msg, id='forwarded-again'))
        event = self.engine.audit(game)[-1]
        self.assertEqual(event['actor'], actor)
        self.assertEqual(event['idem'], 'in:private:original-input')


    def test_maximum_game_id_can_dispatch_and_recover(self):
        game = 'VERIFY-' + 'x' * 121
        self.engine.create(game, list('ABCDEF'), 'long-create', test=False, host=self.config)
        host = KrowHost(self.engine, game)
        row = self.engine.outbox(game)[0]
        host.dispatch(row['id'], self.roster)
        self.assertEqual(host.recover()['unknown'], [row['id']])
        self.assertTrue(self.engine.audit(game))


    def test_authenticated_forward_is_actual_receive_but_tamper_rejected(self):
        uid = next(iter(self.bindings)); seat = self.bindings[uid]
        relay_id = str(uuid.uuid4())
        config = dict(self.config, host_id=uid, relay=dict(worker_id=relay_id, worker_name='leader'))
        game = 'VERIFY-forward'
        self.engine.create(game, list('ABCDEF'), 'forward-create', test=False, host=config)
        host = KrowHost(self.engine, game)
        roster = self.roster + [dict(worker_id=relay_id, worker_name='leader')]
        row = next(r for r in self.engine.outbox(game) if r['recipient'] == seat and r['body']['type'] == 'role')
        call = host.dispatch(row['id'], roster)
        host.receipt(row['id'], dict(success=True, message_id='hop1', delivered_to='leader'))
        msg = dict(id='actually-received', from_worker_id=relay_id, to_worker_id=uid,
                   from_kind='worker', source_trust='first_party', body='forward header\n\n'+call['arguments']['body'])
        wrong = json.loads(call['arguments']['body']); wrong['werewolf_relay']['body']['role'] = 'tampered'
        with self.assertRaises(RuleError): host.relay_delivered(dict(msg, body=json.dumps(wrong)))
        with self.assertRaises(RuleError): host.relay_delivered(dict(msg, from_worker_id=uid))
        result = host.relay_delivered(msg)
        self.assertEqual(result['body'], row['body'])
        self.assertTrue(result['confirmed'])
        self.assertTrue(host.relay_delivered(msg)['confirmed'])
        self.assertEqual(next(r for r in self.engine.outbox(game) if r['id'] == row['id'])['status'], 'sent')


    def test_actual_self_sent_readback_and_relay_share_dedup(self):
        relay_id = str(uuid.uuid4()); uid = next(iter(self.bindings))
        config = dict(self.config, host_id=uid, relay=dict(worker_id=relay_id, worker_name='leader'))
        game = 'VERIFY-sent-readback'
        self.engine.create(game, list('ABCDEF'), 'self-create', test=False, host=config)
        host = KrowHost(self.engine, game)
        for i in range(3):
            self.now += 61; host.tick('self-phase'+str(i))
        original = dict(id='actual-platform-sent', from_worker_id=uid, to_worker_id=relay_id,
                        source_trust='first_party', from_kind='worker', body=json.dumps(dict(game_id=game,
                        phase_id=self.engine.state(game)['phase_id'], action=dict(type='speak', text='readback'))))
        for patch in [dict(from_worker_id=relay_id), dict(to_worker_id=uid), dict(source_trust='external')]:
            with self.assertRaises(RuleError): host.ingest_self_sent(dict(original, **patch))
        host.ingest_self_sent(original)
        wrapper = dict(id='later-relay-copy', from_worker_id=relay_id, to_worker_id=uid,
                       source_trust='first_party', from_kind='worker', body=json.dumps({'werewolf_input': original}))
        with self.assertRaisesRegex(RuleError, 'duplicate key'): host.ingest_relay(wrapper)
        self.assertEqual(sum(ev['idem']=='in:private:actual-platform-sent' for ev in self.engine.audit(game)), 1)


    def test_filtered_outbox_and_compact_status_keep_private_state_out(self):
        state = self.engine.state(self.game)
        prompts = self.engine.outbox(self.game, statuses=['pending'], phase_id=state['phase_id'])
        self.assertTrue(prompts)
        self.assertTrue(all(r['body']['type']=='prompt' for r in prompts))
        row = self.engine.outbox(self.game)[0]
        self.assertEqual(self.engine.outbox(self.game, message_id=row['id']), [row])
        self.assertEqual(self.engine.outbox(self.game, statuses=['unknown']), [])
        all_rows = self.engine.outbox(self.game)
        self.assertEqual(self.engine.outbox(self.game, limit=1), all_rows[:1])
        self.assertEqual(self.engine.outbox(self.game, limit=1, offset=1), all_rows[1:2])
        self.assertTrue(all(r['body']['type']!='prompt' for r in self.engine.outbox(self.game, exclude_types=['prompt'])))
        with self.assertRaises(RuleError): self.engine.outbox(self.game, limit=0)
        with self.assertRaises(RuleError): self.engine.outbox(self.game, statuses=['invalid'])
        compact = self.host.status()
        self.assertEqual(compact['version'], state['version'])
        self.assertEqual(compact['phase_id'], state['phase_id'])
        self.assertNotIn('roles', compact)
        self.assertNotIn('actions', compact)
        self.assertNotIn('host', compact)

if __name__ == '__main__': unittest.main()
