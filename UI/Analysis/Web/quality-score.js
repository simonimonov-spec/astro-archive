"use strict";
// Relative within each comparable observing group. An index, never a probability.
window.AstroArchiveClassicQuality=(()=>{
 const defaults={threshold:20,weights:{fwhm:1,fwhmY:1,roundness:1,stars:1,background:1}};
 const properties=['fwhm','fwhmY','roundness','stars','background'];
 function calculate(rows,config=defaults){
  const groups=new Map(),scores=new Map();
  for(const row of rows){const m=row.meta||{},key=JSON.stringify([m.telescope||'',m.object||'',m.night||'',m.filter||'',m.exposure??null]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);scores.set(row.id,null);}
  for(const group of groups.values()){
   const accum=new Map(group.map(r=>[r.id,{sum:0,weight:0,count:0}]));
   for(const key of properties){
    const weight=Number(config.weights?.[key])||0;if(weight<=0)continue;
    const values=group.filter(r=>Number.isFinite(r[key])).map(r=>({id:r.id,value:key==='roundness'?Math.abs(1-r[key]):r[key]})).sort((a,b)=>a.value-b.value);
    if(values.length<3)continue;
    for(let start=0;start<values.length;){let end=start+1;while(end<values.length&&values[end].value===values[start].value)end++;
     const rank=(start+end-1)/2/(values.length-1),value=100*(key==='stars'?rank:1-rank);
     for(let i=start;i<end;i++){const a=accum.get(values[i].id);a.sum+=value*weight;a.weight+=weight;a.count++;}start=end;}
   }
   for(const row of group){const a=accum.get(row.id);if(a.count>=2&&a.weight>0)scores.set(row.id,Math.round(a.sum/a.weight*10)/10);}
  }
  return scores;
 }
 return{defaults,calculate};
})();
