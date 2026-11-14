"""Read-only FITS windows and versioned HDR preprocessing, shared by both processes.

Native crops are never resized. Overview sampling is explicitly a separate
scale. BSCALE/BZERO/BLANK are applied by astropy.section before conversion.
"""
from __future__ import annotations
import base64
import io
import math
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter

SCHEMA='astro-fits-native-asinh-v1'
DEFAULT=dict(schema=SCHEMA,tileSize=256,stride=224,maxTiles=4096)


def configuration(config):
    if not isinstance(config,dict) or set(config)!=set(DEFAULT) or config.get('schema')!=SCHEMA:
        raise ValueError('Несъвместима FITS preprocessing схема.')
    size=config['tileSize'];stride=config['stride']
    if type(size) is not int or size not in (64,128,256) or type(stride) is not int or not 1<=stride<=size or config['maxTiles']!=4096:
        raise ValueError('Невалидни размери за FITS области.')
    return config


def _hdu(hdus):
    from astropy.io import fits
    hdu=next((h for h in hdus if isinstance(h,(fits.PrimaryHDU,fits.ImageHDU,fits.CompImageHDU)) and h.header.get('NAXIS',0)>=2 and h.header.get('NAXIS1',0)>0 and h.header.get('NAXIS2',0)>0),None)
    if hdu is None:raise ValueError('Няма FITS изображение.')
    return hdu


def _shape(hdu):
    shape=tuple(hdu.shape)
    while len(shape)>2 and shape[0]==1:shape=shape[1:]
    if len(shape)==3 and shape[0] in (3,4):return shape[1:]
    if len(shape)!=2:raise ValueError('Поддържат се mono/CFA и 3/4-plane FITS; спектрални кубове не се приемат.')
    return shape


def _section(hdu,ys,xs):
    shape=tuple(hdu.shape)
    if len(shape)==2: data=hdu.section[ys,xs]
    elif all(n==1 for n in shape[:-2]):data=hdu.section[tuple(0 for _ in shape[:-2])+(ys,xs)]
    elif len(shape)==3 and shape[0] in (3,4):
        data=np.mean(np.asarray(hdu.section[:3,ys,xs],np.float64),axis=0)
    else:raise ValueError('Неподдържан FITS cube.')
    # Float64 subtraction first preserves high integer pedestals and BZERO.
    return np.asarray(data,np.float64)


def _positions(length,size,stride):
    if length<=size:return [0]
    return sorted(set(range(0,length-size+1,stride))|{length-size})


def describe(path,config=None):
    from astropy.io import fits
    config=dict(config or DEFAULT);configuration(config)
    path=Path(path)
    if path.suffix.lower() not in ('.fit','.fits','.fts'):raise ValueError('ResNet изисква FITS кадър.')
    before=path.stat()
    with fits.open(path,mode='readonly',memmap=False,lazy_load_hdus=True) as hdus:
        hdu=_hdu(hdus);height,width=_shape(hdu)
        size=config['tileSize']
        if max(1,math.ceil(max(0,width-size)/config['stride'])+1)*max(1,math.ceil(max(0,height-size)/config['stride'])+1)+1>config['maxTiles']:
            raise ValueError('Кадърът надвишава лимита за пълно покритие.')
        xs=_positions(width,size,config['stride']);ys=_positions(height,size,config['stride'])
        if len(xs)*len(ys)+1>config['maxTiles']:raise ValueError('Кадърът надвишава лимита за пълно покритие; не се оценява частична извадка.')
        step=max(1,math.ceil(max(height,width)/1024))
        sample=_section(hdu,slice(None,None,step),slice(None,None,step))
        valid=sample[np.isfinite(sample)]
        if len(valid)<64:raise ValueError('Недостатъчни крайни FITS пиксели.')
        median=float(np.median(valid));mad=float(1.4826*np.median(abs(valid-median)))
        # Prevent division by zero, without treating an offset as signal.
        scale=max(mad,float(np.spacing(abs(median))),float(np.finfo(np.float64).tiny))
        boxes=[dict(kind='overview',bbox=[0,0,width,height],step=max(1,math.ceil(max(height,width)/size)))]
        boxes.extend(dict(kind='native',bbox=[x,y,min(size,width-x),min(size,height-y)],step=1) for y in ys for x in xs)
        header=dict(hdus[0].header);header.update(dict(hdu.header))
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('FITS е променен при четене.')
    return dict(schema=SCHEMA,config=config,width=width,height=height,median=median,scale=scale,tiles=boxes,
                sourceSize=after.st_size,sourceMtime=after.st_mtime_ns,bayer=str(header.get('BAYERPAT','')),channel='raw CFA/mono' if len(hdu.shape)==2 else 'mean RGB',coverage=1.)


def tile(path,description,index):
    from astropy.io import fits
    if type(index) is not int or not 0<=index<len(description['tiles']):raise ValueError('Невалиден tile index.')
    before=Path(path).stat()
    if (before.st_size,before.st_mtime_ns)!=(description['sourceSize'],description['sourceMtime']):raise ValueError('FITS измерванията са остарели.')
    record=description['tiles'][index];x,y,w,h=record['bbox'];step=record['step'];size=description['config']['tileSize']
    with fits.open(path,mode='readonly',memmap=False,lazy_load_hdus=True) as hdus:
        raw=_section(_hdu(hdus),slice(y,y+h,step),slice(x,x+w,step))
    valid=np.isfinite(raw)
    with np.errstate(over='ignore',invalid='ignore'):
        centered=np.clip(np.where(valid,raw-description['median'],0.)/description['scale'],-1e6,1e6)
    fine=centered-gaussian_filter(centered,1.)
    # Signed HDR intensity + high-pass + explicit validity; no 8-bit stretch.
    channels=np.stack((np.arcsinh(np.clip(centered,-1e6,1e6))/np.arcsinh(1e6),np.arcsinh(np.clip(fine,-1e4,1e4))/np.arcsinh(1e4),valid.astype(float)))
    out=np.zeros((3,size,size),np.float32);out[:,:channels.shape[1],:channels.shape[2]]=channels.astype(np.float32)
    after=Path(path).stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('FITS е променен при четене на област.')
    return out


def encode(array):
    stream=io.BytesIO();np.savez_compressed(stream,pixels=array)
    return base64.b64encode(stream.getvalue()).decode('ascii')


def decode(encoded,size):
    import zipfile
    raw=base64.b64decode(encoded,validate=True)
    if len(raw)>1_000_000:raise ValueError('Прекалено голяма област.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.namelist()!=['pixels.npy'] or sum(i.file_size for i in archive.infolist())>3*size*size*4+4096:raise ValueError('Невалиден размер на област.')
        with archive.open('pixels.npy') as stream:
            if np.lib.format.read_magic(stream)!=(1,0):raise ValueError('Неподдържан numeric array header.')
            shape,fortran,dtype=np.lib.format.read_array_header_1_0(stream,max_header_size=4096)
            if shape!=(3,size,size) or fortran or dtype!=np.dtype('float32'):raise ValueError('Невалиден pixel header.')
            payload=stream.read(3*size*size*4+1)
            if len(payload)!=3*size*size*4:raise ValueError('Непълна pixel област.')
            array=np.frombuffer(payload,dtype=np.float32).reshape(shape).copy()
    if array.shape!=(3,size,size) or array.dtype!=np.float32 or not np.isfinite(array).all():raise ValueError('Невалидни визуални данни.')
    return array
