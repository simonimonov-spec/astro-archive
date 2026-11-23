import pathlib,tempfile,unittest
from tests.support import load
slots=load('slots')

class SlotTests(unittest.TestCase):
    def test_new_slots_are_red_and_numbered(self):
        s=slots.new_slots(3);self.assertEqual([x['number'] for x in s],[1,2,3]);self.assertTrue(all(x['status']=='red' for x in s))
    def test_append_log_keeps_last(self):
        s=slots.new_slots(1)[0]
        for n in range(305):slots.append_log(s,str(n))
        self.assertEqual(len(s['log']),300);self.assertEqual(s['log'][-1],'304')
    def test_gui_missing(self):
        folder=pathlib.Path(tempfile.mkdtemp());cli=folder/'siril-cli.exe';cli.write_bytes(b'')
        with self.assertRaises(ValueError):slots.gui_executable(str(cli))
    def test_gui_empty_path(self):
        with self.assertRaises(ValueError):slots.gui_executable('')
    def test_open_visual_command(self):
        folder=pathlib.Path(tempfile.mkdtemp());cli=folder/'siril-cli.exe';cli.write_bytes(b'');(folder/'siril.exe').write_bytes(b'')
        work=folder/'work';work.mkdir();calls=[]
        class Process:pid=42
        def popen(args,**kw):calls.append((args,kw));return Process()
        self.assertEqual(slots.open_visual(str(cli),work,folder,popen=popen),42)
        self.assertEqual(calls[0][0],[str(folder/'siril.exe'),'-d',str(work)]);self.assertEqual(calls[0][1]['cwd'],str(work))
    def test_open_visual_falls_back_when_folder_gone(self):
        folder=pathlib.Path(tempfile.mkdtemp());cli=folder/'siril-cli.exe';cli.write_bytes(b'');(folder/'siril.exe').write_bytes(b'');calls=[]
        class Process:pid=1
        slots.open_visual(str(cli),folder/'gone',folder,popen=lambda a,**k:calls.append(a) or Process())
        self.assertEqual(calls[0][2],str(folder))

if __name__=='__main__':unittest.main()
