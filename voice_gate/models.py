from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field, ConfigDict, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class SessionCreate(StrictModel):
    name: str = Field(default='Untitled session',max_length=100)

class Transcript(StrictModel):
    id: str = Field(default_factory=lambda:str(uuid4()),min_length=1,max_length=100)
    epoch: int = Field(ge=0)
    revision: int = Field(default=1,ge=1,le=100000)
    text: str = Field(max_length=4000)
    final: bool = True
    complete: bool = True
    start_ms: float = Field(default=0,ge=0,lt=1e12)
    end_ms: float = Field(default=0,ge=0,lt=1e12)
    source: str = Field(default='api',max_length=80)
    timing: str = Field(default='client supplied / unspecified',max_length=150)

    @model_validator(mode='after')
    def validate_time(self):
        if self.end_ms < self.start_ms:
            raise ValueError('end_ms must be at least start_ms')
        return self

class Turn(StrictModel):
    text: str = Field(min_length=1,max_length=4000)
    request_id: str = Field(default_factory=lambda:str(uuid4()),min_length=1,max_length=100)
    session_id: str | None = None
    epoch: int | None = Field(default=None,ge=0)
    source: str = Field(default='api',max_length=80)
    start_ms: float = Field(default=0,ge=0,lt=1e12)
    end_ms: float = Field(default=0,ge=0,lt=1e12)

class Control(StrictModel):
    action: Literal['pause','resume','cancel','discard','speaking_on','speaking_off']

class Dispatch(StrictModel):
    target_id: str = Field(min_length=1,max_length=100)
    confirmed: Literal[True]

class Login(StrictModel):
    token: str = Field(min_length=24,max_length=512)
