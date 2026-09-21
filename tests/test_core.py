import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from PIL import Image
from astrbot_plugin_feeding.core import (
    FeedingError, Journal, download_public, generate_image, image_prompt,
    load_image, normalize_image, parse_food, discover_models, image_model_candidates, transport_reference,
)


def png(size=(32, 32)):
    stream = io.BytesIO()
    Image.new('RGB', size, 'orange').save(stream, 'PNG')
    return stream.getvalue()


class CoreTests(unittest.TestCase):
    def test_character_prompt_does_not_embed_private_identity(self):
        prompt=image_prompt({'description':'food','action':'eat','reply':'yum'})
        for private in ('Juzi', 'silver-white', 'pointed elf', 'orange flower', 'black sailor'):
            self.assertNotIn(private,prompt)
        self.assertIn('PRIMARY',prompt)
        self.assertIn('SECONDARY',prompt)
        self.assertIn('must not fuse',prompt)
        custom=image_prompt({'description':'food','action':'eat','reply':'yum'}, '测试角色的服装描述')
        self.assertIn('测试角色的服装描述',custom)
        self.assertIn('must not fuse',custom)
        single=image_prompt({'description':'food','action':'eat','reply':'yum'}, has_secondary=False)
        self.assertIn('Image 2 is ONLY the food',single)
        self.assertNotIn('Image 3',single)
        self.assertNotIn('SECONDARY',single)
        self.assertIn('Do not force chibi',single)

    def test_classification_is_required_and_contradictions_cannot_allow_food(self):
        food = {'is_food': True, 'description': '橘子', 'action': '剥皮', 'reply': '好呀。'}
        for text in (None, json.dumps(food), json.dumps({**food, 'verdict': 'food', 'is_food': False})):
            with self.subTest(text=text), self.assertRaises(FeedingError):
                parse_food(text)

    def test_rejected_verdict_overrides_food_boolean(self):
        food = {'is_food': True, 'description': '可疑投喂', 'action': '吃', 'reply': '拿开。'}
        for verdict in ('malicious', 'non_food', 'uncertain'):
            result = parse_food(json.dumps({**food, 'verdict': verdict}))
            self.assertFalse(result['is_food'])
            self.assertEqual(result['action'], '')
        for verdict in ('unknown', [], None):
            with self.assertRaises(FeedingError):
                parse_food(json.dumps({**food, 'verdict': verdict}))

    def test_mpo_phone_photo_uses_primary_image(self):
        stream = io.BytesIO()
        primary = Image.new('RGB', (32, 24), 'red')
        auxiliary = Image.new('RGB', (32, 24), 'blue')
        primary.save(stream, format='MPO', save_all=True, append_images=[auxiliary])
        raw = stream.getvalue()
        with Image.open(io.BytesIO(raw)) as source:
            self.assertEqual(source.format, 'MPO')
            self.assertEqual(source.n_frames, 2)
        with Image.open(io.BytesIO(normalize_image(raw, 100000, 1024))) as result:
            self.assertEqual(result.format, 'PNG')
            self.assertEqual(result.size, (32, 24))
            red, green, blue = result.getpixel((0, 0))
            self.assertGreater(red, 240)
            self.assertLess(blue, 15)
            self.assertEqual(getattr(result, 'n_frames', 1), 1)
        with self.assertRaises(FeedingError):
            normalize_image(raw, 100000, 100)

    def test_additional_food_formats_normalize_to_png(self):
        for fmt in ('GIF', 'BMP', 'AVIF'):
            with self.subTest(format=fmt):
                stream = io.BytesIO()
                Image.new('RGB', (32, 24), 'orange').save(stream, format=fmt)
                with Image.open(io.BytesIO(normalize_image(stream.getvalue(), 100000, 1024))) as result:
                    self.assertEqual(result.format, 'PNG')
                    self.assertEqual(result.size, (32, 24))
                    self.assertEqual(result.mode, 'RGB')

    def test_animated_food_uses_first_frame_and_keeps_limits(self):
        stream = io.BytesIO()
        first = Image.new('RGB', (32, 24), 'red')
        second = Image.new('RGB', (32, 24), 'blue')
        first.save(stream, format='GIF', save_all=True, append_images=[second], duration=100, loop=0)
        raw = stream.getvalue()
        with Image.open(io.BytesIO(normalize_image(raw, 100000, 1024))) as result:
            self.assertEqual(result.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(getattr(result, 'n_frames', 1), 1)
        with self.assertRaises(FeedingError):
            normalize_image(raw, 100000, 100)

    def test_image_corruption_and_limits(self):
        raw = png()
        self.assertTrue(normalize_image(raw, len(raw), 1024).startswith(b'\x89PNG'))
        for data, limit, pixels in [(b'not an image', 100, 100), (raw, len(raw)-1, 1024), (raw, len(raw), 1023)]:
            with self.assertRaises(FeedingError): normalize_image(data, limit, pixels)

    def test_transparent_pixels_composite_before_rgb(self):
        source = Image.new('RGBA', (3, 1), (255, 0, 0, 0))
        source.putpixel((1, 0), (0, 0, 0, 128))
        source.putpixel((2, 0), (12, 34, 56, 255))
        stream = io.BytesIO(); source.save(stream, format='PNG')
        with Image.open(io.BytesIO(normalize_image(stream.getvalue(), 10000, 100))) as result:
            self.assertEqual(result.mode, 'RGB')
            self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
            self.assertEqual(result.getpixel((1, 0)), (127, 127, 127))
            self.assertEqual(result.getpixel((2, 0)), (12, 34, 56))
        indexed = Image.new('P', (1, 1), 0)
        indexed.putpalette([255, 0, 0] + [0, 0, 0] * 255)
        stream = io.BytesIO(); indexed.save(stream, format='PNG', transparency=0)
        with Image.open(io.BytesIO(normalize_image(stream.getvalue(), 10000, 100))) as result:
            self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))

    def test_transport_preserves_dimensions_and_uses_smaller_encoding(self):
        im = Image.effect_noise((256, 256), 20).convert('RGB')
        buf = io.BytesIO(); im.save(buf, format='PNG'); raw = buf.getvalue()
        encoded, extension, mime = transport_reference(raw)
        self.assertLessEqual(len(encoded), len(raw))
        with Image.open(io.BytesIO(encoded)) as decoded:
            self.assertEqual(decoded.size, im.size)
            self.assertEqual(decoded.format, 'JPEG' if extension == 'jpg' else 'PNG')
        self.assertEqual(mime, 'image/jpeg' if extension == 'jpg' else 'image/png')
        tiny=png();self.assertEqual(transport_reference(tiny),(tiny,'png','image/png'))

    def test_food_json_is_strict(self):
        food = {'is_food': True, 'verdict': 'food', 'description': '橘子', 'action': '剥皮吃', 'reply': '谢谢。'}
        self.assertEqual(parse_food('```json\n'+json.dumps(food)+'\n```'), food)
        for text in ['{}', 'null', json.dumps({**food, 'is_food': 'true'}), json.dumps({**food, 'reply': []})]:
            with self.assertRaises(FeedingError): parse_food(text)
        self.assertIn('Image 2', image_prompt(food))

    def test_expression_and_reaction_reach_image_prompt(self):
        food = {'is_food': True, 'verdict': 'food', 'description': '面条', 'action': '找水',
                'reply': '水呢！', 'expression': '眼角含泪，皱眉张嘴吸气'}
        parsed = parse_food(json.dumps(food))
        prompt = image_prompt(parsed)
        self.assertIn(food['expression'], prompt)
        self.assertIn(food['reply'], prompt)
        self.assertIn('may close, squint', prompt)
        with self.assertRaises(FeedingError):
            parse_food(json.dumps({**food, 'expression': ['invalid']}))

    def test_admission_persistence_and_restart_no_resubmit(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'tasks.json'; journal = Journal(p)
            a = journal.admit('message1', 'user1', 60, 2)
            self.assertEqual(journal.admit('message1','user1',60,2),'duplicate')
            with self.assertRaises(FeedingError): journal.admit('message2','user1',60,2)
            b = journal.admit('message3','user2',60,2)
            with self.assertRaises(FeedingError): journal.admit('message4','user3',60,2)
            journal.finish(a,'done'); journal.finish(b,'running')
            restored = Journal(p)
            self.assertEqual(restored.records[b]['status'],'interrupted')
            self.assertEqual(restored.admit('message3','user2',60,2),'duplicate')
            with self.assertRaises(FeedingError): restored.admit('message5','user1',60,2)
            self.assertNotIn('user1',p.read_text())


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_discovery_uses_api_and_rejects_bad_response(self):
        mode='valid'
        async def handle(request):
            self.assertEqual(request.headers.get('Authorization'),'Bearer catalog-key')
            if mode=='http':return web.json_response({'error':'private-server-body'},status=401)
            if mode=='invalid':return web.json_response({'wrong':[]})
            return web.json_response({'data':[{'id':'gpt-image-2'},{'id':'gpt-5.6-sol'},
                {'id':'gpt-image-1.5'},{'id':'gpt-image-2'},{'id':'bad\nname'},{'id':None}]})
        app=web.Application();app.router.add_get('/v1/models',handle)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        try:
            url=f'http://127.0.0.1:{port}/v1'
            models=await discover_models(url,'catalog-key')
            self.assertEqual(image_model_candidates(models),['gpt-image-1.5','gpt-image-2'])
            self.assertNotIn('bad\nname',models)
            for mode in ('http','invalid'):
                with self.assertRaises(FeedingError) as error:await discover_models(url,'catalog-key')
                self.assertNotIn('private-server-body',str(error.exception))
        finally:await runner.cleanup()

    async def test_local_file_boundary_and_base64(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'allowed').mkdir()
            p=root/'private.png';p.write_bytes(png())
            with self.assertRaises(FeedingError):
                await load_image(str(p),max_bytes=1024,max_pixels=1024,local_roots=[root/'allowed'])
            result=await load_image('base64://'+base64.b64encode(png()).decode(),max_bytes=1024,max_pixels=1024,local_roots=[])
            self.assertTrue(result.startswith(b'\x89PNG'))

    async def test_local_network_download_blocked(self):
        for url in ['http://127.0.0.1/x','http://[::1]/x','http://192.168.8.228/x','file:///etc/passwd']:
            with self.assertRaises(FeedingError): await download_public(url,1000)

    async def test_edits_wire_and_errors_never_retry(self):
        calls=[];mode='success'
        async def handle(request):
            fields=[]
            async for part in await request.multipart():
                fields.append((part.name,await part.read()))
            calls.append(fields)
            if mode=='http': return web.json_response({'error':'secret-credential'},status=503)
            if mode=='empty': return web.json_response({'data':[]})
            if mode=='corrupt': return web.json_response({'data':[{'b64_json':'broken'}]})
            if mode=='timeout': await asyncio.sleep(.1)
            return web.json_response({'data':[{'b64_json':base64.b64encode(png()).decode()}]})
        app=web.Application();app.router.add_post('/v1/images/edits',handle)
        runner=web.AppRunner(app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        try:
            args=(f'http://127.0.0.1:{port}/v1','test-key','tested-model','one image',[png(),png(),png()])
            out=await generate_image(*args)
            self.assertTrue(out.startswith(b'\x89PNG'))
            self.assertEqual([name for name,_ in calls[0]].count('image[]'),3)
            self.assertEqual(dict(calls[0])['model'],b'tested-model')
            self.assertEqual(dict(calls[0])['size'],b'512x512')
            self.assertEqual(dict(calls[0])['quality'],b'low')
            await generate_image(*args[:4], [png(),png()])
            self.assertEqual([name for name,_ in calls[-1]].count('image[]'),2)
            for mode in ['http','empty','corrupt','timeout']:
                before=len(calls)
                with self.assertRaises(FeedingError) as error:
                    await generate_image(*args,timeout=.02 if mode=='timeout' else 3)
                self.assertEqual(len(calls),before+1)
                self.assertNotIn('secret-credential',str(error.exception))
        finally: await runner.cleanup()


if __name__=='__main__': unittest.main()
