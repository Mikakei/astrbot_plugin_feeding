"""Dynamic guest shortcuts that respect AstrBot's wake and permission pipeline."""
from astrbot.api.event import filter


def parse_shortcut(text):
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    head = parts[0]
    # These commands already have explicit handlers; never handle them twice.
    if not head.startswith('投喂') or head in ('投喂', '投喂状态', '投喂角色', '投喂大肥鱼'):
        return None
    return head[len('投喂'):], parts[1] if len(parts) > 1 else ''


class GuestShortcutFilter(filter.CustomFilter):
    def filter(self, event, cfg):
        # Bare regex handlers can wake ordinary chat. Require the same wake flag
        # as CommandFilter, after the framework has handled the configured prefix.
        return bool(event.is_at_or_wake_command and parse_shortcut(event.get_message_str()))
