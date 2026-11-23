import pathlib,tempfile,unittest
from tests.support import load
parts=load('parts')

def workspace(count):
    folder=pathlib.Path(tempfile.mkdtemp());(folder/'source').mkdir()
    sources=[]
    for n in range(1,count+1):
        name=f'input_{n:06d}.fit';(folder/'source'/name).write_bytes(str(n).encode())
        sources.append(dict(id='id'+str(n),number=n,name=name))
    return folder,sources

class SplitTests(unittest.TestCase):
    def test_split_even(self):self.assertEqual(parts.split(1000,300),[250,250,250,250])
    def test_split_uneven_sums(self):
        s=parts.split(1001,300);self.assertEqual(sum(s),1001);self.assertLessEqual(max(s)-min(s),1)
    def test_split_none(self):
        self.assertEqual(parts.split(80,0),[80]);self.assertEqual(parts.split(80,100),[80]);self.assertEqual(parts.split(100,100),[100])

class DistributeTests(unittest.TestCase):
    def test_distribute_moves_and_renumbers(self):
        tmp,sources=workspace(5);out=parts.distribute(tmp,sources,[3,2])
        self.assertEqual([p['name'] for p in out],['part_01','part_02'])
        self.assertEqual([s['number'] for s in out[1]['sources']],[1,2])
        self.assertEqual([s['id'] for s in out[1]['sources']],['id4','id5'])
        self.assertEqual((tmp/'part_02/source/input_000002.fit').read_bytes(),b'5')
        self.assertFalse(any((tmp/'source').iterdir()))
    def test_distribute_is_idempotent(self):
        tmp,sources=workspace(5);first=parts.distribute(tmp,sources,[3,2]);self.assertEqual(parts.distribute(tmp,sources,[3,2]),first)
    def test_distribute_single_part_gets_folder(self):
        tmp,sources=workspace(5);out=parts.distribute(tmp,sources,[5])
        self.assertEqual(out[0]['name'],'part_01');self.assertEqual(len(list((tmp/'part_01/source').iterdir())),5)
    def test_reset_part_keeps_source(self):
        tmp,sources=workspace(2);parts.distribute(tmp,sources,[2]);part=tmp/'part_01'
        (part/'result.fit').write_bytes(b'x');(part/'r_light_').mkdir();(part/'master-dark.fit').write_bytes(b'd')
        parts.reset_part(part);self.assertEqual(sorted(p.name for p in part.iterdir()),['source'])
        with self.assertRaises(ValueError):parts.reset_part(tmp)
    def test_copy_masters(self):
        tmp,sources=workspace(2);parts.distribute(tmp,sources,[2]);(tmp/'master-flat.fit').write_bytes(b'f')
        parts.copy_masters(tmp,tmp/'part_01');self.assertEqual((tmp/'part_01/master-flat.fit').read_bytes(),b'f')
    def test_prepare_merge_copies_results(self):
        tmp,_=workspace(0)
        for name in ('part_01','part_02'):(tmp/name).mkdir();(tmp/name/'result.fit').write_bytes(name.encode())
        self.assertEqual(parts.prepare_merge(tmp,['part_01','part_02']),'merge')
        self.assertEqual((tmp/'merge/source/part_02.fit').read_bytes(),b'part_02')
    def test_prepare_merge_missing_result(self):
        tmp,_=workspace(0);(tmp/'part_01').mkdir()
        with self.assertRaises(ValueError):parts.prepare_merge(tmp,['part_01'])
    def test_result_relative(self):
        self.assertEqual(parts.result_relative([dict(name='part_01')]),'part_01/result.fit')
        self.assertEqual(parts.result_relative([dict(name='part_01'),dict(name='part_02')]),'merge/result.fit')

class LinkTests(unittest.TestCase):
    def test_remove_links_keeps_targets(self):
        import os
        tmp,sources=workspace(2);parts.distribute(tmp,sources,[2]);part=tmp/'part_01'
        try:os.symlink(part/'source/input_000001.fit',part/'light_00001.fit')
        except OSError:self.skipTest('Symlinks are not allowed on this machine.')
        self.assertEqual(parts.remove_links(tmp),1)
        self.assertFalse((part/'light_00001.fit').exists());self.assertTrue((part/'source/input_000001.fit').is_file())
    def test_reset_part_removes_links(self):
        import os
        tmp,sources=workspace(2);parts.distribute(tmp,sources,[2]);part=tmp/'part_01'
        try:os.symlink(part/'source/input_000002.fit',part/'light_00002.fit')
        except OSError:self.skipTest('Symlinks are not allowed on this machine.')
        parts.reset_part(part);self.assertEqual(sorted(p.name for p in part.iterdir()),['source'])

if __name__=='__main__':unittest.main()
