"""Operator-defined endpoints only. Transcript input cannot choose URLs or headers."""
import asyncio
import json
import os
import httpx

async def dispatch_target(target, decision, session_id):
    headers={'Idempotency-Key':decision['id'],'User-Agent':'voice-gate/0.1'}
    if target.get('token_env'):
        token=os.getenv(target['token_env'])
        if not token:return {'ok':False,'error':'target_credential_unavailable'}
        headers['Authorization']='Bearer '+token
    method='GET' if target['kind']=='health' else 'POST'
    payload=None
    if target['kind']=='openai':
        payload={'model':target.get('model','hermes-agent'),'messages':[{'role':'user','content':decision['text']}],'stream':False}
    elif target['kind']=='webhook':
        payload={'version':'1','event':'command.confirmed','id':decision['id'],'session_id':session_id,
                 'text':decision['text'],'decision':{k:decision[k] for k in ['label','sensitive','turn_id','start_ms','end_ms']}}
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False) as client:
                async with client.stream(method,target['url'],headers=headers,**({'json':payload} if payload else {})) as response:
                    if not response.is_success:return {'ok':False,'error':'target_rejected','http_status':response.status_code}
                    raw=bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        raw.extend(chunk)
                        if len(raw)>64000:return {'ok':False,'error':'target_response_too_large'}
                    if target['kind']=='health':
                        data=json.loads(raw);body={k:data[k] for k in ['status','platform','version'] if k in data}
                    elif target['kind']=='openai':body={'text':json.loads(raw)['choices'][0]['message']['content']}
                    else:body={'text':raw.decode('utf-8',errors='replace')[:8000]}
                    return {'ok':True,'http_status':response.status_code,'result':body}
    except (httpx.HTTPError,ValueError,KeyError,IndexError,TimeoutError):
        return {'ok':False,'error':'target_unavailable_or_invalid_response'}
