/* Tiny offline chart renderer. Text alternatives are server-rendered (UI-09). */
document.querySelectorAll('.chart').forEach((figure)=>{const points=JSON.parse(figure.dataset.points||'[]'),svg=figure.querySelector('svg');if(points.length<2)return;const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),coords=points.map(p=>`${20+760*(p[0]-xmin)/(xmax-xmin||1)},${160-140*(p[1]-ymin)/(ymax-ymin||1)}`).join(' '),line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('points',coords);svg.appendChild(line)});
const refresh=Number(document.body.dataset.refreshMs||0);if(refresh)window.setTimeout(()=>window.location.reload(),refresh);
/* Some privacy-hardened browsers omit Origin on native form navigation. Fetch
   sets it consistently for same-origin POSTs and preserves UI-08's strict
   server-side check. */
document.querySelectorAll('form[method="post"]').forEach((form)=>form.addEventListener('submit',async(event)=>{event.preventDefault();const response=await fetch(form.action,{method:'POST',body:new URLSearchParams(new FormData(form)),headers:{'Content-Type':'application/x-www-form-urlencoded'},redirect:'follow'});if(response.ok){window.location.assign(response.url)}else{document.body.textContent=await response.text()}}));
