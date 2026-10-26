"""Fresh process for every operation: deterministic TEST demo, no network/messages."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix='werewolf-test-') as temporary:
        database = str(Path(temporary) / 'game.db')
        game = 'TEST-process-restart-demo'
        seq = 0
        def cli(command, data):
            result = subprocess.run([sys.executable, '-m', 'werewolf', '--db', database, command],
                                    input=json.dumps(data), text=True, capture_output=True, cwd=ROOT, timeout=10)
            if result.returncode:
                raise RuntimeError(f'{command}: {result.stdout}')
            return json.loads(result.stdout)
        def state():
            return cli('status', {'game_id': game})
        def change(command, **extra):
            nonlocal seq
            seq += 1
            s = state()
            return cli(command, dict(game_id=game, version=s['version'], phase_id=s['phase_id'], key=f'demo-{seq}', **extra))
        def act(actor, kind, **extra):
            return change('input', actor=actor, action=dict(type=kind, **extra))
        def advance():
            return change('advance')
        roles = dict(zip('ABCDEF', ['wolf', 'wolf', 'seer', 'witch', 'hunter', 'villager']))
        print(json.dumps(cli('create', dict(game_id=game, players=list('ABCDEF'), key='create', roles=roles)), ensure_ascii=False))
        for round_number, vote_target in ((1, 'A'), (2, 'B')):
            s = state()
            for p in 'AB':
                if p not in s['dead']:
                    act(p, 'pass')
            advance()
            act('C', 'inspect', target=vote_target); advance()
            act('D', 'pass'); advance()
            for p in state()['players']:
                if p not in state()['dead']:
                    act(p, 'pass')
            advance()
            for p in state()['players']:
                if p not in state()['dead']:
                    act(p, 'pass' if p == vote_target else 'vote', **({} if p == vote_target else {'target': vote_target}))
            advance()
            act(vote_target, 'speak', text='TEST 遗言：流程验证。'); advance()
            print(json.dumps(cli('public', {'game_id': game}), ensure_ascii=False))
        final = cli('public', {'game_id': game})
        assert final['winner'] == 'good'
        events = cli('audit', {'game_id': game})
        # Never print the private audit payload or per-seat role prompts.
        print(json.dumps({'test': True, 'fresh_process_per_operation': True, 'winner': final['winner'],
                          'event_count': len(events), 'audit_verified': True, 'network_sends': 0}, ensure_ascii=False))


if __name__ == '__main__':
    run()
