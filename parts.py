"""Split one night's staged copies into part folders and gather part results for merging.

Only files are moved or copied inside the plugin-owned session; links are never created,
so the host can always clean the session afterwards."""
from __future__ import annotations
import math,os,shutil
from pathlib import Path

def split(count:int,chunk_size:int)->list[int]:
    """Equal part sizes (difference at most 1), so no tiny last part."""
    if not chunk_size or count<=chunk_size:return [count]
    number=math.ceil(count/chunk_size);base,extra=divmod(count,number)
    return [base+(index<extra) for index in range(number)]

def part_name(index:int)->str:return f'part_{index:02d}'

def distribute(folder,sources:list[dict],sizes:list[int])->list[dict]:
    """Move source/<name> into part_XX/source with local numbering from 1.

    Returns [dict(name, sources=[dict(id, number, name)])]. Even one part gets part_01, so a
    rerun can clean its folder without touching the host's session files."""
    folder=Path(folder)
    if sum(sizes)!=len(sources):raise ValueError('Размерите на частите не съвпадат с кадрите.')
    result=[];at=0
    for index,size in enumerate(sizes,1):
        name=part_name(index);target=folder/name/'source';target.mkdir(parents=True,exist_ok=True);members=[]
        for local,source in enumerate(sources[at:at+size],1):
            new=f'input_{local:06d}.fit';origin=folder/'source'/source['name'];destination=target/new
            if origin.is_file():os.replace(origin,destination)
            elif not destination.is_file():raise ValueError('Липсва работно копие: '+source['name'])
            members.append(dict(id=source['id'],number=local,name=new))
        result.append(dict(name=name,sources=members));at+=size
    return result

def prepare_merge(folder,part_names:list[str])->str:
    """Copy every part result into merge/source/part_XX.fit (fresh each time)."""
    folder=Path(folder);merge=folder/'merge'
    if merge.exists():shutil.rmtree(merge)
    (merge/'source').mkdir(parents=True)
    for name in part_names:
        result=folder/name/'result.fit'
        if not result.is_file():raise ValueError('Частта '+name+' няма готов result.fit.')
        shutil.copyfile(result,merge/'source'/(name+'.fit'))
    return 'merge'

def result_relative(parts:list[dict])->str:
    return parts[0]['name']+'/result.fit' if len(parts)==1 else 'merge/result.fit'

def remove_links(folder)->int:
    """Siril's convert links light_XXXXX.fit to the copies. Remove only the links (never
    their targets) so the host can clean the workspace. Returns how many were removed."""
    removed=0
    for parent,dirs,names in os.walk(folder,followlinks=False):
        for name in dirs+names:
            path=Path(parent)/name
            if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
                try:path.unlink()
                except (IsADirectoryError,PermissionError):os.rmdir(path)
                removed+=1
    return removed

KEEP=('source',)
def reset_part(folder)->None:
    """Remove leftovers of an interrupted run in one part folder; inputs in source/ stay."""
    folder=Path(folder)
    if folder.name!='merge' and not folder.name.startswith('part_'):raise ValueError('Не е папка на част.')
    remove_links(folder)
    for path in folder.iterdir():
        if path.name in KEEP:continue
        if path.is_dir():shutil.rmtree(path)
        else:path.unlink()

def copy_masters(session,part)->None:
    for master in Path(session).glob('master-*.fit'):shutil.copyfile(master,Path(part)/master.name)
