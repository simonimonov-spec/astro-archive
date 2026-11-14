"""Compact ResNet-18: 2/2/2/2 BasicBlocks, native 3x3 stem, GroupNorm.

No torchvision, pretrained download, pickle model or executable checkpoint.
This module is imported lazily; heuristic operation does not require torch.
"""
from __future__ import annotations
import hashlib
import io
import re
import zipfile
import numpy as np
from . import visual_data

FORMAT='astro-ai-resnet-v1'
ARCHITECTURE='resnet18-gn-width16-native-v1'
FEATURE_SCHEMA='astro-ai-visual-measurements-v1'
OUTPUT_NAMES=('quality','risk.noise','risk.stars','risk.defects','risk.trails','risk.light','risk.artifacts','event.cloud','event.satellite','event.obstruction')
# Object presence is deliberately NOT a defect head.
MEASUREMENTS=('fwhm','hfr','fwhmY','roundness','eccentricity','stars','fittedStars','measuredStars','saturatedStars','noise','background','backgroundVariation','pixelScale')
FEATURE_NAMES=tuple('measurement.'+n for n in MEASUREMENTS)+tuple('context.'+n for n in ('exposure','gain','bayer','referenceCount'))+tuple(n+'.'+k for n in ('noise','stars','defects','trails','light','artifacts') for k in ('risk','confidence'))
MAX_WEIGHTS=16_000_000


def measurement_features(row,factors):
    meta=row.get('meta') or {};out={'measurement.'+n:row.get(n,meta.get(n)) for n in MEASUREMENTS}
    out.update({'context.'+n:meta.get(n) for n in ('exposure','gain','bayer')});out['context.referenceCount']=row.get('referenceFrames')
    for f in factors:
        if f['id']!='objects':
            out[f['id']+'.risk']=f['risk'];out[f['id']+'.confidence']=f['confidence']
    return out


def validate(model,*,evaluated=True):
    from .contracts import validate as validate_contract
    validate_contract(model,'ai-resnet.schema.json')
    def check(condition,message):
        if not condition:raise ValueError('Невалиден ResNet: '+message)
    check(isinstance(model,dict) and model.get('format')==FORMAT,'format')
    check(model.get('architecture')==ARCHITECTURE and model.get('featureSchema')==FEATURE_SCHEMA,'architecture/schema')
    for k in ('modelId','version'):
        check(isinstance(model.get(k),str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}',model[k]) is not None,k)
    check(len(model['version'])<=64,'version length')
    visual_data.configuration(model.get('preprocessing'))
    names=model.get('heads');check(isinstance(names,list) and 'quality' in names and len(set(names))==len(names) and all(n in OUTPUT_NAMES for n in names),'heads')
    source=model.get('input',{});check(source.get('names')==list(FEATURE_NAMES),'measurement schema')
    for name in ('mean','scale'):
        values=np.asarray(source.get(name),float);check(values.shape==(len(FEATURE_NAMES),) and np.isfinite(values).all(),name)
        if name=='scale':check(bool((values>0).all()),'scale')
    weights=model.get('weights',{});check(type(weights.get('size')) is int and 0<weights['size']<=MAX_WEIGHTS,'weights size')
    check(isinstance(weights.get('hash'),str) and re.fullmatch(r'sha256:[a-f0-9]{64}',weights['hash']) is not None,'weights hash')
    training=model.get('training',{});check(type(training.get('trainedSteps')) is int and training['trainedSteps']>0,'untrained model')
    check(isinstance(training.get('datasetId'),str) and isinstance(training.get('datasetVersion'),str),'dataset provenance')
    check(isinstance(training.get('datasetHash'),str) and re.fullmatch(r'sha256:[a-f0-9]{64}',training['datasetHash']) is not None,'dataset hash')
    if evaluated:
        for split in ('validation','test'):
            result=(model.get('metrics') or {}).get(split,{})
            check(result.get('status')=='evaluated' and result.get('exampleCount',0)>=10 and 'quality' in result.get('heads',{}),split+' was not evaluated')
            for head in names:
                metric=result.get('heads',{}).get(head,{})
                check(metric.get('count',0)>=10 and isinstance(metric.get('mae'),(int,float)) and np.isfinite(metric['mae']),split+' head metrics')
        check(training.get('splitPolicy')=='group-disjoint-object-telescope-night-filter-hash-v1','group policy')
        groups=training.get('groups',{})
        check(all(isinstance(groups.get(k),list) and len(groups[k])>0 for k in ('train','validation','test')),'groups')
        check(not(set(groups['train'])&set(groups['validation']) or set(groups['train'])&set(groups['test']) or set(groups['validation'])&set(groups['test'])),'group leakage')
        check(len(groups['train'])>=2,'two training groups required')
        hashes=training.get('splitFrameHashes',{})
        for split,minimum in (('train',40),('validation',10),('test',10)):
            values=hashes.get(split)
            check(isinstance(values,list) and len(values)>=minimum and len(set(values))==len(values) and all(isinstance(v,str) and re.fullmatch(r'sha256:[a-f0-9]{64}',v) for v in values),'split frame hashes')
        check(not(set(hashes['train'])&set(hashes['validation']) or set(hashes['train'])&set(hashes['test']) or set(hashes['validation'])&set(hashes['test'])),'frame leakage')
    return model


def build(heads,features=len(FEATURE_NAMES)):
    import torch
    from torch import nn
    class Block(nn.Module):
        def __init__(self,a,b,stride=1):
            super().__init__();self.conv1=nn.Conv2d(a,b,3,stride,1,bias=False);self.norm1=nn.GroupNorm(4,b)
            self.conv2=nn.Conv2d(b,b,3,1,1,bias=False);self.norm2=nn.GroupNorm(4,b)
            self.skip=nn.Identity() if a==b and stride==1 else nn.Sequential(nn.Conv2d(a,b,1,stride,bias=False),nn.GroupNorm(4,b))
        def forward(self,x):return torch.relu(self.norm2(self.conv2(torch.relu(self.norm1(self.conv1(x)))))+self.skip(x))
    class ResNet(nn.Module):
        def __init__(self):
            super().__init__();self.stem=nn.Sequential(nn.Conv2d(3,16,3,1,1,bias=False),nn.GroupNorm(4,16),nn.ReLU())
            blocks=[];a=16
            for i,b in enumerate((16,32,64,128)):
                blocks.extend((Block(a,b,1 if i==0 else 2),Block(b,b)));a=b
            self.blocks=nn.Sequential(*blocks)
            self.fusion=nn.Sequential(nn.Linear(256+features*2,64),nn.ReLU(),nn.Linear(64,len(heads)))
        def encode(self,x):return self.blocks(self.stem(x)).mean(dim=(-1,-2))
        def combine(self,embedding,measurements):
            pooled=torch.cat((embedding.mean(0),embedding.max(0).values,measurements),dim=0)
            return self.fusion(pooled)
        def forward(self,x,measurements):return self.combine(self.encode(x),measurements)
    return ResNet()


def numeric_features(model,features):
    mean=np.asarray(model['input']['mean']);scale=np.asarray(model['input']['scale'])
    raw=np.asarray([features.get(n) if isinstance(features.get(n),(int,float)) else np.nan for n in FEATURE_NAMES],float)
    valid=np.isfinite(raw)
    return np.concatenate((np.clip((np.where(valid,raw,mean)-mean)/scale,-15,15),valid.astype(float))).astype(np.float32)


def export_weights(network):
    stream=io.BytesIO();np.savez_compressed(stream,**{k:v.detach().cpu().numpy() for k,v in network.state_dict().items()})
    return stream.getvalue()


def load_weights(model,data,device='cpu'):
    import torch
    validate(model)
    if len(data)!=model['weights']['size'] or 'sha256:'+hashlib.sha256(data).hexdigest()!=model['weights']['hash']:raise ValueError('ResNet weights hash mismatch.')
    net=build(model['heads']);expected=net.state_dict()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist())>MAX_WEIGHTS:raise ValueError('Weights decompression limit.')
        if set(archive.namelist())!={k+'.npy' for k in expected} or len(archive.namelist())!=len(expected):raise ValueError('ResNet tensor keys mismatch.')
        state={}
        for key,tensor in expected.items():
            with archive.open(key+'.npy') as stream:
                if np.lib.format.read_magic(stream)!=(1,0):raise ValueError('Invalid tensor header version.')
                shape,fortran,dtype=np.lib.format.read_array_header_1_0(stream,max_header_size=4096)
                if shape!=tuple(tensor.shape) or dtype!=np.dtype('float32') or fortran:raise ValueError('Invalid tensor shape/dtype.')
                length=tensor.numel()*4;payload=stream.read(length+1)
                if len(payload)!=length:raise ValueError('Invalid tensor data size.')
                array=np.frombuffer(payload,dtype=np.float32).reshape(shape)
            if not np.isfinite(array).all():raise ValueError('Invalid ResNet tensor.')
            state[key]=torch.from_numpy(array.copy())
    net.load_state_dict(state,strict=True);return net.to(device).eval()


def predict(network,model,description,read_tile,features,*,cancel=None):
    import torch
    device=next(network.parameters()).device
    encoded=[];records=[]
    with torch.inference_mode():
        for start in range(0,len(description['tiles']),4):
            if cancel is not None and cancel.is_set():raise InterruptedError('ResNet оценката е отменена.')
            batch=np.stack([read_tile(i) for i in range(start,min(start+4,len(description['tiles'])))])
            embeddings=network.encode(torch.from_numpy(batch).to(device));encoded.append(embeddings)
        embedding=torch.cat(encoded)
        numeric=torch.from_numpy(numeric_features(model,features)).to(device)
        values=torch.sigmoid(network.combine(embedding,numeric)).cpu().tolist()
        # Occlusion of one measurement, with images fixed: no attribution claim for pixels.
        baseline=values[model['heads'].index('quality')];contributions={}
        for i,name in enumerate(FEATURE_NAMES):
            removed=numeric.clone();removed[i]=0;removed[i+len(FEATURE_NAMES)]=0
            contributions[name]=baseline-float(torch.sigmoid(network.combine(embedding,removed))[model['heads'].index('quality')])
        # Region scores are weakly supervised candidates, not segmentation masks.
        if len(description['tiles'])>1:
            local=torch.stack([torch.sigmoid(network.combine(e[None],numeric)) for e in embedding[1:]]).cpu().numpy()
            for j,name in enumerate(model['heads']):
                if not name.startswith(('risk.','event.')):continue
                for i in np.argsort(local[:,j])[-2:]:
                    if local[i,j]>=.7:
                        records.append(dict(head=name,bbox=description['tiles'][int(i)+1]['bbox'],score=float(local[i,j]),method='weakly_supervised_tile_candidate'))
    return dict(values=dict(zip(model['heads'],values)),contributions=contributions,regions=records,coverage=1.,tileCount=len(description['tiles']))
