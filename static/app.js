'use strict';
const $ = id => document.getElementById(id);
let servers = [], selected = '', page = 'overview', history = [], serviceRows = [], ws, term, fit, generation = 0;
let refreshBusy = false;
let tunnelUnit = '';
const titles = {overview:['Server overview','A live view of the machine you’re managing.'],services:['Services','Manage system services from one place.'],logs:['System logs','Follow the selected server’s journal.'],terminal:['Terminal','A direct connection to your server’s shell.'],tunnel:['Cloudflare Tunnel','Monitor connector connections, requests and origin errors.'],servers:['Server connections','Manage the machines in your workspace.'],audit:['Activity','Review recent administration activity.']};
async function api(path, options = {}) {
  const response = await fetch('/api' + path, {credentials:'same-origin', headers:{'Content-Type':'application/json'}, ...options});
  if (!response.ok) {
    let message = await response.text();
    try { message = JSON.parse(message).detail || message; if(Array.isArray(message)) message=message.map(x=>`${x.loc?.at(-1)||'Field'}: ${x.msg}`).join('; '); } catch {}
    if (response.status === 401 && path !== '/login') showLogin();
    throw new Error(message);
  }
  return response.json();
}
function notice(message='') { $('notice').textContent=message; $('notice').hidden=!message; }
function showLogin() { disconnect(); generation++; $('workspace').hidden=true; $('login').hidden=false; }
function bytes(n) { const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++;}return n.toFixed(i?1:0)+' '+units[i]; }
function uptime(n) {const days=Math.floor(n/86400), hours=Math.floor(n%86400/3600);return days?`${days}d ${hours}h`:`${hours}h ${Math.floor(n%3600/60)}m`;}
function cell(row, text) {const td=document.createElement('td');td.textContent=text??'';row.append(td);return td;}
function button(text, handler, cls='secondary') { const b=document.createElement('button');b.textContent=text;b.className=cls;b.onclick=handler;return b; }
async function loadServers() {
  servers=await api('/servers');
  if(!servers.some(s=>s.id===selected)) selected=servers[0]?.id||'';
  $('server-select').replaceChildren(new Option('Choose a server',''));
  servers.forEach(s=>$('server-select').add(new Option(s.name,s.id)));
  $('server-select').value=selected;
  $('empty-state').hidden=!!selected;
  $('metrics-view').hidden=!selected;
  renderServers();
}
async function enter() { await loadServers();$('login').hidden=true;$('workspace').hidden=false;await navigate(page); }
$('login-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;$('login-error').textContent='';try{await api('/login',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});await enter();}catch(err){$('login-error').textContent=err.message;}finally{b.disabled=false;}};
$('logout').onclick=async()=>{try{await api('/logout',{method:'POST'});showLogin();}catch(e){notice(e.message);}};
async function navigate(next) {
  if(page==='terminal'&&next!=='terminal') disconnect();
  generation++;page=next;notice();
  if(next==='tunnel') $('tunnel-port').value=localStorage.getItem('servercp-tunnel-port-'+selected)||'20241';
  for(const key of Object.keys(titles)) $(key+'-page').hidden=key!==page;
  document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  $('page-title').textContent=titles[page][0];$('page-description').textContent=titles[page][1];$('crumb').textContent=page==='audit'?'Activity':page.charAt(0).toUpperCase()+page.slice(1);$('sidebar').classList.remove('open');
  await refresh();
}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>navigate(b.dataset.page));
$('server-select').onchange=async e=>{disconnect();selected=e.target.value;generation++;history=[];tunnelUnit='';$('tunnel-logs').textContent='No connector journal selected.';$('tunnel-port').value=localStorage.getItem('servercp-tunnel-port-'+selected)||'20241';$('empty-state').hidden=!!selected;$('metrics-view').hidden=!selected;await refresh();};
$('mobile-menu').onclick=()=> $('sidebar').classList.toggle('open');
$('theme').onclick=()=>{document.documentElement.classList.toggle('light');localStorage.setItem('servercp-theme',document.documentElement.classList.contains('light')?'light':'dark');};
if(localStorage.getItem('servercp-theme')==='light') document.documentElement.classList.add('light');
function openDialog() { $('server-error').textContent='';$('server-dialog').showModal(); }
$('add-server').onclick=openDialog;$('empty-add').onclick=openDialog;$('close-dialog').onclick=()=> $('server-dialog').close();
$('server-form').onsubmit=async e=>{e.preventDefault();e.submitter.disabled=true;$('server-error').textContent='';try{const body=Object.fromEntries(new FormData(e.target));body.port=Number(body.port);const added=await api('/servers',{method:'POST',body:JSON.stringify(body)});selected=added.id;history=[];generation++;await loadServers();$('server-dialog').close();e.target.reset();await navigate('overview');}catch(err){$('server-error').textContent=typeof err.message==='string'?err.message:'Check the connection details.';}finally{e.submitter.disabled=false;}};
function renderServers() {
  const container=$('server-cards');container.replaceChildren();
  if(!servers.length){const p=document.createElement('p');p.textContent='No servers connected yet. Add your first server to get started.';container.append(p);}
  servers.forEach(s=>{const c=document.createElement('article');c.className='card server-card';const pill=document.createElement('span');pill.className='pill';pill.textContent='SSH connection';const h=document.createElement('h3');h.textContent=s.name;const p=document.createElement('p');p.textContent=`${s.username}@${s.host}:${s.port}`;const small=document.createElement('small');small.textContent=s.fingerprint;const actions=document.createElement('div');actions.className='button-group';actions.append(button('Manage →',async()=>{selected=s.id;generation++;history=[];await loadServers();navigate('overview');},'primary'),button('Remove',async()=>{if(!confirm(`Remove ${s.name} from ServerCP? This removes its saved connection.`))return;try{await api('/servers/'+s.id,{method:'DELETE'});if(selected===s.id){disconnect();selected='';history=[];generation++;}await loadServers();}catch(e){notice(e.message);}}));c.append(pill,h,p,small,actions);container.append(c);});
}
async function refresh() {
  if($('workspace').hidden) return;
  const version=generation,sid=selected,current=page;
  const valid=()=>version===generation&&sid===selected&&current===page;
  try {
    if(page==='overview'&&sid){const m=await api(`/servers/${sid}/metrics`);if(!valid())return;notice();renderMetrics(m);}
    if(page==='services'){if(!sid){serviceRows=[];renderServices();notice('Choose a server to view its services.');return;}const rows=await api(`/servers/${sid}/services`);if(!valid())return;serviceRows=rows;renderServices();}
    if(page==='logs'){if(!sid){$('logs-output').textContent='Choose a server to view its journal.';return;}const logs=await api(`/servers/${sid}/logs`);if(!valid())return;$('logs-output').textContent=logs.text||'No journal entries are available to this SSH account.';}
    if(page==='tunnel'){
      if(!sid){renderTunnel({available:false,message:'Choose a server to inspect its connector.',services:[]});return;}
      const port=Number($('tunnel-port').value);
      if(!Number.isInteger(port)||port<1||port>65535){notice('Enter a metrics port between 1 and 65535.');return;}
      const m=await api(`/servers/${sid}/tunnel?port=${port}`);if(!valid())return;renderTunnel(m);
      if(tunnelUnit){const logs=await api(`/servers/${sid}/logs?unit=${encodeURIComponent(tunnelUnit)}`);if(valid())$('tunnel-logs').textContent=logs.text||'No journal entries are available to this SSH account.';}
    }
    if(page==='audit'){const rows=await api('/audit');if(!valid())return;const body=$('audit-body');body.replaceChildren();rows.forEach(a=>{const tr=document.createElement('tr');cell(tr,new Date(a.at*1000).toLocaleString());cell(tr,a.action);cell(tr,servers.find(s=>s.id===a.server)?.name||a.server||'Workspace');cell(tr,a.detail);body.append(tr);});if(!rows.length){const tr=document.createElement('tr');const td=cell(tr,'No activity yet');td.colSpan=4;body.append(tr);}}
  } catch(e){if(valid()){notice(e.message);if(page==='overview'){$('connection-status').textContent='Unavailable · last sample shown';$('connection-status').classList.add('failed');}}}
}
function renderMetrics(m) {
  const server=servers.find(s=>s.id===selected);
  $('connection-status').textContent='● Connected';$('connection-status').classList.remove('failed');$('host-name').textContent=server.name;$('host-os').textContent=m.os;
  const ram=100*m.memory_used/m.memory_total,disk=100*m.disk_used/m.disk_total;
  $('cpu').textContent=m.cpu.toFixed(1)+'%';$('memory').textContent=ram.toFixed(1)+'%';$('disk').textContent=disk.toFixed(1)+'%';$('uptime').textContent=uptime(m.uptime);
  $('cpu-bar').style.width=m.cpu+'%';$('memory-bar').style.width=ram+'%';$('disk-bar').style.width=disk+'%';
  $('cpu-detail').textContent=m.cores+' logical cores';$('memory-detail').textContent=bytes(m.memory_used)+' of '+bytes(m.memory_total);$('disk-detail').textContent=bytes(m.disk_used)+' of '+bytes(m.disk_total);$('load-detail').textContent='Load '+m.load.map(x=>x.toFixed(2)).join(' / ');
  $('detail-host').textContent=m.hostname;$('detail-kernel').textContent=m.kernel;$('detail-user').textContent=server.username;$('detail-address').textContent=server.host+':'+server.port;
  const previous=history.at(-1);$('network').replaceChildren();m.network.forEach(n=>{const p=document.createElement('div');const old=previous?.network.find(x=>x.name===n.name);let text=n.name+' · ';if(old&&m.timestamp>previous.timestamp){const elapsed=m.timestamp-previous.timestamp;text+='↓ '+bytes(Math.max(0,n.rx-old.rx)/elapsed)+'/s  ↑ '+bytes(Math.max(0,n.tx-old.tx)/elapsed)+'/s';}else{text+='↓ '+bytes(n.rx)+'  ↑ '+bytes(n.tx)+' since boot';}p.textContent=text;$('network').append(p);});
  history.push({...m,ram});history=history.slice(-60);
  for(const [key,id] of [['cpu','cpu-line'],['ram','memory-line']]) $(id).setAttribute('points',history.map((h,i)=>`${600*(i/59)},${180-1.6*h[key]}`).join(' '));
}
function renderServices() {
  const body=$('services-body');body.replaceChildren();const search=$('service-search').value.toLowerCase();
  serviceRows.filter(s=>(s.unit+' '+s.description).toLowerCase().includes(search)).forEach(s=>{const tr=document.createElement('tr');cell(tr,s.unit);const td=cell(tr,'');const tag=document.createElement('span');tag.className='pill'+(s.active==='failed'?' failed':'');tag.textContent=s.active;td.append(tag);cell(tr,s.description);const actions=cell(tr,'');for(const action of ['start','restart','stop'])actions.append(button(action.charAt(0).toUpperCase()+action.slice(1),async e=>{const sid=selected;if(!confirm(`${action.toUpperCase()} ${s.unit} on ${servers.find(x=>x.id===sid)?.name}?`))return;e.target.disabled=true;try{await api(`/servers/${sid}/services/${encodeURIComponent(s.unit)}`,{method:'POST',body:JSON.stringify({action})});if(selected===sid)await refresh();}catch(err){notice(err.message);}finally{e.target.disabled=false;}}));body.append(tr);});
  if(!body.childElementCount){const tr=document.createElement('tr');const td=cell(tr,'No matching services');td.colSpan=4;td.className='table-empty';body.append(tr);}
}
function renderTunnel(m) {
  const count=value=>value==null?'—':Number(value).toLocaleString();
  $('tunnel-connections').textContent=count(m.connections);$('tunnel-requests').textContent=count(m.requests);$('tunnel-errors').textContent=count(m.errors);$('tunnel-streams').textContent=count(m.active_streams);
  $('tunnel-health').textContent=!m.available?'Metrics unavailable':m.connections==null?'Connection count unavailable':m.connections>0?'Connected to Cloudflare':'No edge connections';
  $('tunnel-message').textContent=m.message||'Live connector metrics · refreshed every 5 seconds';
  $('tunnel-version').textContent=m.version||'—';$('tunnel-locations').textContent=m.locations?.join(', ')||'—';$('tunnel-memory').textContent=m.memory_bytes==null?'—':bytes(m.memory_bytes);$('tunnel-uptime').textContent=m.started_at?uptime(Math.max(0,m.timestamp-m.started_at)):'—';$('tunnel-id').textContent=m.tunnel_id||'—';
  const box=$('tunnel-services');box.replaceChildren();
  (m.services||[]).forEach(s=>{const row=document.createElement('div');row.className='tunnel-service';const label=document.createElement('span');label.textContent=s.unit+' · '+s.active;const actions=document.createElement('div');actions.className='button-group';actions.append(button('View logs',()=>{tunnelUnit=s.unit;refresh();}),button('Restart',async()=>{const sid=selected;if(!confirm(`Restart ${s.unit}? Access through this connector may be interrupted.`))return;try{await api(`/servers/${sid}/services/${s.unit}`,{method:'POST',body:JSON.stringify({action:'restart'})});if(selected===sid)refresh();}catch(e){notice(e.message);}}));row.append(label,actions);box.append(row);});
}
$('tunnel-port').onchange=()=>{if(selected)localStorage.setItem('servercp-tunnel-port-'+selected,$('tunnel-port').value);generation++;refresh();};
$('service-search').oninput=renderServices;$('refresh-logs').onclick=refresh;$('quick-terminal').onclick=()=>navigate('terminal');
function disconnect() {if(ws){ws.close();ws=null;}if(term){term.dispose();term=null;}fit=null;$('terminal-container').replaceChildren();$('terminal-status').textContent='Disconnected';$('terminal-placeholder').hidden=false;document.querySelector('.terminal-card').classList.remove('expanded');}
$('terminal-connect').onclick=()=>{
  if(!selected){notice('Choose a server before opening a terminal.');return;}
  disconnect();notice();$('terminal-placeholder').hidden=true;
  term=new Terminal({cursorBlink:true,fontSize:13,fontFamily:'ui-monospace, Consolas, monospace',theme:{background:'#0e1318',foreground:'#d8e4ee'},scrollback:5000});fit=new FitAddon.FitAddon();term.loadAddon(fit);term.open($('terminal-container'));fit.fit();
  const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/terminal/${selected}`);ws=socket;socket.binaryType='arraybuffer';$('terminal-status').textContent='Connecting…';
  socket.onopen=()=>{if(ws!==socket)return;$('terminal-status').textContent='Connected';socket.send(JSON.stringify({type:'resize',cols:term.cols,rows:term.rows}));term.focus();};
  socket.onmessage=e=>{if(ws!==socket||!term)return;if(typeof e.data==='string'){try{notice(JSON.parse(e.data).error);}catch{}}else term.write(new Uint8Array(e.data));};
  socket.onclose=()=>{if(ws===socket){$('terminal-status').textContent='Disconnected';term?.write('\r\n\x1b[90m[Connection closed]\x1b[0m\r\n');}};
  socket.onerror=()=>{if(ws===socket)notice('Terminal connection failed. Check server access and try again.');};
  term.onData(data=>{if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'input',data}));});
  term.onResize(({cols,rows})=>{if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'resize',cols,rows}));});
};
$('terminal-disconnect').onclick=disconnect;
$('terminal-expand').onclick=()=>{document.querySelector('.terminal-card').classList.toggle('expanded');requestAnimationFrame(()=>fit?.fit());};
new ResizeObserver(()=>{if(fit&&term&&!$('terminal-page').hidden)requestAnimationFrame(()=>{try{fit?.fit();}catch{}});}).observe($('terminal-container'));
setInterval(async()=>{if(refreshBusy||document.hidden||!['overview','logs','tunnel'].includes(page))return;refreshBusy=true;try{await refresh();}finally{refreshBusy=false;}},5000);
enter().catch(()=>showLogin());
