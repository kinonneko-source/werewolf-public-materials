"""Transport-neutral ingress for a trusted host's authenticated inbox records.

This is not an HTTP endpoint. The caller must verify sender/channel/message id
using platform metadata, never player-authored text. No network calls are made.
"""
from .engine import RuleError, require


class InboxAdapter:
    def __init__(self, engine, game_id, bindings):
        self.engine, self.game_id = engine, game_id
        s = engine.state(game_id)
        require(isinstance(bindings, dict) and len(bindings) == 6 and set(bindings.values()) == set(s['players']), 'one authenticated sender per seat required')
        self.bindings = bindings.copy()

    def ingest(self, record):
        require(isinstance(record, dict) and set(record) == {'sender', 'message_id', 'channel', 'game_id', 'version', 'phase_id', 'action'}, 'invalid inbox record')
        require(record['game_id'] == self.game_id, 'wrong game')
        actor = self.bindings.get(record['sender'])
        require(actor is not None, 'unbound sender')
        require(isinstance(record['message_id'], str) and record['message_id'], 'transport message id required')
        key = 'in:' + record['message_id']
        return self.engine.input(self.game_id, record['version'], record['phase_id'], actor, key, record['action'], record['channel'])

    def tick(self, key):
        """At most one settlement per call; run again after a fresh state read.

        No sleeps, no infinite waiting and no network work inside transactions.
        """
        s = self.engine.state(self.game_id)
        if s['status'] != 'running':
            return {'advanced': False, 'status': s['status']}
        expected = self.engine.expected(s)
        if self.engine.clock() < s['deadline'] and not all(p in s['actions'] for p in expected):
            return {'advanced': False, 'deadline': s['deadline']}
        result = self.engine.advance(self.game_id, s['version'], s['phase_id'], key)
        return {'advanced': True, 'view': result}

    def player_view(self, authenticated_sender):
        require(authenticated_sender in self.bindings, 'unbound sender')
        return self.engine.private(self.game_id, self.bindings[authenticated_sender])
