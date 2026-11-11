"""Display-only CFA interpolation and robust median/MAD/MTF stretching.

No source pixels are overwritten. Decimation happens after CFA reconstruction.
"""
from __future__ import annotations
import math

PATTERNS={'RGGB','BGGR','GRBG','GBRG'}

def bayer_info(header,shape,override=None):
    pattern=str(override or header.get('BAYERPAT',header.get('BAYERPATN',''))).upper().replace(' ','')
    if pattern not in PATTERNS:return None
    x=int(header.get('XBAYROFF',0))%2;y=int(header.get('YBAYROFF',0))%2
    # ROWORDER describes the sensor matrix convention. A bottom-up even-height
    # array begins on the opposite matrix row.
    if str(header.get('ROWORDER','TOP-DOWN')).upper()=='BOTTOM-UP':y=(y+shape[0]-1)%2
    effective=''.join(pattern[((row+y)%2)*2+(col+x)%2] for row in range(2) for col in range(2))
    return dict(pattern=pattern,effective=effective,x=x,y=y)

def demosaic(image,info):
    import numpy as np
    from scipy.ndimage import convolve
    kernel=np.array([[1,2,1],[2,4,2],[1,2,1]],np.float32);channels=[];valid=np.isfinite(image)
    for color in 'RGB':
        mask=np.zeros(image.shape,np.float32)
        for n,letter in enumerate(info['effective']):
            if letter==color:mask[n//2::2,n%2::2]=valid[n//2::2,n%2::2]
        signal=convolve(np.where(valid,image,0)*mask,kernel,mode='mirror')
        weight=convolve(mask,kernel,mode='mirror');channels.append(np.divide(signal,weight,out=np.zeros_like(signal),where=weight>0))
    return np.stack(channels,axis=-1)

def mtf(image,midtones):
    import numpy as np
    x=np.clip(image,0,1);m=max(1e-6,min(1-1e-6,float(midtones)));den=(2*m-1)*x-m
    return np.divide((m-1)*x,den,out=np.zeros_like(x),where=np.abs(den)>1e-15)

def stretch(data,options=None):
    import numpy as np
    options=options or {};mode=int(options.get('mode',0));independent=bool(options.get('separateRgb',options.get('separate',False)))
    parameters=[]
    def normalize(part):
        stride=max(1,int(math.ceil(math.sqrt(part.size/500000))))
        sample=part[::stride,::stride] if part.ndim==2 else part[::stride,::stride,:]
        values=sample[np.isfinite(sample)]
        if not values.size:return np.zeros_like(part)
        minimum=float(values.min());maximum=float(values.max())
        if mode==0:
            median=float(np.median(values));sigma=1.4826*float(np.median(np.abs(values-median)))
            lo=median-2.8*sigma;hi=maximum
            if hi<=lo:hi=lo+max(abs(lo)*1e-6,1)
            median_normalized=max(1e-6,min(.999999,(median-lo)/(hi-lo)))
            mid=float(mtf(np.array(median_normalized),.25));mid=max(1e-6,min(.999999,mid))
            normalized=mtf((part-lo)/(hi-lo),mid)
            parameters.append(dict(black=lo,white=hi,midtones=mid,background=.25))
        else:
            lo,hi=(np.percentile(values,[float(options.get('blackPercent',0)),float(options.get('whitePercent',100))]) if mode==2 else (minimum,maximum))
            normalized=np.clip((part-lo)/max(float(hi-lo),1e-12),0,1)
            if mode==2:normalized=mtf(normalized,options.get('midtone',options.get('midtones',.5)))
            elif mode==3:normalized=np.arcsinh(10*normalized)/np.arcsinh(10)
            elif mode==4:normalized=np.log1p(100*normalized)/np.log1p(100)
            elif mode==5:normalized=np.sqrt(normalized)
            elif mode==6:normalized=normalized**2
        result=np.clip(normalized,0,1)**(1/max(.1,float(options.get('gamma',1))))
        return np.nan_to_num(1-result if options.get('negative') else result,nan=0,posinf=1,neginf=0)
    result=np.stack([normalize(data[...,c]) for c in range(3)],axis=-1) if independent and data.ndim==3 else normalize(data)
    return result,parameters
