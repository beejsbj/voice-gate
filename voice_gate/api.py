import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
import secrets
import time
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.cors import CORSMiddleware
from .config import Settings
from .engine import Engine, Problem
from .judge import Judge, SAMPLES
from .models import SessionCreate, Transcript, Turn, Control, Dispatch, Login
from .store import CaptureStore

WEB=Path(__file__).parent/'web'
bearer=HTTPBearer(auto_error=False)

def create_app(settings=None,judge=None,store=None,dispatcher=None):
    settings=settings or Settings.from_env()
    settings.validate()
    engine=Engine(settings,judge or Judge(settings),store or CaptureStore(settings.database),**({'dispatcher':dispatcher} if dispatcher else {}))
    logins={};login_attempts=defaultdict(deque)
    @asynccontextmanager
    async def lifespan(app):
        async def sweep():
            while True:
                await asyncio.sleep(30);engine.expire()
                now=time.monotonic()
                for key,(_,expiry) in list(logins.items()):
                    if expiry<now:logins.pop(key,None)
        task=asyncio.create_task(sweep())
        yield
        task.cancel();await asyncio.gather(task,return_exceptions=True);await engine.close()
    app=FastAPI(title='Voice Gate',version='0.1.0',description='Authenticated, transcript-first Jev engine. Audio stays with the client. Commands require an explicit dispatch.',lifespan=lifespan)
    app.state.engine=engine
    if settings.origins:
        app.add_middleware(CORSMiddleware,allow_origins=settings.origins,allow_credentials=True,allow_methods=['GET','POST','DELETE'],allow_headers=['Authorization','Content-Type','Last-Event-ID'])

    @app.middleware('http')
    async def boundary(request,call_next):
        origin=request.headers.get('origin')
        if origin:
            same_origin=f'{request.url.scheme}://{request.headers.get("host","")}'
            if origin.rstrip('/') not in {same_origin,*settings.origins}:
                return JSONResponse({'detail':'origin_not_allowed'},status_code=403)
        if request.method in {'POST','PUT','PATCH'}:
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>32768:return JSONResponse({'detail':'body_too_large'},status_code=413)
            request._body=bytes(body)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        if request.url.path in {'/','/app.js','/ambient.js','/style.css'}:
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(Problem)
    async def problem_handler(request,exc):return JSONResponse({'detail':exc.detail},status_code=exc.status)

    def match_token(token):
        found=None
        for owner,secret in settings.tokens.items():
            if hmac.compare_digest(token.encode(),secret.encode()):found=owner
        return found

    async def owner(request:Request,credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
        if credentials:
            name=match_token(credentials.credentials)
            if name:return name
        elif request.cookies.get('voice_gate_session'):
            cookie=request.cookies['voice_gate_session'];login=logins.get(cookie)
            if login and login[1]>time.monotonic():return login[0]
        raise HTTPException(401,'authentication_required',headers={'WWW-Authenticate':'Bearer'})

    @app.get('/healthz',tags=['operations'])
    async def health():return {'status':'ok','service':'voice-gate','version':'0.1.0'}

    @app.post('/v1/login',tags=['browser'])
    async def login(data:Login,request:Request):
        host=request.client.host if request.client else 'unknown';now=time.monotonic()
        for key,q in list(login_attempts.items()):
            while q and q[0]<now-60:q.popleft()
            if not q:login_attempts.pop(key,None)
        attempts=login_attempts[host]
        if len(attempts)>=10 or len(login_attempts)>512:raise Problem(429,'login_rate_limit')
        attempts.append(now)
        name=match_token(data.token)
        if not name:raise HTTPException(401,'invalid_token')
        if len(logins)>=128:raise Problem(429,'browser_session_limit')
        sid=secrets.token_urlsafe(32);logins[sid]=(name,now+12*3600)
        response=JSONResponse({'client':name})
        response.set_cookie('voice_gate_session',sid,httponly=True,secure=request.url.scheme=='https',samesite='strict',max_age=12*3600,path='/')
        return response

    @app.post('/v1/logout',tags=['browser'])
    async def logout(request:Request):
        logins.pop(request.cookies.get('voice_gate_session'),None)
        response=JSONResponse({'ok':True});response.delete_cookie('voice_gate_session');return response

    @app.get('/v1/config',tags=['discovery'])
    async def config(client=Depends(owner)):
        return {'version':'1','client':client,'provider':settings.provider,'targets':settings.public_targets(),
            'samples':[{'label':k,'text':v} for k,v in SAMPLES],'settle_ms':settings.settle_ms,
            'stale_ms':settings.stale_ms,'threshold':settings.threshold,'persistent_captures':settings.database!=':memory:',
            'limits':{'session_ttl_seconds':settings.session_ttl,'turns_per_session':settings.max_turns,'judgments_per_minute':settings.max_judgments_per_minute}}

    @app.post('/v1/turns',tags=['simple integration'])
    async def turn(data:Turn,client=Depends(owner)):
        """Submit a completed transcript and wait for its decision. Reuse request_id for retries; never dispatches a target."""
        if data.end_ms<data.start_ms:raise Problem(422,'invalid_timing')
        return await engine.turn(client,data)

    @app.post('/v1/sessions',status_code=201,tags=['streaming sessions'])
    async def create(data:SessionCreate,client=Depends(owner)):
        return engine.snapshot(engine.create(client,data.name))

    @app.get('/v1/sessions',tags=['streaming sessions'])
    async def sessions(client=Depends(owner)):
        engine.expire()
        return {'sessions':[{'id':s.id,'name':s.name,'paused':s.paused,'epoch':s.epoch} for s in engine.sessions.values() if s.owner==client]}

    @app.get('/v1/sessions/{sid}',tags=['streaming sessions'])
    async def state(sid:str,client=Depends(owner)):return engine.snapshot(engine.owned(client,sid))

    @app.delete('/v1/sessions/{sid}',tags=['streaming sessions'])
    async def delete_session(sid:str,client=Depends(owner)):
        s=engine.owned(client,sid);s.invalidate();del engine.sessions[sid]
        return {'ok':True,'captures_preserved':True}

    @app.post('/v1/sessions/{sid}/transcripts',status_code=202,tags=['streaming sessions'])
    async def transcript(sid:str,data:Transcript,client=Depends(owner)):
        return engine.submit(engine.owned(client,sid),data)

    @app.post('/v1/sessions/{sid}/controls',tags=['streaming sessions'])
    async def control(sid:str,data:Control,client=Depends(owner)):
        return engine.control(engine.owned(client,sid),data.action)

    @app.get('/v1/sessions/{sid}/events',tags=['streaming sessions'])
    async def events(sid:str,request:Request,client=Depends(owner)):
        """SSE state snapshots. Reconnect receives current state, not a replay of side effects."""
        s=engine.owned(client,sid)
        async def stream():
            last=-1
            while engine.sessions.get(s.id) is s and not await request.is_disconnected():
                if last!=s.sequence:
                    last=s.sequence
                    yield f'id: {last}\nevent: state\ndata: {json.dumps(engine.snapshot(s))}\n\n'
                else:yield ': heartbeat\n\n'
                await asyncio.sleep(.5)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @app.post('/v1/sessions/{sid}/decisions/{did}/dispatch',tags=['explicit handoff'])
    async def dispatch(sid:str,did:str,data:Dispatch,client=Depends(owner)):
        """Explicitly confirm a current command to one configured target. Same decision cannot execute twice. No automatic retries."""
        return await engine.dispatch(engine.owned(client,sid),did,data.target_id)

    @app.get('/v1/captures',tags=['retained material'])
    async def captures(session_id:str|None=None,client=Depends(owner)):
        return {'captures':engine.store.list(client,session_id)}

    @app.delete('/v1/captures/{cid}',tags=['retained material'])
    async def delete_capture(cid:str,client=Depends(owner)):
        if not engine.store.delete(client,cid):raise Problem(404,'capture_not_found')
        return {'ok':True}

    @app.get('/',include_in_schema=False)
    async def index():return FileResponse(WEB/'index.html')
    @app.get('/app.js',include_in_schema=False)
    async def script():return FileResponse(WEB/'app.js',media_type='text/javascript')
    @app.get('/ambient.js',include_in_schema=False)
    async def ambient_script():return FileResponse(WEB/'ambient.js',media_type='text/javascript')
    @app.get('/style.css',include_in_schema=False)
    async def css():return FileResponse(WEB/'style.css',media_type='text/css')
    @app.get('/manifest.webmanifest',include_in_schema=False)
    async def manifest():return {'name':'Ambient','short_name':'Ambient','start_url':'/','display':'standalone','background_color':'#eef2ee','theme_color':'#245d4a'}
    return app
