"""All native dialogs belong to one dedicated GUI thread, never HTTP workers."""
import queue, threading
from concurrent.futures import Future

class Dialogs:
    def __init__(self):
        self.queue=queue.Queue();self.thread=threading.Thread(target=self.loop,daemon=True,name='AstroArchive-dialogs');self.thread.start()
    def loop(self):
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
