"""Public customization fields; never expose provider credentials or personas."""
import hashlib
import json
from .guests import percent, validate_roles

DEFAULTS = {
    'chibi_reference': [], 'turnaround_reference': [],
    'character_prompt': '', 'review_prompt': '',
    'guest_roles': [], 'steal_enabled': False, 'steal_mode': 'average', 'steal_probability': 15,
    'reply_received': '已收到，我先看看。',
    'reply_multiple': '这次先看第一张。',
    'reply_pending': '上一份还在处理中，请稍等。',
    'reply_busy': '当前有些忙，请稍后再来。',
    'reply_cooldown': '请稍等片刻再投喂。',
    'reply_malicious': '这份投喂不合适，我不接受。',
    'reply_non_food': '暂时无法确认这是什么，请补充说明。',
    'reply_done': '收到这份投喂了。',
}


def customization(config):
    return {key: config.get(key, default) for key, default in DEFAULTS.items()}


def revision(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_settings(settings, root):
    if not isinstance(settings, dict) or set(settings) != set(DEFAULTS):
        raise ValueError('设置字段不完整，请刷新页面后重试。')
    result = {}
    for key, value in settings.items():
        if key == 'guest_roles':
            result[key] = validate_roles(value, root)
            continue
        if key == 'steal_enabled':
            if type(value) is not bool: raise ValueError('抢饭开关格式无效。')
            result[key] = value
            continue
        if key == 'steal_mode':
            if value not in ('average', 'custom'): raise ValueError('分配方式无效。')
            result[key] = value
            continue
        if key == 'steal_probability':
            result[key] = percent(value)
            continue
        if key.endswith('_reference'):
            if not isinstance(value, list) or len(value) > 1:
                raise ValueError('每个参考图位置最多保留一张图片。')
            for name in value:
                if not isinstance(name, str) or not name.startswith('files/' + key + '/'):
                    raise ValueError('参考图位置无效，请重新上传。')
                path = (root / name).resolve()
                if not path.is_relative_to((root / 'files' / key).resolve()) or not path.is_file():
                    raise ValueError('参考图已失效，请重新上传。')
            result[key] = list(value)
        else:
            limit = 6000 if key == 'guest_persona' else (4000 if key in ('character_prompt', 'review_prompt', 'guest_character_prompt') else 300)
            if not isinstance(value, str) or len(value) > limit or '\x00' in value:
                raise ValueError(f'文本格式不正确或超过 {limit} 字限制。')
            if key.startswith('reply_') and not value.strip():
                raise ValueError('固定回复不能为空。')
            result[key] = value.strip()
    if result['steal_mode'] == 'custom':
        total = sum(r['probability'] for r in result['guest_roles'] if r['enabled'] and r['random_enabled'])
        if total > 100 + 1e-9:
            raise ValueError(f'抢饭概率合计为{total:g}%，超过上限{total-100:g}%。请降低部分角色的概率后保存。')
    return result
