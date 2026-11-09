from __future__ import annotations

import asyncio
import json
import random
import shutil
import time
import uuid
from pathlib import Path

import aiohttp
import yaml
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Reply, Plain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_temp_path
from astrbot.api.web import request, json_response, error_response

from .core import (FeedingError, Journal, discover_models, generate_image, feeding_prompt,
                   image_model_candidates, image_prompt, load_image, parse_food, normalize_image)
from .connections import resolve_image_credentials, migrate_connection_config
from .customization import DEFAULTS, customization, revision, validate_settings
from .guests import migrate_guests, choose_guest, available, reference_valid, DEFAULT_NOTICE, initialize_default_guest
from .guest_commands import GuestShortcutFilter, parse_shortcut


VERSION = str(yaml.safe_load(Path(__file__).with_name('metadata.yaml').read_text(encoding='utf-8'))['version']).removeprefix('v')


@register('astrbot_plugin_feeding', 'Local', '根据角色设定图生成投喂插画，沿用当前会话人格', VERSION)
class FeedingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        if migrate_connection_config(self.config):
            self.config.save_config()
        self.root = Path(StarTools.get_data_dir('astrbot_plugin_feeding'))
        initialized = initialize_default_guest(self.config, self.root)
        if migrate_guests(self.config) or initialized:
            self.config.save_config()
        for folder in ('references', 'inputs', 'outputs'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        # Migrate existing primary-character assets once; never restore removed uploads.
        if not self.config.get('reference_migration_done', False):
            for field, legacy in (('chibi_reference', 'chibi4.png'), ('turnaround_reference', 'turnaround.png')):
                source = self.root / 'references' / legacy
                if not self.config.get(field) and source.is_file():
                    dest = self.root / 'files' / field / 'reference.png'
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
                    self.config[field] = [dest.relative_to(self.root).as_posix()]
            self.config['reference_migration_done'] = True
            self.config.save_config()
        self.journal = Journal(self.root / 'tasks.json')
        # One shared worker across all groups; old configuration cannot enable parallel generation.
        self.concurrency = 1
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.tasks: set[asyncio.Task] = set()
        self.last_cleanup = 0.0
        self.reference_cache = {}
        self.catalog_task: asyncio.Task | None = None
        self.catalog_signature = ''
        self.catalog_models: list[str] = []
        self.catalog_refreshed = 0.0
        self.customization_lock = asyncio.Lock()
        for route, handler, methods in (
            ('customization', self.page_customization, ['GET']),
            ('customization/save', self.page_save_customization, ['POST']),
            ('reference/<role>', self.page_upload_reference, ['POST']),
        ):
            context.register_web_api('/astrbot_plugin_feeding/' + route, handler, methods, '投喂定制页面')

    def page_data(self):
        settings = customization(self.config)
        return {'settings': settings, 'revision': revision(settings), 'defaults': DEFAULTS,
                'guest_notice_default': DEFAULT_NOTICE,
                'guest_availability': {r['id']: available(r, self.root) for r in settings['guest_roles']},
                'guest_reference_validity': {r['id']: {
                    'reference': reference_valid(r['reference'], self.root),
                    'secondary': reference_valid(r['secondary'], self.root, optional=True)
                } for r in settings['guest_roles']}}

    async def page_customization(self):
        return json_response(self.page_data())

    async def page_save_customization(self):
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response('请求格式不正确。', status_code=400)
        async with self.customization_lock:
            if payload.get('revision') != revision(customization(self.config)):
                return error_response('设置已在其他页面更新，请重新载入后再保存。', status_code=409)
            try:
                settings = validate_settings(payload.get('settings'), self.root)
            except ValueError as exc:
                return error_response(str(exc), status_code=400)
            previous = dict(self.config)
            try:
                self.config.update(settings)
                self.config.save_config()
            except Exception:
                self.config.clear()
                self.config.update(previous)
                return error_response('保存失败，请稍后重试。', status_code=500)
            return json_response(self.page_data())

    async def page_upload_reference(self, role):
        if role not in ('chibi_reference', 'turnaround_reference', 'guest_reference'):
            return error_response('上传位置无效。', status_code=400)
        files = await request.files()
        upload = files.get('file')
        if upload is None:
            return error_response('请选择图片。', status_code=400)
        try:
            raw = await upload.read(10 * 1024 * 1024 + 1)
            data = await asyncio.to_thread(normalize_image, raw, 10 * 1024 * 1024, 20_000_000)
        except FeedingError as exc:
            return error_response(str(exc), status_code=400)
        folder = self.root / 'files' / role
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (uuid.uuid4().hex + '.png')
        await asyncio.to_thread(target.write_bytes, data)
        return json_response({'path': target.relative_to(self.root).as_posix()})

    async def initialize(self):
        await self.refresh_model_options()
        self.catalog_task = asyncio.create_task(self.watch_model_options())

    def model_schema(self):
        schema = getattr(self.config, 'schema', None)
        return schema.get('image_model') if isinstance(schema, dict) else None

    async def refresh_model_options(self):
        field = self.model_schema()
        if field is None:
            return
        current = str(self.config.get('image_model', 'gpt-image-1.5'))
        signature = ''
        try:
            base, key = self.credentials()
            signature = Journal.key(base + '\0' + key)
            if self.catalog_signature != signature:
                self.catalog_models = []
            field['options'] = sorted(set(self.catalog_models + [current]), key=str.casefold)
            field['hint'] = '正在读取当前 API 的生图模型列表；已有选择会保留。'
            available = await discover_models(base, key)
            # Ignore results from a service changed while the request was in flight.
            now_base, now_key = self.credentials()
            if Journal.key(now_base + '\0' + now_key) != signature:
                return
            self.catalog_models = image_model_candidates(available)
            self.catalog_signature = signature
            field['options'] = sorted(set(self.catalog_models + [current]), key=str.casefold)
            missing = ' 当前选择未出现在 API 列表中，已保留。' if current not in available else ''
            field['hint'] = (
                f'已从当前 API 读取 {len(self.catalog_models)} 个生图候选模型。选择后点击保存。'
                '模型需支持参考图编辑；列表每5分钟更新，重新加载插件可立即更新。' + missing
            )
        except Exception as exc:
            field['options'] = sorted(set(self.catalog_models + [current]), key=str.casefold)
            field['hint'] = '暂时无法读取 API 模型列表，已保留当前选择。请检查图像 API 配置后保存并重新加载插件。'
            logger.warning('Feeding model list refresh failed (%s)', type(exc).__name__)
        finally:
            self.catalog_signature = signature
            self.catalog_refreshed = time.monotonic()

    async def watch_model_options(self):
        while True:
            await asyncio.sleep(10)
            try:
                base, key = self.credentials()
                signature = Journal.key(base + '\0' + key)
            except FeedingError:
                signature = ''
            if signature != self.catalog_signature or time.monotonic() - self.catalog_refreshed >= 300:
                await self.refresh_model_options()

    def command_name(self, event, name='投喂'):
        prefixes = self.context.get_config(event.unified_msg_origin).get('wake_prefix', ['#'])
        prefix = '#' if '#' in prefixes else (prefixes[0] if prefixes else '')
        return prefix + name

    async def sources(self, event):
        chain = event.get_messages()
        direct = [c.url or c.file or c.path for c in chain if isinstance(c, Image)]
        if direct:
            return [s for s in direct if s]
        refs = [c for c in chain if isinstance(c, Reply)]
        images = [c.url or c.file or c.path for ref in refs for c in (ref.chain or []) if isinstance(c, Image)]
        if images:
            return [s for s in images if s]
        # Some OneBot messages only include the quoted message id.
        if refs and event.get_platform_name() == 'aiocqhttp':
            result = await asyncio.wait_for(event.bot.api.call_action('get_msg', message_id=int(refs[0].id)), 15)
            message = result.get('message', [])
            if isinstance(message, list):
                return [s['data'].get('url') or s['data'].get('file') for s in message
                        if s.get('type') == 'image' and isinstance(s.get('data'), dict)
                        and (s['data'].get('url') or s['data'].get('file'))]
        return []

    async def persona_prompt(self, event):
        umo = event.unified_msg_origin
        mgr = self.context.conversation_manager
        cid = await mgr.get_curr_conversation_id(umo)
        conv = await mgr.get_conversation(umo, cid) if cid else None
        _, persona, _, _ = await self.context.persona_manager.resolve_selected_persona(
            umo=umo, conversation_persona_id=conv.persona_id if conv else None,
            platform_name=event.get_platform_name(),
            provider_settings=self.context.get_config(umo).get('provider_settings', {}),
        )
        return persona.get('prompt', '') if persona else ''

    def credentials(self):
        try:
            return resolve_image_credentials(self.config, self.context.get_provider_by_id)
        except ValueError as exc:
            raise FeedingError(str(exc)) from None

    async def load_reference(self, name):
        field = {'chibi4.png': 'chibi_reference', 'turnaround.png': 'turnaround_reference', 'guest.png': 'guest_reference'}[name]
        label = '抢饭角色参考图' if field == 'guest_reference' else ('角色主参考图' if field == 'chibi_reference' else '角色辅助参考图')
        selected = self.config.get(field, [])
        if field == 'turnaround_reference' and selected == []:
            return None
        if not isinstance(selected, list) or len(selected) != 1 or not isinstance(selected[0], str):
            raise FeedingError(f'请管理员在插件后台上传且仅保留一张{label}，保存后再试。')
        path = (self.root / selected[0]).resolve()
        if not path.is_relative_to((self.root / 'files' / field).resolve()) or not path.is_file():
            raise FeedingError(f'{label}不存在或位置无效，请管理员重新上传并保存。')
        stat = path.stat()
        signature = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = self.reference_cache.get(name)
        if cached and cached[0] == signature:
            return cached[1]
        data = await load_image(str(path), max_bytes=10 * 1024 * 1024, max_pixels=20_000_000,
                                local_roots=[self.root / 'files' / field])
        self.reference_cache[name] = (signature, data)
        return data

    def cleanup(self):
        if time.time() - self.last_cleanup < 3600:
            return
        self.last_cleanup = time.time()
        cutoff = time.time() - 7 * 86400
        for folder in ('inputs', 'outputs'):
            for p in (self.root / folder).glob('*.png'):
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()

    @filter.command('投喂')
    async def feed(self, event: AstrMessageEvent):
        await self.feed_request(event)

    @filter.command('投喂大肥鱼')
    async def feed_guest(self, event: AstrMessageEvent):
        event.stop_event()
        if not event.is_admin():
            await event.send(event.plain_result('只有 AstrBot 管理员可以指定抢饭角色。'))
            return
        await self.feed_request(event, force_guest=True, guest_name='大肥鱼')

    @filter.custom_filter(GuestShortcutFilter)
    async def feed_shortcut_guest(self, event: AstrMessageEvent):
        parsed = parse_shortcut(event.get_message_str())
        if parsed is None:
            return
        event.stop_event()
        if not event.is_admin():
            await event.send(event.plain_result('只有 AstrBot 管理员可以指定抢饭角色。'))
            return
        name, note = parsed
        await self.feed_request(event, force_guest=True, guest_name=name, note_override=note)

    @filter.command('投喂角色')
    async def feed_named_guest(self, event: AstrMessageEvent):
        event.stop_event()
        if not event.is_admin():
            await event.send(event.plain_result('只有 AstrBot 管理员可以指定抢饭角色。'))
            return
        parts = event.get_message_str().partition('投喂角色')[2].strip().split(maxsplit=1)
        if not parts:
            await event.send(event.plain_result('请填写角色名称，并附带或引用食物图片。'))
            return
        await self.feed_request(event, force_guest=True, guest_name=parts[0],
                                note_override=parts[1] if len(parts) > 1 else '')

    async def feed_request(self, event, force_guest=False, guest_name=None, note_override=None):
        event.stop_event()
        mid = None
        try:
            blocked = {str(uid).strip() for uid in self.config.get('blocked_users', [])}
            if str(event.get_sender_id()).strip() in blocked:
                await event.send(event.plain_result('你的投喂我暂时不收了。'))
                return
            if self.config.get('admin_only', True) and not event.is_admin():
                await event.send(event.plain_result('当前投喂仅限 AstrBot 管理员使用。'))
                return
            images = await self.sources(event)
            if not images:
                await event.send(event.plain_result(f'请发送 {self.command_name(event)} 和一张食物图片，或回复食物图片发送该命令。'))
                return
            try:
                profile = choose_guest(self.config, self.root, random.random(), guest_name if force_guest else None)
            except ValueError as exc:
                raise FeedingError(str(exc)) from None
            guest = profile is not None
            settings = {
                'customization': customization(self.config),
                'credentials': self.credentials(),
                'image_model': str(self.config.get('image_model', 'gpt-image-1.5')),
                'image_timeout': max(30, min(600, int(self.config.get('image_timeout', 300)))),
                'image_size': str(self.config.get('image_size', '512x512')),
                'image_quality': str(self.config.get('image_quality', 'low')),
                'vision_provider_id': str(self.config.get('vision_provider_id', '')).strip(),
                'character_prompt': str(self.config.get('character_prompt', '')).strip()[:4000],
                'guest': guest,
                'guest_persona': profile['persona'] if profile else '',
                'guest_notice': profile.get('notice', DEFAULT_NOTICE).strip() if profile else '',
            }
            if guest:
                if not settings['guest_persona']:
                    raise FeedingError('请先在角色与回复定制页面填写抢饭角色人格。')
                settings['references'] = []
                for source in profile['reference'] + profile['secondary']:
                    settings['references'].append(await load_image(str((self.root / source).resolve()),
                        max_bytes=10 * 1024 * 1024, max_pixels=20_000_000,
                        local_roots=[self.root / 'files/guest_reference']))
                settings['character_prompt'] = profile['character_prompt']
            else:
                settings['references'] = [await self.load_reference('chibi4.png')]
                secondary = await self.load_reference('turnaround.png')
                if secondary is not None:
                    settings['references'].append(secondary)
            mid = self.journal.admit(
                str(event.unified_msg_origin) + ':' + str(event.message_obj.message_id),
                event.get_platform_id() + ':' + str(event.get_sender_id()),
                max(0, int(self.config.get('cooldown_seconds', 60))),
                self.concurrency + max(0, min(20, int(self.config.get('queue_size', 3)))),
                settings['customization'],
                group=(event.get_platform_id() + ':' + str(event.get_group_id()))
                      if event.get_group_id() else '',
                daily_limit=max(0, int(self.config.get('group_daily_limit', 30))),
                admin=event.is_admin(),
            )
            if mid == 'duplicate':
                return
            self.journal.records[mid]['actor'] = 'guest' if guest else 'host'
            if profile:
                self.journal.records[mid]['guest_id'] = profile['id']
            self.journal.save()
            self.cleanup()
            text = event.get_message_str().strip()
            note = text.partition('投喂大肥鱼' if force_guest else '投喂')[2].strip()[:500]
            if note_override is not None:
                note = note_override[:500]
            extra = ' ' + settings['customization']['reply_multiple'] if len(images) > 1 else ''
            await event.send(event.plain_result(settings['customization']['reply_received'] + extra))
            task = asyncio.create_task(self.process(event, mid, images[0], note, settings))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        except FeedingError as exc:
            if mid and mid != 'duplicate':
                self.journal.finish(mid, 'failed')
            await event.send(event.plain_result(str(exc)))
        except Exception as exc:
            if mid and mid != 'duplicate':
                self.journal.finish(mid, 'failed')
            logger.warning('Feeding admission failed (%s)', type(exc).__name__)
            await event.send(event.plain_result('暂时无法读取这次投喂，请重新发送图片。'))

    async def process(self, event, mid, source, note, settings):
        input_path = self.root / 'inputs' / f'{mid}.png'
        stage = 'download'
        started = time.monotonic()
        timings = {}
        try:
            # Download before waiting, while QQ's signed URL is still valid.
            food_image = await load_image(source, max_bytes=10 * 1024 * 1024, max_pixels=20_000_000,
                local_roots=[self.root / 'inputs', Path(get_astrbot_temp_path()),
                             Path(get_astrbot_data_path()) / 'tmp'])
            await asyncio.to_thread(input_path.write_bytes, food_image)
            timings['download'] = round(time.monotonic() - started, 2)
            queued_at = time.monotonic()
            async with self.semaphore:
                timings['queue'] = round(time.monotonic() - queued_at, 2)
                self.journal.finish(mid, 'running')
                stage = 'vision'
                phase_started = time.monotonic()
                persona = await self.persona_prompt(event)
                if settings.get('guest'):
                    persona = (
                        '这是一次条件角色扮演投喂任务。先按任务规则独立判断食物是否合适。'
                        '只有 verdict=food 且 is_food=true 时，使用下方抢饭角色人格，'
                        '表现她抢先拿走原本递给另一位角色的食物；点评、动作、表情均属于抢饭角色。'
                        '其他分类使用原角色人格拒绝或追问，不发生抢饭。'
                        '不解释随机概率或后台机制，不混合两个角色外观，不编造原角色姓名。'
                        '正常食物的抢饭点评先确定一个最突出的情绪瞬间，让 reply、action、expression 一致。'
                        'expression 必须具体写出眼睑开合与视线、眉毛角度、嘴型和身体姿态，'
                        '不能只写开心、可爱、微笑或脸红，也不能仅用鲸尾和手部动作代替面部变化。'
                        '抢饭只是出场方式，不等于偷窃、做坏事或被抓包。默认自在地享用食物，'
                        '优先从自然开心、期待、惊喜、满足、专注品尝中选择贴合情境的情绪，'
                        '不必每次写借口、试探别人反应、护食或担心原角色回来。'
                        '例如满足可闭眼弯眉、鼓腮咀嚼、肩膀放松；'
                        '期待可眉毛轻扬、视线落在食物上、身体自然前倾；'
                        '惊喜可双眼明亮、眉毛扬起、嘴巴自然张开；'
                        '认真品尝可轻收眼睑、看着手里的食物、嘴唇轻抿或自然咀嚼。'
                        '被用户取绰号而反驳时可蹙眉、脸颊鼓起、嘴巴忙着反驳；'
                        '只有用户说明或明确情境提到偷偷拿走、被发现等，才采用侧目躲闪、'
                        '不对称眉毛、心虚笑或狡黠护食，不要仅因发生抢饭就使用这些表情。'
                        '以上只是情绪与表情的对应示例，'
                        '根据用户说明与本次点评选择，不固定套用，不为换表情编造辣烫等食物性质。'
                        '\n原角色人格：\n' + persona + '\n抢饭角色人格：\n' + settings['guest_persona']
                    )
                pid = settings['vision_provider_id']
                pid = pid or await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
                task_prompt = feeding_prompt(note, '' if settings.get('guest') else settings['customization']['review_prompt'])
                response = await asyncio.wait_for(self.context.llm_generate(
                    chat_provider_id=pid, prompt=task_prompt, image_urls=[str(input_path)],
                    system_prompt=persona, contexts=[],
                ), timeout=90)
                food = parse_food(response.completion_text)
                timings['vision'] = round(time.monotonic() - phase_started, 2)
                if not food['is_food']:
                    malicious = food.get('verdict') == 'malicious'
                    fallback = settings['customization']['reply_malicious' if malicious else 'reply_non_food']
                    await event.send(event.plain_result(food['reply'] or fallback))
                    self.journal.finish(mid, 'rejected' if malicious else 'not_food')
                    return
                stage = 'reference'
                references = settings['references']
                stage = 'generation'
                phase_started = time.monotonic()
                base, key = settings['credentials']
                drawing_prompt = image_prompt(food, settings['character_prompt'], len(references) == 2)
                if settings.get('guest'):
                    drawing_prompt += (
                        '\nFor this food-snatching scene, redraw the face for the expression and reaction above. '
                        'The reference is an identity sheet, not a facial-pose template. '
                        'Keep eye COLOR and character identity, but let eyelids, eyebrows, gaze direction, '
                        'cheeks and mouth change visibly with the specific emotional moment. '
                        'Do not copy the reference\'s wide-open sparkling eyes and small cheerful smile by default. '
                        'Natural joy, anticipation, delighted surprise, relaxed closed-eye enjoyment and attentive tasting '
                        'are all valid. Food-snatching does not automatically imply guilt or mischief. '
                        'Use shifty side-eyes, asymmetric scheming brows or guilty smirks only when explicitly '
                        'supported by the user situation, never as the default character face. '
                        'Do not substitute blush, sparkles, tail movement '
                        'or a changed hand pose for the requested facial expression. '
                        'The eyes need not look at the camera; direct the gaze according to the reaction. '
                        'Appearance notes specify stable anatomy and clothing, not a fixed facial expression. '
                        'Render one coherent emotional moment, not a collage of several expressions.'
                    )
                raw = await generate_image(base, key, settings['image_model'],
                    drawing_prompt, references + [food_image],
                    timeout=settings['image_timeout'], size=settings['image_size'], quality=settings['image_quality'])
                timings['generation'] = round(time.monotonic() - phase_started, 2)
                output = self.root / 'outputs' / f'{mid}.png'
                await asyncio.to_thread(output.write_bytes, raw)
                # Persist before sending so delivery failures cannot trigger another paid generation.
                self.journal.finish(mid, 'generated')
                stage = 'delivery'
                fallback = '这份我先接走啦。' if settings.get('guest') else settings['customization']['reply_done']
                # Announce only after generation has finished, immediately before the result.
                # Keep the two platform messages separate; never announce while queued/drawing.
                notice = settings.get('guest_notice', '') if settings.get('guest') else ''
                if notice:
                    await event.send(event.plain_result(notice))
                await event.send(event.chain_result([Plain(food['reply'] or fallback), Image.fromFileSystem(str(output))]))
                self.journal.finish(mid, 'done')
                logger.info('Feeding complete task=%s', mid[:12])
        except asyncio.CancelledError:
            self.journal.finish(mid, 'interrupted')
            raise
        except Exception as exc:
            self.journal.finish(mid, 'delivery_failed' if stage == 'delivery' else 'failed')
            logger.warning('Feeding failed task=%s stage=%s type=%s', mid[:12], stage, type(exc).__name__)
            if isinstance(exc, FeedingError):
                message = str(exc)
            elif stage == 'delivery':
                message = '图片已生成并保存在插件数据目录，但 QQ 发送失败，请联系管理员；不会重新绘制。'
            elif isinstance(exc, asyncio.TimeoutError):
                message = '食物识别或下载等待超时，请稍后再试。'
            elif isinstance(exc, aiohttp.ClientError):
                message = '图片下载或模型连接失败，请稍后再试。'
            else:
                message = '这次投喂未能完成，请管理员检查插件日志；本次未自动重试。'
            try:
                await event.send(event.plain_result(message))
            except Exception:
                logger.warning('Feeding failure notice could not be delivered task=%s', mid[:12])
        finally:
            timings['total'] = round(time.monotonic() - started, 2)
            logger.info('Feeding timing task=%s seconds=%s', mid[:12], json.dumps(timings))
            input_path.unlink(missing_ok=True)

    @filter.command('投喂状态')
    async def status(self, event: AstrMessageEvent):
        event.stop_event()
        if not event.is_admin():
            await event.send(event.plain_result('仅 AstrBot 管理员可查看投喂状态。'))
            return
        counts = {}
        now = time.time()
        for r in self.journal.records.values():
            if now - 86400 <= r['at'] <= now:
                counts[r['status']] = counts.get(r['status'], 0) + 1
        try:
            self.credentials()
            configured = '已配置'
        except FeedingError:
            configured = '未配置'
        vision = str(self.config.get('vision_provider_id', '')).strip()
        vision = vision or await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
        await event.send(event.plain_result(
            f'身临其境的投喂 v{VERSION}｜服务{configured}\n'
            f'人格：跟随当前会话｜入口：{self.command_name(event)}\n'
            f'识图模型：{vision}\n'
            f'生图模型：{self.config.get("image_model", "gpt-image-1.5")}\n'
            f'生图规格：{self.config.get("image_size", "512x512")} / {self.config.get("image_quality", "low")}\n'
            f'近24小时任务：{json.dumps(counts, ensure_ascii=False)}'
        ))

    async def terminate(self):
        if self.catalog_task:
            self.catalog_task.cancel()
            await asyncio.gather(self.catalog_task, return_exceptions=True)
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for mid, record in list(self.journal.records.items()):
            if record['status'] in ('queued', 'running'):
                self.journal.finish(mid, 'interrupted')
