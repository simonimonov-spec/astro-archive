"use strict";
(() => {
  const locale=()=>window.AstroArchiveI18n.locale, en=()=>locale().startsWith('en');
  const duration=value=>{const s=Math.round(value);return `${Math.floor(s/3600)} ч ${Math.floor(s%3600/60)} мин ${s%60} сек`;};
  const n=value=>new Intl.NumberFormat(locale(),{maximumFractionDigits:3}).format(value);
  const label=kind=>kind==='observation'?(en()?'Observation night':'Нощ на наблюдение'):(en()?'Modification day':'Ден на промяна');
  function rowProfile(entry,folder){return window.AstroArchiveCustomization.effective('archive',{...context(folder),kind:entry.details?.kind||entry.kind,name:entry.name,archive:entry.archiveId||folder?.archiveId||'',telescope:entry.relativePath.split(/[\\/]/).filter(Boolean)[0]||entry.info?.telescope||''});}
  function dateKind(entry,folder){const mode=rowProfile(entry,folder).dateMode;return mode&&mode!=='auto'?mode:entry.details?.dateKind||(entry.kind==='fits'?'observation':'modified');}
  function dateText(entry,folder){const d=entry.details;if(dateKind(entry,folder)!=='observation')return entry.modified?new Intl.DateTimeFormat(locale(),{dateStyle:'short'}).format(new Date(entry.modified)):'—';
    // FITS times retain their stated calendar date; do not shift through the browser timezone.
    const format=text=>text?new Intl.DateTimeFormat(locale(),{dateStyle:'short',timeZone:'UTC'}).format(new Date(text.slice(0,10)+'T00:00:00Z')):'—';
    const first=format(d?.firstObservation),last=format(d?.lastObservation);return first===last?first:first+' – '+last;
  }
  function columnTitle(column,entries,folder){if(['exposure','totalExposure'].includes(column.key)&&entries.length&&entries.every(e=>['frame','dark','flat','bias'].includes(e.details?.kind)))return en()?'Exposure [s]':'Експозиция [s]';if(column.key!=='date'&&column.key!=='modified')return column.title;const kinds=new Set(entries.map(e=>dateKind(e,folder)));return kinds.size===1?label([...kinds][0]):en()?'Date':'Дата';}
  function cell(entry,key,columns,folder){const d=entry.details,mode=rowProfile(entry,folder).exposureMode||'auto',aggregate=d&&['stack','folder','archive','day'].includes(d.kind)&&mode!=='single',both=columns.some(c=>c.key==='exposure')&&columns.some(c=>c.key==='totalExposure');
    if(key==='files'||key==='stacks')return d?.[key]==null?'—':n(d[key]);
    if(key==='observed'||key==='created'){const value=key==='created'?d?.created:entry.info?.observed;return value?new Intl.DateTimeFormat(locale(),{dateStyle:'short',timeStyle:'short'}).format(new Date(value)):'—';}
    if(key==='date'||key==='modified')return dateText(entry,folder);
    if(key==='frames')return aggregate&&d.frames!=null?(d.minimumFrames?'≥ ':'')+n(d.frames):aggregate&&d.building?'…':'—';
    if(key==='exposure'||key==='totalExposure'){
      const total=aggregate&&(!both||key==='totalExposure'),single=['frame','dark','flat','bias'].includes(d?.kind)&&mode!=='total'&&(!both||key==='exposure');
      const value=total?d.totalExposure:single?d.exposure:null;return value==null?total&&d.building?'…':'—':(total&&d.minimumExposure?'≥ ':'')+(total?duration(value):n(value)+' s');
    }
    return entry.info?.[key]??'—';
  }
  function context(folder){const entries=folder?.entries||[],kinds=new Set(entries.map(e=>e.details?.kind||e.kind));const path=folder?.relativePath||'',parts=path.split(/[\\/]/).filter(Boolean);
    return {kind:kinds.size===1?[...kinds][0]:'mixed',level:entries.some(e=>e.kind==='archive')?'home':!parts.length?'root':parts.length===1?'telescope':parts.length===2?'object':kinds.has('frame')?'day':'folder',archive:folder?.archiveId||'',telescope:parts[0]||'',name:parts.at(-1)||''};
  }
  let timer,generation=0;
  function stop(){clearTimeout(timer);generation++;}
  function watch(folder,api,valid,redraw){stop();const generationNow=generation;
    const rows=folder.entries.filter(e=>!e.isLink&&['folder','archive','fits','image'].includes(e.kind)).slice(0,100);if(!rows.length)return;
    const run=async()=>{if(generationNow!==generation||!valid())return;if(document.hidden||document.getElementById('page-archive').hidden){timer=setTimeout(run,3000);return;}
      try{const result=await api('archive.rows',{rows:rows.map(e=>({archiveId:e.archiveId||folder.archiveId,relativePath:e.relativePath}))});if(generationNow!==generation||!valid())return;
        let changed=false;for(const item of result){const entry=rows.find(e=>(e.archiveId||folder.archiveId)===item.archiveId&&e.relativePath===item.relativePath);if(!entry)continue;const before=JSON.stringify([entry.details,entry.info,entry.bytes,entry.modified]);Object.assign(entry,{details:item.details,info:item.info,bytes:item.bytes,modified:item.modified});changed ||= before!==JSON.stringify([entry.details,entry.info,entry.bytes,entry.modified]);}
        if(changed)redraw();if(rows.some(e=>e.details?.building))timer=setTimeout(run,2000);
      }catch(error){if(generationNow===generation&&valid())console.warn('Archive row metadata',error.message);}
    };run();
  }
  window.AstroArchiveArchiveRows={cell,columnTitle,context,label,dateKind,watch,stop};
})();
