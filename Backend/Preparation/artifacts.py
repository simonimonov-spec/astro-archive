"""Data reports and chart files, with provenance, for the preparation basket."""
from __future__ import annotations
import base64, csv, io, json, shutil, time, uuid
from pathlib import Path
from Backend.Foundation.runtime import digest, within
from Backend.Fits.images import component

DATA_FORMATS={'.csv','.json','.ecsv','.npz'}

class Artifacts:
    def __init__(self,store,events):self.store=store;self.events=events
    @property
    def folder(self):
        folder=self.store.folder/'Exports';folder.mkdir(exist_ok=True);return folder
    def list(self):
        with self.store.db() as db:return [json.loads(r[0]) for r in db.execute('SELECT document FROM artifacts ORDER BY rowid DESC')]
    def record(self,ident):
        with self.store.db() as db:row=db.execute('SELECT document FROM artifacts WHERE id=?',(ident,)).fetchone()
        if not row:raise FileNotFoundError('Непознат отчет или диаграма.')
        return json.loads(row[0])
    def path(self,ident):return within(self.folder,self.record(ident)['relative'])
    def add(self,source,context=None,label=None):
        source=Path(source);ident='data-'+uuid.uuid4().hex;name=component(label or source.name)
        target=self.folder/(ident+'-'+name);fingerprint=digest(source)
        with target.open('xb') as output,source.open('rb') as input_:shutil.copyfileobj(input_,output,4*1024*1024)
        if digest(target)!=fingerprint:target.unlink();raise OSError('Отчетът не съвпада с копието.')
        document=dict(id=ident,relative=target.name,size=target.stat().st_size,mtime=target.stat().st_mtime_ns,added=time.time(),
            name=name,meta=dict(dict(kind='data',telescope='',object='',night='',frames=0,totalExposure=0),**(context or {})))
        with self.store.db() as db:db.execute('INSERT INTO artifacts VALUES(?,?)',(ident,json.dumps(document,ensure_ascii=False,allow_nan=False)))
        self.events.emit('preparation.changed');return document
    def payload(self,report,format='csv',ids=None,png=None):
        series=report.get('type')=='timeseries'
        selected=set(ids) if ids is not None else None
        rows=[r for r in report['rows' if series else 'frames'] if selected is None or r.get('fileId',r['id']) in selected]
        if not rows:raise ValueError('Няма данни за изнасяне.')
        keys=(['fileId','name','starId','starName','utc','jd','night','filter','exposure','x','y','flux','fluxRate','instrumentalMagnitude','background','backgroundError','warnings'] if series else
              ['id','name','decision','fwhm','fwhmY','hfr','roundness','eccentricity','stars','background','noise','peakSnr','shiftX','shiftY','rotation','scale','registrationRms','recommended','statusText'])
        context=dict(sourceFiles=list(dict.fromkeys(r.get('fileId',r['id']) for r in rows)),
                     sourceNights=sorted({r.get('night',r.get('meta',{}).get('night','')) for r in rows}),
                     telescope=', '.join(sorted({r.get('meta',report.get('sourceMeta',{})).get('telescope','') for r in rows})),
                     object=', '.join(sorted({r.get('meta',report.get('sourceMeta',{})).get('object','') for r in rows})))
        context['night']=', '.join(context['sourceNights'])
        if format=='json':content=json.dumps(dict(report,**{'rows' if series else 'frames':rows}),ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
        elif format in ('csv','ecsv'):
            values=[{key:json.dumps(r.get(key),ensure_ascii=False) if isinstance(r.get(key),(dict,list)) else r.get(key) for key in keys} for r in rows]
            stream=io.StringIO()
            if format=='csv':
                writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows(values);content=stream.getvalue().encode('utf-8-sig')
            else:
                from astropy.table import Table,MaskedColumn
                table=Table()
                for key in keys:
                    column=[r[key] for r in values];numeric=any(isinstance(v,(int,float)) for v in column if v is not None) and all(v is None or isinstance(v,(int,float)) for v in column)
                    table[key]=MaskedColumn([v if v is not None else (0. if numeric else '') for v in column],mask=[v is None for v in column])
                table.meta['limitations']=report.get('limitations',[]);table.write(stream,format='ascii.ecsv');content=stream.getvalue().encode('utf-8')
        elif format=='png':
            from PIL import Image
            if not isinstance(png,str) or not png.startswith('data:image/png;base64,') or len(png)>3_000_000:raise ValueError('Невалидна или прекалено голяма PNG диаграма.')
            data=base64.b64decode(png.split(',',1)[1],validate=True)
            with Image.open(io.BytesIO(data)) as image:
                if image.format!='PNG' or image.width*image.height>16_000_000:raise ValueError('Невалидна PNG диаграма.')
                stream=io.BytesIO();image.save(stream,format='PNG');content=stream.getvalue()
        else:raise ValueError('Отчетите поддържат CSV, JSON, ECSV и PNG диаграма.')
        return content,context
    def preview(self,report,format='csv',ids=None,png=None):
        content,_=self.payload(report,format,ids,png)
        return dict(format=format,bytes=len(content),text=None if format=='png' else content.decode('utf-8-sig')[:20000],truncated=format!='png' and len(content.decode('utf-8-sig'))>20000)
    def report(self,report,format='csv',ids=None,png=None):
        content,context=self.payload(report,format,ids,png)
        temporary=self.folder/('.'+uuid.uuid4().hex+'.'+format)
        try:
            temporary.write_bytes(content);return self.add(temporary,context,('stars-' if report.get('type')=='timeseries' else 'analysis-')+report['id'][:8]+'.'+format)
        finally:temporary.unlink(missing_ok=True)
