"""Guest profiles and explicit, unconditional appearance probabilities."""
import copy
import math
import re
import shutil
import uuid
from pathlib import Path

DEFAULT_NOTICE = '刚递过去，就被半路接走了……'


def initialize_default_guest(config, root):
    """Seed once for new/older installs; never restore deleted or edited profiles."""
    if config.get('default_guest_initialized', False):
        return False
    already_configured = (config.get('guest_roles_migrated', False) or config.get('guest_roles')
                          or config.get('guest_persona') or config.get('guest_reference'))
    if not already_configured:
        bundle = Path(__file__).with_name('default_guests') / 'dafeiyu'
        target = root / 'files/guest_reference' / ('dafeiyu-' + uuid.uuid4().hex + '.png')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle / 'reference.png', target)
        config['guest_roles'] = [{
            'id': 'default-dafeiyu', 'name': '大肥鱼', 'enabled': True,
            'random_enabled': True, 'probability': 15,
            'persona': (bundle / 'persona.txt').read_text(encoding='utf-8').strip(),
            'character_prompt': (bundle / 'appearance.txt').read_text(encoding='utf-8').strip(),
            'reference': [target.relative_to(root).as_posix()], 'secondary': [],
            'notice': DEFAULT_NOTICE,
        }]
    config['default_guest_initialized'] = True
    return True


def migrate_guests(config):
    if config.get('guest_roles_migrated', False):
        changed = False
        for role in config.get('guest_roles', []):
            if 'notice' not in role:
                role['notice'] = DEFAULT_NOTICE
                changed = True
        return changed
    if not config.get('guest_roles') and (config.get('guest_persona') or config.get('guest_reference')):
        config['guest_roles'] = [{
            'id': 'legacy-guest', 'name': '大肥鱼', 'enabled': True,
            'random_enabled': True, 'probability': config.get('steal_probability', 15),
            'persona': config.get('guest_persona', ''),
            'character_prompt': config.get('guest_character_prompt', ''),
            'reference': list(config.get('guest_reference', [])), 'secondary': [],
            'notice': DEFAULT_NOTICE,
        }]
    config.setdefault('guest_roles', [])
    for role in config['guest_roles']:
        role.setdefault('notice', DEFAULT_NOTICE)
    config.setdefault('steal_mode', 'average')
    config['guest_roles_migrated'] = True
    return True


def percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('概率必须是 0–100 的数字。')
    return float(value)


def reference_valid(paths, root, optional=False):
    if optional and paths == []:
        return True
    if not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str):
        return False
    target = (root / paths[0]).resolve()
    return target.is_relative_to((root / 'files/guest_reference').resolve()) and target.is_file()


def available(role, root):
    return bool(role.get('persona', '').strip() and reference_valid(role.get('reference'), root)
                and reference_valid(role.get('secondary'), root, optional=True))


def validate_roles(roles, root):
    if not isinstance(roles, list) or len(roles) > 30:
        raise ValueError('最多添加 30 个抢饭角色。')
    result = copy.deepcopy(roles)
    ids, names = set(), set()
    expected = {'id','name','enabled','random_enabled','probability','persona','character_prompt','reference','secondary','notice'}
    for role in result:
        if not isinstance(role, dict) or set(role) != expected:
            raise ValueError('角色字段不完整，请刷新页面。')
        if not isinstance(role['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', role['id']) or role['id'] in ids:
            raise ValueError('角色 ID 无效或重复。')
        name = role['name']
        if name in ('角色', '状态'):
            raise ValueError('“角色”和“状态”是投喂指令保留字，请使用其他角色名称。')
        if not isinstance(name, str) or not 1 <= len(name) <= 40 or any(c.isspace() or ord(c) < 32 for c in name) or name in names:
            raise ValueError('角色名称须唯一，1–40字，不能含空格或换行。')
        ids.add(role['id']); names.add(name)
        if type(role['enabled']) is not bool or type(role['random_enabled']) is not bool:
            raise ValueError('角色开关格式无效。')
        role['probability'] = percent(role['probability'])
        for key, limit in [('persona',6000),('character_prompt',4000),('notice',300)]:
            if not isinstance(role[key], str) or len(role[key]) > limit or '\0' in role[key]:
                raise ValueError(f'{name} 的提示词格式无效或超过 {limit} 字。')
        for key in ('reference','secondary'):
            if role[key] != [] and not reference_valid(role[key], root):
                raise ValueError(f'{name} 的参考图无效，请重新上传或移除。')
    return result


def distribution(config, root):
    roles = config.get('guest_roles', [])
    candidates = [r for r in roles if r['enabled'] and r['random_enabled'] and available(r, root)]
    if not config.get('steal_enabled') or not candidates:
        return []
    if config.get('steal_mode', 'average') == 'average':
        share = percent(config.get('steal_probability', 15)) / len(candidates)
        return [(r, share) for r in candidates]
    values = [(r, percent(r['probability'])) for r in candidates]
    if sum(p for _, p in values) > 100 + 1e-9:
        raise ValueError('抢饭概率合计超过100%，请在角色页面调整。')
    return values


def choose_guest(config, root, roll, name=None):
    if name is not None:
        role = next((r for r in config.get('guest_roles', []) if r['name'] == name), None)
        if role is None:
            raise ValueError('没有这个抢饭角色，请检查角色名称。')
        if not role['enabled'] or not available(role, root):
            raise ValueError('该抢饭角色未启用，或人格、参考图尚未配置完整。')
        return copy.deepcopy(role)
    ceiling = 0.0
    for role, probability in distribution(config, root):
        ceiling += probability
        if roll * 100 < ceiling:
            return copy.deepcopy(role)
    return None
