"""All native dialogs belong to one dedicated GUI thread, never HTTP workers."""
import json, os, queue, subprocess, threading
from concurrent.futures import Future
from Backend.Foundation.runtime import ROOT


def native_dialog(executable, kind, options):
    process = subprocess.run([str(executable), '--native-dialog'],
        input=json.dumps(dict(kind=kind, options=options), ensure_ascii=False),
        text=True, encoding='utf-8', capture_output=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=executable.parent)
    response = json.loads(process.stdout)
    if process.returncode or 'error' in response:
        raise RuntimeError(response.get('error', 'Native dialog failed'))
    return response['value']

class Dialogs:
    def __init__(self):
        self.queue=queue.Queue();self.thread=threading.Thread(target=self.loop,daemon=True,name='AstroArchive-dialogs');self.thread.start()
    def loop(self):
        executable = ROOT / 'AstroArchive.exe'
        if os.name == 'nt' and executable.is_file():
            # The portable Python distribution has no Tcl/Tk. The .exe provides
            # Windows dialogs on its own STA thread, using bounded JSON pipes.
            while True:
                kind, options, future = self.queue.get()
                try:future.set_result(native_dialog(executable, kind, options))
                except BaseException as error:future.set_exception(error)
            return
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk();root.withdraw()
        def pump():
            try:
                kind,options,future=self.queue.get_nowait()
                try:
                    root.attributes('-topmost',True)
                    value={'folder':filedialog.askdirectory,'files':filedialog.askopenfilenames,'file':filedialog.askopenfilename,'save':filedialog.asksaveasfilename}[kind](parent=root,**options)
                    future.set_result(value)
                except BaseException as error:future.set_exception(error)
            except queue.Empty:pass
            root.after(50,pump)
        pump();root.mainloop()
    def choose(self,kind,**options):
        future=Future();self.queue.put((kind,options,future));return future.result()
