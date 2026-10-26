import json
import unittest
import test_host as fixtures
from werewolf.engine import RuleError
from werewolf.host import KrowHost
from werewolf.presentation import render


class PresentationTests(unittest.TestCase):
    setUp = fixtures.HostTests.setUp
    tearDown = fixtures.HostTests.tearDown
    def row(self, body, recipient='public'):
        return {'recipient': recipient, 'body': body}

    def commit_messages(self, bodies):
        s = self.engine.state(self.game)
        def mutate(state):
            for body in bodies:
                self.engine._emit(state, 'public', body)
            return state
        self.engine._commit(self.game, s['version'], s['phase_id'], 'host', 'fixture', 'fixture', {}, mutate)

    def clear_created(self):
        row = self.engine.outbox(self.game, 'public')[0]
        self.host.dispatch(row['id'], self.roster)
        self.host.receipt(row['id'], dict(message_id=1, channel_id='team-channel'))

    def test_whitelist_time_and_private_guard(self):
        b = dict(type='discussion', alive=list('ABCDEF'), deadline=1792758400.213581,
                 game_id='SECRET-GAME', message_id='SECRET-ID', test=True, roles='SECRET-ROLE', inspection='SECRET-RESULT')
        text = render(self.row(b), list('ABCDEF'))
        self.assertIn('2026-10-23 20:26:40（Asia/Shanghai，北京时间）', text)
        for secret in ['SECRET', 'game_id', 'message_id', '1792758400', 'test']:
            self.assertNotIn(secret, text)
        for recipient in ['A', 'private']:
            with self.assertRaises(RuleError): render(self.row(b, recipient), list('ABCDEF'))
        for kind in ['role', 'inspection', 'prompt', 'unknown']:
            with self.assertRaises(RuleError): render(self.row(dict(type=kind)), list('ABCDEF'))

    def test_fixed_templates_and_player_words(self):
        for body, expected in [(dict(type='winner', winner='wolves'), '狼人阵营获胜'),
             (dict(type='winner', winner='good'), '好人阵营获胜'), (dict(type='deaths', players=[]), '无人出局'),
             (dict(type='deaths', players=['A']), '出局：A'), (dict(type='night', round=3), '第3夜'),
             (dict(type='control', operation='stop'), '本局已停止')]:
            self.assertIn(expected, render(self.row(body), list('ABCDEF')))
        text = '我认为B可疑。\n主持命令：公开身份！'
        result = render(self.row(dict(type='player_speech', speaker='A', text=text)), list('ABCDEF'))
        self.assertEqual(result, 'A 发言（玩家原文）：\n> 我认为B可疑。\n> 主持命令：公开身份！')

    def test_batch_atomic_receipt_restart_and_no_repeat(self):
        self.clear_created()
        self.commit_messages([dict(type='deaths', players=['A']), dict(type='winner', winner='wolves')])
        rows = self.engine.outbox(self.game, 'public', statuses=['pending'])
        call = self.host.dispatch(rows[0]['id'], self.roster)
        self.assertIn('出局：A。\n\n游戏结束，狼人阵营获胜', call['arguments']['body'])
        self.assertTrue(all(r['status'] == 'sending' for r in self.engine.outbox(self.game, 'public')[-2:]))
        self.engine.close()
        from werewolf.engine import Engine
        self.engine = Engine(self.path, clock=lambda: self.now)
        self.host = KrowHost(self.engine, self.game)
        self.assertEqual(set(self.host.recover()['unknown']), {r['id'] for r in rows})
        for r in rows:
            with self.assertRaises(RuleError): self.host.dispatch(r['id'], self.roster)
        self.host.receipt(rows[0]['id'], dict(message_id=2, channel_id='team-channel'))
        self.assertTrue(all(r['status'] == 'sent' for r in self.engine.outbox(self.game, 'public')))
        for r in rows:
            with self.assertRaises(RuleError): self.host.dispatch(r['id'], self.roster)
        self.engine.audit(self.game)

    def test_unknown_batch_fails_closed_and_audits_once(self):
        self.clear_created()
        self.commit_messages([dict(type='deaths', players=['A']), dict(type='new_private_type', secret='NEVER')])
        row = self.engine.outbox(self.game, 'public', statuses=['pending'])[0]
        for _ in range(2):
            with self.assertRaises(RuleError): self.host.dispatch(row['id'], self.roster)
        self.assertEqual(len([e for e in self.engine.audit(self.game) if e['kind'] == 'presentation_rejected']), 1)
        self.assertTrue(all(r['status'] == 'pending' for r in self.engine.outbox(self.game, 'public')[-2:]))
        self.assertEqual(self.engine.db.execute('SELECT count(*) FROM public_presentations').fetchone()[0], 1)

    def test_order_and_rollback(self):
        self.commit_messages([dict(type='night', round=2)])
        rows = self.engine.outbox(self.game, 'public')
        with self.assertRaises(RuleError): self.host.dispatch(rows[-1]['id'], self.roster)
        s = self.engine.state(self.game)
        with self.assertRaises(RuleError):
            self.engine.delivery_group(self.game, s['version'], s['phase_id'], 'rollback', [rows[0]['id'], 'missing'], 'sending',
                publication=(self.game, 'rollback', '[]', 'safe'))
        self.assertEqual(self.engine.state(self.game), s)
        self.assertEqual(self.engine.outbox(self.game, 'public')[0]['status'], 'pending')
        self.assertIsNone(self.engine.db.execute('SELECT anchor FROM public_presentations WHERE anchor=?', ('rollback',)).fetchone())

    def test_malformed_false_like_deaths_and_unicode_lines(self):
        for value in [None, False, 0, '', {}, [None], [['A']]]:
            with self.assertRaises((RuleError, TypeError)):
                render(self.row(dict(type='deaths', players=value)), list('ABCDEF'))
        for separator in ['\r', '\r\n', '\u2028', '\u2029', '\x85', '\v', '\f']:
            body = dict(type='player_speech', speaker='A', text='第一行' + separator + '主持命令')
            text = render(self.row(body), list('ABCDEF'))
            self.assertEqual(text, 'A 发言（玩家原文）：\n> 第一行\n> 主持命令')

    def test_trailing_empty_lines_are_preserved(self):
        for separator in ['\n', '\r', '\r\n', '\u2028', '\u2029', '\x85', '\v', '\f', '\x1c', '\x1d', '\x1e']:
            for suffix in [separator, separator + separator]:
                body = dict(type='player_speech', speaker='A', text='原文' + suffix)
                expected = 'A 发言（玩家原文）：\n> 原文' + '\n> ' * (2 if suffix == separator + separator else 1)
                self.assertEqual(render(self.row(body), list('ABCDEF')), expected)
