import asyncio
import base64
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api.message_components import Image, Reply
from astrbot_plugin_feeding.main import FeedingPlugin
from test_core import png


class Event:
    unified_msg_origin='test:FriendMessage:123'
    def __init__(self,chain=None,mid='1',admin=True,text='投喂 今天做的饭'):
        self.chain=chain or [];self.message_obj=SimpleNamespace(message_id=mid)
        self.sent=[];self.admin=admin;self.stopped=False;self.text=text
        self.bot=SimpleNamespace(api=SimpleNamespace(call_action=AsyncMock(return_value={'message':[{'type':'image','data':{'url':'https://example.com/food.png'}}]})))
    def get_messages(self): return self.chain
    def is_admin(self): return self.admin
    def get_platform_name(self): return 'aiocqhttp'
    def get_platform_id(self): return 'test'
    def get_sender_id(self): return '123'
    def get_group_id(self): return getattr(self, 'group', '')
    def get_message_str(self): return self.text
    def stop_event(self): self.stopped=True
    def plain_result(self,text): return text
    def chain_result(self,chain): return chain
    async def send(self,result): self.sent.append(result)


class Config(dict):
    def __init__(self,path):
        super().__init__({'cooldown_seconds':0, 'image_provider_id':'test-provider', 'reference_migration_done':True, 'chibi_reference':['files/chibi_reference/test.png'], 'turnaround_reference':['files/turnaround_reference/test.png']});self.path=path;self.saves=0
        self.schema={'image_model':{'type':'string','default':'gpt-image-1.5','options':['gpt-image-1.5']}}
    def save_config(self):self.path.write_text(json.dumps(self));self.saves+=1


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_only_exposes_customization_and_saves_without_other_changes(self):
        from astrbot_plugin_feeding.customization import DEFAULTS
        self.plugin.config['advanced']={'image_api_key':'private-test-key'}
        data=self.plugin.page_data()
        self.assertEqual(set(data['settings']),set(DEFAULTS))
        self.assertNotIn('private-test-key',json.dumps(data))
        data['settings']['review_prompt']='保持克制，简短回应。'
        with patch('astrbot_plugin_feeding.main.request',SimpleNamespace(json=AsyncMock(return_value=data))):
            response=await self.plugin.page_save_customization()
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.plugin.config['review_prompt'],'保持克制，简短回应。')
        self.assertEqual(self.plugin.config['advanced']['image_api_key'],'private-test-key')
        saved=json.loads(self.plugin.config.path.read_text())
        self.assertEqual(saved['review_prompt'],'保持克制，简短回应。')
        with patch('astrbot_plugin_feeding.main.request',SimpleNamespace(json=AsyncMock(return_value=data))):
            response=await self.plugin.page_save_customization()
        self.assertEqual(response.status_code,409)

    async def test_page_rejects_unknown_fields_and_oversized_text(self):
        for key,value in [('image_api_key','x'),('review_prompt','x'*4001),('reply_received','')]:
            data=self.plugin.page_data()
            data['settings'][key]=value
            with patch('astrbot_plugin_feeding.main.request',SimpleNamespace(json=AsyncMock(return_value=data))):
                response=await self.plugin.page_save_customization()
            self.assertEqual(response.status_code,400)

    async def test_page_save_failure_restores_memory(self):
        data=self.plugin.page_data();data['settings']['review_prompt']='new'
        with patch('astrbot_plugin_feeding.main.request',SimpleNamespace(json=AsyncMock(return_value=data))), \
             patch.object(self.plugin.config,'save_config',side_effect=OSError('disk full')):
            response=await self.plugin.page_save_customization()
        self.assertEqual(response.status_code,500)
        self.assertEqual(self.plugin.page_data()['settings']['review_prompt'],'')

    async def test_page_upload_is_validated_and_not_selected_until_save(self):
        before=self.plugin.config['chibi_reference'][:]
        upload=SimpleNamespace(read=AsyncMock(return_value=png()))
        with patch('astrbot_plugin_feeding.main.request',SimpleNamespace(files=AsyncMock(return_value={'file':upload}))):
            response=await self.plugin.page_upload_reference('chibi_reference')
        path=json.loads(response.body)['path']
        self.assertTrue((self.root/path).is_file())
        self.assertEqual(self.plugin.config['chibi_reference'],before)
        upload.read.assert_awaited_once_with(10*1024*1024+1)
        response=await self.plugin.page_upload_reference('../outside')
        self.assertEqual(response.status_code,400)

    async def test_custom_review_and_ack_reach_task_without_replacing_persona(self):
        self.plugin.config.update(review_prompt='保持正式与克制',reply_received='已接收。')
        event=Event([Image.fromBase64(base64.b64encode(png()).decode())])
        with patch('astrbot_plugin_feeding.main.generate_image',new_callable=AsyncMock,return_value=png()):
            await self.plugin.feed(event)
            await asyncio.gather(*self.plugin.tasks)
        self.assertEqual(event.sent[0],'已接收。')
        call=self.context.llm_generate.await_args.kwargs
        self.assertIn('保持正式与克制',call['prompt'])
        self.assertEqual(call['system_prompt'],'current personality')

    async def test_one_character_reference_is_sufficient(self):
        self.plugin.config['turnaround_reference'] = []
        event=Event([Image.fromBase64(base64.b64encode(png()).decode())])
        with patch('astrbot_plugin_feeding.main.generate_image',new_callable=AsyncMock,return_value=png()) as generate:
            await self.plugin.feed(event)
            await asyncio.gather(*self.plugin.tasks)
            generate.assert_awaited_once()
            self.assertEqual(len(generate.await_args.args[4]),2)
            self.assertIn('Image 2 is ONLY the food',generate.await_args.args[3])
            self.assertNotIn('SECONDARY',generate.await_args.args[3])

    async def test_missing_or_multiple_references_stop_before_llm(self):
        for selected in ([], ['files/chibi_reference/test.png'] * 2, ['../private.png']):
            self.plugin.config['chibi_reference'] = selected
            event = Event([Image.fromBase64(base64.b64encode(png()).decode())])
            await self.plugin.feed(event)
            self.assertFalse(self.plugin.tasks)
            self.assertTrue(event.sent)
        self.context.llm_generate.assert_not_awaited()

    async def test_legacy_references_migrate_once_without_restoring_deleted_upload(self):
        await self.plugin.terminate()
        for name in ('chibi4.png', 'turnaround.png'):
            (self.root/'references'/name).write_bytes(png())
        config=Config(self.root/'config.json')
        config.update(reference_migration_done=False,chibi_reference=[],turnaround_reference=[])
        with patch('astrbot_plugin_feeding.main.StarTools.get_data_dir',return_value=self.root):
            self.plugin=FeedingPlugin(self.context,config)
        self.assertTrue(config['reference_migration_done'])
        self.assertTrue((self.root/config['chibi_reference'][0]).is_file())
        await self.plugin.terminate()
        config['chibi_reference']=[]
        with patch('astrbot_plugin_feeding.main.StarTools.get_data_dir',return_value=self.root):
            self.plugin=FeedingPlugin(self.context,config)
        self.assertEqual(config['chibi_reference'],[])

    async def test_status_filters_at_query_time_without_changing_journal(self):
        now = 200000.0
        self.plugin.journal.records = {
            'expired': {'at': now - 86401, 'status': 'failed'},
            'boundary': {'at': now - 86400, 'status': 'done'},
            'recent': {'at': now - 10, 'status': 'done'},
            'future': {'at': now + 10, 'status': 'failed'},
        }
        original = json.loads(json.dumps(self.plugin.journal.records))
        event = Event()
        with patch('astrbot_plugin_feeding.main.time.time', return_value=now):
            await self.plugin.status(event)
        counts = json.loads(event.sent[-1].split('近24小时任务：', 1)[1])
        self.assertEqual(counts, {'done': 2})
        self.assertEqual(self.plugin.journal.records, original)
        with patch('astrbot_plugin_feeding.main.time.time', return_value=now + 86420):
            await self.plugin.status(event)
        self.assertEqual(json.loads(event.sent[-1].split('近24小时任务：', 1)[1]), {})

    async def test_status_version_comes_from_metadata(self):
        import yaml
        metadata = yaml.safe_load((Path(__file__).parents[1] / 'metadata.yaml').read_text(encoding='utf-8'))
        event = Event()
        await self.plugin.status(event)
        self.assertIn(metadata['display_name'] + ' ' + metadata['version'] + '｜', event.sent[-1])

    async def test_status_still_requires_admin(self):
        event = Event(admin=False)
        await self.plugin.status(event)
        self.assertIn('仅 AstrBot 管理员', event.sent[-1])
        self.assertNotIn('近24小时任务', event.sent[-1])

    async def test_missing_verdict_never_generates_an_image(self):
        self.context.llm_generate.return_value = SimpleNamespace(completion_text=json.dumps({
            'is_food': True, 'description': '橘子', 'reply': '吃吧', 'action': '吃'}))
        event = Event([Image.fromBase64(base64.b64encode(png()).decode())])
        with patch('astrbot_plugin_feeding.main.generate_image', new_callable=AsyncMock) as generate:
            await self.plugin.feed(event)
            await asyncio.gather(*self.plugin.tasks)
            generate.assert_not_awaited()
        self.assertIn('没看清', event.sent[-1])

    async def test_local_images_follow_framework_paths_and_reject_outside_files(self):
        data = self.root / 'custom-root' / 'data'
        temp = data / 'temp'
        temp.mkdir(parents=True)
        inside = temp / '食物 photo.png'
        outside = self.root / 'outside.png'
        inside.write_bytes(png())
        outside.write_bytes(png())
        with patch('astrbot_plugin_feeding.main.get_astrbot_temp_path', return_value=str(temp)), \
             patch('astrbot_plugin_feeding.main.get_astrbot_data_path', return_value=str(data)), \
             patch('astrbot_plugin_feeding.main.generate_image', new_callable=AsyncMock, return_value=png()) as generate:
            for index, path in enumerate((inside, outside)):
                event = Event([Image.fromFileSystem(str(path))], mid=str(index))
                await self.plugin.feed(event)
                await asyncio.gather(*self.plugin.tasks)
            generate.assert_awaited_once()
        self.assertIn('无法读取', event.sent[-1])

    async def test_partial_custom_credentials_never_reach_catalog_or_models(self):
        self.plugin.config['advanced'] = {'image_base_url': 'https://custom.example/v1'}
        with patch('astrbot_plugin_feeding.main.discover_models', new_callable=AsyncMock) as discover:
            await self.plugin.refresh_model_options()
            discover.assert_not_awaited()
        event = Event([Image.fromURL('https://example.com/food.png')])
        await self.plugin.feed(event)
        self.assertIn('同时填写', event.sent[-1])
        self.assertFalse(self.plugin.tasks)
        self.context.llm_generate.assert_not_awaited()

    async def test_legacy_whitelist_setting_does_not_add_a_permission_gate(self):
        # Events here have already passed the framework's permission pipeline.
        self.plugin.config.update(admin_only=False, whitelist_groups_only=True)
        for group, admin in [('', False), ('group', False), ('group', True)]:
            event = Event(admin=admin)
            event.group = group
            await self.plugin.feed(event)
            self.assertIn('#投喂', event.sent[-1])
        self.plugin.config['admin_only'] = True
        event = Event(admin=False)
        await self.plugin.feed(event)
        self.assertIn('管理员', event.sent[-1])
        self.context.llm_generate.assert_not_awaited()

    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.context=SimpleNamespace(
            register_web_api=lambda *args:None,
            get_config=lambda *args:{'wake_prefix':['橘子','#']},
            get_provider_by_id=lambda pid:SimpleNamespace(provider_config={'api_base':'http://example.invalid/v1'},get_keys=lambda:['test-key']),
            conversation_manager=SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value='cid'),get_conversation=AsyncMock(return_value=SimpleNamespace(persona_id='conversation-persona'))),
            persona_manager=SimpleNamespace(resolve_selected_persona=AsyncMock(return_value=('session-persona',{'prompt':'current personality'},None,False))),
            get_current_chat_provider_id=AsyncMock(return_value='current-provider'),
            llm_generate=AsyncMock(return_value=SimpleNamespace(completion_text='{"is_food":true,"verdict":"food","description":"橘子","action":"吃橘子","reply":"谢谢投喂。"}')),
        )
        with patch('astrbot_plugin_feeding.main.StarTools.get_data_dir',return_value=self.root):
            self.plugin=FeedingPlugin(self.context,Config(self.root/'config.json'))
        for field in ('chibi_reference', 'turnaround_reference'):
            path=self.root/self.plugin.config[field][0]
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(png())

    async def asyncTearDown(self):
        await self.plugin.terminate();self.temp.cleanup()

    async def test_current_over_reply_and_reply_id_fallback(self):
        direct=Image.fromURL('https://example.com/direct.png')
        quoted=Image.fromURL('https://example.com/quoted.png')
        self.assertEqual(await self.plugin.sources(Event([Reply(id=1,chain=[quoted]),direct])),[direct.file])
        self.assertEqual(await self.plugin.sources(Event([Reply(id=1,chain=[quoted])])),[quoted.file])
        event=Event([Reply(id=123)])
        self.assertEqual(await self.plugin.sources(event),['https://example.com/food.png'])
        event.bot.api.call_action.assert_awaited_once_with('get_msg',message_id=123)

    async def test_prefix_help_and_access_gate(self):
        event=Event();await self.plugin.feed(event)
        self.assertIn('#投喂',event.sent[-1]);self.assertTrue(event.stopped)
        event=Event(admin=False);await self.plugin.feed(event)
        self.assertIn('管理员',event.sent[-1]);self.context.llm_generate.assert_not_awaited()

    async def test_persona_uses_conversation_and_session_resolution(self):
        event=Event();self.assertEqual(await self.plugin.persona_prompt(event),'current personality')
        call=self.context.persona_manager.resolve_selected_persona.await_args.kwargs
        self.assertEqual(call['conversation_persona_id'],'conversation-persona')
        self.assertEqual(call['umo'],event.unified_msg_origin)

    async def test_full_pipeline_and_duplicate_only_one_generation(self):
        image=Image.fromBase64(base64.b64encode(png()).decode())
        event=Event([image])
        with patch('astrbot_plugin_feeding.main.generate_image',new_callable=AsyncMock,return_value=png()) as generate:
            await self.plugin.feed(event)
            await asyncio.gather(*self.plugin.tasks)
            await self.plugin.feed(event)
            generate.assert_awaited_once()
            self.assertEqual(len(generate.await_args.args[4]),3)
        args=self.context.llm_generate.await_args.kwargs
        self.assertEqual(args['system_prompt'],'current personality')
        self.assertEqual(args['chat_provider_id'],'current-provider')
        self.assertEqual(len(list((self.root/'outputs').glob('*.png'))),1)
        self.assertEqual(len(list((self.root/'inputs').glob('*'))),0)
        self.assertTrue(any(isinstance(x,list) and any(isinstance(y,Image) for y in x) for x in event.sent))

    async def test_comment_and_image_stay_together(self):
        event=Event([Image.fromBase64(base64.b64encode(png()).decode())])
        async def generate(*args,**kwargs):
            self.assertNotIn('谢谢投喂。',event.sent)
            self.assertFalse(any(isinstance(x,list) for x in event.sent))
            return png()
        with patch('astrbot_plugin_feeding.main.generate_image',side_effect=generate):
            await self.plugin.feed(event);await asyncio.gather(*self.plugin.tasks)
        final=event.sent[-1]
        self.assertEqual(final[0].text,'谢谢投喂。')
        self.assertIsInstance(final[1],Image)

    async def test_reference_cache_refreshes_after_replacement(self):
        with patch('astrbot_plugin_feeding.main.load_image',new_callable=AsyncMock,return_value=b'old') as load:
            self.assertEqual(await self.plugin.load_reference('chibi4.png'),b'old')
            self.assertEqual(await self.plugin.load_reference('chibi4.png'),b'old')
            self.assertEqual(load.await_count,1)
            (self.root/'files/chibi_reference/test.png').write_bytes(png((48,48)))
            load.return_value=b'new'
            self.assertEqual(await self.plugin.load_reference('chibi4.png'),b'new')
            self.assertEqual(load.await_count,2)

    async def test_nonfood_never_generates(self):
        self.context.llm_generate.return_value=SimpleNamespace(completion_text='{"is_food":false,"verdict":"non_food","description":"键盘","action":"","reply":"这个不能吃。"}')
        event=Event([Image.fromBase64(base64.b64encode(png()).decode())])
        with patch('astrbot_plugin_feeding.main.generate_image',new_callable=AsyncMock) as generate:
            await self.plugin.feed(event);await asyncio.gather(*self.plugin.tasks)
            generate.assert_not_awaited()
        self.assertEqual(event.sent[-1],'这个不能吃。')

    async def test_malicious_feeding_replies_firmly_without_generation(self):
        self.context.llm_generate.return_value=SimpleNamespace(completion_text=json.dumps({
            'is_food': True, 'verdict': 'malicious', 'description': '危险物品',
            'action': '吃', 'reply': '拿开。别拿我的安全开玩笑。'}))
        event=Event([Image.fromBase64(base64.b64encode(png()).decode())])
        with patch('astrbot_plugin_feeding.main.generate_image',new_callable=AsyncMock) as generate:
            await self.plugin.feed(event);await asyncio.gather(*self.plugin.tasks)
            generate.assert_not_awaited()
        self.assertEqual(event.sent[-1], '拿开。别拿我的安全开玩笑。')
        self.assertEqual(next(iter(self.plugin.journal.records.values()))['status'], 'rejected')
        self.assertFalse(any(isinstance(item,list) for item in event.sent))

    async def test_delivery_failure_preserves_output_and_dedup(self):
        event=Event([Image.fromBase64(base64.b64encode(png()).decode())])
        original=event.send
        async def send(result):
            if isinstance(result,list):raise RuntimeError('send unavailable')
            await original(result)
        event.send=send
        with patch('astrbot_plugin_feeding.main.generate_image',new_callable=AsyncMock,return_value=png()) as generate:
            await self.plugin.feed(event);await asyncio.gather(*self.plugin.tasks)
            await self.plugin.feed(event);generate.assert_awaited_once()
        self.assertEqual(next(iter(self.plugin.journal.records.values()))['status'],'delivery_failed')
        self.assertEqual(len(list((self.root/'outputs').glob('*.png'))),1)

    async def test_terminate_marks_unstarted_jobs_interrupted(self):
        mid=self.plugin.journal.admit('not-started','user',0,4)
        await self.plugin.terminate()
        self.assertEqual(self.plugin.journal.records[mid]['status'],'interrupted')

    async def test_native_dropdown_populates_without_changing_selection(self):
        self.plugin.config['image_model']='gpt-image-2'
        with patch('astrbot_plugin_feeding.main.discover_models',new_callable=AsyncMock,return_value=['gpt-5.6-sol','gpt-image-1.5','gpt-image-2']):
            await self.plugin.refresh_model_options()
        field=self.plugin.config.schema['image_model']
        self.assertEqual(field['options'],['gpt-image-1.5','gpt-image-2'])
        self.assertEqual(self.plugin.config['image_model'],'gpt-image-2')
        self.assertEqual(self.plugin.config.saves,0)
        self.assertIn('2 个',field['hint'])
        self.assertFalse(hasattr(self.plugin,'models'))

    async def test_native_dropdown_failure_preserves_current_and_avoids_other_api_options(self):
        self.plugin.config['image_model']='custom-image'
        with patch('astrbot_plugin_feeding.main.discover_models',new_callable=AsyncMock,return_value=['gpt-image-2']):
            await self.plugin.refresh_model_options()
        self.assertIn('custom-image',self.plugin.config.schema['image_model']['options'])
        self.plugin.config['advanced']={'image_base_url':'http://another.example/v1', 'image_api_key':'another-test-key'}
        with patch('astrbot_plugin_feeding.main.discover_models',new_callable=AsyncMock,side_effect=RuntimeError('offline')):
            await self.plugin.refresh_model_options()
        self.assertEqual(self.plugin.config.schema['image_model']['options'],['custom-image'])
        self.assertIn('暂时无法',self.plugin.config.schema['image_model']['hint'])
        self.assertEqual(self.plugin.config['image_model'],'custom-image')

    async def test_native_selection_persists_with_real_astrbot_config(self):
        from astrbot.api import AstrBotConfig
        schema=self.plugin.config.schema
        schema['image_model']['options']=['gpt-image-1.5','gpt-image-2']
        path=str(self.root/'native-config.json')
        config=AstrBotConfig(config_path=path,schema=schema)
        config.save_config({'image_model':'gpt-image-2'})
        reloaded=AstrBotConfig(config_path=path,schema=schema)
        self.assertEqual(reloaded['image_model'],'gpt-image-2')

    async def test_advanced_migration_persists_with_real_astrbot_config(self):
        from astrbot.api import AstrBotConfig
        from astrbot_plugin_feeding.connections import migrate_connection_config, resolve_image_credentials
        schema=json.loads((Path(__file__).parents[1]/'_conf_schema.json').read_text(encoding='utf-8'))
        path=self.root/'legacy-config.json'
        path.write_text(json.dumps({'image_base_url':'https://legacy.example/v1',
                                   'image_api_key':'legacy-test-key'}),encoding='utf-8')
        config=AstrBotConfig(config_path=str(path),schema=schema)
        self.assertTrue(migrate_connection_config(config))
        config.save_config()
        reloaded=AstrBotConfig(config_path=str(path),schema=schema)
        self.assertEqual(resolve_image_credentials(reloaded,self.context.get_provider_by_id),
                         ('https://legacy.example/v1','legacy-test-key'))
        self.assertEqual(reloaded['image_api_key'],'')
        self.assertFalse(migrate_connection_config(reloaded))

    async def test_queued_job_keeps_admission_model(self):
        self.plugin.config['image_model']='gpt-image-1.5'
        event=Event([Image.fromBase64(base64.b64encode(png()).decode())])
        with patch('astrbot_plugin_feeding.main.generate_image',new_callable=AsyncMock,return_value=png()) as generate:
            await self.plugin.feed(event)
            self.plugin.config['image_model']='gpt-image-2'
            await asyncio.gather(*self.plugin.tasks)
            self.assertEqual(generate.await_args.args[2],'gpt-image-1.5')

    async def test_two_groups_are_serial_even_with_legacy_concurrency(self):
        await self.plugin.terminate()
        config=Config(self.root/'config.json');config['concurrency']=3
        with patch('astrbot_plugin_feeding.main.StarTools.get_data_dir',return_value=self.root):
            self.plugin=FeedingPlugin(self.context,config)
        self.assertEqual(self.plugin.concurrency,1)
        first_started=asyncio.Event(); release_first=asyncio.Event()
        active=0;peak=0;calls=0
        async def generate(*args,**kwargs):
            nonlocal active,peak,calls
            active+=1;peak=max(peak,active);calls+=1
            try:
                if calls==1:
                    first_started.set()
                    await release_first.wait()
                return png()
            finally:active-=1
        first=Event([Image.fromBase64(base64.b64encode(png()).decode())],mid='group-a')
        second=Event([Image.fromBase64(base64.b64encode(png()).decode())],mid='group-b')
        first.unified_msg_origin='test:GroupMessage:111'
        second.unified_msg_origin='test:GroupMessage:222'
        second.get_sender_id=lambda:'456'
        downloaded_second=asyncio.Event()
        original_load=__import__('astrbot_plugin_feeding.main',fromlist=['load_image']).load_image
        async def load(*args,**kwargs):
            data=await original_load(*args,**kwargs)
            if first_started.is_set():downloaded_second.set()
            return data
        with patch('astrbot_plugin_feeding.main.generate_image',side_effect=generate), patch('astrbot_plugin_feeding.main.load_image',side_effect=load):
            try:
                await self.plugin.feed(first)
                await asyncio.wait_for(first_started.wait(),3)
                await self.plugin.feed(second)
                await asyncio.wait_for(downloaded_second.wait(),3)
                self.assertEqual(calls,1)
                self.assertEqual(self.context.llm_generate.await_count,1)
            finally:
                release_first.set()
            await asyncio.wait_for(asyncio.gather(*self.plugin.tasks),3)
        self.assertEqual(calls,2)
        self.assertEqual(peak,1)
        for event in (first,second):
            self.assertTrue(any(isinstance(message,list) for message in event.sent))

    async def test_model_refresh_task_stops_on_unload(self):
        with patch('astrbot_plugin_feeding.main.discover_models',new_callable=AsyncMock,return_value=['gpt-image-2']):
            await self.plugin.initialize()
            task=self.plugin.catalog_task
            await self.plugin.terminate()
        self.assertTrue(task.done())

if __name__=='__main__':unittest.main()
