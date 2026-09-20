const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let config,state,captures=[],stream,recognition=null,epoch=0,chain=Promise.resolve(),replaying=false;
const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;
const clock=()=>Math.round(performance.now());
async function api(path,data,method){
 const response=await fetch(path,{method:method||(data===undefined?'GET':'POST'),headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
 const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:`Request failed (${response.status})`);return result;
}
const fail=e=>{$('error').textContent=e.message;};
function accept(s){if(!state||state.id!==s.id||s.sequence>=state.sequence){state=s;render();}}
function abortLocal(){epoch++;replaying=false;if(recognition){const r=recognition;recognition=null;r.abort();}$('micState').textContent='Microphone off';$('finish').hidden=true;}
async function control(action){abortLocal();accept(await api(`/v1/sessions/${state.id}/controls`,{action}));}
async function connect(){
 config=await api('/v1/config');const list=await api('/v1/sessions');
 const old=list.sessions.find(s=>s.name==='Browser');accept(old?await api(`/v1/sessions/${old.id}`):await api('/v1/sessions',{name:'Browser'}));
 $('login').hidden=true;$('workspace').hidden=false;$('error').textContent='';
 $('provider').textContent=config.provider==='fixture'?'FIXTURE · synthetic judgments':`LIVE · ${config.provider}`;
 $('storage').textContent=config.persistent_captures?'Exact passages are saved on this server until you delete them.':'Captures are in server memory; they disappear on restart.';
 $('samples').innerHTML=config.samples.map((s,i)=>`<button data-sample="${i}">${esc(s.label)}</button>`).join('');
 $('speechInfo').textContent=Speech?'One utterance per microphone session. No automatic restart. Timing is approximate browser event timing.':'This browser has no Web Speech support. Type, paste, or use your keyboard’s dictation to submit text.';
 if(stream)stream.close();stream=new EventSource(`/v1/sessions/${state.id}/events`);
 stream.addEventListener('state',e=>{accept(JSON.parse(e.data));loadCaptures().catch(fail);});
 stream.onerror=()=>{$('phase').textContent='Reconnecting to engine…';};
 await loadCaptures();render();
}
async function loadCaptures(){captures=(await api('/v1/captures')).captures;
 $('captures').innerHTML=captures.slice(0,50).map(c=>`<article class="item"><span class="badge">${esc(c.source)} · ${c.start_ms}–${c.end_ms} ms</span><p>${esc(c.text)}</p><div class="section-title"><span class="muted">${esc(c.timing)}</span><button class="danger" data-delete="${esc(c.id)}">Delete</button></div></article>`).join('');
}
function render(){
 $('notice').textContent=state.notice;$('phase').textContent=state.paused?'Paused':state.speaking?'Assistant speaking':state.current?.phase||'Ready';
 $('pause').textContent=state.paused?'Resume':'Pause';$('speaking').textContent=`Assistant speaking: ${state.speaking?'on':'off'}`;
 for(const id of ['submit','mic','replay'])$(id).disabled=state.paused||state.speaking||!!recognition||(id==='mic'&&!Speech)||(id==='replay'&&replaying);
 $('current').textContent=state.current?`${state.current.final?'Final':'Provisional'} · ${state.current.complete?'turn complete':'still speaking'}\n${state.current.text}`:'No unfinished words.';
 $('decisions').innerHTML=state.decisions.slice().reverse().map(d=>`<article class="item"><span class="badge">${esc(d.label)}</span> <span class="muted">${esc(d.reason)} · ${d.latency_ms} ms</span><p>${esc(d.text)}</p>${d.sensitive?'<p class="danger">This command may have lasting effects. Review before sending.</p>':''}${d.handoff.status==='pending'?`<div class="actions"><select id="target-${d.id}" aria-label="Assistant target">${config?.targets.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select><button data-dispatch="${d.id}" class="primary">Send command</button></div>`:''}<span class="muted">Handoff: ${esc(d.handoff.status)}</span>${d.handoff.receipt?`<pre>${esc(JSON.stringify(d.handoff.receipt,null,2))}</pre>`:''}<details><summary>Evidence & probabilities</summary><pre>${esc(JSON.stringify(d.judgment,null,2))}</pre></details></article>`).join('');
}
function send(turn,localEpoch){
 chain=chain.catch(()=>{}).then(async()=>{if(localEpoch!==epoch)return;accept(await api(`/v1/sessions/${state.id}/transcripts`,{...turn,epoch:state.epoch}));});
 return chain;
}
async function typed(text){
 abortLocal();const localEpoch=epoch;
 const result=await api('/v1/turns',{session_id:state.id,epoch:state.epoch,request_id:crypto.randomUUID(),text,source:'browser'});
 if(localEpoch===epoch){accept(await api(`/v1/sessions/${state.id}`));await loadCaptures();}
 return result;
}
$('connect').onclick=async()=>{try{await api('/v1/login',{token:$('token').value});$('token').value='';await connect();}catch(e){fail(e);}};
$('token').onkeydown=e=>{if(e.key==='Enter')$('connect').click();};
$('submit').onclick=()=>{if($('text').value.trim())typed($('text').value).catch(fail);};
$('pause').onclick=()=>control(state.paused?'resume':'pause').catch(fail);
$('cancel').onclick=()=>control('cancel').catch(fail);
$('discard').onclick=async()=>{try{await control('discard');$('text').value='';await loadCaptures();}catch(e){fail(e);}};
$('logout').onclick=async()=>{try{await control('pause');stream?.close();await api('/v1/logout',{});location.reload();}catch(e){fail(e);}};
$('speaking').onclick=()=>control(state.speaking?'speaking_off':'speaking_on').catch(fail);
$('samples').onclick=e=>{if(e.target.dataset.sample!==undefined)$('text').value=config.samples[Number(e.target.dataset.sample)].text;};
$('captures').onclick=async e=>{if(e.target.dataset.delete){try{await api(`/v1/captures/${e.target.dataset.delete}`,undefined,'DELETE');await loadCaptures();}catch(err){fail(err);}}};
$('decisions').onclick=async e=>{if(e.target.dataset.dispatch){const id=e.target.dataset.dispatch;e.target.disabled=true;try{await api(`/v1/sessions/${state.id}/decisions/${id}/dispatch`,{target_id:$('target-'+id).value,confirmed:true});accept(await api(`/v1/sessions/${state.id}`));}catch(err){fail(err);render();}}};
$('export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(captures,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='voice-gate-captures.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('replay').onclick=async()=>{
 try{await control('cancel');const localEpoch=epoch;replaying=true;render();
 for(const s of config.samples){if(localEpoch!==epoch)break;$('text').value=s.text;await api('/v1/turns',{session_id:state.id,epoch:state.epoch,request_id:crypto.randomUUID(),text:s.text,source:'synthetic replay'});}
 await loadCaptures();}catch(e){fail(e);}finally{replaying=false;render();}
};
$('mic').onclick=async()=>{
 try{await control('cancel');const localEpoch=epoch;const r=new Speech();recognition=r;r.lang='en-US';r.continuous=false;r.interimResults=true;
 const turn={id:crypto.randomUUID(),revision:0,text:'',final:false,complete:false,start_ms:clock(),end_ms:clock(),source:'browser microphone',timing:'browser session/result timestamps; approximate'};
 let allFinal=false;
 r.onstart=()=>{if(epoch!==localEpoch)return;$('micState').textContent='Microphone ON';$('finish').hidden=false;render();};
 r.onresult=event=>{if(epoch!==localEpoch)return;const parts=Array.from(event.results);turn.text=parts.map(p=>p[0].transcript).join(' ');turn.final=allFinal=parts.every(p=>p.isFinal);turn.revision++;turn.end_ms=clock();$('text').value=turn.text;send({...turn},localEpoch).catch(fail);};
 r.onerror=e=>{if(epoch!==localEpoch)return;fail(new Error(`Speech service: ${e.error}`));control('cancel').catch(fail);};
 r.onend=()=>{if(epoch!==localEpoch)return;recognition=null;$('micState').textContent='Microphone off';$('finish').hidden=true;if(allFinal&&turn.text){turn.revision++;send({...turn,final:true,complete:true},localEpoch).catch(fail);}else control('cancel').catch(fail);render();};
 r.start();}catch(e){abortLocal();fail(e);}
};
$('finish').onclick=()=>recognition?.stop();
window.addEventListener('pagehide',()=>{abortLocal();stream?.close();if(state)fetch(`/v1/sessions/${state.id}/controls`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'pause'}),keepalive:true}).catch(()=>{});});
connect().catch(()=>{$('login').hidden=false;$('workspace').hidden=true;});
