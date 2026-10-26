"""Export a redacted host summary, never the DB, roles or night action payloads."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from werewolf.engine import Engine
p = argparse.ArgumentParser()
p.add_argument('--db', required=True)
p.add_argument('--game', required=True)
a = p.parse_args()
e = Engine(a.db)
s = e.state(a.game)
events = e.audit(a.game)
out = e.outbox(a.game)
roles = []
for row in out:
    if row['body']['type'] == 'role':
        try: receipt = json.loads(row['detail'])
        except ValueError: receipt = {}
        roles.append(dict(player=row['recipient'], outbox_id=row['id'], status=row['status'],
                          platform_message_id=receipt.get('message_id'), delivered_to=receipt.get('delivered_to')))
public_inputs = []
for event in events:
    if event['kind'] == 'input' and event['phase'].split(':')[1] in ('discussion', 'vote', 'last_words'):
        public_inputs.append(dict(player=event['actor'], message_key=event['idem'], phase=event['phase'].split(':')[1]))
result = dict(game=e.public(a.game), audit_valid=True, event_count=len(events),
              immutable_bindings=s['host']['bindings'], public_channel=s['host']['channel_id'],
              relay=s['host'].get('relay'), role_deliveries=roles,
              outbox_status_counts={status: sum(r['status'] == status for r in out) for status in ('pending', 'sending', 'sent', 'failed', 'unknown')},
              public_input_evidence=public_inputs,
              total_private_inputs=sum(ev['kind'] == 'input' and ev['payload'].get('channel') == 'private' for ev in events),
              public_events=[dict(outbox_id=r['id'], body=r['body'], status=r['status'], receipt=r['detail']) for r in out if r['recipient'] == 'public'],
              database_exported=False, private_payloads_exported=False)
print(json.dumps(result, ensure_ascii=False, indent=2))
e.close()
