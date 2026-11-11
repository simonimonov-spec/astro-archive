"""Bounded display-only formulas; never eval, imports, calls or file access."""
import ast, datetime as dt, math, operator, re
from pathlib import Path

def effective(base,context):
    def matches(condition):
        actual=str(context.get(condition.get('field'),'' )).casefold();value=str(condition.get('value','')).casefold()
        return {'equals':lambda:actual==value,'notEquals':lambda:actual!=value,'contains':lambda:value in actual,'startsWith':lambda:actual.startswith(value)}.get(condition.get('operator'),lambda:False)()
    variant=next((v for v in base.get('variants',[]) if v.get('enabled',True) and all(matches(c) for c in v.get('conditions',[]))),{})
    return dict(base,**{k:v for k,v in variant.items() if v is not None})

def display(formula,name,meta,aliases=None):
    fields=dict(meta.get('headers',{}),**{k:v for k,v in meta.items() if k!='headers'})
    fields.update(filename=name,basename=Path(name).stem,extension=Path(name).suffix,
        telescope=(aliases or {}).get(meta.get('telescope'),meta.get('telescope','')),frames=meta.get('frames'),totalExposure=meta.get('totalExposure'))
    def arithmetic(node):
        if isinstance(node,ast.Expression):return arithmetic(node.body)
        if isinstance(node,ast.Name):return float(fields[node.id])
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):return node.value
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):return arithmetic(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
        if isinstance(node,ast.BinOp) and type(node.op) in (ast.Add,ast.Sub,ast.Mult,ast.Div):
            return {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}[type(node.op)](arithmetic(node.left),arithmetic(node.right))
        raise ValueError('Непозволена аритметика.')
    def token(match):
        key,_,format=match[1].partition(':')
        try:
            if key.startswith('='):
                expression=ast.parse(key[1:],mode='eval')
                if sum(1 for _ in ast.walk(expression))>80:raise ValueError()
                value=arithmetic(expression)
            else:value=fields.get(key)
            if value is None:return '—'
            if isinstance(value,(int,float)):
                if not math.isfinite(value):return '—'
                if re.fullmatch(r'0(?:\.[0#]{1,8})?',format):
                    decimals=len(format.partition('.')[2]);text=f'{value:.{decimals}f}'
                    return text.rstrip('0').rstrip('.') if '#' in format and '.' in text else text
                return str(value)
            if format and key.startswith('DATE'):
                stamp=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
                for a,b in [('yyyy','%Y'),('MM','%m'),('dd','%d'),('HH','%H'),('mm','%M'),('ss','%S')]:format=format.replace(a,b)
                return stamp.strftime(format)
            return str(value)
        except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError,RecursionError,SyntaxError):return '—'
    return re.sub(r'\{([^{}]{1,200})\}',token,str(formula)[:512])
