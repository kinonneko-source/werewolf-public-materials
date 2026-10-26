"""Deterministic randomized games, checking observable safety and liveness invariants."""
import random
import tempfile
import unittest
from pathlib import Path
from werewolf import Engine


class SimulationTest(unittest.TestCase):
    def test_100_complete_or_bounded_games(self):
        for seed in range(100):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as temporary:
                rng = random.Random(seed)
                now = [1000.]
                e = Engine(Path(temporary) / 'game.db', lambda: now[0])
                game = f'TEST-fuzz-{seed}'
                players = list('ABCDEF')
                e.create(game, players, 'create', seed=seed, timeout=10)
                seq = 0
                last_dead = set()
                try:
                    for step in range(200):
                        s = e.state(game)
                        self.assertTrue(last_dead <= set(s['dead']))
                        self.assertEqual(set(s['players']), set(s['roles']))
                        self.assertEqual(set(e.alive(s)) | set(s['dead']), set(players))
                        self.assertTrue(set(e.alive(s)).isdisjoint(s['dead']))
                        last_dead = set(s['dead'])
                        if s['status'] == 'finished':
                            wolves = sum(s['roles'][p] == 'wolf' for p in e.alive(s))
                            good = len(e.alive(s)) - wolves
                            self.assertTrue(wolves == 0 if s['winner'] == 'good' else wolves >= good)
                            break
                        for actor in e.expected(s):
                            if rng.random() < 0.1:
                                continue  # absent
                            action = {'type': 'pass'}
                            other = [p for p in e.alive(s) if p != actor]
                            if s['phase'] == 'wolf':
                                targets = [p for p in other if s['roles'][p] != 'wolf']
                                if targets: action = {'type': 'kill', 'target': rng.choice(targets)}
                            elif s['phase'] == 'seer' and other:
                                action = {'type': 'inspect', 'target': rng.choice(other)}
                            elif s['phase'] == 'witch':
                                if s['potions']['save'] and s['night_victim'] and (s['night_victim'] != actor or s['round'] == 1) and rng.random() < .4:
                                    action = {'type': 'save'}
                                elif s['potions']['poison'] and rng.random() < .2:
                                    action = {'type': 'poison', 'target': rng.choice(e.alive(s))}
                            elif s['phase'] in ('vote', 'hunter') and other:
                                action = {'type': 'vote' if s['phase'] == 'vote' else 'shoot', 'target': rng.choice(other)}
                            current = e.state(game)
                            seq += 1
                            e.input(game, current['version'], current['phase_id'], actor, f'action-{seq}', action)
                        current = e.state(game)
                        now[0] = current['deadline']
                        seq += 1
                        e.advance(game, current['version'], current['phase_id'], f'advance-{seq}')
                    else:
                        self.fail('game did not terminate in 200 phases')
                    e.audit(game)
                    public = e.public(game)
                    self.assertNotIn('roles', public)
                    self.assertNotIn('actions', public)
                    for message in e.outbox(game, 'public'):
                        self.assertTrue(set(message['body']).isdisjoint({'role', 'roles', 'teammates', 'potions', 'victim', 'wolf'}))
                finally:
                    e.close()


if __name__ == '__main__':
    unittest.main()
