import copy
import tempfile
import unittest
from pathlib import Path
from astrbot_plugin_feeding.guests import migrate_guests, choose_guest, distribution, validate_roles, initialize_default_guest
from astrbot_plugin_feeding.customization import DEFAULTS, validate_settings


class GuestTests(unittest.TestCase):
    def test_default_guest_is_installed_once_and_deletion_is_preserved(self):
        cfg={}
        self.assertTrue(initialize_default_guest(cfg,self.root))
        role=cfg['guest_roles'][0]
        self.assertEqual(role['name'],'大肥鱼')
        self.assertTrue((self.root/role['reference'][0]).is_file())
        self.assertTrue(role['persona'])
        validate_roles(cfg['guest_roles'],self.root)
        role['persona']='custom';self.assertFalse(initialize_default_guest(cfg,self.root))
        self.assertEqual(role['persona'],'custom')
        cfg['guest_roles']=[]
        self.assertFalse(initialize_default_guest(cfg,self.root))
        self.assertEqual(cfg['guest_roles'],[])

    def test_default_guest_does_not_replace_existing_or_deleted_beta_profile(self):
        for cfg in [{'guest_roles_migrated':True,'guest_roles':[]},
                    {'guest_persona':'legacy','guest_reference':[]},
                    {'guest_roles':copy.deepcopy(self.roles)}]:
            before=copy.deepcopy(cfg)
            initialize_default_guest(cfg,self.root)
            for k,v in before.items():self.assertEqual(cfg[k],v)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);p=self.root/'files/guest_reference/x.png'
        p.parent.mkdir(parents=True);p.write_bytes(b'x')
        self.roles=[dict(id=str(i),name=n,enabled=True,random_enabled=True,probability=q,
                        persona='persona',character_prompt='',reference=['files/guest_reference/x.png'],secondary=[],notice='抢饭提醒')
                    for i,(n,q) in enumerate([('大肥鱼',15),('B',10),('C',5)])]
        self.cfg=dict(guest_roles=self.roles,steal_enabled=True,steal_mode='average',steal_probability=30)

    def test_average_and_custom_intervals(self):
        self.assertEqual([p for _,p in distribution(self.cfg,self.root)],[10,10,10])
        self.assertEqual(choose_guest(self.cfg,self.root,.1)['name'],'B')
        self.assertIsNone(choose_guest(self.cfg,self.root,.3))
        self.cfg['steal_mode']='custom'
        self.assertEqual([p for _,p in distribution(self.cfg,self.root)],[15,10,5])
        for roll,name in [(0,'大肥鱼'),(.15,'B'),(.25,'C')]:
            self.assertEqual(choose_guest(self.cfg,self.root,roll)['name'],name)
        self.assertIsNone(choose_guest(self.cfg,self.root,.3))

    def test_disabled_incomplete_and_zero(self):
        self.roles[0]['enabled']=False;self.roles[1]['persona']=''
        self.assertEqual([p for _,p in distribution(self.cfg,self.root)],[30])
        self.cfg['steal_mode']='custom';self.roles[2]['probability']=0
        self.assertIsNone(choose_guest(self.cfg,self.root,0))
        self.assertEqual(choose_guest(self.cfg,self.root,0,'C')['name'],'C')
        for name in ('大肥鱼','B','missing'):
            with self.assertRaises(ValueError):choose_guest(self.cfg,self.root,0,name)
        self.cfg['steal_enabled']=False
        self.assertEqual(choose_guest(self.cfg,self.root,.9,'C')['name'],'C')

    def test_validation_and_snapshot(self):
        for bad in [float('nan'),float('inf'),-1,101,True]:
            roles=copy.deepcopy(self.roles);roles[0]['probability']=bad
            with self.assertRaises(ValueError):validate_roles(roles,self.root)
        roles=copy.deepcopy(self.roles);roles[1]['name']='大肥鱼'
        with self.assertRaises(ValueError):validate_roles(roles,self.root)
        roles=copy.deepcopy(self.roles);roles[0]['reference']=['../secret.png']
        with self.assertRaises(ValueError):validate_roles(roles,self.root)
        selected=choose_guest(self.cfg,self.root,0);self.roles[0]['persona']='changed'
        self.assertEqual(selected['persona'],'persona')
        self.roles[0]['probability']=99
        settings=copy.deepcopy(DEFAULTS);settings.update(guest_roles=self.roles,steal_mode='custom')
        with self.assertRaisesRegex(ValueError,'超过'):validate_settings(settings,self.root)

    def test_legacy_migration_once_and_no_resurrection(self):
        config={'guest_persona':'old','guest_reference':['files/guest_reference/x.png'],
                'guest_character_prompt':'appearance','steal_probability':15,'steal_enabled':True}
        self.assertTrue(migrate_guests(config))
        self.assertEqual(config['guest_roles'][0]['persona'],'old')
        self.assertEqual(distribution(config,self.root)[0][1],15)
        config['guest_roles']=[]
        self.assertFalse(migrate_guests(config))
        self.assertEqual(config['guest_roles'],[])

    def test_notice_migration_preserves_explicit_empty_and_custom_text(self):
        config=dict(guest_roles_migrated=True,guest_roles=copy.deepcopy(self.roles))
        del config['guest_roles'][0]['notice']
        config['guest_roles'][1]['notice']=''
        config['guest_roles'][2]['notice']='自定义'
        self.assertTrue(migrate_guests(config))
        self.assertTrue(config['guest_roles'][0]['notice'])
        self.assertEqual(config['guest_roles'][1]['notice'],'')
        self.assertEqual(config['guest_roles'][2]['notice'],'自定义')
        self.assertFalse(migrate_guests(config))
