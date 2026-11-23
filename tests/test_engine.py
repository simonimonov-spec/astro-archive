import unittest
from tests.support import load
engine=load('engine')

class SettingsTests(unittest.TestCase):
    def test_settings_accepts_new_keys(self):
        o=engine.settings(dict(chunkSize=250,aiFilter=True,aiPercent=15))
        self.assertEqual((o['chunkSize'],o['aiFilter'],o['aiPercent']),(250,True,15))
    def test_defaults(self):
        o=engine.settings({})
        self.assertEqual((o['chunkSize'],o['aiFilter'],o['aiPercent']),(0,False,10))
    def test_settings_rejects_bad_chunk(self):
        for bad in (5,-1,2.5,'100',True,100001):
            with self.assertRaises(ValueError):engine.settings(dict(chunkSize=bad))
    def test_settings_rejects_bad_percent(self):
        for bad in (-1,91,True,'5'):
            with self.assertRaises(ValueError):engine.settings(dict(aiPercent=bad))
    def test_settings_rejects_bad_ai_flag(self):
        with self.assertRaises(ValueError):engine.settings(dict(aiFilter=1))

class MergePlanTests(unittest.TestCase):
    def stack(self,lines):return [l for l in lines if l.startswith('stack ')][0]
    def test_merge_plan_two_parts_no_rejection(self):
        lines=engine.merge_plan(engine.settings(dict(drizzle=True,background=True,dark='d.fit')),2)
        stack=self.stack(lines)
        self.assertIn(' rej n ',stack+' ');self.assertIn('-weight=nbstack',stack);self.assertIn('-out=result',stack)
        self.assertFalse(any('-debayer' in l or 'calibrate' in l or 'seqsubsky' in l or '-drizzle' in l for l in lines))
        self.assertTrue(any(l.startswith('register ') for l in lines))
    def test_merge_plan_three_parts_uses_sigma(self):
        self.assertIn('rej w 2.5',self.stack(engine.merge_plan(engine.settings(dict(sigmaLow=2.5)),3)))
    def test_merge_plan_respects_rejection_off(self):
        self.assertIn(' rej n ',self.stack(engine.merge_plan(engine.settings(dict(rejection=False)),5))+' ')

if __name__=='__main__':unittest.main()
