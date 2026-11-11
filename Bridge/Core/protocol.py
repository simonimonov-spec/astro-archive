"""One versioned command boundary for the UI, core services and extensions."""
from __future__ import annotations
from dataclasses import dataclass
import json, math, re
from typing import Callable

PROTOCOL_VERSION=1
NAME=re.compile(r'^[a-z][a-zA-Z0-9_-]*(?:\.[a-zA-Z0-9_-]+)*$')

def data_only(value,limit=4_000_000):
    def check(item,depth=0):
        if depth>24:raise ValueError('Прекалено вложени данни.')
        if item is None or type(item) in (str,bool,int):return
        if type(item) is float and math.isfinite(item):return
        if isinstance(item,list):
            if len(item)>100000:raise ValueError('Прекалено дълъг списък.')
            for child in item:check(child,depth+1)
            return
        if isinstance(item,dict) and all(isinstance(k,str) and len(k)<=256 for k in item):
            for child in item.values():check(child,depth+1)
            return
        raise ValueError('Приемат се само крайни числа, текст, списъци и обекти с данни.')
    check(value)
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8'))>limit:raise ValueError('Данните надвишават разрешения размер.')
    return value

@dataclass(frozen=True)
class Caller:
    kind:str='ui'
    plugin:str|None=None
    permissions:frozenset[str]=frozenset()

@dataclass
class Command:
    name:str
    handler:Callable
    section:str
    permission:str|None=None
    description:str=''

class Router:
    def __init__(self):self.commands={}
    def register(self,name,handler,section='application',permission=None,description=''):
        if not NAME.fullmatch(name) or name in self.commands:raise ValueError('Невалидна или повторена основна команда: '+name)
        self.commands[name]=Command(name,handler,section,permission,description)
    def describe(self):
        return dict(protocol=PROTOCOL_VERSION,commands=[dict(name=c.name,section=c.section,pluginPermission=c.permission,description=c.description) for c in self.commands.values()])
    def invoke(self,name,parameters=None,caller=None):
        caller=caller or Caller();parameters={} if parameters is None else parameters
        if not isinstance(name,str) or not isinstance(parameters,dict):raise ValueError('Невалидна именувана команда.')
        command=self.commands.get(name)
        if command is None:raise ValueError('Непозната команда: '+name)
        data_only(parameters)
        if caller.kind=='plugin' and (not command.permission or command.permission not in caller.permissions):raise PermissionError('Плъгинът няма разрешение за тази основна команда: '+name)
        return command.handler(parameters)
    def dispatch(self,request):
        ident=request.get('id') if isinstance(request,dict) else None
        try:
            if not isinstance(request,dict) or request.get('version')!=PROTOCOL_VERSION:raise ValueError('Неподдържана версия на връзката.')
            if not isinstance(ident,str) or not ident or len(ident)>80:raise ValueError('Невалиден идентификатор на заявка.')
            result=self.invoke(request.get('method'),request.get('params',{}))
            return dict(type='response',version=PROTOCOL_VERSION,id=ident,ok=True,result=result)
        except InterruptedError as error:return dict(type='response',version=PROTOCOL_VERSION,id=ident,ok=False,cancelled=True,code='cancelled',error=str(error))
        except PermissionError as error:return dict(type='response',version=PROTOCOL_VERSION,id=ident,ok=False,code='permission_denied',error=str(error))
        except Exception as error:return dict(type='response',version=PROTOCOL_VERSION,id=ident,ok=False,code='operation_failed',error=str(error))
