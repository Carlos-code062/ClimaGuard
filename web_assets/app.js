const $ = id => document.getElementById(id);
const state = {user:null, csrf:'', authMode:'login', page:'dashboard', mode:'demo', preferences:null, providers:null, snapshot:null, alerts:[]};
const channelNames = {email:'E-mail', sms:'SMS', whatsapp:'WhatsApp'};
const pageNames = {dashboard:['VISÃO GERAL','Seu ambiente, sob controle.'], preferences:['MEUS ALERTAS','Os avisos, do seu jeito.'], history:['HISTÓRICO','Cada alerta tem uma história.'], services:['SERVIÇOS DE ENVIO','Conecte os canais de aviso.']};
const escape = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => Number(value).toLocaleString('pt-BR',{maximumFractionDigits:1,minimumFractionDigits:1});
let toastTimer, polling = false;

function toast(message,error=false){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,6500)}
async function api(path,body){
 const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:body===undefined?undefined:JSON.stringify(body)});
 let data;try{data=await response.json()}catch{throw Error('Reinicie o painel pelo arquivo Reiniciar.cmd para carregar a atualização.')}
 if(!response.ok){if(response.status===401&&state.user){state.user=null;showAccess()}throw Error(data.error||'Não foi possível concluir. Tente novamente.')}
 if(data.csrf_token)state.csrf=data.csrf_token;
 return data;
}
function showAccess(){ $('boot').hidden=true;$('application').hidden=true;$('auth').hidden=false }
function authMode(mode){state.authMode=mode;const register=mode==='register';$('name-field').hidden=!register;$('confirm-field').hidden=!register;$('auth-name').required=register;$('auth-confirm').required=register;$('auth-password').autocomplete=register?'new-password':'current-password';$('auth-title').textContent=register?'Crie sua conta':'Entre na sua conta';$('auth-description').textContent=register?'Seu limite e seus canais ficam salvos para você.':'Acompanhe o ambiente e configure seus alertas.';$('auth-submit').textContent=register?'Criar conta e continuar →':'Entrar no painel →';$('register-tab').classList.toggle('active',register);$('login-tab').classList.toggle('active',!register);$('auth-error').textContent='' }
$('login-tab').addEventListener('click',()=>authMode('login'));
$('register-tab').addEventListener('click',()=>authMode('register'));
$('auth-form').addEventListener('submit',async event=>{
 event.preventDefault();$('auth-error').textContent='';
 if(state.authMode==='register'&&$('auth-password').value!==$('auth-confirm').value){$('auth-error').textContent='As senhas precisam ser iguais.';return}
 const button=$('auth-submit');button.disabled=true;
 try{const data=await api('/api/'+state.authMode,{name:$('auth-name').value,email:$('auth-email').value,password:$('auth-password').value});state.user=data.user;$('auth-password').value='';$('auth-confirm').value='';await enterApp()}catch(error){$('auth-error').textContent=error.message}finally{button.disabled=false}
});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{});state.user=null;state.snapshot=null;state.alerts=[];showAccess()}catch(error){toast(error.message,true)}});

async function enterApp(){
 $('boot').hidden=true;$('auth').hidden=true;$('application').hidden=false;
 $('user-name').textContent=state.user.name;$('avatar').textContent=state.user.name.slice(0,1).toUpperCase();$('user-role').textContent=state.user.admin?'Administrador':'Minha conta';$('services-nav').hidden=!state.user.admin;
 const data=await api('/api/preferences');state.preferences=data.preferences;state.providers=data.providers;fillPreferences();setMode('demo');navigate('dashboard');await refresh();
}
function navigate(page){
 if(page==='services'&&!state.user?.admin)return;
 state.page=page;for(const key of Object.keys(pageNames))$('page-'+key).hidden=key!==page;
 document.querySelectorAll('[data-page]').forEach(button=>{button.classList.toggle('active',button.dataset.page===page);button.setAttribute('aria-current',button.dataset.page===page?'page':'false')});
 $('page-caption').textContent=pageNames[page][0];$('page-title').textContent=pageNames[page][1];
 if(page==='services')loadServices().catch(error=>toast(error.message,true));
 if(page==='history')loadHistory().catch(error=>toast(error.message,true));
 if(page==='dashboard')setTimeout(drawChart,20);
}
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.page)));
document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.go)));
function setMode(mode){
 state.mode=mode;state.snapshot=null;$('mode-demo').classList.toggle('active',mode==='demo');$('mode-real').classList.toggle('active',mode==='real');
 $('demo-controls').hidden=mode!=='demo';$('real-help').hidden=mode!=='real';
 $('source-banner').classList.toggle('demo-notice',mode==='demo');
 $('source-banner').innerHTML=mode==='demo'?'<span aria-hidden="true">✦</span><div><strong>Modo de demonstração</strong><p>Cenários simulados. Nenhuma mensagem será enviada por estes controles.</p></div><span class="tag">SEM USB</span>':'<span aria-hidden="true">◉</span><div><strong>Leituras da placa real</strong><p>Dados enviados pelo ESP8266. Sem dados recentes, os valores ficam indisponíveis.</p></div><span class="tag">AO VIVO</span>';
 refresh().catch(error=>toast(error.message,true));
}
$('mode-demo').addEventListener('click',()=>setMode('demo'));$('mode-real').addEventListener('click',()=>setMode('real'));
function fillPreferences(){
 const p=state.preferences;for(const [id,key] of [['pref-enabled','enabled'],['pref-temperature','temperature_enabled'],['pref-power','power_enabled'],['pref-consent','consent']])$(id).checked=p[key];
 $('pref-threshold').value=p.threshold;$('pref-cooldown').value=String(p.cooldown_minutes);$('pref-email').value=p.email;$('pref-phone').value=p.phone;
 document.querySelectorAll('[name=channel]').forEach(input=>input.checked=p.channels.includes(input.value));renderReadiness();renderRules();
}
function renderReadiness(){
 if(!state.providers||!state.preferences)return;
 const p=state.preferences,s=state.providers;
 for(const key of Object.keys(channelNames))$('ready-'+key).textContent=s.ready[key]?'Configurado':'A configurar';
 $('delivery-note').textContent=s.live_enabled?'Envios reais liberados pelo administrador. Confira os canais e autorize os destinatários acima.':'Envios reais estão desativados. Você pode salvar suas regras e demonstrar os avisos sem enviar mensagens.';
 document.querySelectorAll('[data-test]').forEach(button=>{const key=button.dataset.test;button.disabled=!(s.live_enabled&&s.ready[key]&&p.enabled&&p.consent&&p.channels.includes(key));button.title=button.disabled?'Configure o serviço e salve suas preferências com autorização antes de testar.':'Enviar uma mensagem real para o destinatário salvo'});
}
function renderRules(){
 const p=state.preferences;if(!p)return;$('threshold-badge').textContent='Limite: '+number(p.threshold)+' °C';
 $('rules-summary').innerHTML=`<span class="rule-pill">${p.enabled?'Alertas ativos':'Alertas desativados'}</span><div class="rule-line">${p.temperature_enabled?'↑ Acima de <strong>'+escape(number(p.threshold))+' °C</strong>':'Temperatura: desativada'}</div><div class="rule-line">${p.power_enabled?'ϟ Avisar na <strong>falta de energia</strong>':'Energia: desativada'}</div><div class="rule-line">${escape(p.channels.map(c=>channelNames[c]).join(' · ')||'Nenhum canal selecionado')}</div><div class="rule-line">${state.providers?.live_enabled?'Serviços reais liberados':'Demonstração disponível sem credenciais'}</div>`;
}
$('preferences-form').addEventListener('submit',async event=>{
 event.preventDefault();const button=event.submitter;button.disabled=true;$('preferences-saved').textContent='';
 const data={enabled:$('pref-enabled').checked,temperature_enabled:$('pref-temperature').checked,power_enabled:$('pref-power').checked,consent:$('pref-consent').checked,threshold:Number($('pref-threshold').value),cooldown_minutes:Number($('pref-cooldown').value),channels:[...document.querySelectorAll('[name=channel]:checked')].map(i=>i.value),email:$('pref-email').value,phone:$('pref-phone').value};
 try{const result=await api('/api/preferences',data);state.preferences=result.preferences;renderReadiness();renderRules();$('preferences-saved').textContent='Preferências salvas neste notebook.';toast('Preferências salvas. Você já pode testar um cenário.')}catch(error){toast(error.message,true)}finally{button.disabled=false}
});
async function refresh(){
 if(!state.user||polling)return;polling=true;const mode=state.mode;
 try{let data=await api('/api/status?source='+mode);if(mode!==state.mode)return;
  // Start an explicitly simulated, normal scenario for a first demonstration.
  // Never create substitute readings in the real-device stream.
  if(mode==='demo'&&!data.latest){
   const baseline=Math.max(-40,Math.min(80,data.preferences.threshold-2));
   await api('/api/demo',{temperature:baseline,humidity:50,energy:true});
   if(mode!==state.mode)return;
   data=await api('/api/status?source=demo');
   if(mode!==state.mode)return;
   $('demo-temperature').value=baseline;$('demo-humidity').value=50;$('demo-energy').value='true';
  }
  state.snapshot=data;state.providers=data.providers;renderDashboard();renderReadiness();renderRules();if(state.page==='history')await loadHistory()}
 catch(error){$('device-status').textContent='Servidor indisponível';$('temperature').textContent='—';$('humidity').textContent='—';$('energy').className='energy-value';$('energy').innerHTML='<span class="status-dot"></span><span>Sem conexão</span>';if(!state.snapshot)toast(error.message,true)}finally{polling=false}
}
function renderDashboard(){
 const s=state.snapshot,v=s.latest,available=s.online&&v;
 $('temperature').innerHTML=available&&typeof v.temperature==='number'?escape(number(v.temperature))+' <small>°C</small>':'—';
 $('humidity').innerHTML=available&&typeof v.humidity==='number'?escape(number(v.humidity))+' <small>%</small>':'—';
 let note='Aguardando dados';if(available)note=v.temperature===null?'Sensor sem leitura válida':v.temperature>state.preferences.threshold?'Acima do seu limite de alerta':'Dentro do limite configurado';if(s.source==='demo'&&available)note='Simulação • '+note;if(!s.online&&s.source==='real')note='Conecte a placa por USB e mantenha o ClimaGuard aberto; também aceita leituras por Wi-Fi';$('temperature-note').textContent=note;
 const energy=$('energy');energy.className='energy-value'+(available&&v.energy!==null?(v.energy?' good':' bad'):'');energy.innerHTML='<span class="status-dot"></span><span>'+(!available?(s.source==='demo'?'Aguardando':'Sem dados'):v.energy===true?'Presente':v.energy===false?'Ausente':'Indisponível')+'</span>';
 $('device-status').textContent=s.device_online?'Placa conectada ao notebook':'Sem dados recentes da placa';
 $('last-update').textContent=v?(s.source==='demo'?'Cenário aplicado: ':'Última leitura: ')+new Date(v.received_at*1000).toLocaleTimeString('pt-BR'):'Nenhuma leitura';
 $('chart-empty').hidden=s.history.some(r=>typeof r.temperature==='number');drawChart();
}
function drawChart(){
 const canvas=$('chart');if($('page-dashboard').hidden)return;const box=canvas.getBoundingClientRect();if(!box.width)return;
 const ratio=window.devicePixelRatio||1;canvas.width=Math.round(box.width*ratio);canvas.height=Math.round(230*ratio);const x=canvas.getContext('2d');x.scale(ratio,ratio);
 const width=box.width,height=230,pad={l:38,r:12,t:12,b:25},w=width-pad.l-pad.r,h=height-pad.t-pad.b;
 const rows=state.snapshot?.history||[],valid=rows.filter(r=>typeof r.temperature==='number'),limit=state.preferences?.threshold??30;
 const low=Math.min(...valid.map(r=>r.temperature),limit,25)-2,high=Math.max(...valid.map(r=>r.temperature),limit,30)+2;
 const py=t=>pad.t+h-(t-low)/(high-low)*h;
 x.font='10px Segoe UI, sans-serif';x.strokeStyle='#e9eee6';x.fillStyle='#9aa693';x.lineWidth=1;
 for(let i=0;i<4;i++){const y=pad.t+i*h/3;x.beginPath();x.moveTo(pad.l,y);x.lineTo(width-pad.r,y);x.stroke();x.fillText(Math.round(high-i*(high-low)/3)+'°',0,y+3)}
 x.setLineDash([4,4]);x.strokeStyle='#c9aa69';x.beginPath();x.moveTo(pad.l,py(limit));x.lineTo(width-pad.r,py(limit));x.stroke();x.setLineDash([]);
 if(rows.length){let pen=false;x.strokeStyle='#4d9670';x.lineWidth=2.5;x.beginPath();rows.forEach((row,i)=>{if(typeof row.temperature!=='number'){pen=false;return}const px=pad.l+(rows.length===1?w/2:i/(rows.length-1)*w),y=py(row.temperature);pen?x.lineTo(px,y):x.moveTo(px,y);pen=true});x.stroke();for(const [i,row] of rows.entries()){if(typeof row.temperature!=='number')continue;const px=pad.l+(rows.length===1?w/2:i/(rows.length-1)*w);x.fillStyle='#4d9670';x.beginPath();x.arc(px,py(row.temperature),2.5,0,Math.PI*2);x.fill()}x.fillStyle='#9aa693';x.fillText(new Date(rows[0].received_at*1000).toLocaleTimeString('pt-BR'),pad.l,height-4);if(rows.length>1){const label=new Date(rows.at(-1).received_at*1000).toLocaleTimeString('pt-BR');x.fillText(label,width-pad.r-x.measureText(label).width,height-4)}}
}
window.addEventListener('resize',drawChart);
async function applyDemo(row){
 try{await api('/api/demo',row);await refresh();toast(state.preferences.enabled?'Cenário aplicado. Consulte os avisos simulados no histórico.':'Cenário aplicado. Ative suas regras em Meus alertas para gerar avisos simulados.')}catch(error){toast(error.message,true)}
}
document.querySelectorAll('[data-scenario]').forEach(button=>button.addEventListener('click',async()=>{
 const threshold=state.preferences.threshold;let row={temperature:Math.max(-40,threshold-2),humidity:50,energy:true};
 if(button.dataset.scenario==='hot'){if(threshold>=80){toast('O limite de 80 °C não pode ser ultrapassado pelo intervalo deste sensor. Escolha um limite menor.',true);return}row.temperature=Math.min(80,threshold+2)}
 if(button.dataset.scenario==='off')row.energy=false;
 if(button.dataset.scenario==='sensor'){row.temperature=null;row.humidity=null}
 if(row.temperature!==null){$('demo-temperature').value=row.temperature;$('demo-humidity').value=row.humidity}$('demo-energy').value=String(row.energy);
 button.disabled=true;try{await applyDemo(row)}finally{button.disabled=false}
}));
$('demo-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await applyDemo({temperature:Number($('demo-temperature').value),humidity:Number($('demo-humidity').value),energy:$('demo-energy').value==='true'})}finally{button.disabled=false}});
const statusNames={simulated:'Simulado',queued:'Na fila',sending:'Enviando',accepted:'Aceito pelo serviço',failed:'Falha no envio',blocked:'Não enviado'};
async function loadHistory(){const data=await api('/api/alerts');state.alerts=data.alerts;renderHistory()}
function renderHistory(){
 const filter=$('history-filter').value,rows=state.alerts.filter(a=>filter==='all'||a.source===filter),root=$('history-list');
 if(!rows.length){root.innerHTML='<div class="empty-state"><span aria-hidden="true">◷</span><h2>Nenhum aviso por aqui ainda.</h2><p>Salve suas preferências, ative os alertas e aplique um cenário acima do limite ou sem energia na demonstração.</p></div>';return}
 root.innerHTML=rows.map(row=>`<article class="history-item"><div class="history-icon" aria-hidden="true">${row.kind==='power'?'ϟ':row.kind==='temperature'?'↑':'✉'}</div><div><h3>${row.kind==='power'?'Falta de energia simulada':row.kind==='temperature'?'Temperatura acima do limite':'Teste de envio real'}</h3><p>${row.source==='demo'?'Demonstração':row.source==='test'?'Teste solicitado':'Placa real'} · ${escape(channelNames[row.channel])} · ${escape(row.destination)}</p><p class="message">${escape(row.message)}</p><p class="detail">${escape(row.detail)}</p></div><div><span class="status-tag ${escape(row.status)}">${escape(statusNames[row.status]||row.status)}</span><time datetime="${new Date(row.created*1000).toISOString()}">${new Date(row.created*1000).toLocaleString('pt-BR')}</time></div></article>`).join('');
}
$('history-filter').addEventListener('change',renderHistory);
document.querySelectorAll('[data-test]').forEach(button=>button.addEventListener('click',async()=>{
 const channel=button.dataset.test,destination=channel==='email'?state.preferences.email:state.preferences.phone;
 if(!window.confirm(`Enviar uma mensagem REAL por ${channelNames[channel]} para ${destination}? O serviço pode cobrar pelo envio.`))return;
 button.disabled=true;try{await api('/api/test-message',{channel,confirm:true});toast('Teste real colocado na fila. Veja o resultado no histórico.');navigate('history')}catch(error){toast(error.message,true)}finally{renderReadiness()}
}));
async function loadServices(){
 const data=await api('/api/providers'),p=data.providers;state.providers=p;
 for(const input of $('services-form').querySelectorAll('input[name], select[name]')){input.value=p.fields[input.name]||'';if(p.saved?.[input.name])input.placeholder='Credencial salva • deixe vazio para manter'}
 $('services-live').checked=p.live_enabled;renderReadiness();
}
$('services-form').addEventListener('submit',async event=>{
 event.preventDefault();const button=event.submitter;button.disabled=true;const data=Object.fromEntries(new FormData(event.target));data.live_enabled=$('services-live').checked;
 try{const result=await api('/api/providers',data);state.providers=result.providers;for(const input of event.target.querySelectorAll('input[type=password]'))input.value='';await loadServices();renderRules();$('services-saved').textContent='Configuração salva. Nenhuma mensagem de teste foi enviada.';toast('Serviços salvos. Use Meus alertas para autorizar e testar um destinatário.')}catch(error){toast(error.message,true)}finally{button.disabled=false}
});
setInterval(()=>{if(state.user&&!document.hidden)refresh()},3000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.user)refresh()});
try{const result=await api('/api/session');state.user=result.user;if(state.user)await enterApp();else showAccess()}catch(error){$('boot').textContent=error.message}
