"""Deterministic public projection. Never serialize unrecognized payloads."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from .engine import RuleError, require


def render(row, players):
    require(row.get('recipient') == 'public', 'private message cannot be presented')
    b = row['body']
    kind = b.get('type')
    def names(value):
        require(isinstance(value, list) and len(value) <= 6 and len(set(value)) == len(value)
                and all(p in players for p in value), 'invalid public seats')
        return '、'.join(value) or '无'
    if kind == 'created':
        return '六席游戏已准备好。参与席位：' + names(b['players']) + '。'
    if kind in ('discussion', 'vote'):
        require(type(b['deadline']) in (int, float), 'invalid public deadline')
        try:
            deadline = datetime.fromtimestamp(b['deadline'], ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, OverflowError, OSError):
            raise RuleError('invalid public deadline')
        phase = '白天发言开始，请各位提交自己的发言。' if kind == 'discussion' else '白天投票开始，请提交投票或弃权。'
        return phase + '\n存活席位：' + names(b['alive']) + '。\n截止：' + deadline + '（Asia/Shanghai，北京时间）。'
    if kind == 'deaths':
        seats = names(b['players'])  # Validate even false-like malformed values.
        return ('本次结算无人出局。' if b['players'] == [] else '本次结算出局：' + seats + '。')
    if kind == 'winner':
        require(b['winner'] in ('wolves', 'good'), 'invalid public winner')
        return '游戏结束，' + ('狼人阵营' if b['winner'] == 'wolves' else '好人阵营') + '获胜。'
    if kind == 'night':
        require(type(b['round']) is int and 1 <= b['round'], 'invalid public round')
        return f"第{b['round']}夜开始，请等待各自的私密提示。"
    if kind == 'control':
        require(b['operation'] in ('pause', 'resume', 'stop'), 'invalid public control')
        return {'pause': '游戏已暂停。', 'resume': '游戏已恢复。', 'stop': '本局已停止，不再继续。'}[b['operation']]
    if kind == 'player_speech':
        require(b['speaker'] in players and isinstance(b['text'], str) and 1 <= len(b['text']) <= 2000, 'invalid public speech')
        # Keep player words visibly distinct from authoritative host text.
        return b['speaker'] + ' 发言（玩家原文）：\n' + '\n'.join('> ' + line for line in re.split(r'\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]', b['text']))
    raise RuleError('unsupported public event; publication blocked')


def render_batch(rows, players):
    require(rows and len(rows) <= 20, 'invalid public batch')
    return '\n\n'.join(render(row, players) for row in rows)
