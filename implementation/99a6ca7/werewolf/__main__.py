"""Trusted host CLI. JSON stdin, JSON stdout, no secret output on errors."""
import argparse
import json
import sqlite3
import sys
from .engine import Engine, RuleError
from .adapter import InboxAdapter
from .host import KrowHost


def main():
    parser = argparse.ArgumentParser(description='Six-seat deterministic werewolf host')
    parser.add_argument('--db', required=True, help='host-only SQLite file')
    parser.add_argument('command', choices=['create', 'public', 'private', 'status', 'input', 'ingest', 'tick', 'advance', 'control', 'correct', 'outbox', 'delivery', 'audit', 'host-mail', 'host-hall', 'host-tick', 'host-dispatch', 'host-receipt', 'host-recover', 'host-relay-input', 'host-relay-receipt', 'host-relay-delivered', 'host-self-sent', 'host-status'])
    args = parser.parse_args()
    engine = None
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise RuleError('JSON object required')
        engine = Engine(args.db)
        if args.command.startswith('host-'):
            host = KrowHost(engine, data.pop('game_id'))
            method = {'mail': 'ingest_mail', 'hall': 'ingest_hall', 'relay-input': 'ingest_relay', 'relay-receipt': 'relay_receipt', 'relay-delivered': 'relay_delivered', 'self-sent': 'ingest_self_sent'}.get(args.command[5:], args.command[5:])
            result = getattr(host, method)(**data)
        elif args.command in ('ingest', 'tick'):
            adapter = InboxAdapter(engine, data.pop('game_id'), data.pop('bindings'))
            result = getattr(adapter, args.command)(**data)
        elif args.command == 'status':
            result = engine.state(**data)  # host-only; never post in hall
        else:
            result = getattr(engine, args.command)(**data)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (RuleError, TypeError, ValueError, KeyError) as error:
        # Never echo arguments / DB contents / player text in error output.
        message = str(error) if isinstance(error, RuleError) else 'invalid request schema'
        print(json.dumps({'error': message}, ensure_ascii=False))
        return 2
    except (sqlite3.Error, OSError):
        print(json.dumps({'error': 'local storage unavailable; no successful commit acknowledged'}))
        return 3
    finally:
        if engine:
            engine.close()


if __name__ == '__main__':
    sys.exit(main())
