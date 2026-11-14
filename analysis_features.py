"""Reuses computed chart metrics; never reads pixels or imports application internals."""
import math
import numpy as np

RATIOS={'analysis_fwhm_y_ratio':'fwhmY','analysis_weighted_fwhm_ratio':'weightedFwhm','analysis_hfr_ratio':'hfr','analysis_background_ratio':'background','analysis_noise_ratio':'noise','analysis_snr_ratio':'peakSnr','analysis_gradient_ratio':'backgroundVariation'}
def number(value):return type(value) in (int,float) and math.isfinite(value)
def extend(row,group):
    out={}
    def put(key,value):
        good=number(value);out[key]=float(np.clip(value,-10,10)) if good else 0.;out[key+'_missing']=0. if good else 1.
    for key,field in RATIOS.items():
        baseline=[r.get(field) for r in group if number(r.get(field))]
        norm=abs(float(np.median(baseline))) if baseline else 0.
        value=row.get(field);put(key,value/norm if number(value) and norm>1e-12 else None)
    for key,field in [('analysis_roundness','roundness'),('analysis_eccentricity','eccentricity'),('analysis_dark_fraction','darkFraction')]:put(key,row.get(field))
    for key,field,size in [('analysis_shift_x','shiftX','width'),('analysis_shift_y','shiftY','height')]:
        value=row.get(field);extent=row.get(size);put(key,value/extent if number(value) and number(extent) and extent>0 else None)
    for key,num,den in [('analysis_saturated_fraction','saturatedStars','fittedStars'),('analysis_measured_fraction','measuredStars','stars')]:
        a,b=row.get(num),row.get(den);put(key,a/b if number(a) and number(b) and b>0 else None)
    valid,invalid=row.get('validPixels'),row.get('invalidPixels')
    put('analysis_invalid_fraction',invalid/(valid+invalid) if number(valid) and number(invalid) and valid+invalid>0 else None)
    return out
