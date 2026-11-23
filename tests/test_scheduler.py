import unittest
from tests.support import load
scheduler=load('scheduler')
Queue=scheduler.Queue

def day(ident,count):
    return dict(id=ident,parts=[dict(name=('.' if count==1 else f'part_{i:02d}'),state='pending') for i in range(1,count+1)],merge=None if count==1 else 'pending')

class QueueTests(unittest.TestCase):
    def test_parts_then_merge(self):
        q=Queue([day('a',3)]);jobs=[q.next_job() for _ in range(3)]
        self.assertEqual([j['kind'] for j in jobs],['part']*3);self.assertIsNone(q.next_job())
        self.assertEqual(q.day_state('a'),'running')
        for j in jobs:q.finish(j,True)
        self.assertEqual(q.day_state('a'),'merging')
        m=q.next_job();self.assertEqual(m,dict(day='a',kind='merge',name='merge'))
        self.assertEqual(q.day_state('a'),'merging');q.finish(m,True);self.assertEqual(q.day_state('a'),'review')
    def test_merge_prioritized_when_parts_done(self):
        q=Queue([day('a',2),day('b',2)]);a1,a2,b1=q.next_job(),q.next_job(),q.next_job()
        q.finish(a1,True);q.finish(a2,True);self.assertEqual(q.next_job(),dict(day='a',kind='merge',name='merge'))
    def test_single_part_has_no_merge(self):
        q=Queue([day('a',1)]);j=q.next_job();q.finish(j,True);self.assertEqual(q.day_state('a'),'review');self.assertIsNone(q.next_job())
    def test_error_keeps_other_parts_running(self):
        q=Queue([day('a',3)]);j1,j2,j3=q.next_job(),q.next_job(),q.next_job()
        q.finish(j1,False);q.finish(j2,True);self.assertEqual(q.day_state('a'),'running')
        q.finish(j3,True);self.assertEqual(q.day_state('a'),'error');self.assertIsNone(q.next_job())
    def test_reset_failed_reruns_only_failed(self):
        d=day('a',3);q=Queue([d]);j1,j2,j3=q.next_job(),q.next_job(),q.next_job();q.finish(j1,False);q.finish(j2,True);q.finish(j3,True)
        q.reset_failed('a');self.assertEqual(q.next_job(),dict(day='a',kind='part',name='part_01'));self.assertIsNone(q.next_job())
        q.finish(dict(day='a',kind='part',name='part_01'),True);self.assertEqual(q.next_job()['kind'],'merge')
    def test_merge_error_reset(self):
        q=Queue([day('a',2)]);[q.finish(q.next_job(),True) for _ in range(2)];m=q.next_job();q.finish(m,False)
        self.assertEqual(q.day_state('a'),'error');q.reset_failed('a');self.assertEqual(q.next_job()['kind'],'merge')
    def test_resume_after_restart(self):
        d=day('a',10)
        for p in d['parts'][:6]:p['state']='done'
        for p in d['parts'][6:8]:p['state']='running'
        q=Queue([d]);q.resume()
        self.assertEqual(sum(p['state']=='pending' for p in d['parts']),4)
        self.assertEqual(sum(1 for _ in iter(q.next_job,None)),4)
    def test_resume_running_merge(self):
        d=day('a',2)
        for p in d['parts']:p['state']='done'
        d['merge']='running';q=Queue([d]);q.resume();self.assertEqual(q.next_job()['kind'],'merge')
    def test_progress(self):
        d=day('a',4);q=Queue([d]);j=q.next_job();q.finish(j,True);self.assertEqual(q.progress('a'),(1,4))

if __name__=='__main__':unittest.main()
