import httpx
import pytest
import voice_gate.targets as module

@pytest.fixture
def decision():return {'id':'decision','text':'Exact original request.','label':'command','sensitive':False,'turn_id':'turn','start_ms':12,'end_ms':34}

async def test_targets_preserve_payload_and_do_not_follow_redirects(monkeypatch,decision):
    calls=[];original=httpx.AsyncClient
    async def handler(request):
        calls.append(request)
        if request.url.path=='/health':return httpx.Response(200,json={'status':'ok','platform':'assistant','private':'excluded'})
        if request.url.path=='/redirect':return httpx.Response(302,headers={'Location':'https://other.example'})
        return httpx.Response(200,json={'choices':[{'message':{'content':'Acknowledged'}}]})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    result=await module.dispatch_target({'kind':'health','url':'https://service.test/health'},decision,'session')
    assert result['result']=={'status':'ok','platform':'assistant'}
    result=await module.dispatch_target({'kind':'openai','url':'https://service.test/chat','model':'test'},decision,'session')
    import json
    assert json.loads(calls[-1].content)['messages']==[{'role':'user','content':decision['text']}]
    assert calls[-1].headers['Idempotency-Key']=='decision'
    assert result['result']['text']=='Acknowledged'
    result=await module.dispatch_target({'kind':'health','url':'https://service.test/redirect'},decision,'session')
    assert result['error']=='target_rejected' and len(calls)==3

async def test_target_stream_stops_before_unbounded_read(monkeypatch,decision):
    consumed=[];original=httpx.AsyncClient
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(1000):consumed.append(1);yield b'x'*8192
    async def handler(request):return httpx.Response(200,stream=Stream())
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    result=await module.dispatch_target({'kind':'webhook','url':'https://service.test'},decision,'session')
    assert result['error']=='target_response_too_large' and len(consumed)<10
