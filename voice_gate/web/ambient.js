// One explicit start arms a continuous speech session. Audio remains with Web Speech.
export class AmbientListener {
 constructor({Speech,onTurn,onState,onError,onPreview=()=>{},quietMs=900,restartMs=300}) {
  Object.assign(this,{Speech,onTurn,onState,onError,onPreview,quietMs,restartMs});
  this.active=false;this.generation=0;this.recognition=null;this.restarts=[];
 }
 start(){if(this.active)return;this.active=true;this.generation++;this.restarts=[];this.open(this.generation);}
 stop(){this.active=false;this.generation++;clearTimeout(this.quiet);clearTimeout(this.restart);const r=this.recognition;this.recognition=null;r?.abort();this.onPreview('');this.onState('off');}
 error(message){this.stop();this.onError(new Error(message));}
 open(generation){
  if(!this.active||generation!==this.generation)return;
  const r=new this.Speech();this.recognition=r;this.onState('starting');
  r.lang='en-US';r.continuous=true;r.interimResults=true;
  let consumed=0,results=[],started=performance.now();
  const valid=()=>this.active&&generation===this.generation&&this.recognition===r;
  const flush=(ending=false)=>{
   if(!valid())return;
   let pending=results.slice(consumed);if(ending){const firstInterim=pending.findIndex(x=>!x.final);if(firstInterim>=0)pending=pending.slice(0,firstInterim);}
   if(!pending.length||pending.some(x=>!x.final))return;
   const text=pending.map(x=>x.text).join(' ').trim();consumed+=pending.length;
   if(text){this.onTurn({text,start_ms:Math.round(started),end_ms:Math.round(performance.now())});started=performance.now();}
   this.onPreview('');
  };
  r.onstart=()=>{if(valid())this.onState('listening');};
  r.onresult=event=>{
   if(!valid())return;clearTimeout(this.quiet);
   results=Array.from(event.results,x=>({text:x[0].transcript,final:x.isFinal}));
   const pending=results.slice(consumed);this.onPreview(pending.map(x=>x.text).join(' '));
   if(pending.reduce((n,x)=>n+x.text.length,0)>4000){this.error('Speech passage too long. Listening stopped; start again after a pause.');return;}
   if(pending.length&&pending.every(x=>x.final))this.quiet=setTimeout(flush,this.quietMs);
  };
  r.onerror=event=>{if(valid()&&event.error!=='no-speech')this.error(`Listening stopped: speech service ${event.error}. Check microphone access, then start again.`);};
  r.onend=()=>{
   if(!valid())return;clearTimeout(this.quiet);flush(true);this.recognition=null;this.onPreview('');
   if(!this.active)return;
   const now=Date.now();this.restarts=this.restarts.filter(t=>now-t<60000);this.restarts.push(now);
   if(this.restarts.length>12){this.error('Speech service keeps disconnecting. Listening stopped; start again when it is available.');return;}
   this.onState('reconnecting');this.restart=setTimeout(()=>this.open(generation),this.restartMs);
  };
  try{r.start();}catch{this.error('Could not start microphone. Check browser microphone permission.');}
 }
}
