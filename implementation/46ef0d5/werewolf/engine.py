"""Trusted local host API. Never expose this module's host operations to players."""
import copy
import hashlib
import json
import os
import random
import sqlite3
import time
from collections import Counter
from pathlib import Path

ROLES = ['wolf', 'wolf', 'seer', 'witch', 'hunter', 'villager']
PHASES = {'wolf', 'seer', 'witch', 'discussion', 'vote', 'hunter', 'last_words'}


class RuleError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise RuleError(message)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Engine:
    """SQLite is the only source of truth. Each command and its outbox commit together.

    Host methods are privileged; adapters authenticate sender->seat before input().
    State / event / database access is host-only, not a player-facing service.
    """
    def __init__(self, database, clock=time.time):
        self.path = Path(database)
        require(not self.path.is_symlink(), 'database symlink forbidden')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS games(id TEXT PRIMARY KEY, version INTEGER NOT NULL, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(game_id TEXT, version INTEGER, phase TEXT, actor TEXT,
          idem TEXT, kind TEXT, payload TEXT, at REAL, previous_hash TEXT, hash TEXT,
          PRIMARY KEY(game_id,version), UNIQUE(game_id,idem));
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, game_id TEXT, version INTEGER,
          recipient TEXT, body TEXT, status TEXT DEFAULT 'pending', attempt INTEGER DEFAULT 0,
          detail TEXT DEFAULT '');
        ''')

    def close(self):
        self.db.close()

    def state(self, game_id):
        row = self.db.execute('SELECT state FROM games WHERE id=?', (game_id,)).fetchone()
        require(row is not None, 'unknown game')
        return json.loads(row[0])

    def _emit(self, s, recipient, body):
        # Bodies are generated from allowlisted templates, never from the private state dump.
        if body.get('type') == 'prompt':
            kind = {'wolf': 'kill', 'seer': 'inspect', 'witch': 'pass', 'hunter': 'shoot',
                    'discussion': 'speak', 'vote': 'vote', 'last_words': 'speak'}[s['phase']]
            example = {'type': kind}
            if kind == 'speak':
                example['text'] = '填写本席发言'
            elif kind != 'pass':
                example['target'] = '从alive选择合法目标'
            body = dict(body, alive=self.alive(s),
                        reply_schema={'game_id': s['game_id'], 'phase_id': s['phase_id'], 'action': example},
                        reply_instruction='正文仅含game_id、phase_id、action三字段；action必须为对象。模板仅说明格式，请自主选择合法行动；弃权用action={"type":"pass"}。')
        s['_messages'].append((recipient, body))

    def _phase(self, s, phase, duration=None):
        s['phase'] = phase
        s['serial'] += 1
        s['phase_id'] = f"{s['round']}:{phase}:{s['serial']}"
        s['actions'] = {}
        s['deadline'] = self.clock() + (duration or s['timeout'])
        if phase == 'wolf':
            for p in self.expected(s):
                self._emit(s, p, {'type': 'prompt', 'phase_id': s['phase_id'], 'action': 'kill', 'deadline': s['deadline']})
        elif phase in ('seer', 'witch', 'hunter'):
            for p in self.expected(s):
                body = {'type': 'prompt', 'phase_id': s['phase_id'], 'action': phase, 'deadline': s['deadline']}
                if phase == 'witch':
                    body.update(victim=s['night_victim'], potions=s['potions'].copy())
                self._emit(s, p, body)
        elif phase in ('discussion', 'vote'):
            self._emit(s, 'public', {'type': phase, 'phase_id': s['phase_id'], 'deadline': s['deadline'], 'alive': self.alive(s)})
            for p in self.expected(s):
                self._emit(s, p, {'type': 'prompt', 'phase_id': s['phase_id'], 'action': 'speak' if phase == 'discussion' else 'vote', 'deadline': s['deadline'], 'alive': self.alive(s)})
        elif phase == 'last_words':
            self._emit(s, s['speaker'], {'type': 'prompt', 'action': 'last_words', 'phase_id': s['phase_id'], 'deadline': s['deadline']})

    @staticmethod
    def alive(s):
        return [p for p in s['players'] if p not in s['dead']]

    def expected(self, s):
        phase = s['phase']
        alive = self.alive(s)
        if phase in ('wolf', 'seer', 'witch'):
            return [p for p in alive if s['roles'][p] == phase or (phase == 'wolf' and s['roles'][p] == 'wolf')]
        if phase in ('discussion', 'vote'):
            return alive
        if phase == 'hunter':
            return [s['shooter']]
        if phase == 'last_words':
            return [s['speaker']]
        return []

    def create(self, game_id, players, key, *, timeout=60, seed=None, roles=None, test=True, host=None):
        require(type(test) is bool, 'test must be boolean')
        require(isinstance(game_id, str) and 0 < len(game_id) <= 128, 'invalid game id')
        if test:
            require(game_id.startswith('TEST-'), 'test games require TEST- prefix')
            require(host is None, 'TEST games cannot use live host')
        else:
            from .host import validate_host
            validate_host(host, players)
            require(game_id.startswith('VERIFY-' if host['mode'] == 'verification' else 'LIVE-'), 'game prefix must match host mode')
            require(seed is None and roles is None, 'live roles must be randomly dealt')
        require(isinstance(players, list) and len(players) == 6 and len(set(players)) == 6, 'six unique players required')
        require(all(isinstance(p, str) and p not in ('host', 'public') and 0 < len(p) <= 64 for p in players), 'invalid player')
        require(type(timeout) in (int, float) and 1 <= timeout <= 3600, 'timeout must be 1..3600 seconds')
        deck = ROLES.copy()
        random.Random(seed).shuffle(deck)
        roles = roles or dict(zip(players, deck))
        require(set(roles) == set(players) and sorted(roles.values()) == sorted(ROLES), 'invalid roles')
        s = {'game_id': game_id, 'version': 0, 'players': players, 'roles': roles,
             'dead': {}, 'status': 'running', 'phase': 'setup', 'phase_id': 'setup', 'serial': 0,
             'round': 1, 'timeout': timeout, 'potions': {'save': True, 'poison': True},
             'night_victim': None, 'winner': None, 'next_phase': None,
             'death_queue': [], 'words_queue': [], 'shooter': None, 'speaker': None,
             'deadline': None, 'test': test, 'host': copy.deepcopy(host), '_messages': []}
        self._emit(s, 'public', {'type': 'created', 'game_id': game_id, 'test': test, 'players': players})
        for p in players:
            body = {'type': 'role', 'role': roles[p], 'test': test, 'players': players}
            if roles[p] == 'wolf':
                body['teammates'] = [q for q in players if roles[q] == 'wolf' and q != p]
            self._emit(s, p, body)
        self._phase(s, 'wolf')
        return self._commit(game_id, 0, 'setup', 'host', key, 'create', {'players': players, 'test': test}, lambda _: s, creating=True)

    def _commit(self, game_id, version, phase_id, actor, key, kind, payload, mutate, creating=False):
        require(isinstance(key, str) and 0 < len(key) <= 128, 'idempotency key required')
        require(type(version) is int, 'version must be integer')
        now = float(self.clock())
        try:
            self.db.execute('BEGIN IMMEDIATE')
            require(not self.db.execute('SELECT 1 FROM events WHERE game_id=? AND idem=?', (game_id, key)).fetchone(), 'duplicate key')
            if creating:
                require(not self.db.execute('SELECT 1 FROM games WHERE id=?', (game_id,)).fetchone(), 'game already exists')
                s = None
            else:
                s = self.state(game_id)
                require(s['version'] == version, 'stale version')
                require(s['phase_id'] == phase_id, 'out of order phase')
                s['_messages'] = []
            s = mutate(s)
            messages = s.pop('_messages', [])
            s['version'] = version + 1
            row = self.db.execute('SELECT hash FROM events WHERE game_id=? ORDER BY version DESC LIMIT 1', (game_id,)).fetchone()
            previous = row[0] if row else ''
            payload = dict(payload, state_hash=hashlib.sha256(encode(s).encode()).hexdigest())
            event = {'game_id': game_id, 'version': version + 1, 'phase': phase_id, 'actor': actor,
                     'idem': key, 'kind': kind, 'payload': payload, 'at': now, 'previous_hash': previous}
            digest = hashlib.sha256(encode(event).encode()).hexdigest()
            self.db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (game_id, version + 1, phase_id, actor, key, kind, encode(payload), now, previous, digest))
            self.db.execute('INSERT INTO games VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,state=excluded.state',
                            (game_id, version + 1, encode(s)))
            for i, (recipient, body) in enumerate(messages):
                mid = f'{game_id}:{version + 1}:{i}'
                body = dict(body, game_id=game_id, test=s['test'], message_id=mid)
                if s.get('host'):
                    body['mode'] = s['host']['mode']
                    body['label'] = s['host']['label']
                self.db.execute('INSERT INTO outbox(id,game_id,version,recipient,body) VALUES(?,?,?,?,?)',
                                (mid, game_id, version + 1, recipient, encode(body)))
            self.db.execute('COMMIT')
            return self.public(game_id)
        except Exception:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _validate_action(self, s, actor, action, channel):
        require(s['status'] == 'running', 'game is not running')
        require(self.clock() < s['deadline'], 'deadline passed')
        require(actor in self.expected(s), 'actor cannot act in this phase')
        require(isinstance(action, dict), 'action must be object')
        phase = s['phase']
        require(channel in ('private', 'hall'), 'invalid channel')
        require(channel == 'private' or phase in ('discussion', 'vote', 'last_words'), 'secret action requires private channel')
        kind = action.get('type')
        allowed = {'wolf': ('kill', 'pass'), 'seer': ('inspect', 'pass'), 'witch': ('save', 'poison', 'pass'),
                   'hunter': ('shoot', 'pass'), 'vote': ('vote', 'pass'), 'discussion': ('speak', 'pass'),
                   'last_words': ('speak', 'pass')}
        require(kind in allowed[phase], 'illegal action type')
        fields = {'type', 'target'} if kind in ('kill', 'inspect', 'poison', 'shoot', 'vote') else {'type', 'text'} if kind == 'speak' else {'type'}
        require(set(action) == fields, 'unexpected or missing action fields')
        if 'target' in fields:
            target = action['target']
            require(target in self.alive(s), 'target must be alive')
            if kind in ('kill', 'inspect', 'shoot', 'vote'):
                require(target != actor, 'self targeting forbidden')
            if kind == 'kill':
                require(s['roles'][target] != 'wolf', 'wolf cannot kill teammate')
        if kind == 'save':
            require(s['potions']['save'] and s['night_victim'] is not None, 'save unavailable')
            require(s['night_victim'] != actor or s['round'] == 1, 'self save only first night')
        if kind == 'poison':
            require(s['potions']['poison'], 'poison unavailable')
        if kind == 'speak':
            require(isinstance(action['text'], str) and 0 < len(action['text']) <= 2000, 'speech must be 1..2000 characters')

    def input(self, game_id, version, phase_id, actor, key, action, channel='private'):
        def mutate(s):
            self._validate_action(s, actor, action, channel)
            require(actor not in s['actions'], 'already acted')
            s['actions'][actor] = copy.deepcopy(action)
            # Seer result is immediate and private; settlement never sends it again.
            if action['type'] == 'inspect':
                self._emit(s, actor, {'type': 'inspection', 'target': action['target'], 'wolf': s['roles'][action['target']] == 'wolf'})
            if action['type'] == 'speak':
                self._emit(s, 'public', {'type': 'player_speech', 'speaker': actor, 'text': action['text']})
            return s
        return self._commit(game_id, version, phase_id, actor, key, 'input', {'action': action, 'channel': channel}, mutate)

    def advance(self, game_id, version, phase_id, key):
        def mutate(s):
            require(s['status'] == 'running', 'game is not running')
            expected = self.expected(s)
            require(self.clock() >= s['deadline'] or all(p in s['actions'] for p in expected), 'collection still open')
            s['last_absent'] = [p for p in expected if p not in s['actions']]
            phase = s['phase']
            actions = list(s['actions'].values())
            if phase == 'wolf':
                votes = [a['target'] for a in actions if a['type'] == 'kill']
                s['night_victim'] = self._plurality(votes)
                self._phase(s, 'seer')
            elif phase == 'seer':
                self._phase(s, 'witch')
            elif phase == 'witch':
                victim = s['night_victim']
                poison = None
                for a in actions:
                    if a['type'] == 'save':
                        s['potions']['save'] = False
                        victim = None
                    if a['type'] == 'poison':
                        s['potions']['poison'] = False
                        poison = a['target']
                deaths = {}
                if victim:
                    deaths[victim] = 'wolf'
                if poison:
                    deaths[poison] = 'poison'  # poison overrides hunter's gun
                self._deaths(s, deaths, 'discussion', first_night=s['round'] == 1)
            elif phase == 'discussion':
                self._phase(s, 'vote')
            elif phase == 'vote':
                victim = self._plurality([a['target'] for a in actions if a['type'] == 'vote'])
                self._deaths(s, {victim: 'vote'} if victim else {}, 'next_night')
            elif phase == 'hunter':
                shots = [a['target'] for a in actions if a['type'] == 'shoot']
                if shots:
                    target = shots[0]
                    s['dead'][target] = 'shot'
                    self._emit(s, 'public', {'type': 'deaths', 'players': [target]})
                s['shooter'] = None
                self._pending(s)
            elif phase == 'last_words':
                s['speaker'] = None
                self._pending(s)
            return s
        return self._commit(game_id, version, phase_id, 'host', key, 'advance', {}, mutate)

    @staticmethod
    def _plurality(votes):
        counts = Counter(votes)
        if not counts:
            return None
        best = max(counts.values())
        winners = [p for p, n in counts.items() if n == best]
        return winners[0] if len(winners) == 1 else None

    def _deaths(self, s, deaths, next_phase, first_night=False):
        s['next_phase'] = next_phase
        for p in s['players']:
            if p not in deaths:
                continue
            cause = deaths[p]
            s['dead'][p] = cause
            if s['roles'][p] == 'hunter' and cause != 'poison':
                s['death_queue'].append(p)
            if cause == 'vote' or first_night:
                s['words_queue'].append(p)
        self._emit(s, 'public', {'type': 'deaths', 'players': [p for p in s['players'] if p in deaths]})
        self._pending(s)

    def _pending(self, s):
        if s['death_queue']:
            s['shooter'] = s['death_queue'].pop(0)
            self._phase(s, 'hunter')
            return
        if s['words_queue']:
            s['speaker'] = s['words_queue'].pop(0)
            self._phase(s, 'last_words')
            return
        wolves = sum(s['roles'][p] == 'wolf' for p in self.alive(s))
        good = len(self.alive(s)) - wolves
        winner = 'good' if wolves == 0 else 'wolves' if wolves >= good else None
        if winner:
            s.update(winner=winner, status='finished', phase='finished', deadline=None)
            s['serial'] += 1
            s['phase_id'] = f"{s['round']}:finished:{s['serial']}"
            self._emit(s, 'public', {'type': 'winner', 'winner': winner})
            return
        phase = s['next_phase']
        if phase == 'next_night':
            s['round'] += 1
            s['night_victim'] = None
            self._phase(s, 'wolf')
            self._emit(s, 'public', {'type': 'night', 'round': s['round']})
        else:
            self._phase(s, phase)

    def control(self, game_id, version, phase_id, key, operation, reason):
        require(isinstance(reason, str) and 0 < len(reason) <= 1000, 'reason required')
        def mutate(s):
            require(s['status'] not in ('stopped', 'finished'), 'terminal game cannot restart')
            if operation == 'pause':
                require(s['status'] == 'running', 'not running')
                s['remaining'] = max(0, s['deadline'] - self.clock())
                s['status'] = 'paused'
            elif operation == 'resume':
                require(s['status'] == 'paused', 'not paused')
                s['deadline'] = self.clock() + s.pop('remaining')
                s['status'] = 'running'
            elif operation == 'stop':
                s['status'] = 'stopped'
                s['deadline'] = None
            else:
                raise RuleError('unknown control')
            self._emit(s, 'public', {'type': 'control', 'operation': operation})
            return s
        return self._commit(game_id, version, phase_id, 'host', key, operation, {'reason': reason}, mutate)

    def correct(self, game_id, version, phase_id, key, actor, action, reason):
        """Host may correct unclosed collection while paused; never rewrite settled history."""
        require(isinstance(reason, str) and 0 < len(reason) <= 1000, 'reason required')
        payload = {'player': actor, 'action': action, 'reason': reason}
        def mutate(s):
            require(s['status'] == 'paused', 'pause before correcting')
            require(s['phase'] not in ('seer', 'discussion', 'last_words'), 'already disclosed actions cannot be corrected; stop and create a new TEST game')
            payload['previous_action'] = copy.deepcopy(s['actions'].get(actor))
            saved = s['deadline']
            s['status'] = 'running'
            s['deadline'] = self.clock() + 1
            self._validate_action(s, actor, action, 'private')
            s['status'] = 'paused'
            s['deadline'] = saved
            s['actions'][actor] = copy.deepcopy(action)
            return s
        return self._commit(game_id, version, phase_id, 'host', key, 'correct', payload, mutate)

    def public(self, game_id):
        s = self.state(game_id)
        # Night subphases, hunter queue and votes are never in a public view.
        phase = s['phase'] if s['phase'] in ('discussion', 'vote', 'finished') else 'resolution' if s['phase'] in ('hunter', 'last_words') and s['next_phase'] == 'next_night' else 'night'
        return {k: v for k, v in {'game_id': game_id, 'test': s['test'], 'version': s['version'],
                'status': s['status'], 'round': s['round'], 'phase': phase,
                'alive': self.alive(s), 'winner': s['winner'],
                'label': s.get('host', {}).get('label') if s.get('host') else 'TEST'}.items()}

    def private(self, game_id, player):
        s = self.state(game_id)
        require(player in s['players'], 'unknown player')
        result = self.public(game_id)
        result.update(role=s['roles'][player], alive=player not in s['dead'], may_act=False)
        if s['roles'][player] == 'wolf':
            result['teammates'] = [p for p in s['players'] if p != player and s['roles'][p] == 'wolf']
        if player in self.expected(s) and s['status'] in ('running', 'paused'):
            result.update(phase_id=s['phase_id'], deadline=s['deadline'] if s['status'] == 'running' else None,
                          may_act=s['status'] == 'running' and self.clock() < s['deadline'] and player not in s['actions'])
            if s['status'] == 'paused':
                result['remaining'] = s['remaining']
            if s['phase'] == 'witch':
                result.update(victim=s['night_victim'], potions=s['potions'])
        return result

    def outbox(self, game_id, recipient=None, *, statuses=None, phase_id=None, message_id=None, exclude_types=None, limit=None, offset=0):
        sql = 'SELECT id,recipient,body,status,attempt,detail FROM outbox WHERE game_id=?'
        args = [game_id]
        if recipient is not None:
            sql += ' AND recipient=?'
            args.append(recipient)
        if statuses is not None:
            require(isinstance(statuses, list) and statuses and set(statuses) <= {'pending', 'sending', 'sent', 'failed', 'unknown'}, 'invalid outbox status filter')
            sql += ' AND status IN (' + ','.join('?' for _ in statuses) + ')'
            args.extend(statuses)
        if message_id is not None:
            require(isinstance(message_id, str), 'invalid message filter')
            sql += ' AND id=?'
            args.append(message_id)
        sql += ' ORDER BY version,rowid'
        rows = [dict(zip(('id', 'recipient', 'body', 'status', 'attempt', 'detail'),
                         (r[0], r[1], json.loads(r[2]), r[3], r[4], r[5]))) for r in self.db.execute(sql, args)]
        if phase_id is not None:
            require(isinstance(phase_id, str), 'invalid phase filter')
            rows = [r for r in rows if r['body'].get('phase_id') == phase_id]
        if exclude_types is not None:
            require(isinstance(exclude_types, list) and all(isinstance(t, str) for t in exclude_types), 'invalid outbox type filter')
            rows = [r for r in rows if r['body']['type'] not in exclude_types]
        require(type(offset) is int and offset >= 0, 'invalid outbox offset')
        require(limit is None or (type(limit) is int and 1 <= limit <= 100), 'outbox limit must be 1..100')
        return rows[offset:] if limit is None else rows[offset:offset + limit]

    def delivery(self, game_id, version, phase_id, key, message_id, status, detail=''):
        require(status in ('sending', 'sent', 'failed', 'unknown'), 'invalid delivery status')
        require(isinstance(detail, str) and len(detail) <= 1000, 'invalid detail')
        def mutate(s):
            row = self.db.execute('SELECT status,attempt,body FROM outbox WHERE game_id=? AND id=?', (game_id, message_id)).fetchone()
            require(row is not None, 'unknown message')
            old, attempt, body_text = row
            body = json.loads(body_text)
            if status == 'sending':
                if body['type'] in ('prompt', 'role', 'inspection'):
                    require(s['status'] == 'running', 'dispatch requires running game')
                if body['type'] == 'control':
                    target_status = {'pause': 'paused', 'resume': 'running', 'stop': 'stopped'}[body['operation']]
                    require(s['status'] == target_status, 'outdated control notice')
                if body['type'] == 'prompt':
                    require(body['phase_id'] == s['phase_id'] and self.clock() < s['deadline'], 'expired prompt cannot dispatch')
            allowed = {'pending': {'sending'}, 'failed': {'sending'}, 'sending': {'sent', 'failed', 'unknown'}, 'unknown': {'sent', 'failed'}, 'sent': set()}
            require(status in allowed[old], 'illegal delivery transition')
            self.db.execute('UPDATE outbox SET status=?,attempt=?,detail=? WHERE id=?',
                            (status, attempt + (status == 'sending'), detail, message_id))
            return s
        return self._commit(game_id, version, phase_id, 'host', key, 'delivery', {'message_id': message_id, 'status': status, 'detail': detail}, mutate)

    def audit(self, game_id):
        events = []
        previous = ''
        for row in self.db.execute('SELECT * FROM events WHERE game_id=? ORDER BY version', (game_id,)):
            keys = ('game_id', 'version', 'phase', 'actor', 'idem', 'kind', 'payload', 'at', 'previous_hash', 'hash')
            event = dict(zip(keys, row))
            event['payload'] = json.loads(event['payload'])
            digest = event.pop('hash')
            require(event['previous_hash'] == previous and hashlib.sha256(encode(event).encode()).hexdigest() == digest, 'audit chain broken')
            require(event['version'] == len(events) + 1, 'event sequence broken')
            event['hash'] = digest
            events.append(event)
            previous = digest
        state = self.state(game_id)
        require(len(events) == state['version'], 'state version mismatch')
        require(events and events[-1]['payload']['state_hash'] == hashlib.sha256(encode(state).encode()).hexdigest(), 'state snapshot mismatch')
        return events
