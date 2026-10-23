"""Validated pivot axes and decimal arithmetic; no execution of user code."""
import ast
from decimal import Decimal, InvalidOperation, localcontext
from django.core.exceptions import ValidationError

FIELDS={'building':'Объект','complex_object':'Комплексный объект','package':'Комплекс работ','work':'Работа / ресурс','kind':'Раздел','month':'Месяц','date':'День'}
BASE={'plan':'План','fact':'Факт','delta':'Отклонение','completion':'Выполнение, %','real_plan':'Реальная · план, ₽','real_fact':'Реальная · факт, ₽','estimated_plan':'Оценочная · план, ₽','estimated_fact':'Оценочная · факт, ₽'}
DEFAULT={'rows':['building','complex_object','package','work'],'columns':['month'],'measures':['plan','fact','delta'],'formulas':[]}
VARS={'plan':(1,0),'fact':(1,0),'real_plan':(0,1),'real_fact':(0,1),'estimated_plan':(0,1),'estimated_fact':(0,1)}


def formula_tree(expression):
    if not isinstance(expression,str) or len(expression)>250:raise ValidationError('Формула должна содержать не более 250 символов.')
    try:tree=ast.parse(expression,mode='eval')
    except (SyntaxError,ValueError):raise ValidationError('Некорректная формула.')
    if len(list(ast.walk(tree)))>60:raise ValidationError('Слишком сложная формула.')
    def dimension(node):
        if isinstance(node,ast.Expression):return dimension(node.body)
        if isinstance(node,ast.Name) and node.id in VARS:return VARS[node.id]
        if isinstance(node,ast.Constant) and type(node.value) in (int,float) and abs(node.value)<=10**12:return (0,0)
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):return dimension(node.operand)
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=dimension(node.left),dimension(node.right)
            if isinstance(node.op,(ast.Add,ast.Sub)):
                if a!=b:raise ValidationError('В формуле нельзя складывать объём и деньги или число без единицы.')
                return a
            sign=-1 if isinstance(node.op,ast.Div) else 1
            return tuple(x+sign*y for x,y in zip(a,b))
        raise ValidationError('Доступны только показатели, числа, скобки и операции + − * /.')
    dim=dimension(tree)
    if dim not in {(0,0),(1,0),(0,1),(-1,1),(1,-1)}:
        raise ValidationError('Результат формулы должен быть объёмом, деньгами, коэффициентом или удельной стоимостью.')
    return tree,dim


def evaluate(tree,values):
    def value(n):
        if isinstance(n,ast.Expression):return value(n.body)
        if isinstance(n,ast.Name):return values.get(n.id)
        if isinstance(n,ast.Constant):return Decimal(str(n.value))
        if isinstance(n,ast.UnaryOp):
            x=value(n.operand)
            return None if x is None else -x if isinstance(n.op,ast.USub) else x
        a,b=value(n.left),value(n.right)
        if a is None or b is None:return None
        if isinstance(n.op,ast.Add):return a+b
        if isinstance(n.op,ast.Sub):return a-b
        if isinstance(n.op,ast.Mult):return a*b
        return a/b if b else None
    try:
        with localcontext() as ctx:
            ctx.prec=28
            result=value(tree)
            return result if result is None or result.is_finite() and abs(result)<Decimal('1e24') else None
    except (InvalidOperation,ArithmeticError):return None


def validate_config(config):
    if not isinstance(config,dict):raise ValidationError('Некорректные настройки отчёта.')
    axes=[]
    for key in ['rows','columns']:
        values=config.get(key,[])
        if not isinstance(values,list) or any(not isinstance(x,str) or x not in FIELDS for x in values):raise ValidationError('Неизвестное поле строк или столбцов.')
        axes+=values
    if len(axes)!=len(set(axes)):raise ValidationError('Поле можно использовать только на одной оси и один раз.')
    if not axes:raise ValidationError('Добавьте хотя бы одно поле строк или столбцов.')
    if 'date' in axes and 'month' in axes:raise ValidationError('Выберите день или месяц, а не оба одновременно.')
    formulas=config.get('formulas',[])
    if not isinstance(formulas,list) or len(formulas)>10:raise ValidationError('Можно добавить до 10 формул.')
    cleaned=[]
    for index,f in enumerate(formulas):
        if not isinstance(f,dict):raise ValidationError('Некорректная формула.')
        name=f.get('name','');expression=f.get('expression','')
        if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValidationError('Укажите название формулы до 80 символов.')
        _,dim=formula_tree(expression)
        cleaned.append({'id':'custom_'+str(index),'name':name.strip(),'expression':expression,'percent':bool(f.get('percent')) and dim==(0,0)})
    measures=config.get('measures',[])
    available=set(BASE)|{f['id'] for f in cleaned}
    if not isinstance(measures,list) or not measures or any(not isinstance(x,str) or x not in available for x in measures) or len(measures)!=len(set(measures)):raise ValidationError('Выберите показатели отчёта.')
    return {'rows':config['rows'],'columns':config['columns'],'measures':measures,'formulas':cleaned}


def pivot_grid(records,report,config):
    """Aggregate temporal measures within one source row before adding like units."""
    fields=config['rows']+config['columns'];temporal='date' if 'date' in fields else 'month' if 'month' in fields else None
    groups={};row_keys=set();col_keys=set()
    formulas={f['id']:f for f in config['formulas']}
    compiled={f['id']:formula_tree(f['expression']) for f in config['formulas']}
    for record in records:
        row=record['row']
        cells=list(zip(report['columns'],row['cells'])) if temporal else [(None,row['total'])]
        for column,cell in cells:
            dims={**record,'kind':{'works':'Работы','labor':'Люди','equipment':'Техника','fuel':'ГСМ'}.get(record['kind'],record['kind']),'month':column['date'].strftime('%Y-%m') if column else '', 'date':column['date'].isoformat() if column else ''}
            rk=tuple(dims[k] for k in config['rows']);ck=tuple(dims[k] for k in config['columns'])
            for metric in config['measures']:
                custom=formulas.get(metric);tree=None
                if custom:
                    tree,dim=compiled[metric];quantity=any(isinstance(n,ast.Name) and n.id in {'plan','fact'} for n in ast.walk(tree))
                    unit=row['unit'] if dim==(1,0) else '₽' if dim==(0,1) else '₽/'+row['unit'] if dim==(-1,1) else row['unit']+'/₽' if dim==(1,-1) else '%' if custom['percent'] else 'коэф.';label=custom['name']
                else:
                    quantity=metric in {'plan','fact','delta','completion'};label=BASE[metric];unit='%' if metric=='completion' else row['unit'] if quantity else '₽'
                if not quantity and record['kind']!='works':continue
                signature=(row['unit'],record['kind'],row.get('operation','sum')) if quantity else ('₽','works','sum')
                # Quantities with different units / averaging rules are always separate.
                display_rk=rk+(label,unit,({'works':'Работы','labor':'Люди','equipment':'Техника','fuel':'ГСМ'}.get(signature[1],signature[1])+' · '+signature[0]+(' · среднее' if signature[2]=='average' else ' · остаток' if signature[2]=='last' else '') if quantity else ''),metric)
                row_keys.add(display_rk);col_keys.add(ck)
                key=(display_rk,ck)
                if len(groups)>100000:raise ValidationError('Слишком большая таблица. Сократите период или число объектов.')
                entry=groups.setdefault(key,{'parts':[],'tree':tree,'custom':custom})
                entry['parts'].append({'plan':cell.get('plan'),'fact':cell.get('fact'),
                    'real_plan':cell.get('real_amount'),'real_fact':cell.get('real_fact_amount'),
                    'estimated_plan':cell.get('estimated_amount'),'estimated_fact':cell.get('estimated_fact_amount')})
    cols=sorted(col_keys);output=[]
    for rk in sorted(row_keys,key=lambda key:(key[:-4],config['measures'].index(key[-1]),key[-3],key[-2])):
        values=[]
        for ck in cols:
            entry=groups.get((rk,ck))
            if entry is None:values.append(None);continue
            metric=rk[-1];parts=entry['parts']
            totals={name:sum((p[name] for p in parts if p[name] is not None),Decimal(0)) if any(p[name] is not None for p in parts) else None for name in VARS}
            if entry['tree']:
                result=evaluate(entry['tree'],totals)
                if result is not None and entry['custom']['percent']:result*=100
            elif metric in totals:result=totals[metric]
            elif totals['fact'] is None or totals['plan'] is None:result=None
            elif metric=='delta':result=totals['fact']-totals['plan']
            else:result=totals['fact']/totals['plan']*100 if totals['plan'] else None
            values.append(result)
        output.append({'levels':rk[:-4],'label':rk[-4],'unit':rk[-3],'indicator':rk[-2], 'values':values})
    if len(output)*max(1,len(cols))>100000:raise ValidationError('Слишком большая сводная таблица. Сократите период или число объектов.')
    return {'rows':output,'columns':[{'key':c,'label':' / '.join(c) or 'За период'} for c in cols], 'row_labels':[FIELDS[k] for k in config['rows']]}
