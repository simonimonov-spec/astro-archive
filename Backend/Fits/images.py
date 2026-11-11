"""Single FITS/ordinary-image boundary shared by archive, preview and analysis."""
from __future__ import annotations
from Backend.Foundation.branding import companion as companion_path
import datetime as dt, math, re
from pathlib import Path

FITS={'.fit','.fits','.fts'}
IMAGES=FITS|{'.png','.jpg','.jpeg','.tif','.tiff'}
METADATA_VERSION=4
METADATA_FIELDS=('metadataVersion','object','telescope','night','observed','filter','exposure','totalExposure','frames','framesKnown','frameCountRecorded','kind','width','height','headers','missingFields')

def finite(value,default=None):
    try:
        value=float(value)
        return value if math.isfinite(value) else default
    except (TypeError,ValueError):return default

def missing_fields(meta):
    missing=[]
    for key in ('object','telescope','night'):
        if not meta.get(key) or str(meta[key]).startswith('Неизвест'):missing.append(key)
    if finite(meta.get('totalExposure')) is None:missing.append('totalExposure')
    if meta.get('framesKnown') is False:missing.append('frames')
    if not meta.get('width') or not meta.get('height'):missing.append('dimensions')
    return missing

def image_hdu(hdus):
    """Select the first nonempty image HDU, never a binary/ASCII table HDU."""
    from astropy.io import fits
    image_types=(fits.PrimaryHDU,fits.ImageHDU,fits.CompImageHDU)
    selected=next((hdu for hdu in hdus if isinstance(hdu,image_types)
        and hdu.header.get('NAXIS',0)>=2
        and all(int(hdu.header.get(f'NAXIS{axis}',0))>0 for axis in (1,2))),None)
    if selected is None:raise ValueError('FITS файлът няма изображение.')
    return selected

def metadata(argument):
    path=Path(argument);stat=path.stat();h={};width=height=0
    if path.suffix.lower() in FITS:
        from astropy.io import fits
        # Header-only access deliberately avoids image data and pixel scaling.
        with fits.open(path,mode='readonly',memmap=True,lazy_load_hdus=True,do_not_scale_image_data=True) as hdus:
            selected=image_hdu(hdus);header=selected.header
            info=selected.fileinfo()
            if info and not isinstance(selected,fits.CompImageHDU) and info['datLoc']+selected.size>stat.st_size:raise ValueError('FITS файлът е непълен: липсват пикселни данни.')
            # Image extensions may keep observation metadata in the primary HDU.
            combined=dict(hdus[0].header);combined.update(dict(header))
            h={str(k):(str(v) if isinstance(v,float) and not math.isfinite(v) else v) for k,v in combined.items() if isinstance(v,(str,int,float,bool))}
            width=int(h.get('NAXIS1',0));height=int(h.get('NAXIS2',0))
            if isinstance(h.get('PSUTF8'),str) and len(h['PSUTF8'])<32000:
                import base64,json
                try:
                    unicode_values=json.loads(base64.urlsafe_b64decode(h['PSUTF8']))
                    if isinstance(unicode_values,dict):h.update({k:v for k,v in unicode_values.items() if k in ('OBJECT','TELESCOP','FILTER','DETECTOR','PSTSCOPS') and isinstance(v,str)})
                except (ValueError,TypeError):pass
    else:
        from PIL import Image
        with Image.open(path) as image:
            width,height=image.size;exif=image.getexif();stamp=exif.get(36867) or exif.get(306)
            if isinstance(stamp,str) and len(stamp)>=19:
                try:h['DATE']=dt.datetime.strptime(stamp[:19],'%Y:%m:%d %H:%M:%S').isoformat()
                except ValueError:pass
    exposure=next((v for k in ('EXPTIME','EXPOSURE') if (v:=finite(h.get(k))) is not None and v>=0),None)
    frame_count=next((int(v) for k in ('NCOMBINE','STACKCNT','STACKNUM','NFRAMES','NIMAGES') if (v:=finite(h.get(k))) is not None and v>=1 and v.is_integer()),None)
    frames=frame_count or 1
    total=next((v for k in ('TOTEXP','TOTALEXP','EXPTOT','TEXPTIME') if (v:=finite(h.get(k))) is not None and v>=0),None)
    # Total exposure has precedence. EXPTIME convention is recorded as an assumption.
    assumed=total is None and frames>1
    if total is None and exposure is not None:total=exposure*frames
    observed=str(h.get('DATE-OBS',h.get('DATE-BEG','')))
    night=observed[:10]
    if observed:
        try:
            stamp=dt.datetime.fromisoformat(observed.replace('Z','+00:00'))
            night=(stamp-dt.timedelta(hours=12)).date().isoformat()
        except ValueError:pass
    kind='stack' if frames>1 or 'stack' in path.stem.lower() else 'frame' if path.suffix.lower() in FITS else 'photo'
    calibration=str(h.get('IMAGETYP',h.get('FRAME',''))).lower()
    for name in ('dark','flat','bias'):
        if name in calibration:kind=name
    frames_known=kind!='stack' or frame_count is not None
    if not frames_known and not any(finite(h.get(k)) is not None and finite(h[k])>=0 for k in ('TOTEXP','TOTALEXP','EXPTOT','TEXPTIME')):total=None
    telescope=str(h.get('TELESCOP',h.get('INSTRUME','Неизвестен телескоп')))
    # The portable companion is also used by formats without a FITS header.
    from Backend.Foundation.runtime import read_json
    extra=read_json(companion_path(path),{})
    result=dict(object=str(h.get('OBJECT','Неизвестен обект')),telescope=telescope,night=str(h.get('PSNIGHT',night)),
        observed=observed,filter=str(h.get('FILTER','')),exposure=exposure,totalExposure=total,frames=frames,
        kind=kind,width=width,height=height,headers=h,exposureAssumed=assumed,
        size=stat.st_size,mtime=stat.st_mtime_ns,metadataVersion=METADATA_VERSION,framesKnown=frames_known,frameCountRecorded=frame_count is not None,
        sensor=str(h.get('DETECTOR',h.get('CAMERA',h.get('INSTRUME','')))),resolution=f'{width} × {height}',
        created=str(h.get('DATE',dt.datetime.fromtimestamp(getattr(stat,'st_birthtime',stat.st_mtime),dt.timezone.utc).isoformat())),
        sourceStackCount=int(v) if (v:=finite(h.get('PSSTKCNT'))) is not None and v>=0 else None,telescopes=str(h.get('PSTSCOPS',telescope)).split(';'))
    side=companion_path(path);result['companionMtime']=side.stat().st_mtime_ns if side.exists() else 0
    for key in ('object','telescope','night','observed','created','filter','sensor','exposure','totalExposure','frames','sourceStackCount','telescopes','sourceFiles','sourceNights','kind','provenanceOverride','sourceStacks','combination','processing'):
        if key in extra:result[key]=extra[key]
    if 'frames' in extra:result['framesKnown']=True;result['frameCountRecorded']=True
    result['missingFields']=missing_fields(result);return result

def pixels(path,edge=None,dtype='float32'):
    import numpy as np
    path=Path(path)
    if path.suffix.lower() in FITS:
        from astropy.io import fits
        # Copy the selected data and close handles before any later archive move.
        with fits.open(path,mode='readonly',memmap=False) as hdus:
            hdu=image_hdu(hdus)
            if hdu.data is None or hdu.data.ndim<2:raise ValueError('FITS файлът няма изображение.')
            data=np.array(hdu.data,dtype=dtype,copy=True);header=dict(hdus[0].header);header.update(dict(hdu.header))
        data=np.squeeze(data)
        if data.ndim==3 and data.shape[0] in (1,3,4):data=np.moveaxis(data,0,-1)
    else:
        from PIL import Image
        with Image.open(path) as image:data=np.array(image.convert('RGB') if image.mode in ('P','CMYK','YCbCr') else image,dtype=dtype)
        header={}
    if data.ndim not in (2,3):raise ValueError('Неподдържани размери на изображението.')
    shape=data.shape
    if edge and max(shape[:2])>edge:
        step=max(1,math.ceil(max(shape[:2])/edge));data=data[::step,::step].copy()
    return data,header,shape

def gray(data):
    import numpy as np
    return np.mean(data[...,:3],axis=-1,dtype=np.float32) if data.ndim==3 else data

def component(text):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(text)).strip(' .')[:110] or 'Неизвестно'
    if value.upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(10)],*[f'LPT{i}' for i in range(10)]}:value='_'+value
    return value
