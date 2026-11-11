"""One provenance document for manual returns and automation plugins."""
def context(records,as_stack=False,used_ids=None):
    used=set(used_ids) if used_ids is not None else None
    records=[r for r in records if used is None or r['id'] in used]
    if not records:raise ValueError('Липсват изходни файлове за резултата.')
    metas=[r['meta'] for r in records];nights=sorted({n for m in metas for n in (m.get('sourceNights') or [m.get('night','')]) if n})
    stacks=[r for r in records if r['meta'].get('kind')=='stack']
    result=dict(object=metas[0]['object'],telescope=metas[0]['telescope'],night=nights[0] if len(nights)==1 else '_'.join([nights[0],nights[-1]]) if nights else '',
                sourceNights=nights,sourceFiles=[r['id'] for r in records],sourceStacks=[r['id'] for r in stacks],
                combination='stacks' if len(stacks)>1 else 'frames',kind='stack' if as_stack else None,
                sourceStackCount=len(stacks),telescopes=sorted({t for m in metas for t in (m.get('telescopes') or [m.get('telescope')]) if t}))
    # Combining already accepted stacks does not silently re-infer exposure from EXPTIME.
    if as_stack and len(stacks)==len(records):
        result['provenanceOverride']=dict(frames=sum(m.get('frames',1) for m in metas),totalExposure=sum(m['totalExposure'] for m in metas) if all(m.get('totalExposure') is not None for m in metas) else None)
    return result
