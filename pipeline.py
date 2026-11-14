"""Bounded-memory group learning and on-demand previews through the SDK."""
from __future__ import annotations
import json,threading
from pathlib import Path
import numpy as np
from . import model,quality

def atomic(path,data):
    import os,uuid
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)

def grouping(row):
    m=row['meta'];h=m.get('headers',{})
    return tuple(str(m.get(k,'')) for k in ('object','telescope','filter','night'))+(row.get('width'),row.get('height'),str(h.get('INSTRUME','')),str(h.get('GAIN','')),str(h.get('XBINNING',1)),str(h.get('YBINNING',1)))

def status_for(row):
    if row.get('qualityProblem'):return 'Напълно проблемен — за ръчна проверка'
    if row.get('warnings'):return 'Предложен за ръчна проверка'
    if row.get('uncertainty'):return 'Несигурно — '+row['uncertainty']
    return 'Няма установен временен дефект' if row.get('reliable') else 'Несигурно — недостатъчна референция'

def assess(row,options):
    """Reapply thresholds without changing measurements or a human decision."""
    if row.get('error') or not row.get('analyzerData'):
        row.update(aiQuality=None,qualityProblem=False,warnings=[]);return row
    from .heuristic import fuse
    value,confidence,_=fuse(row['analyzerData']['factors'])
    row['aiQuality']=100*value if value is not None else None
    row['scores']={}
    row['qualityProblem']=row['aiQuality'] is not None and row['aiQuality']<=options.get('qualityCutoff',35)
    findings=[]
    if row['qualityProblem']:
        findings.append(dict(type='quality',origin='ai',score=round(1-row['aiQuality']/100,4),severity='high',message=f"Напълно проблемен според AI: индекс {row['aiQuality']:.1f}% е под зададения праг. Провери ръчно."))
    row['warnings']=findings;row['status']=status_for(row);return row

class Pipeline:
    def __init__(self,context,folder):self.ctx=context;self.folder=Path(folder);self.io_lock=threading.Lock()

    def run(self,ids,report_id,options,cancel,progress):
        from .local_pipeline import run
        return run(self,ids,report_id,options,cancel,progress)

    def preview(self,row):
        import io,base64
        from PIL import Image,ImageDraw
        sample=self.ctx.inspection('sample',dict(id=row['id'],edge=row.get('edge',768)))
        if sample['signature']!=f"{row['sourceSize']}:{row['sourceMtime']}":raise ValueError('Кадърът е променен; преизчисли инспекцията.')
        image=self.ctx.inspection('warp',dict(image=np.nan_to_num(sample['image']),matrix=row['matrix'],shape=tuple(row['shape'])))
        finite=image[np.isfinite(image)];lo,hi=np.percentile(finite,[1,99.8]);display=np.arcsinh(15*np.clip((image-lo)/max(1e-8,hi-lo),0,1))/np.arcsinh(15)
        result=Image.fromarray((display*255).astype(np.uint8)).convert('RGB');draw=ImageDraw.Draw(result)
        for line in row.get('regions',{}).get('lines',[]):draw.line([tuple(line['a']),tuple(line['b'])],fill='#ffbe65',width=3)
        box=row.get('regions',{}).get('dark')
        if box:draw.rectangle((box['x'],box['y'],box['x']+box['width'],box['y']+box['height']),outline='#ff6c86',width=3)
        for factor in row.get('aiResult',{}).get('factors',[]):
            for evidence in factor['evidence']:
                if evidence.get('coordinateSpace')!='native' or not evidence.get('bbox'):continue
                x,y,w,h=evidence['bbox'];matrix=np.asarray(row['matrix'])@np.asarray(sample['pixelToSample'])
                corners=np.array([[x,y,1.],[x+w,y+h,1.]])@matrix.T;corners=corners[:,:2]/corners[:,2,None]
                draw.rectangle(tuple(corners.ravel()),outline='#ff9933',width=2)
        stream=io.BytesIO();result.save(stream,'PNG');return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
