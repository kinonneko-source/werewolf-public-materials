import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from werewolf import Engine, RuleError
from werewolf.adapter import InboxAdapter

PLAYERS = ['A', 'B', 'C', 'D', 'E', 'F']
ROLES = dict(zip(PLAYERS, ['wolf', 'wolf', 'seer', 'witch', 'hunter', 'villager']))


class Clock:
    def __init__(self):
        self.now = 1000.
    def __call__(self):
        return self.now


class GameTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'game.db'
        self.clock = Clock()
        self.e = Engine(self.path, self.clock)
        self.g = 'TEST-unit'
        self.seq = 0
        self.e.create(self.g, PLAYERS, 'create', timeout=10, roles=ROLES)
    def tearDown(self):
        self.e.close()
        self.temp.cleanup()
    def key(self):
        self.seq += 1
        return f'k{self.seq}'
    def call(self, method, **kwargs):
        s = self.e.state(self.g)
        return getattr(self.e, method)(self.g, s['version'], s['phase_id'], key=self.key(), **kwargs)
    def act(self, actor, kind='pass', **kwargs):
        return self.call('input', actor=actor, action=dict(type=kind, **kwargs))
    def advance(self):
        return self.call('advance')
    def timeout(self):
        self.clock.now = self.e.state(self.g)['deadline']
        return self.advance()
    def night(self, victim='F', witch='pass', target=None):
        for actor in ('A', 'B'):
            self.act(actor, 'kill', target=victim)
        self.advance()
        self.act('C', 'inspect', target='A')
        self.advance()
        self.act('D', witch, **({'target': target} if target else {}))
        self.advance()
    def flush_words(self):
        while self.e.state(self.g)['phase'] in ('hunter', 'last_words'):
            self.timeout()
    def vote(self, target):
        if self.e.state(self.g)['phase'] == 'discussion':
            self.timeout()
        for actor in self.e.alive(self.e.state(self.g)):
            self.act(actor, 'pass' if actor == target else 'vote', **({} if actor == target else {'target': target}))
        self.advance()

    def test_setup_and_private_views(self):
        public = json.dumps(self.e.public(self.g))
        for word in ('roles', 'teammates', 'night_victim', 'potions', 'wolf', 'seer'):
            self.assertNotIn(word, public)
        self.assertEqual(self.e.private(self.g, 'A')['teammates'], ['B'])
        self.assertNotIn('teammates', self.e.private(self.g, 'C'))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with self.assertRaises(RuleError):
            self.e.create('real-game', PLAYERS, 'x')
    def test_full_good_victory_no_role_reveal(self):
        self.night(witch='save')
        self.assertEqual(self.e.state(self.g)['phase'], 'discussion')
        self.vote('A')
        self.flush_words()
        self.timeout()  # B absent -> no kill
        self.timeout()  # seer
        self.timeout()  # witch
        self.vote('B')
        self.flush_words()
        self.assertEqual(self.e.public(self.g)['winner'], 'good')
        for msg in self.e.outbox(self.g, 'public'):
            for field in ('role', 'roles', 'teammates', 'wolf', 'potions', 'victim'):
                self.assertNotIn(field, msg['body'])
        self.e.audit(self.g)
    def test_wolf_victory_after_queues(self):
        self.night('F')
        self.flush_words()
        self.vote('C')
        self.assertIsNone(self.e.state(self.g)['winner'])
        self.flush_words()
        self.assertEqual(self.e.state(self.g)['winner'], 'wolves')
    def test_no_early_advance_and_deadline(self):
        with self.assertRaisesRegex(RuleError, 'collection still open'):
            self.advance()
        self.act('A', 'kill', target='F')
        self.timeout()
        self.assertEqual(self.e.state(self.g)['night_victim'], 'F')
        self.assertEqual(self.e.state(self.g)['last_absent'], ['B'])
    def test_tied_wolves_no_kill(self):
        self.act('A', 'kill', target='F')
        self.act('B', 'kill', target='C')
        self.advance()
        self.assertIsNone(self.e.state(self.g)['night_victim'])
    def test_vote_tie_and_all_abstain(self):
        self.night(witch='save')
        self.timeout()
        self.act('A', 'vote', target='B')
        self.act('B', 'vote', target='A')
        self.timeout()
        self.assertEqual(self.e.state(self.g)['dead'], {})
        self.timeout(); self.timeout(); self.timeout()
        self.timeout(); self.timeout()
        self.assertEqual(self.e.state(self.g)['dead'], {})
    def test_duplicate_stale_wrong_phase_and_atomic_failure(self):
        s = self.e.state(self.g)
        self.e.input(self.g, s['version'], s['phase_id'], 'A', 'same', {'type': 'pass'})
        after = self.e.state(self.g)
        for version, phase, key in [(after['version'], after['phase_id'], 'same'), (s['version'], s['phase_id'], 'new'), (after['version'], 'stale', 'other')]:
            with self.assertRaises(RuleError):
                self.e.input(self.g, version, phase, 'B', key, {'type': 'pass'})
        self.assertEqual(after, self.e.state(self.g))
        self.e.audit(self.g)
    def test_illegal_actions_and_hall_secrets(self):
        for actor, action in [('C', {'type': 'inspect', 'target': 'A'}), ('A', {'type': 'kill', 'target': 'B'}), ('A', {'type': 'kill', 'target': 'A'}), ('A', {'type': 'pass', 'extra': 1})]:
            with self.assertRaises(RuleError):
                self.call('input', actor=actor, action=action)
        with self.assertRaises(RuleError):
            self.call('input', actor='A', action={'type': 'pass'}, channel='hall')
        self.clock.now += 10
        with self.assertRaisesRegex(RuleError, 'deadline'):
            self.act('A')
    def test_poison_hunter_no_shot_and_first_night_words(self):
        self.night('E', 'poison', 'E')
        self.assertEqual(self.e.state(self.g)['dead']['E'], 'poison')
        self.assertEqual(self.e.state(self.g)['phase'], 'last_words')
        self.flush_words()
        self.assertEqual(self.e.state(self.g)['phase'], 'discussion')
        with self.assertRaises(RuleError):
            self.act('E', 'shoot', target='A')
    def test_hunter_shot_then_words_and_no_dead_vote(self):
        self.night('E')
        self.assertEqual(self.e.state(self.g)['phase'], 'hunter')
        self.act('E', 'shoot', target='A')
        self.advance()
        self.assertEqual(self.e.state(self.g)['dead']['A'], 'shot')
        self.act('E', 'speak', text='我的遗言')
        self.advance()
        with self.assertRaises(RuleError):
            self.act('E', 'speak', text='再说一次')
    def test_witch_one_potion_self_save_and_consumption(self):
        self.night('D', 'save')
        self.assertNotIn('D', self.e.state(self.g)['dead'])
        self.assertFalse(self.e.state(self.g)['potions']['save'])
        self.vote('A'); self.flush_words()
        self.timeout(); self.timeout()
        with self.assertRaises(RuleError):
            self.act('D', 'save')
        self.act('D', 'poison', target='B')
        with self.assertRaises(RuleError):
            self.act('D')
        self.advance(); self.flush_words()
        self.assertEqual(self.e.state(self.g)['winner'], 'good')
    def test_self_save_second_night_forbidden(self):
        self.night('F'); self.flush_words()
        self.vote('A'); self.flush_words()
        self.act('B', 'kill', target='D'); self.advance()
        self.timeout()
        with self.assertRaises(RuleError):
            self.act('D', 'save')
    def test_seer_result_only_private_once(self):
        self.timeout()
        self.act('C', 'inspect', target='A')
        self.advance()
        inspect = [m for m in self.e.outbox(self.g) if m['body']['type'] == 'inspection']
        self.assertEqual(len(inspect), 1)
        self.assertEqual(inspect[0]['recipient'], 'C')
        self.assertTrue(inspect[0]['body']['wolf'])
    def test_pause_resume_stop_and_correct(self):
        self.act('A', 'kill', target='F')
        self.clock.now += 3
        self.call('control', operation='pause', reason='test pause')
        with self.assertRaises(RuleError): self.act('B')
        self.call('correct', actor='A', action={'type': 'kill', 'target': 'C'}, reason='correct test input')
        self.clock.now += 100
        self.call('control', operation='resume', reason='resume test')
        self.assertEqual(self.e.state(self.g)['deadline'] - self.clock.now, 7)
        self.call('control', operation='stop', reason='owner stopped test')
        for op in ('resume', 'pause', 'stop'):
            with self.assertRaises(RuleError): self.call('control', operation=op, reason='test')
        self.e.audit(self.g)
    def test_correction_cannot_rewrite_disclosed_or_settled(self):
        self.timeout()
        self.call('control', operation='pause', reason='test')
        with self.assertRaises(RuleError):
            self.call('correct', actor='C', action={'type': 'inspect', 'target': 'A'}, reason='test')
    def test_restart_no_repeated_settlement(self):
        self.night('F')
        before = self.e.state(self.g)
        outbox = self.e.outbox(self.g)
        self.e.close()
        self.e = Engine(self.path, self.clock)
        self.assertEqual(self.e.state(self.g), before)
        self.assertEqual(self.e.outbox(self.g), outbox)
        with self.assertRaises(RuleError):
            self.e.advance(self.g, before['version'] - 1, before['phase_id'], 'old')
        self.e.audit(self.g)
    def test_delivery_failure_no_reply_and_unknown(self):
        message = self.e.outbox(self.g, 'A')[0]
        for status in ('sending', 'unknown'):
            self.call('delivery', message_id=message['id'], status=status)
        with self.assertRaises(RuleError):
            self.call('delivery', message_id=message['id'], status='sending')
        self.call('delivery', message_id=message['id'], status='failed', detail='confirmed transport failure')
        self.call('delivery', message_id=message['id'], status='sending')
        self.call('delivery', message_id=message['id'], status='sent')
        with self.assertRaises(RuleError):
            self.call('delivery', message_id=message['id'], status='sending')
        self.assertNotIn('A', self.e.state(self.g)['actions'])
        self.timeout()
        self.assertIn('A', self.e.state(self.g)['last_absent'])
    def test_inbox_binding_replay_and_channel(self):
        adapter = InboxAdapter(self.e, self.g, {f'user:{p}': p for p in PLAYERS})
        s = self.e.state(self.g)
        record = dict(sender='user:A', message_id='m1', channel='private', game_id=self.g, version=s['version'], phase_id=s['phase_id'], action={'type': 'pass'})
        adapter.ingest(record)
        with self.assertRaises(RuleError): adapter.ingest(record)
        record.update(sender='forged', message_id='m2')
        with self.assertRaises(RuleError): adapter.ingest(record)
        self.assertFalse(adapter.tick('tick')['advanced'])
        self.clock.now += 10
        self.assertTrue(adapter.tick('tick')['advanced'])
    def test_concurrent_writers_only_one_wins(self):
        s = self.e.state(self.g)
        def attempt(actor):
            e = Engine(self.path, self.clock)
            try:
                e.input(self.g, s['version'], s['phase_id'], actor, actor, {'type': 'pass'})
                return 'ok'
            except RuleError:
                return 'rejected'
            finally:
                e.close()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, ('A', 'B')))
        self.assertCountEqual(results, ['ok', 'rejected'])
        self.e.audit(self.g)
    def test_rollback_including_outbox_when_storage_fails(self):
        s = self.e.state(self.g)
        messages = self.e.outbox(self.g)
        self.e.db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.Error): self.act('A')
        self.assertEqual(s, self.e.state(self.g))
        self.assertEqual(messages, self.e.outbox(self.g))
    def test_audit_tamper_detected(self):
        self.e.db.execute("UPDATE events SET actor='forged'")
        with self.assertRaises(RuleError): self.e.audit(self.g)
    def test_snapshot_tamper_detected(self):
        s = self.e.state(self.g)
        s['potions']['save'] = False
        self.e.db.execute('UPDATE games SET state=? WHERE id=?', (json.dumps(s), self.g))
        with self.assertRaisesRegex(RuleError, 'snapshot mismatch'):
            self.e.audit(self.g)
    def test_stale_prompt_and_stopped_dispatch_rejected(self):
        prompt = [m for m in self.e.outbox(self.g, 'A') if m['body']['type'] == 'prompt'][0]
        self.timeout()
        with self.assertRaisesRegex(RuleError, 'expired prompt'):
            self.call('delivery', message_id=prompt['id'], status='sending')
        self.call('control', operation='stop', reason='test')
        role = self.e.outbox(self.g, 'A')[0]
        with self.assertRaisesRegex(RuleError, 'running'):
            self.call('delivery', message_id=role['id'], status='sending')

    def test_terminal_control_notice_can_dispatch(self):
        self.call('control', operation='pause', reason='test')
        pause = self.e.outbox(self.g, 'public')[-1]
        self.call('delivery', message_id=pause['id'], status='sending')
        self.call('delivery', message_id=pause['id'], status='sent')
        self.call('control', operation='stop', reason='test')
        stop = self.e.outbox(self.g, 'public')[-1]
        self.call('delivery', message_id=stop['id'], status='sending')
        self.call('delivery', message_id=stop['id'], status='sent')

    def test_integer_clock_audit(self):
        self.e.close()
        self.e = Engine(self.path, lambda: 1000)
        self.e.create('TEST-int-clock', PLAYERS, 'create', roles=ROLES)
        self.e.audit('TEST-int-clock')
        self.e.close()
        self.e = Engine(self.path, lambda: 1000)
        self.e.audit('TEST-int-clock')
    def test_private_may_act_pause_stop_and_expired(self):
        self.assertTrue(self.e.private(self.g, 'A')['may_act'])
        self.call('control', operation='pause', reason='test')
        self.assertFalse(self.e.private(self.g, 'A')['may_act'])
        self.assertIsNone(self.e.private(self.g, 'A')['deadline'])
        self.call('control', operation='resume', reason='test')
        self.clock.now += 10
        self.assertFalse(self.e.private(self.g, 'A')['may_act'])
        self.call('control', operation='stop', reason='test')
        self.assertFalse(self.e.private(self.g, 'A')['may_act'])
    def test_first_night_words_strict_seat_order(self):
        self.night('F', 'poison', 'C')
        self.assertEqual(self.e.state(self.g)['speaker'], 'C')
        self.assertEqual(self.e.state(self.g)['words_queue'], ['F'])

    def test_cli_actual_process(self):
        result = subprocess.run([sys.executable, '-m', 'werewolf', '--db', str(self.path), 'public'], input=json.dumps({'game_id': self.g}), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['game_id'], self.g)
        bad = subprocess.run([sys.executable, '-m', 'werewolf', '--db', str(self.path), 'input'], input='{}', text=True, capture_output=True, timeout=10)
        self.assertEqual(bad.returncode, 2)
        self.assertIn('error', json.loads(bad.stdout))


if __name__ == '__main__':
    unittest.main()
