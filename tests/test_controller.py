"""End-to-end controller tests with a fake host and a fake Siril (no real Siril needed)."""
import json,pathlib,shutil,tempfile,threading,time,unittest,uuid
from tests.support import load
main=load('main')

def flat(items):
    # Settings are grouped into sections (V-2.1); look through every level.
    for item in items:
        yield item
        yield from flat(item.get('children',[]))

class FakeEngine:
    reject={}      # folder-name -> set of local numbers Siril "drops"
    fail=set()     # folder names that fail
    links=False    # create a symlink like Siril's convert
    def __init__(self):self.process=None
    def stop(self):pass
    def run(self,executable,folder,commands,cancel,log,status,progress):
        folder=pathlib.Path(folder);status('green','работи')
        if folder.name in FakeEngine.fail:raise RuntimeError('Siril приключи с грешка (1).')
        count=len(list((folder/'source').iterdir()))
        sequence=[c for c in commands if c.startswith('stack ')][0].split()[1]
        drop=FakeEngine.reject.get(folder.name,set())
        lines=[f'S "{sequence}" 1 {count} {count-len(drop)} 0 0 0 0']+[f'I {n} {0 if n in drop else 1}' for n in range(1,count+1)]
        (folder/(sequence+'.seq')).write_text('\n'.join(lines),encoding='utf-8');(folder/'result.fit').write_bytes(b'R')
        if FakeEngine.links:
            import os
            os.symlink(next((folder/'source').iterdir()),folder/'light_00001.fit')
        progress(100,100,'');log('done '+folder.name)

class FakeContext:
    def __init__(self,root,ai=None):
        self.root=pathlib.Path(root);self.commands={};self.published={};self.imports=[];self.discarded=[];self.ai=ai;self.saved={}
        (self.root/'storage').mkdir(exist_ok=True);(self.root/'work').mkdir(exist_ok=True)
    def storage(self):return str(self.root/'storage')
    def settings(self):return {}
    def save_settings(self,values):self.saved=dict(values)
    def command(self,name,handler):self.commands[name]=handler
    def publish(self,ident,elements):self.published[ident]=elements
    def task(self,title,work,section=None):
        try:work(threading.Event(),lambda *a:None)
        except InterruptedError:pass
        except RuntimeError as error:self.task_error=str(error)
        return dict(taskId='t')
    def capability(self,ident,p):
        if self.ai is None:raise ValueError('Няма включен плъгин за функцията.')
        op=p['operation']
        if op=='available':return dict(ok=True)
        if op=='score-start':self.scored=p['ids'];return dict(ticket='T')
        if op=='score-status':return dict(state='completed',fraction=1,message='')
        if op=='score-result':
            if self.ai=='broken':raise ValueError('AI грешка')
            return dict(scores={i:float(n) for n,i in enumerate(self.scored)})
    def processing(self,action,p,cancel=None,progress=None):
        if action=='describe':return dict(rows=[dict(id=i,kind='frame',telescope='T',object='M31',night=i.split('-')[0],filter='L',width=10,height=10) for i in p['ids']])
        if action=='stage':
            ident=uuid.uuid4().hex;folder=self.root/'work'/ident;(folder/'source').mkdir(parents=True);sources=[]
            for n,i in enumerate(p['ids'],1):
                name=f'input_{n:06d}.fit';(folder/'source'/name).write_bytes(i.encode());sources.append(dict(id=i,number=n,name=name,meta=dict(headers={})))
            (folder/'session.json').write_text('{}')
            return dict(session=ident,folder=str(folder),sources=sources)
        if action=='discard' and p['session'] in getattr(self,'locked',set()):raise OSError('Файлът е заключен.')
        if action=='discard' and any(x.is_symlink() for x in (self.root/'work'/p['session']).rglob('*')):raise ValueError('Работната папка съдържа връзка и е запазена за проверка.')
        if action=='discard':self.discarded.append(p['session']);shutil.rmtree(self.root/'work'/p['session'],ignore_errors=True);return dict(removed=True)
        if action=='preview':return dict(image='data:image/png;base64,AA')
        if action=='import':self.imports.append(p);return dict(id='stack-'+str(len(self.imports)))
        raise AssertionError(action)

def frames(night,count):return [f'{night}-{n}' for n in range(count)]

class ControllerTests(unittest.TestCase):
    def setUp(self):
        FakeEngine.reject={};FakeEngine.fail=set();FakeEngine.links=False
        self.tmp=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,self.tmp,ignore_errors=True)
    def make(self,ai=None):
        ctx=FakeContext(self.tmp,ai);c=main.Controller(ctx,engine_factory=FakeEngine);c.register();return ctx,c
    def run_cmd(self,ctx,name,**p):return ctx.commands[name](p)
    def screen(self,ctx):return [g['id'] for g in ctx.published['automatic-stacking']]
    def start(self,ctx,ids,**values):
        self.run_cmd(ctx,'prepare',context=dict(ids=ids))
        self.run_cmd(ctx,'configure',values=dict(executable='x/siril-cli.exe',**values))
        self.run_cmd(ctx,'start')
    def days(self,c):return {g['night']:g for g in c.state['groups']}

    def test_prepare_shows_only_settings(self):
        ctx,c=self.make();self.run_cmd(ctx,'prepare',context=dict(ids=frames('A',3)))
        self.assertEqual(self.screen(ctx),['settings'])
    def test_start_switches_to_work_and_hides_settings(self):
        ctx,c=self.make();self.start(ctx,frames('A',3))
        self.assertEqual(self.screen(ctx),['days','sirils']);self.assertEqual(self.days(c)['A']['state'],'review')
    def test_day_split_into_parts_and_merged(self):
        ctx,c=self.make();self.start(ctx,frames('A',25),chunkSize=10,parallel=2)
        day=self.days(c)['A'];self.assertEqual(len(day['parts']),3);self.assertEqual(day['merge'],'done')
        self.assertEqual(day['state'],'review');self.assertEqual(day['relative'],'merge/result.fit')
    def test_accept_imports_union_of_used(self):
        FakeEngine.reject={'part_02':{1}}
        ctx,c=self.make();self.start(ctx,frames('A',25),chunkSize=10)
        day=self.days(c)['A'];c.state['selected']=day['id'];self.run_cmd(ctx,'accept')
        used=ctx.imports[0]['usedIds'];self.assertEqual(len(used),24);self.assertNotIn(day['parts'][1]['sources'][0]['id'],used)
        self.assertEqual(day['state'],'approved');self.assertEqual(ctx.imports[0]['relative'],'merge/result.fit');self.assertEqual(ctx.imports[0]['settings']['parts'],3)
    def test_merge_dropping_a_part_excludes_its_frames(self):
        FakeEngine.reject={'merge':{2}}
        ctx,c=self.make();self.start(ctx,frames('A',30),chunkSize=10)
        day=self.days(c)['A'];self.assertEqual(len(day['usedIds']),20)
    def test_reconfigure_runs_only_marked_days(self):
        ctx,c=self.make();self.start(ctx,frames('A',3)+frames('B',3)+frames('C',3))
        d=self.days(c);c.state['selected']=d['A']['id'];self.run_cmd(ctx,'accept')
        for night in 'BC':c.state['selected']=d[night]['id'];self.run_cmd(ctx,'repeat')
        self.run_cmd(ctx,'reconfigure');self.assertEqual(self.screen(ctx),['settings'])
        self.assertEqual(sorted(c.state['scope']),sorted([d['B']['id'],d['C']['id']]))
        self.run_cmd(ctx,'configure',values=dict(sigmaLow=2.0));self.assertEqual(d['B']['settings']['sigmaLow'],2.0);self.assertEqual(c.options['sigmaLow'],3.0)
        attempts=d['A']['attempt'];self.run_cmd(ctx,'start')
        self.assertEqual(d['A']['state'],'approved');self.assertEqual(d['A']['attempt'],attempts)
        self.assertEqual((d['B']['state'],d['C']['state']),('review','review'));self.assertFalse(d['B'].get('repeat'))
        self.assertEqual(d['B']['effectiveSettings']['sigmaLow'],2.0)
    def test_ai_filter_removes_per_day(self):
        ctx,c=self.make(ai='ok');self.run_cmd(ctx,'prepare',context=dict(ids=frames('A',10)+frames('B',10)))
        self.assertIn('aiPercent',{x['id'] for x in flat(ctx.published['automatic-stacking'][0]['children'])})
        self.run_cmd(ctx,'configure',values=dict(executable='x',aiFilter=True,aiPercent=20));self.run_cmd(ctx,'start')
        for night in 'AB':
            day=self.days(c)[night];self.assertEqual(day['aiRemovedIds'],[night+'-0',night+'-1'])
            self.assertEqual(sum(len(p['sources']) for p in day['parts']),8)
    def test_ai_option_hidden_without_evaluator(self):
        ctx,c=self.make();self.run_cmd(ctx,'prepare',context=dict(ids=frames('A',3)))
        self.assertNotIn('aiPercent',{x['id'] for x in flat(ctx.published['automatic-stacking'][0]['children'])})
    def test_ai_failure_does_not_stop_batch(self):
        ctx,c=self.make(ai='broken');self.run_cmd(ctx,'prepare',context=dict(ids=frames('A',10)))
        self.run_cmd(ctx,'configure',values=dict(executable='x',aiFilter=True));self.run_cmd(ctx,'start')
        day=self.days(c)['A'];self.assertEqual(day['state'],'review');self.assertIn('AI',day['problem'])
    def test_part_error_marks_day_and_retry_reruns_only_failed(self):
        FakeEngine.fail={'part_02'}
        ctx,c=self.make();self.start(ctx,frames('A',30),chunkSize=10)
        day=self.days(c)['A'];self.assertEqual(day['state'],'error');self.assertEqual([p['state'] for p in day['parts']],['done','error','done'])
        FakeEngine.fail=set();c.state['selected']=day['id'];self.run_cmd(ctx,'retry')
        self.assertEqual(day['state'],'review')
    def test_restart_keeps_done_parts(self):
        ctx,c=self.make();self.start(ctx,frames('A',100),chunkSize=10)
        day=self.days(c)['A']
        for p in day['parts'][6:]:p['state']='running'
        day['merge']='pending';day['state']='running';c.save(force=True)
        ctx2,c2=self.make();day2=self.days(c2)['A']
        self.assertEqual(day2['state'],'paused');self.assertEqual(sum(p['state']=='done' for p in day2['parts']),6)
        ran=[];orig=FakeEngine.run
        def spy(self_,executable,folder,*a):ran.append(pathlib.Path(folder).name);return orig(self_,executable,folder,*a)
        FakeEngine.run=spy
        try:self.run_cmd(ctx2,'resume');self.run_cmd(ctx2,'start')
        finally:FakeEngine.run=orig
        self.assertEqual(sorted(ran),['merge','part_07','part_08','part_09','part_10']);self.assertEqual(day2['state'],'review')
    def test_abandon_requires_second_click_and_keeps_approved(self):
        ctx,c=self.make();self.start(ctx,frames('A',3)+frames('B',3))
        d=self.days(c);c.state['selected']=d['A']['id'];self.run_cmd(ctx,'accept')
        self.run_cmd(ctx,'abandon');self.assertEqual(len(c.state['groups']),2)
        session=d['B']['session'];self.run_cmd(ctx,'abandon')
        self.assertEqual(c.state['groups'],[]);self.assertIn(session,ctx.discarded);self.assertEqual(len(ctx.imports),1)
        self.assertEqual(self.screen(ctx),['settings'])
    def test_activity_lists_unfinished(self):
        ctx,c=self.make();self.start(ctx,frames('A',3))
        self.assertEqual(ctx.published['sessions'][0]['items'][0]['id'],'siril-work')
    def test_slots_publish_and_console(self):
        ctx,c=self.make();self.start(ctx,frames('A',30),chunkSize=10,parallel=3)
        sirils=ctx.published['automatic-stacking'][1]['children'];slots=next(x for x in sirils if x['id']=='slots')
        self.assertEqual(len(slots['items']),3);self.assertTrue(all(i['tone']=='error' for i in slots['items']))
        self.run_cmd(ctx,'slot',selected=dict(id='2'));self.assertEqual(c.state['selectedSlot'],2)

    # ---- final review fixes ----
    def test_retry_after_finalize_error_reruns_merge(self):
        FakeEngine.reject={'merge':{1,2,3}}
        ctx,c=self.make();self.start(ctx,frames('A',30),chunkSize=10)
        day=self.days(c)['A'];self.assertEqual(day['state'],'error')
        FakeEngine.reject={};c.state['selected']=day['id'];self.run_cmd(ctx,'retry')
        self.assertEqual(day['state'],'review')
    def test_continue_after_finalize_error_single_part(self):
        FakeEngine.reject={'part_01':{1,2,3}}
        ctx,c=self.make();self.start(ctx,frames('A',3))
        day=self.days(c)['A'];self.assertEqual(day['state'],'error')
        FakeEngine.reject={};self.run_cmd(ctx,'start');self.assertEqual(day['state'],'review')
    def test_failed_discard_is_kept_and_retried(self):
        ctx,c=self.make();self.start(ctx,frames('A',3))
        day=self.days(c)['A'];session=day['session'];ctx.locked={session}
        c.state['selected']=day['id'];self.run_cmd(ctx,'accept')
        self.assertIn(session,c.state['orphanSessions'])
        ctx.locked=set();self.run_cmd(ctx,'abandon');self.run_cmd(ctx,'abandon')
        self.assertIn(session,ctx.discarded);self.assertEqual(c.state['orphanSessions'],[])
    def test_abandon_with_locked_folder_keeps_reference(self):
        ctx,c=self.make();self.start(ctx,frames('A',3))
        session=self.days(c)['A']['session'];ctx.locked={session}
        self.run_cmd(ctx,'abandon')
        with self.assertRaises(OSError):self.run_cmd(ctx,'abandon')
        self.assertEqual(c.state['orphanSessions'],[session])
    def test_bad_day_settings_do_not_stop_batch(self):
        ctx,c=self.make();self.run_cmd(ctx,'prepare',context=dict(ids=frames('A',3)+frames('B',3)))
        self.run_cmd(ctx,'configure',values=dict(executable='x'))
        self.days(c)['A']['settings']={'chunkSize':5}
        self.run_cmd(ctx,'start')
        self.assertEqual(self.days(c)['A']['state'],'error');self.assertEqual(self.days(c)['B']['state'],'review');self.assertFalse(c.busy)
    def test_reconfigure_form_shows_settings_the_day_used(self):
        ctx,c=self.make();self.start(ctx,frames('A',3),sigmaLow=2.5)
        self.run_cmd(ctx,'configure',values=dict(sigmaLow=4.0))
        day=self.days(c)['A'];c.state['selected']=day['id'];self.run_cmd(ctx,'repeat');self.run_cmd(ctx,'reconfigure')
        sigma=next(x for x in flat(ctx.published['automatic-stacking'][0]['children']) if x['id']=='sigmaLow')
        self.assertEqual(sigma['value'],2.5)

    def test_siril_links_do_not_block_cleanup(self):
        import os,tempfile as tf
        probe=pathlib.Path(tf.mkdtemp())
        try:os.symlink(probe,probe/'l')
        except OSError:self.skipTest('Symlinks are not allowed on this machine.')
        FakeEngine.links=True
        ctx,c=self.make();self.start(ctx,frames('A',20)+frames('B',3),chunkSize=10)
        d=self.days(c);c.state['selected']=d['A']['id'];session=d['A']['session'];self.run_cmd(ctx,'accept')
        self.assertIn(session,ctx.discarded);self.assertEqual(c.state.get('orphanSessions',[]),[])
        other=d['B']['session'];self.run_cmd(ctx,'abandon');self.run_cmd(ctx,'abandon');self.assertIn(other,ctx.discarded)
    def test_old_orphan_with_links_is_cleaned(self):
        import os
        ctx,c=self.make();self.start(ctx,frames('A',3))
        day=self.days(c)['A'];folder=pathlib.Path(day['folder'])
        try:os.symlink(folder/'part_01/source/input_000001.fit',folder/'part_01/light_00009.fit')
        except OSError:self.skipTest('Symlinks are not allowed on this machine.')
        c.state['orphanSessions']=[day['session']];day.pop('session');day.pop('folder')
        self.run_cmd(ctx,'abandon');self.run_cmd(ctx,'abandon');self.assertEqual(c.state['orphanSessions'],[])

    def test_abandon_returns_to_day_selection(self):
        ctx,c=self.make();self.start(ctx,frames('A',3))
        first=self.run_cmd(ctx,'abandon');self.assertNotIn('returnToPreparation',first)
        second=self.run_cmd(ctx,'abandon')
        self.assertTrue(second.get('returnToPreparation'));self.assertEqual(second.get('taskId'),'t');self.assertEqual(c.state['groups'],[])
    def test_finish_from_activity_also_returns(self):
        ctx,c=self.make();self.start(ctx,frames('A',3))
        self.run_cmd(ctx,'finish');self.assertTrue(self.run_cmd(ctx,'finish').get('returnToPreparation'))

if __name__=='__main__':unittest.main()
