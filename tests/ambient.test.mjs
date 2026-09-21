import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AmbientListener} from '../voice_gate/web/ambient.js';
const wait=ms=>new Promise(r=>setTimeout(r,ms));
function setup(){
 const calls=[],turns=[],states=[],errors=[];
 class Speech{constructor(){calls.push(this);}start(){this.onstart?.();}abort(){this.onend?.();}}
 const a=new AmbientListener({Speech,onTurn:t=>turns.push(t),onState:s=>states.push(s),onError:e=>errors.push(e.message),quietMs:15,restartMs:5});
 const result=(r,parts)=>r.onresult({results:parts.map(([text,final])=>Object.assign([{transcript:text}],{isFinal:final}))});
 return {a,calls,turns,states,errors,result};
}
test('continuous final segments submit once, interim never submits, restart stays armed',async()=>{
 const {a,calls,turns,result}=setup();a.start();const r=calls[0];assert.equal(r.continuous,true);
 result(r,[['first',false]]);await wait(30);assert.equal(turns.length,0);
 result(r,[['first',true]]);await wait(30);assert.equal(turns[0].text,'first');
 result(r,[['first',true]]);await wait(30);assert.equal(turns.length,1);
 result(r,[['first',true],['second',true]]);await wait(30);assert.equal(turns[1].text,'second');
 r.onend();await wait(15);assert.equal(calls.length,2);assert(a.active);
 result(calls[1],[['third',true]]);await wait(30);assert.equal(turns[2].text,'third');a.stop();
});
test('stop cancels quiet/restart timers and ignores obsolete callbacks',async()=>{
 const {a,calls,turns,result}=setup();a.start();const r=calls[0];result(r,[['pending',true]]);a.stop();
 result(r,[['late',true]]);r.onend();await wait(30);assert.equal(turns.length,0);assert.equal(calls.length,1);
 a.start();calls[1].onend();a.stop();await wait(20);assert.equal(calls.length,2);
});
test('permission/network errors stop without restart loops',async()=>{
 const {a,calls,errors}=setup();a.start();calls[0].onerror({error:'not-allowed'});await wait(20);
 assert.equal(a.active,false);assert.equal(calls.length,1);assert.match(errors[0],/not-allowed/);
});
test('unfinished text at service end is discarded, final text flushes',async()=>{
 const {a,calls,turns,result}=setup();a.start();result(calls[0],[['partial',false]]);calls[0].onend();await wait(15);assert.equal(turns.length,0);
 result(calls[1],[['done',true]]);calls[1].onend();assert.equal(turns[0].text,'done');a.stop();
});

test('service end preserves final prefix before an unfinished tail',()=>{
 const {a,calls,turns,result}=setup();a.start();result(calls[0],[['Keep this thought.',true],['unfinished',false]]);calls[0].onend();
 assert.deepEqual(turns.map(t=>t.text),['Keep this thought.']);a.stop();
});

test('rapid service-ending loops eventually stop visibly',async()=>{
 const {a,calls,errors}=setup();a.start();
 for(let i=0;i<13;i++){calls.at(-1).onend();await wait(10);}
 assert.equal(a.active,false);assert.match(errors[0],/disconnecting/);a.stop();
});
