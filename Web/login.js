"use strict";
// SameSite=Strict omits the cookie on an external entry navigation. Once this
// public document is committed, an existing same-origin session can resume.
fetch('api/hello',{cache:'no-store'}).then(r=>{if(r.ok)location.replace('./');}).catch(()=>{});
fetch('api/health').then(r=>r.json()).then(x=>{document.getElementById('connection-kind').textContent=x.mode==='vpn'?'HTTPS · защитена връзка през Tailscale':'HTTPS · защитена домашна връзка';}).catch(()=>{});
document.getElementById('device').value=/Android|iPhone|iPad/i.test(navigator.userAgent)?'Моят телефон':'Моят компютър';
document.getElementById('pair-form').onsubmit=async event=>{event.preventDefault();const button=document.getElementById('connect'),error=document.getElementById('pair-error');button.disabled=true;error.hidden=true;try{const response=await fetch('api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.getElementById('device').value,code:document.getElementById('pair-code').value})});const value=await response.json();if(!response.ok)throw Error(value.error||'Свързването не успя.');location.replace('./');}catch(e){error.textContent=e.message;error.hidden=false;button.disabled=false;}};
