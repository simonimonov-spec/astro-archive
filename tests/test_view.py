import unittest
from tests.support import load
engine=load('engine');view=load('view')

OPTS=engine.settings({})
def day(ident,state,**extra):return dict(id=ident,object='M31',night='2026-10-0'+ident[-1],ids=list(range(10)),state=state,**extra)
def slot(number,status):return dict(number=number,status=status,task='',log=['line '+str(number)])
def walk(items):
    for item in items:
        yield item
        yield from walk(item.get('children',[]))
def find(groups,ident):return next((c for g in groups for c in walk(g.get('children',[])) if c['id']==ident),None)
def ids(groups):return {c['id'] for g in groups for c in walk(g['children'])}

class SettingsScreenTests(unittest.TestCase):
    def test_settings_screen_is_single_group(self):self.assertEqual(len(view.settings_screen(OPTS,[],False,False,False)),1)
    def test_ai_fields_only_when_available(self):
        self.assertNotIn('aiPercent',ids(view.settings_screen(OPTS,[],False,False,False)))
        self.assertIn('aiPercent',ids(view.settings_screen(OPTS,[],True,False,False)))
    def test_new_parallel_fields(self):
        self.assertTrue({'parallel','chunkSize','start','abandon'}<=ids(view.settings_screen(OPTS,[],False,False,False)))
    def test_scope_note(self):
        g=view.settings_screen(OPTS,[day('d1','review')],False,False,False)
        self.assertIn('M31 · 2026-10-01',find(g,'scope')['value']);self.assertEqual(find(g,'start')['label'],'Стартирай отново избраните дни')
    def test_confirm_cancel(self):
        g=view.settings_screen(OPTS,[],False,False,True);self.assertIn('Потвърди',find(g,'abandon')['label']);self.assertIsNotNone(find(g,'abandon-note'))
    def test_running_blocks_start_only(self):
        g=view.settings_screen(OPTS,[],False,False,False,running=True)
        self.assertTrue(find(g,'start')['disabled']);self.assertFalse(find(g,'parallel')['disabled']);self.assertIsNotNone(find(g,'running-note'))
    def test_back_only_with_work(self):
        self.assertIsNone(find(view.settings_screen(OPTS,[],False,False,False),'back'))
        self.assertIsNotNone(find(view.settings_screen(OPTS,[],False,False,False,has_work=True),'back'))

    def test_settings_are_grouped_into_sections(self):
        top=view.settings_screen(OPTS,[],True,False,False)[0]['children']
        sections=[c['id'] for c in top if c['type']=='group']
        self.assertEqual(sections,['g-siril','g-parallel','g-processing','g-calibration','g-ai','g-after'])
        self.assertTrue(all(c['label'] for c in top if c['type']=='group'))
    def test_switch_titled_subgroups(self):
        g=view.settings_screen(OPTS,[],False,False,False)
        for group,switch,fields in (('g-drizzle','drizzle',['scale','pixfrac']),('g-rejection','rejection',['sigmaLow','sigmaHigh'])):
            sub=find(g,group);self.assertEqual(sub['label'],'');self.assertEqual(sub['children'][0]['id'],switch);self.assertEqual(sub['children'][0]['type'],'checkbox')
            self.assertEqual([c['id'] for c in sub['children'][1:]],fields)
    def test_actions_stay_on_top_level(self):
        top=[c['id'] for c in view.settings_screen(OPTS,[],False,False,False,has_work=True)[0]['children']]
        self.assertTrue({'intro','start','back','abandon'}<=set(top))

class WorkScreenTests(unittest.TestCase):
    def state(self,*groups,selected=''):return dict(groups=list(groups),selected=selected)
    def test_two_groups(self):
        g=view.work_screen(self.state(day('d1','running')),[slot(1,'green')],1,True,False)
        self.assertEqual([x['id'] for x in g],['days','sirils']);self.assertNotIn('parallel',ids(g))
    def test_lists_by_state_and_tones(self):
        g=view.work_screen(self.state(day('d1','approved'),day('d2','error'),day('d3','running'),day('d4','review'),day('d5','review',repeat=True)),[slot(1,'green')],1,True,False)
        self.assertEqual([t['tone'] for t in find(g,'list-work')['items']],['error','primary'])
        self.assertEqual(find(g,'list-review')['actionLabel'],'Оцени');self.assertEqual(find(g,'list-review')['command'],'review')
        self.assertEqual(find(g,'list-repeat')['actionLabel'],'Пренастрой');self.assertEqual(find(g,'list-repeat')['command'],'reconfigure')
        self.assertEqual(find(g,'list-done')['items'][0]['tone'],'success')
    def test_slot_tones(self):
        g=view.work_screen(self.state(),[slot(1,'green'),slot(2,'yellow'),slot(3,'red')],2,False,False)
        self.assertEqual([r['tone'] for r in find(g,'slots')['items']],['success','warning','error'])
        self.assertEqual(find(g,'console')['value'],'line 2')
    def test_visual_button_always_enabled(self):
        self.assertFalse(find(view.work_screen(self.state(),[slot(1,'red')],1,False,False),'visual')['disabled'])
    def test_review_buttons_for_selected(self):
        g=view.work_screen(self.state(day('d1','review'),selected='d1'),[slot(1,'red')],1,False,False,image='data:image/png;base64,AA')
        self.assertTrue({'accept','repeat','result'}<=ids(g))
    def test_continue_button_when_idle_with_waiting(self):
        self.assertIsNotNone(find(view.work_screen(self.state(day('d1','paused')),[slot(1,'red')],1,False,False),'start'))
        self.assertIsNone(find(view.work_screen(self.state(day('d1','paused')),[slot(1,'red')],1,True,False),'start'))

class TextTests(unittest.TestCase):
    def test_status_text_accepted(self):
        g=dict(state='approved',ids=list(range(1000)),usedIds=list(range(812)),parts=[{}]*4,aiRemovedIds=list(range(100)))
        self.assertEqual(view.day_status_text(g),'Прибран · 812/1000 кадъра · 4 части · AI махна 100')
    def test_status_text_running_parts(self):
        g=dict(state='running',ids=[],progress=45,parts=[dict(state='done')]*3+[dict(state='running')]*7)
        self.assertEqual(view.day_status_text(g),'Обработва се · части 3/10 · 45 %')
    def test_ai_note_visible_on_ready_day(self):
        item=view.day_item(dict(id='d',object='M31',night='N',ids=[1],state='review',problem='AI филтърът не е приложен: зает'))
        self.assertIn('AI филтърът не е приложен',item['description'])
    def test_activity_empty_without_unfinished(self):self.assertEqual(view.activity([day('d1','approved')],False),[])
    def test_activity_resume_and_finish(self):
        a=view.activity([day('d1','paused')],False);self.assertEqual([x['id'] for x in a],['unfinished-siril','finish'])

if __name__=='__main__':unittest.main()
