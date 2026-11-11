"""Independent Gaussian PSF measurements in native pixel units.

Only NumPy/SciPy and the application's FITS boundary are used. No executable,
Siril import, sequence file, reference result or external star list is read.
"""
from __future__ import annotations
import math

FWHM_FACTOR=math.sqrt(8*math.log(2))
DETECTION=dict(sigma=1.,radius=5,roundness=.5,maxStars=2000,profile='Gaussian')


def detection_settings(value=None):
    if value is None:return dict(DETECTION)
    if not isinstance(value,dict) or set(value)-{'sigma','radius','roundness','maxStars'}:raise ValueError('Невалидни настройки за откриване на звезди.')
    result=dict(DETECTION,**value)
    for key,low,high in [('sigma',.05,10),('radius',3,20),('roundness',0,.95),('maxStars',20,2000)]:
        number=result[key]
        if type(number) not in (int,float) or not math.isfinite(number) or not low<=number<=high:raise ValueError('Невалидна настройка: '+key)
        if key in ('radius','maxStars'):
            if number!=int(number):raise ValueError('Изисква се цяло число: '+key)
            result[key]=int(number)
        else:result[key]=float(number)
    return result


def angular_scale(header):
    """Arcseconds per native pixel; WCS first, then physical sensor geometry."""
    try:
        if header.get('CDELT1'):
            value=abs(float(header['CDELT1']))*3600
            if math.isfinite(value) and value>0:return value
        if header.get('CD1_1') is not None:
            value=math.hypot(float(header['CD1_1']),float(header.get('CD2_1',0)))*3600
            if math.isfinite(value) and value>0:return value
        pixel=float(header.get('XPIXSZ',header.get('PIXSIZE',0)));focal=float(header.get('FOCALLEN',header.get('FOCAL',0)))
        if pixel>0 and focal>0:
            value=206.264806247*pixel*max(1,float(header.get('XBINNING',1)))/focal
            if math.isfinite(value) and value>0:return value
    except (ValueError,TypeError,OverflowError):pass
    return None


def analysis_plane(data,header):
    import numpy as np
    from scipy.ndimage import convolve
    from Backend.Imaging.display import bayer_info
    if data.ndim==3:
        return data[...,1 if data.shape[-1]>=3 else 0].astype(np.float32,copy=False),'Green' if data.shape[-1]>=3 else 'Mono'
    info=bayer_info(header,data.shape)
    if not info:return data.astype(np.float32,copy=False),'Mono'
    green=np.zeros(data.shape,bool)
    for index,color in enumerate(info['effective']):
        if color=='G':green[index//2::2,index%2::2]=True
    valid=green & np.isfinite(data)
    kernel=np.array([[0,1,0],[1,0,1],[0,1,0]],np.float32)
    sums=convolve(np.where(valid,data,0).astype(np.float32),kernel,mode='mirror')
    counts=convolve(valid.astype(np.float32),kernel,mode='mirror')
    interpolated=np.divide(sums,counts,out=np.full_like(sums,np.nan),where=counts>0)
    # Preserve every measured green photosite. Interpolate missing sites only.
    return np.where(valid,data,interpolated).astype(np.float32),'CFA Green'


def background_statistics(image):
    import numpy as np
    from astropy.stats import sigma_clipped_stats
    # Odd flat stride samples all CFA phases instead of one sensor sublattice.
    step=max(1,math.ceil(image.size/700000));step+=int(step%2==0)
    sample=image.ravel()[::step];sample=sample[np.isfinite(sample)]
    if sample.size<100:raise ValueError('Недостатъчно валидни пиксели за измерване.')
    _,_,fallback=sigma_clipped_stats(sample,sigma=3,maxiters=5)
    median=float(np.median(sample))
    # Robust second differences suppress sky gradients and stellar structure.
    # The two-pixel lag avoids adjacent-pixel correlation after CFA interpolation.
    delta=np.abs(2*image[:,2:-2]-image[:,:-4]-image[:,4:])
    per_row=np.nanmedian(np.where(np.isfinite(delta),delta,np.nan),axis=1)
    sigma=float(np.nanmedian(per_row))*(1.482602218505602/math.sqrt(6))
    if not math.isfinite(sigma) or sigma<=0:sigma=float(fallback)
    return median,max(sigma,np.finfo(np.float32).eps)


def saturation_level(header):
    for key in ('SATURATE','SATLEVEL'):
        try:
            value=float(header.get(key,0))
            if math.isfinite(value) and value>0:return value
        except (ValueError,TypeError):pass
    try:
        bits=int(header.get('BITPIX',0));scale=float(header.get('BSCALE',1));offset=float(header.get('BZERO',0))
        if bits in (8,16,32) and scale>0:
            return ((2**bits-1) if bits==8 else (2**(bits-1)-1))*scale+offset
    except (ValueError,TypeError,OverflowError):pass
    return None


def fit_star(image,x,y,noise,background,*,radius=None,saturation=None):
    """Fit a rotated elliptical Gaussian plus a free local constant sky."""
    import numpy as np
    from scipy.optimize import least_squares
    from scipy.ndimage import gaussian_filter
    ix,iy=int(round(x)),int(round(y));height,width=image.shape
    probe=6
    if min(ix,iy,width-1-ix,height-1-iy)<probe:return None
    cut=image[iy-probe:iy+probe+1,ix-probe:ix+probe+1].astype(float)
    if np.isfinite(cut).sum()<cut.size*.9:return None
    sky=float(np.nanmedian(np.concatenate((cut[0],cut[-1],cut[1:-1,0],cut[1:-1,-1]))))
    smooth=gaussian_filter(np.nan_to_num(cut,nan=sky),1)
    signal=np.maximum(smooth-sky,0);yy,xx=np.indices(cut.shape)
    total=signal.sum()
    if total<=0:return None
    cx=float((signal*xx).sum()/total);cy=float((signal*yy).sum()/total)
    dx=xx-cx;dy=yy-cy
    covariance=np.array([[(signal*dx*dx).sum(),(signal*dx*dy).sum()],[(signal*dx*dy).sum(),(signal*dy*dy).sum()]])/total
    eigen,vectors=np.linalg.eigh(covariance)
    major=math.sqrt(max(.5,float(eigen[1])-1));minor=math.sqrt(max(.4,float(eigen[0])-1))
    angle=math.atan2(vectors[1,1],vectors[0,1])
    radius=int(radius or max(5,min(30,math.ceil(major*3.7))))
    if min(ix,iy,width-1-ix,height-1-iy)<radius:return None
    cut=image[iy-radius:iy+radius+1,ix-radius:ix+radius+1].astype(float)
    yy,xx=np.indices(cut.shape,dtype=float)
    valid=np.isfinite(cut)
    saturated=np.zeros(cut.shape,bool) if saturation is None else cut>=saturation-max(abs(saturation)*1e-6,1e-6)
    valid&=~saturated
    if valid.sum()<30:return None
    amplitude=float(np.nanmax(cut)-sky)
    if amplitude<5*noise:return None
    values=(cut-sky)/amplitude;xx=xx[valid];yy=yy[valid];values=values[valid]
    initial=[1.,radius+cx-probe,radius+cy-probe,max(.6,major),max(.6,minor),angle,0.]
    def model(p,jacobian=False):
        amp,cx,cy,sx,sy,theta,base=p;c,s=math.cos(theta),math.sin(theta)
        dx,dy=xx-cx,yy-cy;u=c*dx+s*dy;v=-s*dx+c*dy
        gaussian=np.exp(-.5*((u/sx)**2+(v/sy)**2));star=amp*gaussian
        if not jacobian:return star+base-values
        return np.column_stack((gaussian,star*(u*c/sx**2-v*s/sy**2),star*(u*s/sx**2+v*c/sy**2),
                                star*u*u/sx**3,star*v*v/sy**3,star*u*v*(1/sy**2-1/sx**2),np.ones_like(xx)))
    try:
        fit=least_squares(model,initial,jac=lambda p:model(p,True),
                          bounds=([.01,radius-3,radius-3,.3,.3,-10,-2],[5,radius+3,radius+3,radius,radius,10,2]),
                          loss='linear',max_nfev=100,x_scale='jac',ftol=1e-7,xtol=1e-7,gtol=1e-7)
    except (ValueError,FloatingPointError):return None
    if not fit.success:return None
    amp,cx,cy,sx,sy,angle,base=fit.x
    if sy>sx:sx,sy=sy,sx;angle+=math.pi/2
    fwhm,fwhmy=FWHM_FACTOR*sx,FWHM_FACTOR*sy
    rms=float(np.sqrt(np.mean(model(fit.x)**2)))*amplitude
    peak=float(amp*amplitude)
    if not .9<=fwhmy<=fwhm<=40 or rms/max(peak,1e-15)>.2:return None
    distances=np.hypot(xx-cx,yy-cy);flux=np.maximum(values-base,0)
    order=np.argsort(distances);cumulative=np.cumsum(flux[order])
    hfr=float(distances[order][min(len(order)-1,np.searchsorted(cumulative,cumulative[-1]*.5))])
    return dict(x=float(ix-radius+cx),y=float(iy-radius+cy),fwhm=float(fwhm),fwhmY=float(fwhmy),
                roundness=float(sy/sx),eccentricity=float(math.sqrt(max(0,1-(sy/sx)**2))),
                hfr=hfr,peakSnr=peak/noise,weight=peak,angle=float(angle),background=float(sky+base*amplitude),
                amplitude=peak,rmse=rms,saturated=bool(saturated.any()))


def detect_stars(image,background,noise,options=None,*,saturation=None):
    import numpy as np
    from scipy.ndimage import gaussian_filter,maximum_filter
    options=dict(DETECTION,**(options or {}))
    clean=np.where(np.isfinite(image),image,background)
    smooth=gaussian_filter(clean,2.,mode='mirror')
    radius=int(options['radius']);threshold=background+float(options['sigma'])*5*noise
    maxima=(smooth>threshold)&(smooth==maximum_filter(smooth,2*radius+1,mode='mirror'))
    maxima[:radius]=False;maxima[-radius:]=False;maxima[:,:radius]=False;maxima[:,-radius:]=False
    ys,xs=np.nonzero(maxima);order=np.argsort(smooth[ys,xs])[::-1]
    stars=[]
    for index in order[:min(len(order),max(10000,int(options['maxStars'])*4))]:
        x,y=int(xs[index]),int(ys[index])
        box=profile_box(smooth,x,y,float(options['sigma'])*5*noise)
        if box is None:continue
        star=fit_star(image,x,y,noise,background,radius=box,saturation=saturation)
        if star is None or star['roundness']<options['roundness']:continue
        sx,sy=star['fwhm']/FWHM_FACTOR,star['fwhmY']/FWHM_FACTOR
        smoothed_peak=star['amplitude']*sx*sy/math.sqrt((sx*sx+4)*(sy*sy+4))
        if smoothed_peak<float(options['sigma'])*5*noise:continue
        stars.append(star)
        if len(stars)>=options['maxStars']:break
    stars.sort(key=lambda star:star['amplitude']*star['fwhm']*star['fwhmY'],reverse=True)
    return stars,int(len(order))


def profile_box(smooth,x,y,minimum_amplitude):
    """Check four radial Gaussian profiles and size a 99.9% energy fit box.

    The inflection of a Gaussian occurs one sigma from its centre. Independent
    estimates from either side reject gradients, blends and single-pixel peaks.
    """
    import numpy as np
    limit=min(35,x,y,smooth.shape[1]-1-x,smooth.shape[0]-1-y)
    if limit<6:return None
    widths=[];amplitudes=[]
    for values in (smooth[y,x-limit:x+limit+1],smooth[y-limit:y+limit+1,x]):
        values=values.astype(float);first=np.gradient(values);second=np.gradient(first)
        centre=limit
        for direction in (-1,1):
            previous=centre;crossing=None
            for distance in range(1,limit-1):
                index=centre+direction*distance
                if second[index]>=0 and second[previous]<0:
                    fraction=-second[previous]/(second[index]-second[previous])
                    crossing=abs(previous+direction*fraction-centre)
                    slope=abs(first[previous]*(1-fraction)+first[index]*fraction)
                    break
                previous=index
            if crossing is None or crossing<1:return None
            widths.append(crossing);amplitudes.append(slope*crossing*math.sqrt(math.e))
    if min(amplitudes)<minimum_amplitude:return None
    if any(max(widths[i:i+2])/min(widths[i:i+2])>2 for i in (0,2)):return None
    return max(5,min(40,math.ceil(max(widths)*math.sqrt(-2*math.log(.001)))))
