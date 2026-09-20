"""Transcript lifecycle, session ownership, exact capture, and explicit handoff."""
import asyncio
import copy
from collections import deque
import hashlib
import json
import time
from uuid import uuid4
from .judge import policy
from .targets import dispatch_target

class Problem(Exception):
    def __init__(self,status,detail):self.status,self.detail=status,detail

class Session:
    def __init__(self,owner,name):
        self.id=str(uuid4());self.owner=owner;self.name=name
        self.epoch=0;self.version=0;self.sequence=0
        self.paused=False;self.speaking=False
        self.current=None;self.task=None
        self.closed={};self.decisions=[];self.recent=[]
        self.events=deque(maxlen=96)
        self.updated=time.monotonic();self.notice='Ready'
    def supersede_proposals(self):
        for decision in self.decisions:
            if decision['handoff']['status']=='pending':decision['handoff']['status']='superseded'
    def invalidate(self):
        self.supersede_proposals()
        self.epoch+=1;self.version+=1
        if self.task and not self.task.done():self.task.cancel()
        if self.current:self.closed[self.current['id']]=self.current
        self.current=None

class Engine:
    def __init__(self,settings,judge,store,dispatcher=dispatch_target):
        self.settings=settings;self.judge=judge;self.store=store;self.dispatcher=dispatcher
        self.sessions={};self.requests={};self.budgets={};self.handoffs=set()

    def owned(self,owner,sid):
        s=self.sessions.get(sid)
        if not s or s.owner!=owner:raise Problem(404,'session_not_found')
        s.updated=time.monotonic();return s

    def create(self,owner,name='Untitled session'):
        self.expire()
        if len(self.sessions)>=self.settings.max_sessions:raise Problem(429,'session_limit')
        s=Session(owner,name);self.sessions[s.id]=s;self.changed(s);return s

    def expire(self):
        now=time.monotonic()
        for sid,s in list(self.sessions.items()):
            if now-s.updated>self.settings.session_ttl:
                s.invalidate();del self.sessions[sid]
        for key,record in list(self.requests.items()):
            if now-record['created']>self.settings.session_ttl:self.requests.pop(key,None)

    def consume_budget(self,owner):
        now=time.monotonic();q=self.budgets.setdefault(owner,deque())
        while q and q[0]<now-60:q.popleft()
        if len(q)>=self.settings.max_judgments_per_minute:raise Problem(429,'judgment_budget_exhausted')
        q.append(now)

    def snapshot(self,s):
        return copy.deepcopy({'id':s.id,'name':s.name,'epoch':s.epoch,'sequence':s.sequence,
            'paused':s.paused,'speaking':s.speaking,'current':s.current,'notice':s.notice,
            'decisions':s.decisions,'recent':s.recent,'captures':self.store.list(s.owner,s.id)})

    def changed(self,s):
        s.sequence+=1;s.updated=time.monotonic()
        s.events.append({'id':s.sequence,'event':'state'})

    def control(self,s,action):
        if action=='resume':s.paused=False;s.notice='Ready'
        elif action=='speaking_off':s.speaking=False;s.notice='Assistant finished'
        else:
            s.invalidate()
            if action=='pause':s.paused=True;s.notice='Paused; pending work cancelled'
            elif action=='cancel':s.notice='Current turn cancelled'
            elif action=='speaking_on':s.speaking=True;s.notice='Assistant speaking; input suppressed'
            elif action=='discard':
                s.paused=True;s.speaking=False;s.decisions=[];s.recent=[];s.closed={};s.events.clear()
                self.store.discard(s.owner,s.id);s.notice='Local session material discarded; paused'
                for record in self.requests.values():
                    if record['sid']==s.id:
                        record.pop('result',None);record['discarded']=True
        self.changed(s);return self.snapshot(s)

    def submit(self,s,event):
        turn=event.model_dump()
        if turn['epoch']!=s.epoch:raise Problem(409,'stale_session_epoch')
        if s.paused or s.speaking:raise Problem(409,'input_suppressed')
        tid=turn['id']
        if tid in s.closed:
            old=s.closed[tid]
            if all(old.get(k)==v for k,v in turn.items()):return self.snapshot(s)
            raise Problem(409,'turn_already_consumed')
        if len(s.closed)>=self.settings.max_turns:raise Problem(429,'session_turn_limit')
        if s.current and s.current['id']==tid:
            if turn['revision']<s.current['revision']:raise Problem(409,'stale_revision')
            if turn['revision']==s.current['revision']:
                if all(s.current.get(k)==v for k,v in turn.items()):return self.snapshot(s)
                raise Problem(409,'revision_conflict')
        elif s.current:s.closed[s.current['id']]=s.current
        if s.task and not s.task.done():s.task.cancel()
        # A new transcript also invalidates any older, unconfirmed handoff.
        s.supersede_proposals()
        s.version+=1
        turn['phase']='settling' if turn['final'] and turn['complete'] else 'provisional'
        s.current=turn;s.notice='Waiting for final turn' if turn['phase']=='provisional' else 'Waiting for cancellation window'
        s.task=asyncio.create_task(self.process(s,s.version,copy.deepcopy(turn)))
        self.changed(s);return self.snapshot(s)

    def valid(self,s,version):
        return self.sessions.get(s.id) is s and s.version==version and not s.paused and not s.speaking

    async def process(self,s,version,turn):
        if not turn['final'] or not turn['complete']:
            await asyncio.sleep(self.settings.stale_ms/1000)
            if self.valid(s,version):
                s.closed[turn['id']]=turn;s.current=None;s.notice='Stale partial discarded';self.changed(s)
            return
        await asyncio.sleep(self.settings.settle_ms/1000)
        if not self.valid(s,version):return
        s.current['phase']='judging';self.changed(s)
        started=time.monotonic()
        try:
            self.consume_budget(s.owner)
            result=await self.judge({'current':{k:v for k,v in turn.items() if k!='phase'},'recent':s.recent[-6:]})
            label,reason,sensitive=policy(result,self.settings.threshold)
        except asyncio.CancelledError:raise
        except Problem as exc:
            result={'error':exc.detail};label,reason,sensitive='uncertain',exc.detail,False
        except Exception:
            result={'error':'provider_unavailable_or_invalid_response'};label,reason,sensitive='uncertain','provider_error',False
        if not self.valid(s,version):return
        decision={'id':str(uuid4()),'session_id':s.id,'turn_id':turn['id'],'revision':turn['revision'],
            'text':turn['text'],'start_ms':turn['start_ms'],'end_ms':turn['end_ms'],
            'source':turn['source'],'timing':turn['timing'],'label':label,'reason':reason,'sensitive':sensitive,
            'judgment':result,'latency_ms':round((time.monotonic()-started)*1000),
            'created_at':time.time(),'version':version,
            'handoff':{'status':'pending' if label=='command' and self.settings.targets else 'unsupported' if label=='command' else 'not_applicable'}}
        if label=='capture':
            try:decision['capture']=self.store.add(s.owner,s.id,turn)
            except Exception:
                decision.update(label='uncertain',reason='capture_storage_unavailable')
        s.closed[turn['id']]=turn;s.current=None
        s.recent=(s.recent+[{'text':turn['text'],'outcome':decision['label']}])[-6:]
        s.decisions=(s.decisions+[decision])[-64:]
        s.notice={'capture':'Exact passage retained','command':'Command ready for explicit handoff','uncertain':'Uncertain; no action taken'}.get(decision['label'],'No action needed')
        record=self.requests.get((s.owner,turn['id']))
        if record is not None and not record.get('discarded'):
            record['result']={'session_id':s.id,'decision':copy.deepcopy(decision)}
        self.changed(s)

    async def turn(self,owner,request):
        self.expire()
        key=(owner,request.request_id)
        fingerprint=hashlib.sha256(json.dumps(request.model_dump(),sort_keys=True).encode()).hexdigest()
        record=self.requests.get(key)
        if record:
            if record['fingerprint']!=fingerprint:raise Problem(409,'request_id_conflict')
            if record.get('discarded'):raise Problem(409,'request_discarded')
            if record.get('result'):return copy.deepcopy(record['result'])
            s=self.owned(owner,record['sid'])
        else:
            if len(self.requests)>=2048:raise Problem(429,'idempotency_capacity')
            if request.session_id and request.epoch is None:raise Problem(422,'epoch_required_for_session')
            s=self.owned(owner,request.session_id) if request.session_id else self.create(owner,'API turn')
            from .models import Transcript
            self.submit(s,Transcript(id=request.request_id,epoch=request.epoch if request.session_id else s.epoch,text=request.text,source=request.source,start_ms=request.start_ms,end_ms=request.end_ms))
            self.requests[key]={'sid':s.id,'fingerprint':fingerprint,'created':time.monotonic()}
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            for d in s.decisions:
                if d['turn_id']==request.request_id:return {'session_id':s.id,'decision':copy.deepcopy(d)}
            if s.current is None or s.current['id']!=request.request_id:raise Problem(409,'turn_cancelled_or_superseded')
            await asyncio.sleep(.02)
        raise Problem(504,'decision_timeout')

    async def dispatch(self,s,did,target_id):
        d=next((x for x in s.decisions if x['id']==did),None)
        if not d:raise Problem(404,'decision_not_found')
        status=d['handoff']['status']
        if status in {'running','succeeded','failed','cancelled_after_dispatch'}:
            if d['handoff'].get('target_id')!=target_id:raise Problem(409,'decision_already_dispatched')
            return copy.deepcopy(d)
        if d['label']!='command' or not self.valid(s,d['version']):raise Problem(409,'decision_not_actionable')
        target=next((t for t in self.settings.targets if t['id']==target_id),None)
        if not target:raise Problem(404,'target_not_found')
        self.consume_budget(s.owner)
        # Claim before await: concurrent confirmations cannot dispatch twice.
        d['handoff']={'status':'running','target_id':target_id};self.changed(s)
        version=s.version
        task=asyncio.current_task();self.handoffs.add(task)
        try:reply=await self.dispatcher(target,copy.deepcopy(d),s.id)
        except asyncio.CancelledError:raise
        except Exception:reply={'ok':False,'error':'target_unavailable'}
        finally:self.handoffs.discard(task)
        if not self.valid(s,version):
            d['handoff']={'status':'cancelled_after_dispatch','target_id':target_id}
            # Discard removed this object from live state; never reinsert it.
            if d in s.decisions:self.changed(s)
        else:
            d['handoff']={'status':'succeeded' if reply.get('ok') else 'failed','target_id':target_id,'receipt':reply};self.changed(s)
        return copy.deepcopy(d)

    async def close(self):
        tasks=[]
        for s in self.sessions.values():
            if s.task:tasks.append(s.task)
            s.invalidate()
        for task in self.handoffs:task.cancel();tasks.append(task)
        await asyncio.gather(*tasks,return_exceptions=True)
        await self.judge.close();self.store.close()
