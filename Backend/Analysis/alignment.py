"""Catalogue matching and projective geometry, independent of external tools."""
from __future__ import annotations
import itertools,math
import numpy as np
from scipy.spatial import cKDTree


def project(matrix,points):
    points=np.asarray(points,float);homogeneous=np.column_stack((points,np.ones(len(points))))@np.asarray(matrix,float).T
    with np.errstate(divide='ignore',invalid='ignore'):
        return homogeneous[:,:2]/homogeneous[:,2,None]


def similarity(source,target):
    a=source-source.mean(axis=0);b=target-target.mean(axis=0);u,_,vt=np.linalg.svd(a.T@b);rotation=vt.T@u.T
    if np.linalg.det(rotation)<0:vt[-1]*=-1;rotation=vt.T@u.T
    scale=np.sum(b*(a@rotation.T))/max(np.sum(a*a),1e-15)
    matrix=np.eye(3);matrix[:2,:2]=rotation*scale;matrix[:2,2]=target.mean(axis=0)-matrix[:2,:2]@source.mean(axis=0)
    return matrix


def homography(source,target):
    def normalized(points):
        centre=points.mean(axis=0);scale=math.sqrt(2)/max(float(np.mean(np.linalg.norm(points-centre,axis=1))),1e-12)
        matrix=np.array([[scale,0,-scale*centre[0]],[0,scale,-scale*centre[1]],[0,0,1.]])
        return project(matrix,points),matrix
    source=np.asarray(source,float);target=np.asarray(target,float)
    a,ta=normalized(source);b,tb=normalized(target)
    x,y=a.T;u,v=b.T;z=np.zeros(len(a));ones=np.ones(len(a))
    equations=np.empty((2*len(a),9))
    equations[::2]=np.column_stack((-x,-y,-ones,z,z,z,u*x,u*y,u))
    equations[1::2]=np.column_stack((z,z,z,-x,-y,-ones,v*x,v*y,v))
    _,_,vt=np.linalg.svd(equations,full_matrices=len(a)==4)
    matrix=np.linalg.inv(tb)@vt[-1].reshape(3,3)@ta
    if abs(matrix[2,2])<1e-12:raise ValueError('Изродено подравняване.')
    return matrix/matrix[2,2]


def triangle_index(points):
    values=[];indices=[]
    for triplet in itertools.combinations(range(min(40,len(points))),3):
        p=points[list(triplet)];sides=np.array([np.linalg.norm(p[1]-p[2]),np.linalg.norm(p[0]-p[2]),np.linalg.norm(p[0]-p[1])]);order=np.argsort(sides)
        area=abs(np.linalg.det(np.column_stack((p[1]-p[0],p[2]-p[0]))))
        if sides[order[0]]<7 or area<20:continue
        values.append(sides[order[:2]]/sides[order[2]]);indices.append(np.array(triplet)[order])
    return np.asarray(values),indices


def unique_pairs(points,reference,tree,matrix,limit):
    mapped=project(matrix,points)
    if not np.isfinite(mapped).all():return np.array([],int),np.array([],int)
    distance,nearest=tree.query(mapped);candidates=np.flatnonzero(distance<limit)
    candidates=candidates[np.argsort(distance[candidates])];used=set();source=[];target=[]
    for i in candidates:
        j=int(nearest[i])
        if j not in used:source.append(int(i));target.append(j);used.add(j)
    return np.asarray(source,int),np.asarray(target,int)


def register(source,target,min_pairs=10):
    points=np.asarray([(s['x'],s['y']) for s in source['starList'][:256]],float)
    reference=np.asarray([(s['x'],s['y']) for s in target['starList'][:256]],float)
    if min(len(points),len(reference))<min_pairs:return None
    triangles,ids=triangle_index(points);ref_triangles,ref_ids=triangle_index(reference)
    if not len(triangles) or not len(ref_triangles):return None
    ratios=cKDTree(ref_triangles);tree=cKDTree(reference);best=None
    # Bright stars seed the match; all retained fitted stars take part in scoring.
    for index in range(0,len(triangles),max(1,len(triangles)//1200)):
        distances,neighbors=ratios.query(triangles[index],k=min(2,len(ref_triangles)))
        for distance,neighbor in zip(np.atleast_1d(distances),np.atleast_1d(neighbors)):
            if distance>.006:continue
            matrix=similarity(points[ids[index]],reference[ref_ids[int(neighbor)]])
            if not .8<np.linalg.det(matrix[:2,:2])<1.25:continue
            a,b=unique_pairs(points,reference,tree,matrix,4)
            if len(a)>=min_pairs:
                error=float(np.mean(np.linalg.norm(project(matrix,points[a])-reference[b],axis=1)))
                if best is None or (len(a),-error)>(len(best[1]),-best[3]):best=(matrix,a,b,error)
        if best is not None and len(best[1])>=min(len(points),len(reference))*.85:break
    if best is None:return None
    matrix,a,b,_=best;rng=np.random.default_rng(7183)
    best_inliers=np.array([],int);best_error=math.inf
    candidates=[np.arange(len(a))]+[rng.choice(len(a),4,replace=False) for _ in range(80)]
    for sample in candidates:
        try:
            candidate=homography(points[a[sample]],reference[b[sample]])
            residual=np.linalg.norm(project(candidate,points[a])-reference[b],axis=1)
        except (ValueError,np.linalg.LinAlgError):continue
        inliers=np.flatnonzero(residual<2.5)
        if len(inliers)<min_pairs:continue
        error=float(np.mean(residual[inliers]))
        if len(inliers)>len(best_inliers) or (len(inliers)==len(best_inliers) and error<best_error):
            matrix,best_inliers,best_error=candidate,inliers,error
    for _ in range(3):
        a,b=unique_pairs(points,reference,tree,matrix,2.5)
        if len(a)<min_pairs:return None
        matrix=homography(points[a],reference[b])
    residual=np.linalg.norm(project(matrix,points[a])-reference[b],axis=1)
    rms=float(np.sqrt(np.mean(residual**2)))
    if not np.isfinite(matrix).all() or rms>2 or not .8<np.linalg.det(matrix[:2,:2])<1.25:return None
    width=source.get('width',float(points[:,0].max()+1));height=source.get('height',float(points[:,1].max()+1))
    corners=np.array([[0,0],[width,0],[width,height],[0,height]])
    denominator=np.column_stack((corners,np.ones(4)))@matrix[2]
    if np.any(abs(denominator)<1e-6) or not (np.all(denominator>0) or np.all(denominator<0)):return None
    mapped=project(matrix,corners)
    if np.ptp(mapped[:,0])>width*4 or np.ptp(mapped[:,1])>height*4:return None
    return matrix,rms,len(a)


def displayed_transform(matrix,width,height):
    # Stored geometry is top-down array coordinates. Registration plots use the
    # FITS bottom-up origin and displacements opposite to the applied transform.
    flip=np.array([[1,0,0],[0,-1,height-1],[0,0,1.]])
    fit=flip@matrix@flip
    scale_x=np.hypot(fit[0,0],fit[0,1]);scale_y=np.hypot(fit[1,0],fit[1,1])
    return dict(shiftX=float(-fit[0,2]),shiftY=float(-fit[1,2]),rotation=float(np.degrees(np.arctan2(fit[0,1],fit[0,0]))),scale=float((scale_x+scale_y)/2))


def warp(image,matrix,output_shape,*,origin=(0,0),order=1,cval=0):
    """Bounded-memory inverse projective resampling; also handles affine data."""
    from scipy.ndimage import map_coordinates
    inverse=np.linalg.inv(np.asarray(matrix,float));height,width=output_shape
    result=np.empty(output_shape,dtype=np.float32)
    for y0 in range(0,height,64):
        yy,xx=np.mgrid[y0:min(height,y0+64),:width];xx=xx+origin[0];yy=yy+origin[1]
        denominator=inverse[2,0]*xx+inverse[2,1]*yy+inverse[2,2]
        with np.errstate(divide='ignore',invalid='ignore'):
            sx=(inverse[0,0]*xx+inverse[0,1]*yy+inverse[0,2])/denominator
            sy=(inverse[1,0]*xx+inverse[1,1]*yy+inverse[1,2])/denominator
        invalid=~np.isfinite(sx)|~np.isfinite(sy)|(abs(denominator)<1e-10)
        sx[invalid]=-1e9;sy[invalid]=-1e9
        result[y0:y0+len(yy)]=map_coordinates(image,[sy,sx],order=order,mode='constant',cval=cval,prefilter=False)
    return result
