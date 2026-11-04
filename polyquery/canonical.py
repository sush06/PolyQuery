"""Versioned canonical constraints, independent of planners and SQL text."""
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

TAXONOMY = {
    'scope':'Entity/table and source projection', 'predicate':'Typed row filter, including temporal comparisons',
    'aggregation':'Function and measured field', 'grouping':'Grouping keys',
    'ordering':'Ordered field/direction pairs', 'limit':'Maximum returned rows',
    'distinct':'Uniqueness requirement', 'traversal':'Relationship direction, endpoints, and depth',
    'projection':'Requested result fields',
    'join':'Join keys and join type',
}


@dataclass(frozen=True)
class Literal:
    kind: str
    value: str | bool | None

    def __post_init__(self):
        if self.kind == 'number':
            if type(self.value) not in (str,int,float) or isinstance(self.value,bool):
                raise ValueError('Numeric literal requires a finite decimal value')
            try:
                number=Decimal(str(self.value))
                if not number.is_finite() or len(str(self.value))>2048 or abs(number.adjusted())>1000:
                    raise ValueError('Unbounded numeric literal')
                text=format(number,'f')
                if '.' in text: text=text.rstrip('0').rstrip('.')
                if len(text)>2048: raise ValueError('Unbounded canonical numeric literal')
                object.__setattr__(self,'value',text if number else '0')
            except InvalidOperation:
                raise ValueError('Invalid numeric literal') from None
        elif self.kind in ('string','date'):
            if type(self.value) is not str or len(self.value)>512:
                raise ValueError('String literal requires bounded text')
            if self.kind == 'date':
                object.__setattr__(self,'value',date.fromisoformat(self.value).isoformat())
        elif self.kind == 'boolean':
            if type(self.value) is not bool:
                raise ValueError('Boolean literal requires bool')
        elif self.kind != 'null' or self.value is not None:
            raise ValueError('Invalid literal type')

    def to_dict(self):
        return {'kind':self.kind,'value':self.value}


@dataclass(frozen=True)
class Predicate:
    operator: str
    field: str | None = None
    value: Literal | None = None
    children: tuple = ()

    def __post_init__(self):
        if type(self.children) is not tuple:
            raise ValueError('Predicate children must be an immutable tuple')
        if self.operator in ('and','or','not'):
            if self.field is not None or self.value is not None or not all(isinstance(c,Predicate) for c in self.children):
                raise ValueError('Boolean predicates require predicate children only')
            invalid_arity = len(self.children)<2 if self.operator in ('and','or') else len(self.children)!=1
            if invalid_arity:
                raise ValueError('Invalid Boolean arity')
        elif self.operator in ('eq','ne','lt','le','gt','ge','is_null','is_not_null'):
            if type(self.field) is not str or not re.fullmatch(r'[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*',self.field) or self.children:
                raise ValueError('Comparison requires a qualified field and no children')
            if self.operator in ('is_null','is_not_null'):
                if self.value is not None:
                    raise ValueError('Null predicates have no value')
            elif not isinstance(self.value,Literal) or self.value.kind=='null':
                raise ValueError('Use is_null/is_not_null for null tests')
            if self.operator in ('lt','le','gt','ge') and self.value.kind not in ('number','date'):
                raise ValueError('Ordered comparison requires numeric/date values')
        else:
            raise ValueError('Unknown predicate operator')

    def canonical(self):
        if self.operator in ('and','or'):
            children=[]
            for child in self.children:
                normalized=child.canonical()
                children.extend(normalized.children if normalized.operator==self.operator else (normalized,))
            unique={json.dumps(c.to_dict(),sort_keys=True):c for c in children}
            ordered=tuple(unique[k] for k in sorted(unique))
            return ordered[0] if len(ordered)==1 else Predicate(self.operator,children=ordered)
        # Do not distribute Boolean expressions or infer unknown/null semantics.
        return Predicate('not',children=(self.children[0].canonical(),)) if self.operator=='not' else self

    def to_dict(self):
        return {'operator':self.operator,'field':self.field,'value':self.value.to_dict() if self.value else None,
                'children':[c.to_dict() for c in self.children]}

    @classmethod
    def from_dict(cls,value,depth=0):
        if depth>12 or not isinstance(value,dict) or set(value)!={'operator','field','value','children'} or not isinstance(value['children'],list) or len(value['children'])>64:
            raise ValueError('Invalid or overly deep predicate')
        literal=value['value']
        if literal is not None and (not isinstance(literal,dict) or set(literal)!={'kind','value'}):
            raise ValueError('Invalid typed literal')
        return cls(value['operator'],value['field'],Literal(**literal) if literal is not None else None,
                   tuple(cls.from_dict(c,depth+1) for c in value['children']))


@dataclass(frozen=True)
class Constraint:
    kind: str
    field: str
    operator: str
    value: str | int | bool | None = None
    predicate: Predicate | None = None

    def __post_init__(self):
        if self.kind not in TAXONOMY or type(self.field) is not str or not self.field or len(self.field)>512 or type(self.operator) is not str or not self.operator:
            raise ValueError('Invalid constraint header')
        if type(self.value) not in (str,int,bool,type(None)):
            raise ValueError('Constraint value must be scalar')
        allowed={'scope':{'rows'},'predicate':{'where'},'aggregation':{'count','sum','mean','median','sample_stdev','pearson'},
                 'grouping':{'key'},'ordering':{'asc','desc'},'limit':{'max_rows'},'distinct':{'enabled'},
                 'traversal':{'source','target','strategy','max_hops','direction'},'projection':{'fields'},'join':{'inner','left'}}
        if self.operator not in allowed[self.kind]:
            raise ValueError('Invalid operator for constraint kind')
        if self.kind=='predicate':
            if not isinstance(self.predicate,Predicate) or self.value is not None or self.operator!='where':
                raise ValueError('Predicate constraint requires a WHERE tree')
        elif self.predicate is not None:
            raise ValueError('Unexpected predicate tree')
        if self.kind=='limit' and (type(self.value) is not int or self.value<0):
            raise ValueError('Limit must be a nonnegative integer')
        if self.kind=='distinct' and type(self.value) is not bool:
            raise ValueError('Distinct requires a Boolean value')
        if self.kind in ('scope','aggregation','grouping','ordering') and self.value is not None:
            raise ValueError('Unexpected value for structural constraint')
        if self.kind=='traversal' and self.operator in ('source','target','strategy','direction') and type(self.value) is not str:
            raise ValueError('Traversal identity/strategy requires a string')
        if self.kind=='traversal' and self.operator=='max_hops' and (type(self.value) is not int or self.value<0):
            raise ValueError('Traversal bound requires a nonnegative integer')
        if self.kind=='ordering' and self.operator not in ('asc','desc'):
            raise ValueError('Ordering requires asc or desc')

    def to_dict(self):
        return {'kind':self.kind,'field':self.field,'operator':self.operator,'value':self.value,
                'predicate':self.predicate.canonical().to_dict() if self.predicate else None}


@dataclass(frozen=True)
class CanonicalRequest:
    source: str
    constraints: tuple[Constraint,...] = ()
    version: int = 1

    def __post_init__(self):
        if type(self.version) is not int or self.version!=1 or type(self.source) is not str or not self.source or type(self.constraints) is not tuple or not all(isinstance(c,Constraint) for c in self.constraints):
            raise ValueError('Invalid canonical request')
        if len(self.constraints)>128:
            raise ValueError('Too many constraints')

    def to_dict(self):
        # Constraint sequence remains ordered: sorting ORDER BY keys changes meaning.
        return {'version':self.version,'source':self.source,'constraints':[c.to_dict() for c in self.constraints]}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or set(value)!={'version','source','constraints'} or not isinstance(value['constraints'],list):
            raise ValueError('Invalid canonical request shape')
        constraints=[]
        for c in value['constraints']:
            if not isinstance(c,dict) or set(c)!={'kind','field','operator','value','predicate'}:
                raise ValueError('Invalid canonical constraint shape')
            constraints.append(Constraint(c['kind'],c['field'],c['operator'],c['value'],Predicate.from_dict(c['predicate']) if c['predicate'] is not None else None))
        return cls(value['source'],tuple(constraints),value['version'])


def validate_preservation(requested, implemented):
    """Compare typed constraints without trusting model claims about preservation."""
    if requested.source!=implemented.source:
        return {'preserved':False,'missing':['source mismatch'],'invented':[]}
    # Order matters within each kind; order between kinds does not.
    missing,invented=[],[]
    for kind in TAXONOMY:
        expected=[c.to_dict() for c in requested.constraints if c.kind==kind]
        actual=[c.to_dict() for c in implemented.constraints if c.kind==kind]
        if expected!=actual:
            if expected: missing.append({'kind':kind,'constraints':expected})
            if actual: invented.append({'kind':kind,'constraints':actual})
    return {'preserved':not missing and not invented,'missing':missing,'invented':invented}
