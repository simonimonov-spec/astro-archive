"""Siril worker slots: status colour, current task, own console, and opening Siril with a window.

A slot is a plain dict so it can be saved and published. The visual Siril is a separate GUI
process in the same work folder; it cannot attach to a running background siril-cli."""
from __future__ import annotations
import subprocess
from pathlib import Path

LOG_LIMIT=300

def new_slots(count:int)->list[dict]:
    return [dict(number=n,status='red',task='',pid=None,cpu=None,memory=None,folder='',log=[]) for n in range(1,count+1)]

def append_log(slot:dict,line:str,limit:int=LOG_LIMIT)->None:
    slot['log'].append(str(line)[:16000])
    if len(slot['log'])>limit:del slot['log'][:-limit]

def gui_executable(cli:str)->Path:
    if not cli:raise ValueError('Първо посочи siril-cli.exe в настройките.')
    gui=Path(cli).with_name('siril.exe')
    if not gui.is_file():raise ValueError('Липсва siril.exe до siril-cli.exe. Визуалният Siril е част от същата инсталация.')
    return gui

def open_visual(cli:str,folder,fallback,popen=subprocess.Popen)->int:
    """Start Siril with a window in the slot's work folder (or the plugin folder if it is gone)."""
    gui=gui_executable(cli);target=Path(folder) if folder and Path(folder).is_dir() else Path(fallback)
    process=popen([str(gui),'-d',str(target)],cwd=str(target),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return process.pid
