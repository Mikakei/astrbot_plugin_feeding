"""Resolve one complete connection without mixing credential sources."""


def migrate_connection_config(config):
    """Move legacy fields once, then clear them so they cannot reappear."""
    legacy = {name: config.get(name, '') for name in ('image_base_url', 'image_api_key')}
    if not any(legacy.values()):
        return False
    advanced = config.get('advanced') or {}
    if not isinstance(advanced, dict):
        raise ValueError('高级选项配置格式不正确，请检查插件配置。')
    if not any(advanced.get(name) for name in legacy):
        config['advanced'] = {**advanced, **legacy}
    for name in legacy:
        config[name] = ''
    return True


def resolve_image_credentials(config, get_provider):
    advanced = config.get('advanced', {})
    if not isinstance(advanced, dict):
        raise ValueError('高级选项配置格式不正确，请检查插件配置。')
    base = str(advanced.get('image_base_url') or '').strip()
    key = str(advanced.get('image_api_key') or '').strip()
    if base or key:
        if not base or not key:
            raise ValueError('自定义绘图连接必须同时填写地址和密钥；或同时清空以复用 AstrBot 提供商。')
        return base.rstrip('/'), key

    pid = str(config.get('image_provider_id') or '').strip()
    if not pid:
        raise ValueError('请管理员在投喂插件后台选择绘图连接提供商。')
    provider = get_provider(pid)
    if provider is None:
        raise ValueError('所选绘图提供商不可用，请管理员在插件后台重新选择。')
    base = str(provider.provider_config.get('api_base') or '').strip()
    keys = provider.get_keys()
    if isinstance(keys, str):
        keys = [keys]
    key = next((value.strip() for value in (keys or [])
                if isinstance(value, str) and value.strip()), '')
    if not base or not key:
        raise ValueError('所选提供商缺少 API 地址或密钥，请在 AstrBot 提供商配置中补全。')
    return base.rstrip('/'), key
