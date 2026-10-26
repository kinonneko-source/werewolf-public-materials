"""Krow bridge for a trusted local host using native MCP tools.

The CLI consumes raw platform envelopes, never player-supplied sender claims.
This is a privileged bridge, not a public authentication endpoint.
"""
import json
import hashlib
import uuid
from .engine import RuleError, require
from .adapter import InboxAdapter
from .presentation import render_batch


def validate_host(host, players):
    require(isinstance(host, dict) and set(host) == {'mode', 'label', 'authorization', 'host_id', 'channel_id', 'bindings', 'routes', 'relay'}, 'live host config required')
    require(host['mode'] in ('verification', 'live'), 'invalid host mode')
    require(isinstance(host['label'], str) and 1 <= len(host['label']) <= 100, 'visible game label required')
    require(host['mode'] != 'verification' or '验证局' in host['label'], 'verification label required')
    require(isinstance(host['authorization'], str) and 1 <= len(host['authorization']) <= 300, 'host authorization reference required')
    require(isinstance(host['channel_id'], str) and host['channel_id'], 'explicit public channel required')
    bindings = host['bindings']
    require(isinstance(bindings, dict) and len(bindings) == 6 and set(bindings.values()) == set(players), 'six immutable sender bindings required')
    require(isinstance(host['routes'], dict) and set(host['routes']) == set(bindings), 'six sender routes required')
    require(all(isinstance(n, str) and n for n in host['routes'].values()) and len(set(host['routes'].values())) == 6, 'six unique recipient names required')
    try:
        for sender in [host['host_id'], *bindings]:
            require(str(uuid.UUID(sender)) == sender, 'canonical worker UUID required')
    except (ValueError, TypeError, AttributeError):
        raise RuleError('canonical worker UUID required')
    relay = host['relay']
    require(host['host_id'] not in bindings or isinstance(relay, dict), 'player-host needs non-player relay')
    if relay is not None:
        require(isinstance(relay, dict) and set(relay) == {'worker_id', 'worker_name'}, 'invalid relay')
        try:
            require(str(uuid.UUID(relay['worker_id'])) == relay['worker_id'], 'canonical relay UUID required')
        except (ValueError, TypeError, AttributeError):
            raise RuleError('canonical relay UUID required')
        require(relay['worker_id'] not in bindings and relay['worker_id'] != host['host_id'], 'relay must not be a player')
        require(isinstance(relay['worker_name'], str) and relay['worker_name'], 'relay name required')


class KrowHost:
    def __init__(self, engine, game_id):
        self.engine, self.game_id = engine, game_id
        state = engine.state(game_id)
        require(state['test'] is False and state.get('host'), 'live host not configured')
        self.config = state['host']
        validate_host(self.config, state['players'])
        self.adapter = InboxAdapter(engine, game_id, self.config['bindings'])

    def ingest_mail(self, message):
        """message is one raw team_inbox/team_read_message item, not its body."""
        require(isinstance(message, dict), 'platform envelope required')
        require(message.get('to_worker_id') == self.config['host_id'], 'wrong host recipient')
        require(message.get('source_trust') == 'first_party' and message.get('from_kind') == 'worker', 'untrusted platform sender')
        return self._ingest(message.get('from_worker_id'), message.get('id'), 'private', message.get('body'))

    def ingest_hall(self, message, channel_id):
        """Only structured platform records; rendered display-name text is insufficient."""
        require(channel_id == self.config['channel_id'], 'wrong public channel')
        require(isinstance(message, dict), 'structured hall envelope required')
        return self._ingest(message.get('worker_id'), str(message.get('id', '')), 'hall', message.get('body'))

    def _ingest(self, sender, message_id, channel, body):
        require(sender in self.config['bindings'], 'unbound sender')
        require(isinstance(message_id, str) and message_id, 'platform message id required')
        try:
            data = json.loads(body)
        except (TypeError, ValueError):
            raise RuleError('body must be a single action JSON object')
        require(isinstance(data, dict) and set(data) == {'game_id', 'phase_id', 'action'}, 'body requires exactly game_id/phase_id/action; action must be an object such as {type:speak,text:...}')
        require(data['game_id'] == self.game_id, 'wrong game')
        # The player cannot choose version; refresh it without ever changing phase.
        state = self.engine.state(self.game_id)
        require(state['phase_id'] == data['phase_id'], 'out of order phase')
        record = dict(sender=sender, message_id=f'{channel}:{message_id}', channel=channel,
                      game_id=self.game_id, phase_id=data['phase_id'], version=state['version'], action=data['action'])
        return self.adapter.ingest(record)

    def status(self):
        state = self.engine.state(self.game_id)
        return dict(self.engine.public(self.game_id), phase_id=state['phase_id'],
                    phase=state['phase'], deadline=state['deadline'],
                    expected_count=len(self.engine.expected(state)), responded_count=len(state['actions']))

    def tick(self, key):
        return self.adapter.tick(key)

    def dispatch(self, message_id, roster):
        """Persist sending BEFORE returning the exact tool call. Invoke it once."""
        require(isinstance(roster, list), 'fresh platform roster required')
        current = {w.get('worker_id'): w.get('worker_name') for w in roster if isinstance(w, dict)}
        require(all(current.get(uid) == name for uid, name in self.config['routes'].items()), 'roster identity changed; pause and revalidate')
        relay = self.config['relay']
        if relay:
            require(current.get(relay['worker_id']) == relay['worker_name'], 'relay identity changed; pause and revalidate')
        rows = [x for x in self.engine.outbox(self.game_id) if x['id'] == message_id]
        require(len(rows) == 1, 'unknown message')
        row = rows[0]
        state = self.engine.state(self.game_id)
        recipient = row['recipient']
        body = json.dumps(row['body'], ensure_ascii=False, sort_keys=True)
        if recipient == 'public':
            return self._dispatch_public(row)
        else:
            workers = [sender for sender, seat in self.config['bindings'].items() if seat == recipient]
            require(len(workers) == 1, 'recipient not bound')
            call = {'tool': 'team_send_message', 'arguments': {'to_worker': self.config['routes'][workers[0]],
                    'subject': self.config['label'] + ' ' + row['body']['type'], 'body': body}}
        if recipient != 'public' and workers[0] == self.config['host_id']:
            call['arguments']['to_worker'] = relay['worker_name']
            call['arguments']['subject'] = self.config['label'] + ' 请将本原信私密转给' + self.config['routes'][workers[0]] + '（你是非玩家中继，不提交行动）'
            call['arguments']['body'] = json.dumps({'werewolf_relay': {'game_id': self.game_id,
                'message_id': message_id, 'to_worker': self.config['routes'][workers[0]], 'body': row['body']}}, ensure_ascii=False)
        self.engine.delivery(self.game_id, state['version'], state['phase_id'],
                             'dispatch:' + hashlib.sha256(f'{message_id}:{row["attempt"]}'.encode()).hexdigest(), message_id, 'sending')
        return call

    def _dispatch_public(self, row):
        # First outstanding public record only; never skip an ambiguous earlier send.
        rows = self.engine.outbox(self.game_id, recipient='public')
        outstanding = [r for r in rows if r['status'] != 'sent']
        require(outstanding and outstanding[0]['id'] == row['id'], 'public messages must dispatch in order')
        require(row['status'] in ('pending', 'failed'), 'public delivery not retryable')
        saved = self.engine.db.execute('SELECT members,text FROM public_presentations WHERE game_id=? AND anchor=?', (self.game_id, row['id'])).fetchone()
        if saved:
            ids, text = json.loads(saved[0]), saved[1]
            batch = [r for r in rows if r['id'] in ids]
            require(len(batch) == len(ids) and all(r['status'] == 'failed' for r in batch), 'batch must be reconciled together')
        else:
            version = self.engine.db.execute('SELECT version FROM outbox WHERE id=?', (row['id'],)).fetchone()[0]
            batch = []
            for r in outstanding:
                v = self.engine.db.execute('SELECT version FROM outbox WHERE id=?', (r['id'],)).fetchone()[0]
                if v != version or r['status'] != 'pending' or len(batch) == 20:
                    break
                batch.append(r)
            ids = [r['id'] for r in batch]
            try:
                text = render_batch(batch, self.engine.state(self.game_id)['players'])
            except (RuleError, KeyError, TypeError, ValueError):
                state = self.engine.state(self.game_id)
                key = 'presentation-rejected:' + hashlib.sha256(row['id'].encode()).hexdigest()
                if not self.engine.db.execute('SELECT 1 FROM events WHERE game_id=? AND idem=?', (self.game_id, key)).fetchone():
                    self.engine._commit(self.game_id, state['version'], state['phase_id'], 'host', key,
                        'presentation_rejected', {'message_ids': ids, 'reason': 'unsupported or malformed public event'}, lambda s: s)
                raise RuleError('public presentation blocked; inspect host audit')
        state = self.engine.state(self.game_id)
        publication = None if saved else (self.game_id, row['id'], json.dumps(ids), text)
        self.engine.delivery_group(self.game_id, state['version'], state['phase_id'],
            'dispatch:' + hashlib.sha256(f'{row["id"]}:{row["attempt"]}'.encode()).hexdigest(), ids, 'sending',
            publication=publication)
        return {'tool': 'hall_post', 'arguments': {'channel_id': self.config['channel_id'], 'body': text}}

    def receipt(self, message_id, result):
        """Accept an actual MCP result. Missing proof becomes unknown, never resend."""
        require(isinstance(result, dict), 'platform receipt required')
        rows = [x for x in self.engine.outbox(self.game_id) if x['id'] == message_id]
        require(len(rows) == 1, 'unknown message')
        row = rows[0]
        if row['recipient'] == 'public':
            groups = self.engine.db.execute('SELECT anchor,members FROM public_presentations WHERE game_id=?', (self.game_id,)).fetchall()
            require(not any(message_id in json.loads(members) and anchor != message_id for anchor, members in groups), 'public receipt requires batch anchor')
            success = result.get('message_id') is not None and result.get('channel_id') == self.config['channel_id']
        else:
            uid = next(uid for uid, seat in self.config['bindings'].items() if seat == row['recipient'])
            success = result.get('success') is True and bool(result.get('message_id')) and result.get('delivered_to') == self.config['routes'][uid]
        if row['recipient'] != 'public' and self.config['bindings'].get(self.config['host_id']) == row['recipient']:
            success = False  # first-hop relay receipt is not delivery to the player
        status = 'sent' if success else 'unknown'
        state = self.engine.state(self.game_id)
        detail = json.dumps({k: result[k] for k in ('message_id', 'thread_id', 'channel_id', 'delivered_to') if k in result}, ensure_ascii=False)
        saved = self.engine.db.execute('SELECT members FROM public_presentations WHERE game_id=? AND anchor=?', (self.game_id, message_id)).fetchone()
        ids = json.loads(saved[0]) if saved else [message_id]
        return self.engine.delivery_group(self.game_id, state['version'], state['phase_id'],
                                    'receipt:' + hashlib.sha256(f'{message_id}:{row["attempt"]}'.encode()).hexdigest(), ids, status, detail)

    def recover(self):
        """A crash leaves sending ambiguous. Do not send it again automatically."""
        recovered = []
        for row in self.engine.outbox(self.game_id):
            if row['status'] == 'sending':
                state = self.engine.state(self.game_id)
                self.engine.delivery(self.game_id, state['version'], state['phase_id'],
                                     'recover:' + hashlib.sha256(f'{row["id"]}:{row["attempt"]}'.encode()).hexdigest(), row['id'], 'unknown', 'host restarted; reconcile platform receipt')
                recovered.append(row['id'])
        return {'unknown': recovered}

    def _relay_envelope(self, message, field):
        relay = self.config['relay']
        require(relay is not None, 'relay not configured')
        require(isinstance(message, dict) and message.get('source_trust') == 'first_party' and message.get('from_kind') == 'worker', 'untrusted relay envelope')
        require(message.get('from_worker_id') == relay['worker_id'] and message.get('to_worker_id') == self.config['host_id'], 'wrong relay identity')
        require(isinstance(message.get('id'), str) and message['id'], 'relay message id required')
        try:
            body = json.loads(message.get('body'))
        except (TypeError, ValueError):
            raise RuleError('invalid relay body')
        require(isinstance(body, dict) and set(body) == {field}, 'invalid relay schema')
        require(isinstance(body[field], dict), 'relay payload required')
        return body[field]

    def ingest_relay(self, message):
        original = self._relay_envelope(message, 'werewolf_input')
        return self.ingest_self_sent(original)

    def ingest_self_sent(self, message):
        # Read back the actual outgoing envelope with team_read_message. A send
        # request or its player-authored body alone is never sufficient proof.
        relay = self.config['relay']
        require(relay is not None and self.config['host_id'] in self.config['bindings'], 'host-player relay required')
        require(isinstance(message, dict) and message.get('source_trust') == 'first_party' and message.get('from_kind') == 'worker', 'untrusted original sender')
        require(message.get('from_worker_id') == self.config['host_id'] and message.get('to_worker_id') == relay['worker_id'], 'only actual host-player message to bound relay accepted')
        return self._ingest(message['from_worker_id'], message.get('id'), 'private', message.get('body'))

    def relay_receipt(self, message):
        data = self._relay_envelope(message, 'werewolf_receipt')
        require(set(data) == {'game_id', 'message_id', 'result'} and data['game_id'] == self.game_id, 'wrong relay game or schema')
        rows = [r for r in self.engine.outbox(self.game_id) if r['id'] == data['message_id']]
        require(len(rows) == 1, 'unknown relay message')
        row = rows[0]
        seat = self.config['bindings'].get(self.config['host_id'])
        require(row['recipient'] == seat, 'relay may only confirm host-player delivery')
        result = data['result']
        require(isinstance(result, dict) and result.get('success') is True and bool(result.get('message_id')) and result.get('delivered_to') == self.config['routes'][self.config['host_id']], 'final relay delivery proof required')
        if row['status'] == 'sent':
            proof = json.loads(row['detail'])
            require(proof.get('message_id') == result['message_id'], 'different final receipt for sent message')
            return {'already_confirmed': True}
        state = self.engine.state(self.game_id)
        return self.engine.delivery(self.game_id, state['version'], state['phase_id'],
            'relay-receipt:' + message['id'], row['id'], 'sent', json.dumps(result, ensure_ascii=False))

    def relay_delivered(self, message):
        """Actual authenticated receive is stronger proof than a send receipt.

        Krow forward_message wraps the original JSON with a fixed mail header.
        Only a configured relay, exact stored body and host-player recipient pass.
        """
        relay = self.config['relay']
        require(relay is not None and isinstance(message, dict), 'relay required')
        require(message.get('from_worker_id') == relay['worker_id'] and message.get('to_worker_id') == self.config['host_id'] and message.get('source_trust') == 'first_party' and message.get('from_kind') == 'worker', 'untrusted relay delivery')
        require(isinstance(message.get('id'), str) and message['id'], 'platform receive id required')
        text = message.get('body')
        require(isinstance(text, str), 'relay body required')
        try:
            data = json.loads(text)
        except ValueError:
            # Do not interpret prose as commands; inspect only the final JSON object.
            position = text.rfind('\n\n{')
            require(position >= 0, 'forwarded JSON required')
            try: data = json.loads(text[position + 2:])
            except ValueError: raise RuleError('invalid forwarded JSON')
        require(isinstance(data, dict), 'invalid relay delivery')
        if set(data) == {'werewolf_relay'}:
            packet = data['werewolf_relay']
            require(isinstance(packet, dict) and set(packet) == {'game_id', 'message_id', 'to_worker', 'body'}, 'invalid forwarded packet')
            require(packet['game_id'] == self.game_id and packet['to_worker'] == self.config['routes'][self.config['host_id']], 'wrong forwarded route')
            data = packet['body']
            require(isinstance(data, dict) and data.get('message_id') == packet['message_id'], 'forwarded message mismatch')
        require(data.get('game_id') == self.game_id, 'wrong received game')
        row = next((r for r in self.engine.outbox(self.game_id) if r['id'] == data.get('message_id')), None)
        require(row is not None and row['recipient'] == self.config['bindings'].get(self.config['host_id']) and row['body'] == data, 'received body differs from private outbox')
        if row['status'] != 'sent':
            state = self.engine.state(self.game_id)
            self.engine.delivery(self.game_id, state['version'], state['phase_id'],
                'received:' + message['id'], row['id'], 'sent', json.dumps(dict(message_id=message['id'],
                delivered_to=self.config['routes'][self.config['host_id']], proof='authenticated actual receive'), ensure_ascii=False))
        return {'confirmed': True, 'body': data}
