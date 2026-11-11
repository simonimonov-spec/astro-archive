"use strict";
// Coverage remains numeric until an image comparison is explicitly requested.
(() => {
 const $=id=>document.getElementById(id),images=new Map();let serial=0;
 function draw(product){
  if(!product)return;const mode=$('q-coverage-view').value,canvas=$('q-map'),grid=$('q-map-mode').value==='seconds'&&product.seconds?product.seconds:product.counts;
  if(!grid?.length)return;const h=grid.length,w=grid[0].length,token=++serial;canvas.width=mode==='compare'?w*2:w;canvas.height=h;
  const c=canvas.getContext('2d'),map=document.createElement('canvas');map.width=w;map.height=h;const mc=map.getContext('2d'),pixels=mc.createImageData(w,h),values=grid.flat().filter(v=>v!=null&&v>0),max=values.reduce((a,b)=>Math.max(a,b),1e-12),min=$('q-map-mode').value==='seconds'||product.continuous?values.reduce((a,b)=>Math.min(a,b),Infinity):1;
  const palette=[[40,100,255],[20,190,100],[245,225,30],[255,140,20],[230,35,55]];
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
   if(!product.counts[y][x])continue;const t=Math.max(0,Math.min(1,(grid[y][x]-min)/(max-min||1))),i=(y*w+x)*4;
   let rgb=[t*255,t*255,t*255];if($('q-palette').value==='spectrum'){const p=Math.min(3.999,t*4),j=Math.floor(p),f=p-j;rgb=palette[j].map((v,k)=>v*(1-f)+palette[j+1][k]*f);}
   for(let k=0;k<3;k++)pixels.data[i+k]=rgb[k];pixels.data[i+3]=255;
  }
  mc.putImageData(pixels,0,0);c.clearRect(0,0,canvas.width,h);
  $('q-map-area').dataset.mode=mode;
  $('q-cover-alpha').closest('label').querySelector('span').textContent='Прозрачност на покритието · '+$('q-cover-alpha').value+'%';
  $('q-cover-low').closest('label').querySelector('span').textContent='Слаби зони под '+$('q-cover-low').value+'% от максималната експозиция';
  $('q-low-warning').hidden=mode!=='low'||product.exposureKnown!==false;
  $('q-low-warning').textContent='Липсва експозиция за '+(product.unknownExposures||0)+' кадъра. Не се показва заключение за слаба експозиция; допълни данните и преизчисли анализа.';
  if(mode==='counts'){c.drawImage(map,0,0);return;}
  if(!product.averageAsset){c.drawImage(map,0,0);return;}
  const src='analysis-image/'+encodeURIComponent(product.averageAsset);
  let image=images.get(src);if(!image){image=new Image();image.src=src;images.set(src,image);while(images.size>6)images.delete(images.keys().next().value);}
  const paint=()=>{if(token!==serial)return;c.clearRect(0,0,canvas.width,h);c.globalAlpha=1;c.drawImage(image,0,0,w,h);
   if(mode==='compare'){c.drawImage(map,w,0);return;}
   if(mode==='overlay'){c.globalAlpha=Number($('q-cover-alpha').value)/100;c.drawImage(map,0,0);c.globalAlpha=1;return;}
   if(product.exposureKnown===false)return;
   const exposure=product.seconds||product.counts,maximum=exposure.flat().reduce((a,b)=>Math.max(a,b),1),threshold=maximum*Number($('q-cover-low').value)/100;
   const tint=mc.createImageData(w,h);for(let y=0;y<h;y++)for(let x=0;x<w;x++){const value=exposure[y][x];if(!product.counts[y][x]||value>=threshold)continue;const i=(y*w+x)*4;tint.data[i]=255;tint.data[i+1]=48;tint.data[i+2]=65;tint.data[i+3]=Math.round(80+130*(1-value/threshold));}mc.putImageData(tint,0,0);c.drawImage(map,0,0);
  };
  if(image.complete&&image.naturalWidth)paint();else image.decode().then(paint).catch(()=>{if(token===serial)$('q-map-note').textContent='Прегледът не може да се зареди. Покритието остава запазено.';});
 }
 window.AstroArchiveCoverageView={draw};
})();
