import unittest
from tests.support import load
ai_filter=load('ai_filter')

class Ctx:
    def __init__(self,raises=None,scores=None,states=('running','completed')):
        self.raises=raises;self.scores=scores or {};self.states=list(states);self.calls=[]
    def capability(self,ident,p):
        self.calls.append((ident,p['operation']))
        if self.raises:raise self.raises
        op=p['operation']
        if op=='available':return dict(ok=True)
        if op=='score-start':return dict(ticket='T',taskId='x')
        if op=='score-status':return dict(state=self.states.pop(0) if len(self.states)>1 else self.states[0],fraction=.5,message='m')
        if op=='score-result':return dict(scores=self.scores)
        if op=='score-cancel':return dict(stopping=True)

class RemovedTests(unittest.TestCase):
    def test_removed_worst_percent(self):self.assertEqual(ai_filter.removed({'a':10,'b':90,'c':50,'d':70},50),['a','c'])
    def test_none_scores_never_removed_nor_counted(self):self.assertEqual(ai_filter.removed({'a':10,'b':None,'c':None,'d':80},50),['a'])
    def test_zero_percent(self):self.assertEqual(ai_filter.removed({'a':1},0),[])
    def test_floor(self):self.assertEqual(len(ai_filter.removed({str(i):i for i in range(9)},20)),1)

class CapabilityTests(unittest.TestCase):
    def test_available_false_when_plugin_missing(self):
        self.assertFalse(ai_filter.available(Ctx(raises=ValueError('Няма включен плъгин'))))
        self.assertFalse(ai_filter.available(Ctx(raises=PermissionError('x'))))
    def test_available_true(self):self.assertTrue(ai_filter.available(Ctx()))
    def test_scores_polls_until_completed(self):
        ctx=Ctx(scores={'a':1.0},states=('running','running','completed'))
        self.assertEqual(ai_filter.scores(ctx,['a'],lambda:False,lambda f,m:None,interval=0),{'a':1.0})
    def test_scores_failed(self):
        with self.assertRaises(RuntimeError):ai_filter.scores(Ctx(states=('failed',)),['a'],lambda:False,lambda f,m:None,interval=0)
    def test_scores_stopped_cancels(self):
        ctx=Ctx(states=('running',))
        with self.assertRaises(InterruptedError):ai_filter.scores(ctx,['a'],lambda:True,lambda f,m:None,interval=0)
        self.assertIn(('inspector.quality.v1','score-cancel'),ctx.calls)

class RobustnessTests(unittest.TestCase):
    def test_status_busy_retried(self):
        ctx=Ctx(scores={'a':1.0});calls={'n':0};original=ctx.capability
        def flaky(ident,p):
            if p['operation']=='score-status' and calls['n']<2:calls['n']+=1;raise ValueError('зает')
            return original(ident,p)
        ctx.capability=flaky
        self.assertEqual(ai_filter.scores(ctx,['a'],lambda:False,lambda f,m:None,interval=0),{'a':1.0})
    def test_failure_cancels_ticket(self):
        ctx=Ctx(states=('running',));original=ctx.capability
        def broken(ident,p):
            if p['operation']=='score-status':raise ValueError('вечно зает')
            return original(ident,p)
        ctx.capability=broken
        with self.assertRaises(ValueError):ai_filter.scores(ctx,['a'],lambda:False,lambda f,m:None,interval=0)
        self.assertIn(('inspector.quality.v1','score-cancel'),ctx.calls)

if __name__=='__main__':unittest.main()
