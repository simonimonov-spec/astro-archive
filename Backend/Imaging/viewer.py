"""Bounded linear-pixel cache and display rendering for the existing JS viewer."""
from __future__ import annotations
import collections, io, json, math, threading, uuid
from pathlib import Path
from Backend.Fits.images import pixels, gray, FITS
from Backend.Imaging.display import bayer_info,demosaic,stretch

class Viewer:
    def __init__(self,catalog,events,display_preferences=None):
        self.catalog=catalog;self.events=events;self.display_preferences=display_preferences;self.frames=collections.OrderedDict();self.lock=threading.RLock();self.assets={}
        self.current = self.loading = None
        self.buffer = {}
        self.nearby = ()
        self.prefetch_queue = collections.deque()
        self.worker = None

    def open(self,path,source_id=None,can_navigate=True,display_name=None,comparison=False,nearby=None):
        path=Path(path);ident=uuid.uuid4().hex
        with self.lock:
            if not comparison:
                self.loading = ident
                self.events.emit('viewer.loading',id=ident,fileName=display_name or path.name,sourceId=source_id)
            cached = self.buffer.pop(path, None) if not comparison else None
        try:
            preferences = self.display_preferences() if self.display_preferences else {}
            stamp = self._stamp(path)
            if cached and cached['stamp'] != stamp:
                cached = None
            frame = cached or self._read_frame(path, stamp)
            if frame.get('preferences') != preferences:
                self._prepare(frame, preferences)
            frame.update(sourceId=source_id,canNavigate=can_navigate,name=display_name or path.name,comparison=comparison)
            if not comparison:
                if not can_navigate:
                    nearby = ()
                elif nearby is None:
                    try:
                        nearby = self._nearby_paths(path)
                    except OSError:
                        nearby = ()
            with self.lock:
                if not comparison and self.loading != ident:
                    return dict(opened=False)
                previous = None
                if not comparison:
                    previous = self.frames.pop(self.current, None)
                    self.assets.pop(self.current, None)
                self.frames[ident] = frame
                self.assets[ident] = frame['png']
                document = self._document(ident, frame)
                if not comparison:
                    self.current, self.loading = ident, None
                    self._move_buffer(nearby, path, previous)
                self.trim()
                if not comparison:
                    self.events.emit('viewer.loaded',document=document)
        except Exception as error:
            with self.lock:
                if not comparison and self.loading == ident:
                    self.loading = None
                    self.close(self.current)
                    self.events.emit('viewer.problem',id=ident,error=str(error))
                elif comparison:
                    self.close(ident)
            raise
        return dict(viewerDocument=document,opened=True)

    @staticmethod
    def _stamp(path):
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    @staticmethod
    def _read_frame(path, stamp):
        data, header, shape = pixels(path)
        return dict(path=path, data=data, header=header, shape=shape, stamp=stamp)

    def _prepare(self, frame, preferences):
        data, header = frame['data'], frame['header']
        debayer = preferences.get('debayer')
        pattern = preferences.get('pattern')
        if data.ndim != 2 or not bayer_info(header, data.shape):
            debayer, pattern = False, 'auto'
        self._render_frame(frame, preferences.get('options', {}), preferences.get('edge', 1600), debayer, pattern)
        frame['preferences'] = preferences

    @staticmethod
    def _size(frame):
        return frame['data'].nbytes + (frame['cfaCache'][1].nbytes if frame.get('cfaCache') else 0) + len(frame.get('png', b''))

    def _memory_used(self):
        return sum(map(self._size, self.frames.values())) + sum(map(self._size, self.buffer.values()))

    def _budget(self):
        return self.catalog.settings.value['memoryMb'] * 1024**2

    def _move_buffer(self, paths, current_path, previous):
        self.nearby = tuple(dict.fromkeys(path for path in paths if path != current_path))[:10]
        self.buffer = {path: self.buffer[path] for path in self.nearby if path in self.buffer}
        if previous and previous['path'] in self.nearby:
            self.buffer[previous['path']] = previous
        self.prefetch_queue = collections.deque(path for path in self.nearby if path not in self.buffer)
        if self.prefetch_queue and self.worker is None:
            self.worker = threading.Thread(target=self._prefetch, daemon=True, name='viewer-prefetch')
            self.worker.start()

    def _prefetch(self):
        while True:
            with self.lock:
                if self.loading or not self.prefetch_queue or self._memory_used() >= self._budget():
                    self.worker = None
                    return
                path = self.prefetch_queue.popleft()
            frame = None
            try:
                if path.is_symlink():
                    continue
                frame = self._read_frame(path, self._stamp(path))
                with self.lock:
                    if path not in self.nearby or self._memory_used() + self._size(frame) > self._budget():
                        continue
                self._prepare(frame, self.display_preferences() if self.display_preferences else {})
            except MemoryError:
                with self.lock:
                    self.worker = None
                return
            except Exception:
                # An unreadable neighbour still reports its usual error when explicitly opened.
                continue
            with self.lock:
                if path in self.nearby and path not in self.buffer and self._memory_used() + self._size(frame) <= self._budget():
                    self.buffer[path] = frame

    def _document(self, ident, frame):
        return dict(frame['preview'], id=ident, name=frame['name'], path=str(frame['path']),
                    sourceId=frame.get('sourceId'), canNavigate=frame.get('canNavigate', True),
                    url='viewer-image/'+ident+'?v='+uuid.uuid4().hex)

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
        with self.lock:
            budget = self._budget()
            for path in reversed(self.nearby):
                if self._memory_used() <= budget:
                    break
                self.buffer.pop(path, None)
            while len(self.frames) > 1 and self._memory_used() > budget:
                ident = next(iter(self.frames))
                self.close(ident)

    def get(self,ident):
        with self.lock:
            if ident not in self.frames:raise ValueError('Прегледът е затворен или освободен от RAM.')
            self.frames.move_to_end(ident);return self.frames[ident]
    def render(self,ident,options,edge=1600,debayer=None,pattern=None):
        frame = self.get(ident)
        # Render off-lock. A concurrent close or move must not commit stale pixels.
        updated = dict(frame)
        self._render_frame(updated, options, edge, debayer, pattern)
        with self.lock:
            if self.frames.get(ident) is not frame:
                raise FileNotFoundError('Прегледът е затворен.')
            updated['preferences'] = None
            frame.update(updated)
            self.assets[ident] = frame['png']
            self.trim()
            return self._document(ident, frame)

    def _render_frame(self,f,options,edge,debayer,pattern):
        import numpy as np
        from PIL import Image
        data=f['data'];header=f['header'];width,height=f['shape'][1],f['shape'][0]
        info=bayer_info(header,data.shape,pattern if pattern in ('RGGB','BGGR','GRBG','GBRG') else None) if data.ndim==2 else None
        debayer=bool(info) if debayer is None else bool(debayer and info)
        if debayer:
            cache=f.get('cfaCache')
            if not cache or cache[0]!=info['effective']:
                cache=(info['effective'],demosaic(data,info));f['cfaCache']=cache
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
        f['png']=stream.getvalue();f['previewShape']=(image.height,image.width)
        linear=data[::max(1,data.shape[0]//400),::max(1,data.shape[1]//400)];channels=[linear[...,i] for i in range(min(3,linear.shape[-1]))] if linear.ndim==3 else [linear]
        values=linear[np.isfinite(linear)];lo=float(values.min()) if values.size else 0;hi=float(values.max()) if values.size else 1
        hist=[np.histogram(c[np.isfinite(c)],bins=256,range=(lo,hi if hi>lo else lo+1))[0].tolist() for c in channels]
        f['preview'] = dict(width=width,height=height,previewWidth=image.width,previewHeight=image.height,
            fits=f['path'].suffix.lower() in FITS,
            color=data.ndim==3,canDebayer=bool(info),
            bayerPattern=info['pattern'] if info else '',effectiveBayer=info['effective'] if info else '',stretchParameters=stretch_parameters,
            histogram=dict(channels=hist,minimum=lo,maximum=hi,low=lo,high=hi,mean=float(np.mean(values)) if values.size else 0,median=float(np.median(values)) if values.size else 0),
            statistics=dict(mean=float(np.mean(values)) if values.size else 0,median=float(np.median(values)) if values.size else 0),
            options=options,edge=edge,debayer=debayer,pattern=pattern or 'auto',separateRgb=options.get('separateRgb',False),bitpix=header.get('BITPIX',8),objectName=header.get('OBJECT',''),date=header.get('DATE-OBS',''))
    def pixel(self,ident,x,y):
        import numpy as np
        f=self.get(ident);data=f['data'];ph,pw=f.get('previewShape',data.shape[:2]);x=int((x+.5)*data.shape[1]/pw);y=int((y+.5)*data.shape[0]/ph)
        if not 0<=x<data.shape[1] or not 0<=y<data.shape[0]:return dict(values=None)
        return dict(values=[float(v) if np.isfinite(v) else None for v in np.atleast_1d(data[y,x])])
    def header(self,ident):return dict(cards=[f'{k} = {v}' for k,v in self.get(ident)['header'].items()])
    def close(self,ident,comparison=False):
        with self.lock:
            frame=self.frames.get(ident)
            if comparison and frame and not frame.get('comparison'):
                raise ValueError('Изображението не е от работното пространство за сравнение.')
            if ident is not None and ident == self.loading:
                self.loading = None
                ident = self.current
            self.frames.pop(ident,None);self.assets.pop(ident,None)
            if ident == self.current:
                self.current = self.loading = None
                self.nearby = ()
                self.prefetch_queue.clear()
                self.buffer.clear()
        return dict(closed=True)

    @staticmethod
    def _folder_images(path):
        from Backend.Fits.images import IMAGES
        return sorted(p for p in path.parent.iterdir() if p.suffix.lower() in IMAGES and not p.is_symlink() and p.is_file())

    def _nearby_paths(self, path):
        files = self._folder_images(path)
        if path not in files:
            return ()
        index = files.index(path)
        return tuple(files[index + step] for distance in range(1, 6) for step in (distance, -distance)
                     if 0 <= index + step < len(files))

    def adjacent_path(self,frame,delta):
        if not frame.get('canNavigate',True):return None
        path=frame['path']
        files=self._folder_images(path)
        if path not in files:return None
        index=files.index(path)+int(delta)
        return files[index] if 0<=index<len(files) else None
    def neighbor(self,ident,delta):
        frame=self.get(ident)
        path=self.adjacent_path(frame,delta)
        if path is None:return dict(opened=False)
        comparison = frame.get('comparison', False)
        result = self.open(path, comparison=comparison)
        if comparison and result['opened']:
            self.close(ident)
        return result
