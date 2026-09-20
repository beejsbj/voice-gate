import asyncio
import copy
import pytest
from voice_gate.config import Settings
from voice_gate.engine import Engine, Problem
from voice_gate.judge import Judge, policy, SAMPLES
from voice_gate.models import Transcript, Turn
from voice_gate.store import CaptureStore

@pytest.fixture
async def engine():
    settings=Settings(tokens={'one':'a'*32},settle_ms=1,stale_ms=15,targets=[{'id':'health','kind':'health','url':'http://example.test/health'}])
    e=Engine(settings,Judge(settings),CaptureStore())
    yield e
    await e.close()

def event(s,text=SAMPLES[-1][1],**kwargs):
    return Transcript(epoch=s.epoch,text=text,id='turn',**kwargs)

async def wait(s):
    if s.task:await s.task

async def test_five_labels_and_literal_capture(engine):
    for label,text in SAMPLES:
        response=await engine.turn('one',Turn(text=text))
        assert response['decision']['label']==label
        if label=='capture':assert response['decision']['capture']['text']==text
    assert len(engine.store.list('one'))==1

async def test_completed_turn_idempotency_and_conflict(engine):
    request=Turn(text=SAMPLES[-1][1],request_id='repeat')
    a,b=await asyncio.gather(engine.turn('one',request),engine.turn('one',request))
    assert a==b
    assert len(engine.sessions)==1
    with pytest.raises(Problem) as e:await engine.turn('one',request.model_copy(update={'text':'different'}))
    assert e.value.status==409

async def test_partial_final_and_endpointing(engine):
    s=engine.create('one');engine.submit(s,event(s,final=True,complete=False));await wait(s)
    assert not s.decisions and s.current is None
    with pytest.raises(Problem):engine.submit(s,event(s,revision=2))

async def test_revision_and_new_turn_supersession(engine):
    s=engine.create('one');engine.submit(s,event(s));engine.submit(s,event(s,SAMPLES[3][1],revision=2))
    with pytest.raises(Problem):engine.submit(s,event(s,revision=1))
    await wait(s);assert s.decisions[-1]['label']=='no_action'

async def test_cancel_ignores_provider_that_finishes_late(engine):
    entered=asyncio.Event();release=asyncio.Event();original=engine.judge
    class StubbornJudge:
        async def __call__(self,data):
            entered.set()
            try:await release.wait()
            except asyncio.CancelledError:pass
            return await original(data)
        async def close(self):pass
    engine.judge=StubbornJudge()
    s=engine.create('one');engine.submit(s,event(s));task=s.task;await entered.wait()
    engine.control(s,'cancel');release.set();await task
    assert not s.decisions and not engine.store.list('one')

@pytest.mark.parametrize('control',['pause','discard','speaking_on'])
async def test_controls_block_and_invalidate(engine,control):
    s=engine.create('one');old=event(s);engine.submit(s,old);engine.control(s,control)
    await asyncio.sleep(.01)
    assert not s.decisions
    with pytest.raises(Problem):engine.submit(s,old)
    with pytest.raises(Problem):engine.submit(s,event(s))

async def test_one_confirmed_handoff_despite_concurrent_retries(engine):
    calls=[];started=asyncio.Event();release=asyncio.Event()
    async def dispatch(target,decision,sid):calls.append(decision['text']);started.set();await release.wait();return {'ok':True}
    engine.dispatcher=dispatch
    r=await engine.turn('one',Turn(text=SAMPLES[-1][1]));s=engine.owned('one',r['session_id']);d=r['decision']
    task=asyncio.create_task(engine.dispatch(s,d['id'],'health'));await started.wait()
    again=await engine.dispatch(s,d['id'],'health');assert again['handoff']['status']=='running'
    release.set();done=await task;assert done['handoff']['status']=='succeeded'
    await engine.dispatch(s,d['id'],'health');assert calls==[SAMPLES[-1][1]]

async def test_obsolete_decision_cannot_dispatch(engine):
    r=await engine.turn('one',Turn(text=SAMPLES[-1][1]));s=engine.owned('one',r['session_id'])
    engine.submit(s,Transcript(epoch=s.epoch,text='new partial',final=False,complete=False))
    with pytest.raises(Problem) as e:await engine.dispatch(s,r['decision']['id'],'health')
    assert e.value.status==409

async def test_discard_during_dispatch_cannot_restore_text(engine):
    started=asyncio.Event();release=asyncio.Event()
    async def dispatch(*args):started.set();await release.wait();return {'ok':True,'private':'stale response'}
    engine.dispatcher=dispatch
    r=await engine.turn('one',Turn(text=SAMPLES[-1][1]));s=engine.owned('one',r['session_id'])
    task=asyncio.create_task(engine.dispatch(s,r['decision']['id'],'health'));await started.wait()
    engine.control(s,'discard');release.set();result=await task
    assert not s.decisions and not s.recent
    assert result['handoff']['status']=='cancelled_after_dispatch'
    assert 'stale response' not in str(result)

async def test_unknown_target_and_ordinary_not_dispatchable(engine):
    r=await engine.turn('one',Turn(text=SAMPLES[0][1]));s=engine.owned('one',r['session_id'])
    with pytest.raises(Problem):await engine.dispatch(s,r['decision']['id'],'health')
    r=await engine.turn('one',Turn(text=SAMPLES[-1][1]));s=engine.owned('one',r['session_id'])
    with pytest.raises(Problem):await engine.dispatch(s,r['decision']['id'],'not-configured')

async def test_rate_limit_is_visible_uncertainty(engine):
    engine.settings.max_judgments_per_minute=1
    await engine.turn('one',Turn(text=SAMPLES[0][1]))
    r=await engine.turn('one',Turn(text=SAMPLES[1][1]))
    assert r['decision']['label']=='uncertain' and r['decision']['reason']=='judgment_budget_exhausted'
    assert not engine.store.list('one')

async def test_provider_error_never_captures_or_dispatches(engine):
    class Failed:
        async def __call__(self,_):raise ValueError('secret provider detail')
        async def close(self):pass
    engine.judge=Failed()
    r=await engine.turn('one',Turn(text=SAMPLES[1][1]))
    assert r['decision']['reason']=='provider_error'
    assert 'secret' not in str(r)

async def test_policy_uses_relevant_independent_probabilities(engine):
    result=await engine.judge({'current':{'text':SAMPLES[-1][1]}})
    result['answers']['addressed']['noul']=.3
    assert policy(result,.7)[:2]==('uncertain','unclear_addressee')
    result['answers']['addressed']['noul']=1;result['answers']['complete']['noul']=.2
    assert policy(result,.7)[:2]==('uncertain','incomplete_meaning')
    result['answers']['cancelled']['noul']=.9
    assert policy(result,.7)[:2]==('no_action','cancelled')

async def test_cross_owner_isolation(engine):
    r=await engine.turn('one',Turn(text=SAMPLES[1][1]))
    with pytest.raises(Problem):engine.owned('two',r['session_id'])
    assert engine.store.list('two')==[]
    assert not engine.store.delete('two',r['decision']['capture']['id'])

def test_capture_survives_restart_and_preserves_whitespace(tmp_path):
    path=str(tmp_path/'captures.sqlite');store=CaptureStore(path)
    text='  Exact\ntext — unchanged. '
    capture=store.add('one','s',Transcript(epoch=0,id='t',text=text,start_ms=123,end_ms=456).model_dump())
    store.close();store=CaptureStore(path)
    assert store.list('one')[0]['text']==text
    assert store.list('one')[0]['start_ms']==123
    store.discard('two','s');assert len(store.list('one'))==1
    store.delete('one',capture['id']);assert not store.list('one');store.close()

async def test_session_expiry_preserves_captures_but_cancels_pending(engine):
    r=await engine.turn('one',Turn(text=SAMPLES[1][1]));s=engine.owned('one',r['session_id'])
    s.updated-=engine.settings.session_ttl+1;engine.expire()
    assert s.id not in engine.sessions and len(engine.store.list('one'))==1

async def test_delayed_simple_turn_cannot_cross_cancel_epoch(engine):
    s=engine.create('one');request=Turn(text=SAMPLES[1][1],session_id=s.id,epoch=s.epoch)
    engine.control(s,'cancel')
    with pytest.raises(Problem) as error:await engine.turn('one',request)
    assert error.value.detail=='stale_session_epoch'
    assert not engine.store.list('one')
    with pytest.raises(Problem) as error:await engine.turn('one',Turn(text='x',session_id=s.id))
    assert error.value.detail=='epoch_required_for_session'

async def test_idempotent_result_survives_visible_history_eviction(engine):
    first=await engine.turn('one',Turn(text=SAMPLES[1][1],request_id='original'))
    s=engine.owned('one',first['session_id']);s.decisions=[]
    assert await engine.turn('one',Turn(text=SAMPLES[1][1],request_id='original'))==first
    engine.control(s,'discard')
    with pytest.raises(Problem) as error:await engine.turn('one',Turn(text=SAMPLES[1][1],request_id='original'))
    assert error.value.detail=='request_discarded'
    assert not engine.store.list('one')
