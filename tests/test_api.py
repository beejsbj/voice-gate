import asyncio
import httpx
import pytest
from voice_gate.api import create_app
from voice_gate.config import Settings
from voice_gate.judge import SAMPLES

@pytest.fixture
async def clients():
    settings=Settings(tokens={'a':'a'*32,'b':'b'*32},settle_ms=1,targets=[{'id':'health','kind':'health','url':'http://example.test/health'}])
    calls=[]
    async def dispatch(*args):calls.append(args);return {'ok':True,'http_status':200}
    app=create_app(settings,dispatcher=dispatch)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            yield c,calls

A={'Authorization':'Bearer '+'a'*32};B={'Authorization':'Bearer '+'b'*32}

async def test_auth_and_ownership(clients):
    c,_=clients
    assert (await c.get('/v1/config')).status_code==401
    r=await c.post('/v1/sessions',json={},headers=A);assert r.status_code==201;sid=r.json()['id']
    assert (await c.get('/v1/sessions/'+sid,headers=B)).status_code==404
    assert (await c.get('/v1/sessions',headers=B)).json()=={'sessions':[]}

async def test_simple_api_and_explicit_confirmation(clients):
    c,calls=clients
    r=await c.post('/v1/turns',json={'text':SAMPLES[-1][1],'request_id':'a'},headers=A)
    assert r.status_code==200;r=r.json();assert r['decision']['handoff']['status']=='pending';assert not calls
    path=f"/v1/sessions/{r['session_id']}/decisions/{r['decision']['id']}/dispatch"
    assert (await c.post(path,json={'target_id':'health','confirmed':False},headers=A)).status_code==422
    assert (await c.post(path,json={'target_id':'health','confirmed':True},headers=A)).json()['handoff']['status']=='succeeded'
    await c.post(path,json={'target_id':'health','confirmed':True},headers=A);assert len(calls)==1

async def test_retry_returns_same_decision(clients):
    c,_=clients;data={'text':SAMPLES[1][1],'request_id':'idempotent'}
    a=await c.post('/v1/turns',json=data,headers=A);b=await c.post('/v1/turns',json=data,headers=A)
    assert a.json()==b.json();assert len((await c.get('/v1/captures',headers=A)).json()['captures'])==1
    assert (await c.post('/v1/turns',json={**data,'text':'different'},headers=A)).status_code==409

async def test_login_cookie_origin_and_logout(clients):
    c,_=clients
    r=await c.post('/v1/login',json={'token':'a'*32});assert r.status_code==200
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert (await c.get('/v1/config')).status_code==200
    assert (await c.post('/v1/sessions',json={},headers={'Origin':'https://evil.example'})).status_code==403
    await c.post('/v1/logout',json={});assert (await c.get('/v1/config')).status_code==401

async def test_body_limit_and_invalid_timing(clients):
    c,_=clients
    assert (await c.post('/v1/turns',json={'text':'x'*4001},headers=A)).status_code==422
    assert (await c.post('/v1/turns',content=b'x'*32769,headers=A)).status_code==413
    assert (await c.post('/v1/turns',json={'text':'x','start_ms':10,'end_ms':0},headers=A)).status_code==422

async def test_pause_stale_epoch_and_capture_deletion(clients):
    c,_=clients
    s=(await c.post('/v1/sessions',json={},headers=A)).json();sid=s['id']
    await c.post(f'/v1/sessions/{sid}/controls',json={'action':'pause'},headers=A)
    r=await c.post(f'/v1/sessions/{sid}/transcripts',json={'epoch':s['epoch'],'text':'x'},headers=A);assert r.status_code==409
    r=(await c.post('/v1/turns',json={'text':SAMPLES[1][1]},headers=A)).json();cid=r['decision']['capture']['id']
    assert (await c.delete('/v1/captures/'+cid,headers=B)).status_code==404
    assert (await c.delete('/v1/captures/'+cid,headers=A)).status_code==200

async def test_discovery_does_not_expose_target_urls_or_tokens(clients):
    c,_=clients;r=(await c.get('/v1/config',headers=A)).json()
    assert 'example.test' not in str(r) and 'a'*32 not in str(r)
    assert r['targets']==[{'id':'health','name':'health','kind':'health'}]
    schema=(await c.get('/openapi.json')).json()
    assert '/v1/turns' in schema['paths'] and schema['components']['securitySchemes']['HTTPBearer']

def test_config_fail_closed():
    with pytest.raises(ValueError):Settings().validate()
    with pytest.raises(ValueError):Settings(tokens={'a':'short'}).validate()
    with pytest.raises(ValueError):Settings(tokens={'a':'a'*32},targets=[{'id':'x','kind':'webhook','url':'file:///etc/passwd'}]).validate()
