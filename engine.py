"""Noise-normalized, sky-relative features; no archive or application imports."""
from __future__ import annotations
import math
import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree

FEATURES=('transparency_loss','flux_scatter','star_loss','positive_area','negative_area','diffuse_peak','dark_area','dark_edge','edge_contact','line_length','line_snr','line_fill','reference_count','overlap','noise_ratio')
VERSION='sky-relative-3-local'

def robust_noise(image):
    values=np.asarray(image,np.float32)
    if min(values.shape)<8:return 1.
    delta=2*values[1:-1,1:-1]-values[1:-1,:-2]-values[1:-1,2:]
    delta=delta.ravel()[::max(1,delta.size//100000)];delta=delta[np.isfinite(delta)]
    if not len(delta):return 1.
    noise=float(np.median(np.abs(delta-np.median(delta)))*1.4826022185/math.sqrt(6))
    return max(noise,float(np.nanmax(np.abs(values)))*1e-7,1e-7)

def project(matrix,points):
    points=np.asarray(points,float)
    if not len(points):return np.empty((0,2))
    mapped=np.column_stack((points,np.ones(len(points))))@np.asarray(matrix).T
    return mapped[:,:2]/mapped[:,2,None]

def photometry(row,reference):
    stars=[s for s in row.get('starList',[]) if not s.get('saturated') and s.get('peakSnr',0)>10]
    targets=[s for s in reference.get('starList',[]) if not s.get('saturated') and s.get('peakSnr',0)>20]
    if row.get('transform') is None or len(stars)<4 or len(targets)<8:return dict(ratio=None,scatter=0.,matched=0,loss=0.)
    mapped=project(row['transform'],[(s['x'],s['y']) for s in stars]);tree=cKDTree([(s['x'],s['y']) for s in targets]);distance,index=tree.query(mapped)
    exposure=float(row['meta'].get('exposure') or 0);ref_exp=float(reference['meta'].get('exposure') or 0)
    if min(exposure,ref_exp)<=0:return dict(ratio=None,scatter=0.,matched=0,loss=0.)
    pairs=[];used=set()
    for i in np.argsort(distance):
        j=int(index[i])
        if distance[i]>3 or j in used:continue
        a,b=stars[i],targets[j];f=a.get('amplitude',a.get('weight',0))*a['fwhm']*a['fwhmY']/exposure;g=b.get('amplitude',b.get('weight',0))*b['fwhm']*b['fwhmY']/ref_exp
        if f>0 and g>0:pairs.append(f/g);used.add(j)
    if len(pairs)<4:return dict(ratio=None,scatter=0.,matched=len(pairs),loss=0.)
    values=np.asarray(pairs);ratio=float(np.median(values));scatter=float(1.4826*np.median(abs(values-ratio))/max(ratio,1e-12))
    return dict(ratio=ratio,scatter=scatter,matched=len(values),loss=float(max(0,1-len(values)/max(1,min(len(targets),64)))))

def star_mask(shape,stars,matrix=None):
    mask=np.zeros(shape,bool)
    if matrix is None:matrix=np.eye(3)
    positions=project(matrix,[(s['x'],s['y']) for s in stars]);scale=math.sqrt(abs(np.linalg.det(np.asarray(matrix)[:2,:2])))
    for star,(x,y) in zip(stars,positions):
        radius=max(4,min(24,star.get('fwhm',3)*scale*2.5));x0=max(0,int(x-radius));x1=min(shape[1],int(x+radius)+2);y0=max(0,int(y-radius));y1=min(shape[0],int(y+radius)+2)
        if x1<=x0 or y1<=y0:continue
        yy,xx=np.mgrid[y0:y1,x0:x1];mask[y0:y1,x0:x1]|=(xx-x)**2+(yy-y)**2<=radius**2
    return mask

def remove_plane(image,reference,valid,stars):
    diff=image-reference;mask=valid&~stars
    yy,xx=np.indices(image.shape,dtype=np.float32);x=(xx/(image.shape[1]-1)-.5);y=(yy/(image.shape[0]-1)-.5)
    positions=np.flatnonzero(mask);positions=positions[::max(1,len(positions)//18000)]
    if len(positions)<100:return diff,float(np.nanmedian(diff[valid])) if valid.any() else 0
    design=np.column_stack([np.ones(len(positions)),x.ravel()[positions],y.ravel()[positions],(x*y).ravel()[positions],(x*x).ravel()[positions],(y*y).ravel()[positions]])
    z=diff.ravel()[positions];keep=np.isfinite(z)
    for _ in range(4):
        if keep.sum()<100:break
        coefficients=np.linalg.lstsq(design[keep],z[keep],rcond=None)[0];residual=z-design@coefficients;center=np.median(residual[keep]);sigma=max(1e-8,1.4826*np.median(abs(residual[keep]-center)))
        keep=np.isfinite(z)&(abs(residual-center)<3*sigma)
    background=coefficients[0]+coefficients[1]*x+coefficients[2]*y+coefficients[3]*x*y+coefficients[4]*x*x+coefficients[5]*y*y
    return diff-background,float(coefficients[0])

def component(mask,minimum):
    labels,count=ndi.label(mask);sizes=np.bincount(labels.ravel());sizes[0]=0
    if not count or sizes.max()<minimum:return np.zeros_like(mask),None
    ident=int(np.argmax(sizes));selected=labels==ident;yy,xx=np.nonzero(selected)
    return selected,dict(x=int(xx.min()),y=int(yy.min()),width=int(np.ptp(xx)+1),height=int(np.ptp(yy)+1),pixels=int(len(xx)))

def find_lines(z,valid,stars):
    """Bounded Hough search; disjoint collinear segments may form one trail."""
    high=ndi.gaussian_filter(np.where(valid,z,0),.7)-ndi.gaussian_filter(np.where(valid,z,0),4)
    # The filtered white-noise standard deviation is about 0.37 of native z.
    threshold=max(2.8,5*robust_noise(high));points=np.column_stack(np.nonzero((high>threshold)&valid&~stars))
    if len(points)<20:return [],dict(length=0.,snr=0.,fill=0.),False
    if len(points)>12000:
        strength=high[points[:,0],points[:,1]];points=points[np.argpartition(strength,-12000)[-12000:]]
    height,width=z.shape;diagonal=math.hypot(height,width);rho_max=int(math.ceil(diagonal));angles=np.linspace(0,math.pi,180,endpoint=False);accumulator=np.zeros((180,2*rho_max+1),np.int32)
    for i,theta in enumerate(angles):
        rho=np.rint(points[:,1]*math.cos(theta)+points[:,0]*math.sin(theta)).astype(int)+rho_max
        accumulator[i]=np.bincount(rho,minlength=2*rho_max+1)
    # Repeated, evenly spaced parallel ridges are ambiguous sensor structure.
    # A single trail or two crossing trails do not satisfy this veto. Applied
    # in Hough space so a rotated camera pattern is handled as well.
    from scipy.signal import find_peaks
    structured=False
    for i in np.argsort(accumulator.max(axis=1))[-6:]:
        profile=accumulator[i].astype(float)
        peaks,_=find_peaks(profile,height=max(24,diagonal*.12,profile.max()*.45),prominence=max(10,profile.max()*.25),distance=4)
        if len(peaks)>=4:
            gaps=np.diff(peaks)
            if np.std(gaps)/np.mean(gaps)<.27 and np.ptp(peaks)>min(height,width)*.45:structured=True;break
    candidates=[]
    for _ in range(6):
        i,j=np.unravel_index(np.argmax(accumulator),accumulator.shape);votes=int(accumulator[i,j])
        if votes<max(16,diagonal*.035):break
        theta=angles[i];rho=j-rho_max;normal=np.array([math.cos(theta),math.sin(theta)]);xy=points[:,::-1].astype(float)
        selected=xy[abs(xy@normal-rho)<2.2]
        accumulator[max(0,i-3):min(180,i+4),max(0,j-6):min(2*rho_max+1,j+7)]=0
        if len(selected)<20:continue
        center=selected.mean(axis=0);_,_,vectors=np.linalg.svd(selected-center,full_matrices=False);along=(selected-center)@vectors[0];cross=(selected-center)@vectors[1]
        lo,hi=np.percentile(along,[2,98]);length=hi-lo
        if length<max(30,diagonal*.14):continue
        bins=np.unique(np.rint(along-lo).astype(int));fill=len(bins)/max(1,length)
        thickness=float(np.percentile(abs(cross),80));snr=float(np.median(high[selected[:,1].astype(int),selected[:,0].astype(int)]))
        if fill<.32 or thickness>3.2 or length/max(1,thickness*2)<10:continue
        a=center+vectors[0]*lo;b=center+vectors[0]*hi
        if any(min(np.linalg.norm(a-np.array(c['a'])),np.linalg.norm(a-np.array(c['b'])))<10 for c in candidates):continue
        candidates.append(dict(a=a.tolist(),b=b.tolist(),length=float(length),snr=snr,fill=float(min(1,fill)),width=max(1,thickness*2)))
        if len(candidates)>=3:break
    if not candidates:return [],dict(length=0.,snr=0.,fill=0.),structured
    best=max(candidates,key=lambda c:c['length']*c['fill']);return candidates,dict(length=best['length']/diagonal,snr=best['snr']/10,fill=best['fill']),structured

def extract(image,reference=None,*,valid=None,reference_noise=None,stars=None,transparency=1.,flux_scatter=0.,star_loss=0.,reference_count=0):
    image=np.asarray(image,np.float32);valid=np.isfinite(image) if valid is None else np.asarray(valid,bool)&np.isfinite(image)
    stars=np.zeros(image.shape,bool) if stars is None else stars
    reliable=reference is not None and reference_count>=3
    if reliable:
        reference=np.asarray(reference,np.float32);valid&=np.isfinite(reference);valid&=ndi.distance_transform_edt(valid)>5
        scaled=image/max(.08,float(transparency));residual,offset=remove_plane(scaled,reference,valid,stars)
    else:
        clean=np.where(valid,image,np.nanmedian(image[valid]) if valid.any() else 0);reference=ndi.gaussian_filter(clean,10);residual=clean-reference;offset=0.
    if valid.sum()<300:raise ValueError('Недостатъчно общо валидно покритие за инспекция.')
    noise=robust_noise(np.where(valid,residual,np.nan));noise_image=robust_noise(np.where(valid,image,np.nan))
    if reference_noise is None:reference_noise=np.zeros_like(image)
    denominator=np.maximum(noise,np.sqrt(noise_image**2+np.asarray(reference_noise)**2));z=np.where(valid,residual/denominator,0)
    usable=valid&~stars;count=max(1,int(usable.sum()));smooth=ndi.gaussian_filter(np.where(usable,z,0),3);weights=ndi.gaussian_filter(usable.astype(np.float32),3)
    smooth=np.divide(smooth,weights,out=np.zeros_like(smooth),where=weights>.35)*2*math.sqrt(math.pi)*3*np.sqrt(weights)
    # Masked star cores are interpolated from neighbouring sky. Treating the
    # holes as separate disconnected regions would hide trees in dense fields.
    clean=valid&(weights>.35)
    positive,_=component((smooth>5)&clean,max(20,int(count*.008)))
    negative,negative_box=component((smooth<-6)&clean,max(20,int(count*.008)))
    dark,dark_box=component((smooth<-10)&clean,max(25,int(count*.012)))
    gradient=ndi.gaussian_gradient_magnitude(z,1)
    # A high-significance blurred mask extends beyond a hard physical edge.
    # Measure that edge inside its region, not only on the threshold contour.
    boundary=ndi.binary_dilation(dark,iterations=4)&valid&~stars
    sharp=float(np.percentile(gradient[boundary],98)) if boundary.any() else 0.
    rim=valid&~ndi.binary_erosion(valid,iterations=8);contact=float((ndi.binary_dilation(dark,iterations=5)&rim).sum()/max(1,rim.sum()))
    lines,line,structured=find_lines(z,valid,stars)
    # Whole-row/column medians retain hot columns even when the bounded Hough
    # point sample favours only one of many equally bright parallel ridges.
    from scipy.signal import find_peaks
    for axis in (0,1):
        samples=np.where(usable,z,np.nan)
        if axis==0:samples[:,~usable.any(axis=0)]=0
        else:samples[~usable.any(axis=1),:]=0
        profile=np.nanmedian(samples,axis=axis)
        profile=np.nan_to_num(profile)
        peaks,_=find_peaks(profile,height=max(3,float(profile.max())*.4),prominence=2,distance=4)
        if len(peaks)>=4:
            gaps=np.diff(peaks)
            if np.std(gaps)/np.mean(gaps)<.22 and np.ptp(peaks)>len(profile)*.45:structured=True
    features=dict(transparency_loss=max(0.,1-float(transparency)),flux_scatter=float(flux_scatter),star_loss=float(star_loss),positive_area=float(positive.sum()/count),negative_area=float(negative.sum()/count),diffuse_peak=float(np.percentile(abs(smooth[clean]),99.5)/50) if clean.any() else 0.,dark_area=float(dark.sum()/count),dark_edge=sharp/10,edge_contact=contact,line_length=line['length'],line_snr=line['snr'],line_fill=line['fill'],reference_count=min(1,reference_count/5),overlap=float(valid.mean()),noise_ratio=noise_image/max(noise,1e-8))
    features={k:float(np.clip(features[k],0,10)) for k in FEATURES}
    reasons=[]
    if not reliable:reasons.append(f'Недостатъчни референции: {reference_count}; нужни са поне 3')
    if valid.mean()<=.35:reasons.append(f'Общо покритие {100*valid.mean():.1f}%; нужно е над 35% от този кадър')
    if structured:reasons.append('Повтарящ се сензорен рисунък / успоредни ивици')
    return dict(features=features,regions=dict(lines=lines,dark=dark_box,negative=negative_box),reliable=bool(reliable and valid.mean()>.35 and not structured),uncertainty=' · '.join(reasons),noise=noise_image,backgroundOffset=offset,z=z,valid=valid)
