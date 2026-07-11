/* Tiny offline chart renderer. Text alternatives are server-rendered (UI-09). */
document.querySelectorAll('.chart').forEach((figure)=>{const points=JSON.parse(figure.dataset.points||'[]'),svg=figure.querySelector('svg');if(points.length<2)return;const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),coords=points.map(p=>`${20+760*(p[0]-xmin)/(xmax-xmin||1)},${160-140*(p[1]-ymin)/(ymax-ymin||1)}`).join(' '),line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('points',coords);svg.appendChild(line)});
const refresh=Number(document.body.dataset.refreshMs||0);if(refresh)window.setTimeout(()=>window.location.reload(),refresh);
/* Some privacy-hardened browsers omit Origin on native form navigation. Fetch
   sets it consistently for same-origin POSTs and preserves UI-08's strict
   server-side check. */
document.querySelectorAll('form[method="post"]').forEach((form)=>form.addEventListener('submit',async(event)=>{event.preventDefault();const response=await fetch(form.action,{method:'POST',body:new URLSearchParams(new FormData(form)),headers:{'Content-Type':'application/x-www-form-urlencoded'},redirect:'follow'});if(response.ok){window.location.assign(response.url)}else{document.body.textContent=await response.text()}}));

/* M7 uses one timestamp union per panel. Nulls are intentional gaps: joining
   across a suspend or missing rollup would imply evidence FTMON never saw. */
const trendNode=document.getElementById('disk-trend-data');
if(trendNode&&window.uPlot){const trend=JSON.parse(trendNode.textContent),syncKey='ftmon-disk';
const align=(sets)=>{const xs=[...new Set(sets.flatMap(s=>s.map(p=>p[0])))].sort((a,b)=>a-b);return [xs,...sets.map(s=>{const m=new Map(s);return xs.map(x=>m.has(x)?m.get(x):null)})]};
const markers=(items)=>({hooks:{draw:[u=>{const ctx=u.ctx;ctx.save();ctx.strokeStyle='#c01c28';ctx.setLineDash([4,4]);items.forEach(i=>{const x=Math.round(u.valToPos(i.opened_ts,'x',true));ctx.beginPath();ctx.moveTo(x,u.bbox.top);ctx.lineTo(x,u.bbox.top+u.bbox.height);ctx.stroke()});ctx.restore()}]}});
const common=(title,series,scales={})=>({title,width:Math.max(320,document.getElementById('disk-trends').clientWidth-24),height:240,scales:{x:{time:true},...scales},axes:[{},{}],series,cursor:{sync:{key:syncKey}},plugins:[markers(trend.incidents)]});
const capacity=align([trend.capacity.points,trend.capacity.lower,trend.capacity.upper]);
const capSeries=[{}, {label:'Used %',stroke:'#3273dc',width:2},{label:'Minimum',stroke:'#3273dc88',width:1},{label:'Maximum',stroke:'#3273dc88',width:1}];
[['Notice','space_notice_pct','#9a6700'],['Warning','space_warn_pct','#d97706'],['Error','space_crit_pct','#c01c28']].forEach(([label,key,color])=>{if(key in trend.thresholds){capacity.push(capacity[0].map(()=>trend.thresholds[key]));capSeries.push({label,stroke:color,width:1,dash:[6,4]})}});
const capOpts=common('Capacity',capSeries,{y:{range:[0,100]}});capOpts.bands=[{series:[2,3],fill:'#3273dc22'}];new uPlot(capOpts,capacity,document.querySelector('[data-panel="capacity"]'));
const rate=align([trend.rate,trend.confidence]),rateOpts=common('Signed fill rate and confidence',[{}, {label:'Bytes/hour',stroke:'#9a6700',width:2,scale:'rate'},{label:'Confidence',stroke:'#6f42c1',width:2,scale:'confidence'}],{rate:{},confidence:{range:[0,1]}});rateOpts.axes=[{}, {scale:'rate'}, {scale:'confidence',side:1,values:(u,ticks)=>ticks.map(v=>`${Math.round(v*100)}%`)}];new uPlot(rateOpts,rate,document.querySelector('[data-panel="rate"]'));
const projection=align([trend.projection]);new uPlot(common('Qualified hours remaining',[{}, {label:'Hours',stroke:'#c01c28',width:2}]),projection,document.querySelector('[data-panel="projection"]'));
}
