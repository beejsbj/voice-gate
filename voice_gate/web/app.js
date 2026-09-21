import {AmbientListener} from './ambient.js';
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let config,state,captures=[],stream,ambient=null,micStarting=false,queued=0,epoch=0,chain=Promise.resolve(),replaying=false;
const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;
async function api(path,data,method){
 const response=await fetch(path,{method:method||(data===undefined?'GET':'POST'),headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
 const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:`Request failed (${response.status})`);return result;
}
const fail=e=>{$('error').textContent=e.message;};
function accept(s){if(!state||state.id!==s.id||s.sequence>=state.sequence){state=s;render();}}
function abortLocal(){epoch++;replaying=false;ambient?.stop();render();}
async function control(action){abortLocal();accept(await api(`/v1/sessions/${state.id}/controls`,{action}));}
async function connect(){
 config=await api('/v1/config');
 accept(await api('/v1/sessions',{name:'Browser ambient'}));
 $('login').hidden=true;$('workspace').hidden=false;$('error').textContent='';
 $('provider').textContent=config.provider==='fixture'?'FIXTURE · synthetic judgments':`LIVE · ${config.provider}`;
 $('storage').textContent=config.persistent_captures?'Exact passages are saved on this server until you delete them.':'Captures are in server memory; they disappear on restart.';
 $('samples').innerHTML=config.samples.map((s,i)=>`<button data-sample="${i}">${esc(s.label)}</button>`).join('');
 $('speechInfo').textContent=Speech?'Start once, then speak freely. Listening continues between utterances, without a wake word. Keep this page open; browser or device sleep can interrupt it.':'This browser has no Web Speech support. Type, paste, or use your keyboard’s dictation to submit text.';
 if(stream)stream.close();stream=new EventSource(`/v1/sessions/${state.id}/events`);
 stream.addEventListener('state',e=>{const update=JSON.parse(e.data);if(update.id===state.id){accept(update);loadCaptures().catch(fail);}});
 stream.onerror=()=>{abortLocal();$('phase').textContent='Engine disconnected — listening stopped';};
 await loadCaptures();render();
}
async function loadCaptures(){captures=(await api('/v1/captures')).captures;
 $('captures').innerHTML=captures.slice(0,50).map(c=>`<article class="item"><span class="badge">${esc(c.source)} · ${c.start_ms}–${c.end_ms} ms</span><p>${esc(c.text)}</p><div class="section-title"><span class="muted">${esc(c.timing)}</span><button class="danger" data-delete="${esc(c.id)}">Delete</button></div></article>`).join('');
}
function render(){
 if(!state)return; if((state.paused||state.speaking)&&ambient?.active)abortLocal();
 $('notice').textContent=state.notice;$('phase').textContent=state.paused?'Paused':state.speaking?'Assistant speaking':state.current?.phase||'Ready';
 $('pause').textContent=state.paused?'Resume':'Pause';$('speaking').textContent=`Assistant speaking: ${state.speaking?'on':'off'}`;
 for(const id of ['submit','mic','replay'])$(id).disabled=(state.paused&&id!=='mic')||state.speaking||(ambient?.active&&id!=='mic')||(id==='mic'&&(!Speech||micStarting))||(id==='replay'&&replaying);
 $('mic').textContent=ambient?.active?'Stop listening':'Start ambient listening';
 $('current').textContent=state.current?`${state.current.final?'Final':'Provisional'} · ${state.current.complete?'turn complete':'still speaking'}\n${state.current.text}`:'No unfinished words.';
 $('decisions').innerHTML=state.decisions.slice().reverse().map(d=>`<article class="item"><span class="badge">${esc(d.label)}</span> <span class="muted">${esc(d.reason)} · ${d.latency_ms} ms</span><p>${esc(d.text)}</p>${d.sensitive?'<p class="danger">This command may have lasting effects. Review before sending.</p>':''}${d.handoff.status==='pending'?`<div class="actions"><select id="target-${d.id}" aria-label="Assistant target">${config?.targets.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('')}</select><button data-dispatch="${d.id}" class="primary">Send command</button></div>`:''}<span class="muted">Handoff: ${esc(d.handoff.status)}</span>${d.handoff.receipt?`<pre>${esc(JSON.stringify(d.handoff.receipt,null,2))}</pre>`:''}<details><summary>Evidence & probabilities</summary><pre>${esc(JSON.stringify(d.judgment,null,2))}</pre></details></article>`).join('');
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
async function ambientTurn(turn,context){
 const localEpoch=epoch;
 if(queued>=8){await control('pause');fail(new Error('Engine is falling behind. Listening paused; unsent speech discarded.'));return;}
 queued++;
 chain=chain.catch(()=>{}).then(async()=>{
  try{
   if(localEpoch!==epoch)return;
   if(state.id===context.id&&state.turns_used>=config.limits.turns_per_session){
    const next=await api('/v1/sessions',{name:'Browser ambient'});if(localEpoch!==epoch)return;
    context.id=next.id;context.epoch=next.epoch;accept(next);
    stream?.close();stream=new EventSource(`/v1/sessions/${next.id}/events`);
    stream.addEventListener('state',e=>{const update=JSON.parse(e.data);if(update.id===state.id){accept(update);loadCaptures().catch(fail);}});
    stream.onerror=()=>{abortLocal();fail(new Error('Engine disconnected — listening stopped'));};
   }
   const result=await api('/v1/turns',{...turn,session_id:context.id,epoch:context.epoch,request_id:crypto.randomUUID(),source:'ambient browser microphone'});
   if(localEpoch!==epoch)return;
   accept(await api(`/v1/sessions/${context.id}`));await loadCaptures();
   if(['provider_error','judgment_budget_exhausted','capture_storage_unavailable'].includes(result.decision.reason)){
    await control('pause');fail(new Error('Listening paused: the engine could not process speech ('+result.decision.reason+'). Check the provider/account, then resume and start again.'));
   }
  }catch(e){if(localEpoch===epoch){abortLocal();fail(e);api(`/v1/sessions/${context.id}/controls`,{action:'pause'}).then(accept).catch(fail);}}
  finally{queued--;}
 });
}
$('mic').onclick=async()=>{
 if(micStarting)return;micStarting=true;render();
 try{
  if(ambient?.active){await control('pause');return;}
  const expectedEpoch=epoch+1;await control(state.paused?'resume':'cancel');
  if(epoch!==expectedEpoch||state.paused||state.speaking)return;
  $('error').textContent='';
  const context={id:state.id,epoch:state.epoch};
  ambient=new AmbientListener({Speech,onTurn:turn=>ambientTurn(turn,context).catch(fail),
   onPreview:text=>{$('text').value=text;},
   onState:phase=>{$('micState').textContent={off:'Microphone off',starting:'Starting microphone…',listening:'Listening · microphone ON',reconnecting:'Reconnecting microphone…'}[phase];render();},
   onError:e=>{epoch++;fail(e);api(`/v1/sessions/${state.id}/controls`,{action:'pause'}).then(accept).catch(fail);}});
  ambient.start();
 }catch(e){abortLocal();fail(e);}finally{micStarting=false;render();}
};
window.addEventListener('pagehide',()=>{abortLocal();stream?.close();if(state)fetch(`/v1/sessions/${state.id}/controls`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'pause'}),keepalive:true}).catch(()=>{});});
connect().catch(()=>{$('login').hidden=false;$('workspace').hidden=true;});
