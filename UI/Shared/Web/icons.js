"use strict";
// Astro Archive's shared vector set. No external fonts, assets or scripts.
(()=>{
 const paths={
  previous:'<path d="M19 5H8a3 3 0 0 0-3 3v8a3 3 0 0 0 3 3h11" opacity=".45"/><path d="m13 8-4 4 4 4m-4-4h12"/>',
  next:'<path d="M5 5h11a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H5" opacity=".45"/><path d="m11 8 4 4-4 4m4-4H3"/>',
  fit:'<path d="M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5"/><rect x="8" y="8" width="8" height="8" rx="1.5" opacity=".55"/>',
  actual:'<rect x="3" y="4" width="18" height="16" rx="3" opacity=".5"/><path d="m6.5 10 2-1v6m7-5 2-1v6"/><circle cx="12" cy="10" r=".65" fill="currentColor" stroke="none"/><circle cx="12" cy="14" r=".65" fill="currentColor" stroke="none"/>',
  zoomIn:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5M7.5 10.5h6m-3-3v6"/>',
  zoomOut:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5M7.5 10.5h6"/>',
  rotate:'<path d="M7 6a8 8 0 1 1-3 7m3-7H3m4 0V2"/><rect x="9" y="9" width="7" height="7" rx="1.5" transform="rotate(12 12.5 12.5)" opacity=".55"/>',
  flipX:'<path d="m3 17 6-10v10Zm18 0L15 7v10Z"/><path d="M12 3v18" stroke-dasharray="2 3" opacity=".55"/>',
  flipY:'<path d="m7 3 10 6H7Zm0 18 10-6H7Z"/><path d="M3 12h18" stroke-dasharray="2 3" opacity=".55"/>',
  panel:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M14 4v16m3-11h1m-1 4h1m-1 4h1"/><path d="m6 14 2-3 3 4" opacity=".55"/>',
  close:'<path d="m7 7 10 10M17 7 7 17"/>',
  reset:'<path d="M5 8a8 8 0 1 1-1 8M5 8H1m4 0V4"/><path d="M9 12h6m-3-3v6" opacity=".55"/>',
  header:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Zm0 0v6h6M8 12h8m-8 4h5"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>'
 };
 function markup(name){return '<svg class="app-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'+(paths[name]||paths.header)+'</svg>';}
 function mount(element,name,label){element.innerHTML=markup(name);element.classList.add('app-icon-button');element.dataset.appIcon=name;if(label){element.title=label;element.setAttribute('aria-label',label);}return element;}
 window.AstroArchiveIcons={markup,mount};
})();
