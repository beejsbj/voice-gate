from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

@dataclass
class Settings:
    tokens: dict[str, str] = field(default_factory=dict)
    provider: str = 'fixture'
    api_key: str = ''
    base_url: str = ''
    model: str = ''
    database: str = ':memory:'
    targets: list[dict] = field(default_factory=list)
    settle_ms: int = 650
    stale_ms: int = 4000
    session_ttl: int = 3600
    max_sessions: int = 128
    max_turns: int = 256
    threshold: float = .70
    max_judgments_per_minute: int = 60
    origins: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls):
        config_path=os.getenv('VG_CONFIG_FILE')
        if config_path:
            values=json.loads(Path(config_path).read_text())
            if not isinstance(values,dict) or any(not isinstance(k,str) or not k.isidentifier() or not isinstance(v,str) for k,v in values.items()):
                raise ValueError('VG_CONFIG_FILE must contain a JSON object of environment names to strings')
            for key,value in values.items():
                os.environ.setdefault(key,value)
        provider = os.getenv('VG_PROVIDER', 'fixture')
        if provider not in {'fixture', 'typesafe', 'openrouter'}:
            raise ValueError('VG_PROVIDER must be fixture, typesafe or openrouter')
        s = cls(tokens=json.loads(os.getenv('VG_TOKENS', '{}')),provider=provider,
            api_key=os.getenv('TYPESAFE_API_KEY' if provider=='typesafe' else 'OPENROUTER_API_KEY',''),
            base_url=os.getenv('VG_JEV_BASE_URL', 'https://openrouter.ai/api' if provider=='openrouter' else 'https://api.typesafe.ai'),
            model=os.getenv('VG_JEV_MODEL','typesafe/jev-1.13' if provider=='openrouter' else 'jev-1.13.0'),
            database=os.getenv('VG_DATABASE', ':memory:'),targets=json.loads(os.getenv('VG_TARGETS','[]')),
            origins=[x.rstrip('/') for x in os.getenv('VG_ORIGINS','').split(',') if x])
        s.validate()
        return s

    def validate(self):
        if not self.tokens or any(not isinstance(k,str) or not k or not isinstance(v,str) or len(v)<24 for k,v in self.tokens.items()):
            raise ValueError('VG_TOKENS must map client names to distinct secrets of at least 24 characters')
        if len(set(self.tokens.values())) != len(self.tokens):
            raise ValueError('Client tokens must be distinct')
        if self.provider!='fixture' and not self.api_key:
            raise ValueError('Provider API key is required in live mode')
        ids=set()
        for target in self.targets:
            tid=target.get('id','')
            if not tid or not tid.replace('_','').replace('-','').isalnum() or tid in ids:
                raise ValueError('Target ids must be unique simple identifiers')
            ids.add(tid)
            if target.get('kind') not in {'health','webhook','openai'}:
                raise ValueError('Unknown target kind')
            url=urlsplit(target.get('url',''))
            if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.fragment:
                raise ValueError('Targets need an operator-configured HTTP(S) URL, without embedded credentials')

    def public_targets(self):
        return [{'id':t['id'],'name':t.get('name',t['id']),'kind':t['kind']} for t in self.targets]
