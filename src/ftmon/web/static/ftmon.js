/* Tiny offline chart renderer. Text alternatives are server-rendered (UI-09). */
document.querySelectorAll('.chart').forEach((figure)=>{const points=JSON.parse(figure.dataset.points||'[]'),svg=figure.querySelector('svg');if(points.length<2)return;const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys),coords=points.map(p=>`${20+760*(p[0]-xmin)/(xmax-xmin||1)},${160-140*(p[1]-ymin)/(ymax-ymin||1)}`).join(' '),line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('points',coords);svg.appendChild(line)});
const refresh=Number(document.body.dataset.refreshMs||0);if(refresh)window.setTimeout(()=>window.location.reload(),refresh);
/* Some privacy-hardened browsers omit Origin on native form navigation. Fetch
   sets it consistently for same-origin POSTs and preserves UI-08's strict
   server-side check. */
document.querySelectorAll('form[method="post"]').forEach((form)=>form.addEventListener('submit',async(event)=>{event.preventDefault();const response=await fetch(form.action,{method:'POST',body:new URLSearchParams(new FormData(form)),headers:{'Content-Type':'application/x-www-form-urlencoded'},redirect:'follow'});if(response.ok){window.location.assign(response.url)}else{document.body.textContent=await response.text()}}));
document.querySelectorAll('[data-trend-select]').forEach(select=>select.addEventListener('change',()=>{const range=new URLSearchParams(window.location.search).get('range')||'24h';window.location.assign(`/trends/${select.value}?range=${encodeURIComponent(range)}`)}));
/* Monitor/entity choices are dependent. Reloading at each boundary keeps the
   URL canonical and makes the database—not stale browser state—the catalog. */
document.querySelectorAll('[data-metric-monitor]').forEach(select=>select.addEventListener('change',()=>{const form=select.form,params=new URLSearchParams(new FormData(form));params.delete('entity');params.delete('metric');window.location.assign(`/metrics?${params}`)}));
document.querySelectorAll('[data-metric-entity]').forEach(select=>select.addEventListener('change',()=>{const form=select.form,params=new URLSearchParams(new FormData(form));params.delete('metric');window.location.assign(`/metrics?${params}`)}));

/* One adapter owns time alignment and incident overlays for both Metrics and
   Trends. Separate copies previously made the diagnostic chart disagree with
   the interpreted chart about gaps and time positioning (D14/UI-13). */
const alignTimeSeries=(sets)=>{const xs=[...new Set(sets.flatMap(s=>s.map(p=>p[0])))].sort((a,b)=>a-b);return [xs,...sets.map(s=>{const m=new Map(s);return xs.map(x=>m.has(x)?m.get(x):null)})]};
const incidentMarkerPlugin=(items)=>({hooks:{draw:[u=>{const ctx=u.ctx;ctx.save();ctx.strokeStyle='#c01c28';ctx.setLineDash([4,4]);items.forEach(i=>{const x=Math.round(u.valToPos(i.opened_ts,'x',true));ctx.beginPath();ctx.moveTo(x,u.bbox.top);ctx.lineTo(x,u.bbox.top+u.bbox.height);ctx.stroke()});ctx.restore()}]}});
/* Baselines keep their native five-minute timestamps. Drawing from the points
   directly avoids inserting nulls at raw/hourly timestamps, while the exact
   delta check prevents a dashed stroke from claiming evidence across a gap. */
const baselinePlugin=(points)=>({hooks:{draw:[u=>{if(!points.length)return;const ctx=u.ctx;ctx.save();ctx.strokeStyle='#6f42c1';ctx.lineWidth=2;ctx.setLineDash([7,5]);points.forEach((point,index)=>{const x=u.valToPos(point[0],'x',true),y=u.valToPos(point[1],'y',true);ctx.beginPath();ctx.arc(x,y,2,0,Math.PI*2);ctx.fillStyle='#6f42c1';ctx.fill();if(index&&point[0]-points[index-1][0]===300){const previous=points[index-1];ctx.beginPath();ctx.moveTo(u.valToPos(previous[0],'x',true),u.valToPos(previous[1],'y',true));ctx.lineTo(x,y);ctx.stroke()}});ctx.restore()}]}});
const timeChartOptions=(container,title,series,incidents,scales={},syncKey='ftmon-chart',plugins=[])=>({title,width:Math.max(320,container.clientWidth-24),height:260,scales:{x:{time:true},...scales},axes:[{},{}],series,cursor:{drag:{x:true,y:false,setScale:true},sync:{key:syncKey}},plugins:[incidentMarkerPlugin(incidents),...plugins]});

/* Trends use one timestamp union per panel. Nulls are intentional gaps: joining
   across a suspend or missing rollup would imply evidence FTMON never saw. */
const trendNode=document.getElementById('trend-data');
if(trendNode&&window.uPlot){const trend=JSON.parse(trendNode.textContent),syncKey='ftmon-trend',panels=trend.panels;
const common=(title,series,scales={})=>timeChartOptions(document.getElementById('trend-charts'),title,series,trend.incidents,scales,syncKey);
const addThresholds=(data,series,thresholds)=>thresholds.forEach((threshold,index)=>{data.push(data[0].map(()=>threshold.value));series.push({label:threshold.parameter,stroke:['#9a6700','#d97706','#c01c28'][index%3],width:1,dash:[6,4]})});
const valueData=alignTimeSeries([panels.value.points,panels.value.lower,panels.value.upper]),valueSeries=[{}, {label:`${panels.value.metric} (${panels.value.unit})`,stroke:'#3273dc',width:2},{label:'Minimum',stroke:'#3273dc88',width:1},{label:'Maximum',stroke:'#3273dc88',width:1}];addThresholds(valueData,valueSeries,panels.value.thresholds);const valueOpts=common('Value',valueSeries,panels.value.unit==='percent'?{y:{range:[0,100]}}:{});valueOpts.bands=[{series:[2,3],fill:'#3273dc22'}];new uPlot(valueOpts,valueData,document.querySelector('[data-panel="value"]'));
const rateData=alignTimeSeries([panels.rate.points]),rateSeries=[{}, {label:`${panels.rate.metric} (${panels.rate.unit})`,stroke:'#9a6700',width:2}];addThresholds(rateData,rateSeries,panels.rate.thresholds);new uPlot(common('Signed rate',rateSeries),rateData,document.querySelector('[data-panel="rate"]'));
if(panels.confidence){const confidence=alignTimeSeries([panels.confidence.points]);if(panels.confidence.threshold!==null){confidence.push(confidence[0].map(()=>panels.confidence.threshold))}const series=[{}, {label:'Confidence',stroke:'#6f42c1',width:2},...(panels.confidence.threshold!==null?[{label:'Threshold',stroke:'#9a6700',dash:[6,4]}]:[])];new uPlot(common('Confidence',series,{y:{range:[0,1]}}),confidence,document.querySelector('[data-panel="confidence"]'))}
if(panels.projection){const projection=alignTimeSeries([panels.projection.points]);new uPlot(common('Qualified hours remaining',[{}, {label:'Hours',stroke:'#c01c28',width:2}]),projection,document.querySelector('[data-panel="projection"]'))}
}

const metricNode=document.getElementById('metric-data');
if(metricNode&&window.uPlot){const metric=JSON.parse(metricNode.textContent),panel=metric.panel,container=document.querySelector('[data-metric-chart]'),data=alignTimeSeries([panel.points,panel.lower,panel.upper]),series=[{}, {label:`${metric.metric} (${metric.unit})`,stroke:'#3273dc',width:2},{label:'Minimum',stroke:'#3273dc88',width:1},{label:'Maximum',stroke:'#3273dc88',width:1}],plugins=metric.baseline?[baselinePlugin(metric.baseline.points)]:[],opts=timeChartOptions(container,metric.metric,series,metric.incidents,metric.unit==='percent'?{y:{range:[0,100]}}:{},'ftmon-chart',plugins);opts.bands=[{series:[2,3],fill:'#3273dc22'}];new uPlot(opts,data,container)}
