"""Bounded linear-pixel cache and display rendering for the existing JS viewer."""
from __future__ import annotations
import collections, io, json, math, threading, uuid
from pathlib import Path
from Backend.Fits.images import pixels, gray, FITS
from Backend.Imaging.display import bayer_info,demosaic,stretch

class Viewer:
    def __init__(self,catalog,events,display_preferences=None):
        self.catalog=catalog;self.events=events;self.display_preferences=display_preferences;self.frames=collections.OrderedDict();self.lock=threading.RLock();self.assets={}
    def open(self,path,source_id=None,can_navigate=True,display_name=None):
        path=Path(path);ident=uuid.uuid4().hex
        self.events.emit('viewer.loading',id=ident,fileName=display_name or path.name)
        try:
            data,header,shape=pixels(path)
            with self.lock:
                self.frames[ident]=dict(path=path,data=data,header=header,shape=shape,sourceId=source_id,canNavigate=can_navigate,name=display_name or path.name)
                self.trim()
            preferences=self.display_preferences() if self.display_preferences else {}
            if data.ndim!=2 or not bayer_info(header,data.shape):preferences={**preferences,'debayer':False,'pattern':'auto'}
            result=self.render(ident,preferences.get('options',{}),preferences.get('edge',1600),preferences.get('debayer'),preferences.get('pattern'))
        except Exception as error:
            self.close(ident);self.events.emit('viewer.problem',id=ident,error=str(error));raise
        self.events.emit('viewer.loaded',document=result)
        return dict(viewerDocument=result,opened=True)
    def thumbnail(self,source_id):return self.preview_file(self.catalog.path(source_id),240)
    def preview_file(self,path,edge=1000):
        import base64
        import numpy as np
        from PIL import Image
        data,header,_=pixels(path);info=bayer_info(header,data.shape) if data.ndim==2 else None
        if info:data=demosaic(data,info)
        step=max(1,math.ceil(max(data.shape[:2])/edge));display,_=stretch(data[::step,::step],dict(separateRgb=data.ndim==3))
        stream=io.BytesIO();Image.fromarray((display*255).astype(np.uint8)).save(stream,format='PNG')
        return dict(image='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())
    def trim(self):
        budget=self.catalog.settings.value['memoryMb']*1024**2
        while len(self.frames)>1 and sum(f['data'].nbytes+(f['cfaCache'][1].nbytes if f.get('cfaCache') else 0) for f in self.frames.values())>budget:
            ident,_=self.frames.popitem(last=False);self.assets.pop(ident,None)
    def get(self,ident):
        with self.lock:
            if ident not in self.frames:raise ValueError('Прегледът е затворен или освободен от RAM.')
            self.frames.move_to_end(ident);return self.frames[ident]
    def render(self,ident,options,edge=1600,debayer=None,pattern=None):
        import numpy as np
        from PIL import Image
        from scipy.ndimage import convolve
        f=self.get(ident);data=f['data'];header=f['header'];width,height=f['shape'][1],f['shape'][0]
        info=bayer_info(header,data.shape,pattern if pattern in ('RGGB','BGGR','GRBG','GBRG') else None) if data.ndim==2 else None
        debayer=bool(info) if debayer is None else bool(debayer and info)
        if debayer:
            cache=f.get('cfaCache')
            if not cache or cache[0]!=info['effective']:
                cache=(info['effective'],demosaic(data,info));f['cfaCache']=cache;self.trim()
            data=cache[1]
        options=dict(options);options.setdefault('separateRgb',data.ndim==3)
        step=max(1,math.ceil(max(height,width)/(edge or max(height,width))))
        if not edge and data.shape[0]*data.shape[1]>16_000_000:step=max(step,math.ceil(math.sqrt(data.shape[0]*data.shape[1]/16_000_000)))
        display=data[::step,::step].copy()
        mode=int(options.get('mode',0));channel=int(options.get('channel',0));palette=int(options.get('palette',0))
        if channel and display.ndim==3:display=gray(display) if channel==1 else display[...,min(channel-2,display.shape[-1]-1)]
        rendered,stretch_parameters=stretch(display,options)
        if rendered.ndim==2 and palette:
            x=rendered
            rendered=np.stack([np.clip(3*x-1,0,1),np.clip(3*x,0,1),np.clip(2-3*x,0,1)],axis=-1) if palette==1 else np.stack([x,x,np.sqrt(x)],axis=-1)
        image=Image.fromarray((rendered*255).astype(np.uint8));stream=io.BytesIO();image.save(stream,format='PNG')
        with self.lock:self.assets[ident]=stream.getvalue();f['previewShape']=(image.height,image.width)
        linear=data[::max(1,data.shape[0]//400),::max(1,data.shape[1]//400)];channels=[linear[...,i] for i in range(min(3,linear.shape[-1]))] if linear.ndim==3 else [linear]
        values=linear[np.isfinite(linear)];lo=float(values.min()) if values.size else 0;hi=float(values.max()) if values.size else 1
        hist=[np.histogram(c[np.isfinite(c)],bins=256,range=(lo,hi if hi>lo else lo+1))[0].tolist() for c in channels]
        return dict(id=ident,name=f['name'],path=str(f['path']),sourceId=f.get('sourceId'),width=width,height=height,previewWidth=image.width,previewHeight=image.height,
            url='viewer-image/'+ident+'?v='+uuid.uuid4().hex,fits=f['path'].suffix.lower() in FITS,
            color=data.ndim==3,canDebayer=bool(info),
            bayerPattern=info['pattern'] if info else '',effectiveBayer=info['effective'] if info else '',stretchParameters=stretch_parameters,canNavigate=f.get('canNavigate',True),
            histogram=dict(channels=hist,minimum=lo,maximum=hi,low=lo,high=hi,mean=float(np.mean(values)) if values.size else 0,median=float(np.median(values)) if values.size else 0),
            statistics=dict(mean=float(np.mean(values)) if values.size else 0,median=float(np.median(values)) if values.size else 0),
            options=options,edge=edge,debayer=debayer,pattern=pattern or 'auto',separateRgb=options.get('separateRgb',False),bitpix=header.get('BITPIX',8),objectName=header.get('OBJECT',''),date=header.get('DATE-OBS',''))
    def pixel(self,ident,x,y):
        import numpy as np
        f=self.get(ident);data=f['data'];ph,pw=f.get('previewShape',data.shape[:2]);x=int((x+.5)*data.shape[1]/pw);y=int((y+.5)*data.shape[0]/ph)
        if not 0<=x<data.shape[1] or not 0<=y<data.shape[0]:return dict(values=None)
        return dict(values=[float(v) if np.isfinite(v) else None for v in np.atleast_1d(data[y,x])])
    def header(self,ident):return dict(cards=[f'{k} = {v}' for k,v in self.get(ident)['header'].items()])
    def close(self,ident):
        with self.lock:self.frames.pop(ident,None);self.assets.pop(ident,None)
        return dict(closed=True)
    def neighbor(self,ident,delta):
        if not self.get(ident).get('canNavigate',True):return dict(opened=False)
        path=self.get(ident)['path'];from Backend.Fits.images import IMAGES
        files=sorted(p for p in path.parent.iterdir() if p.suffix.lower() in IMAGES and not p.is_symlink())
        index=files.index(path)+int(delta)
        if not 0<=index<len(files):return dict(opened=False)
        result=self.open(files[index]);self.close(ident);return result
