"""Pivot presentation and roll-ups, always recomputed from source contributions."""
from .pivot_engine import pivot_grid, FIELDS
from django.core.exceptions import ValidationError


def build_layout(records, report, config):
    axes=config['rows'];columns=config['columns'];totals=config['totals'];cache={}
    def grid(r,c):
        key=(r,c)
        if key not in cache:
            # Rebuild from the source, not by summing visible ratios or averages.
            data=pivot_grid(records,report,{**config,'rows':axes[:r],'columns':columns[:c]})
            index={}
            for row in data['rows']:
                signature=(tuple(row['levels']),row['measure'],row['unit'],row['indicator'])
                index[signature]={tuple(column['key']):value for column,value in zip(data['columns'],row['values'])}
            cache[key]=(data,index)
        return cache[key]
    full,_=grid(len(axes),len(columns))
    column_keys=[tuple(c['key']) for c in full['columns']]
    display_columns=[]
    position=totals['position']
    def column_groups(keys,depth=0,prefix=()):
        if depth==len(columns):
            if keys:display_columns.append({'key':prefix,'depth':depth,'kind':'detail','label':' / '.join(prefix) or 'За период'})
            return
        groups={}
        for key in keys:groups.setdefault(key[depth],[]).append(key)
        for name,members in sorted(groups.items()):
            path=prefix+(name,);level=depth+1
            subtotal=columns[depth] in totals['column_levels'] and level<len(columns)
            item={'key':path,'depth':level,'kind':'subtotal','label':'Итого: '+' / '.join(path)}
            if subtotal and position=='before':display_columns.append(item)
            column_groups(members,level,path)
            if subtotal and position=='after':display_columns.append(item)
    if columns:column_groups(column_keys)
    elif column_keys:display_columns.append({'key':(),'depth':0,'kind':'detail','label':'За период'})
    if columns and column_keys and totals['column_grand']:display_columns.append({'key':(),'depth':0,'kind':'grand','label':'Общий итог'})
    def values(row,r):
        signature=(tuple(row['levels']),row['measure'],row['unit'],row['indicator'])
        return [grid(r,col['depth'])[1].get(signature,{}).get(col['key']) for col in display_columns]
    def summary(prefix,kind,depth,ancestors):
        data,_=grid(len(prefix),len(columns))
        rows=[row for row in data['rows'] if tuple(row['levels'])==prefix]
        return [{**row,'kind':kind,'depth':depth,'ancestors':' '.join(ancestors),'values':values(row,len(prefix))} for row in rows]
    output=[];sequence=0
    def walk(rows,prefix=(),depth=0,ancestors=()):
        nonlocal sequence
        if depth==len(axes):
            for row in rows:output.append({**row,'kind':'detail','depth':depth,'ancestors':' '.join(ancestors),'values':values(row,depth)})
            return
        groups={}
        for row in rows:groups.setdefault(row['levels'][depth],[]).append(row)
        for name,members in sorted(groups.items()):
            path=prefix+(name,);sequence+=1;identifier='pivot-node-'+str(sequence)
            subtotal=axes[depth] in totals['row_levels'] and depth+1<len(axes)
            if config['view']=='hierarchy':
                leaf=depth+1==len(axes)
                if subtotal or leaf:
                    # Each measure/unit keeps its own numbers. Only the first
                    # measure row carries the group's disclosure control.
                    summaries=summary(path,'group',depth,ancestors) if subtotal else [
                        {**row,'values':values(row,len(path))} for row in members]
                    for index,row in enumerate(summaries):
                        output.append({**row,'kind':'group','label':name,'measure_label':row['label'],
                            'node_id':identifier if index==0 and not leaf else '',
                            'depth':depth,'ancestors':' '.join(ancestors),'field':FIELDS[axes[depth]],
                            'continuation':index>0,'inline_total':subtotal})
                    if leaf:continue
                else:
                    output.append({'kind':'group','label':name,'node_id':identifier,'depth':depth,'ancestors':' '.join(ancestors),'field':FIELDS[axes[depth]],'values':[None]*len(display_columns)})
                walk(members,path,depth+1,ancestors+(identifier,))
            else:
                if subtotal and position=='before':output.extend(summary(path,'subtotal',depth+1,()))
                walk(members,path,depth+1,())
                if subtotal and position=='after':output.extend(summary(path,'subtotal',depth+1,()))
    walk(full['rows'])
    if axes and full['rows'] and totals['row_grand']:output.extend(summary((),'grand',0,()))
    if len(output)*max(1,len(display_columns))>150000:raise ValidationError('Слишком большая таблица с подытогами. Сократите период или число уровней итогов.')
    # Consecutive hierarchy headings are merged; subtotal and grand-total headings
    # span the remaining header levels so dates and nested column fields stay clear.
    headers=[]
    for level in range(max(1,len(columns))):
        cells=[];last_index=-2
        for index,col in enumerate(display_columns):
            depth=col['depth'];kind=col['kind']
            if kind=='grand' or not columns:
                if level:continue
                label=col['label'];rowspan=max(1,len(columns));identity=('grand',index)
            elif kind=='subtotal' and level>=depth-1:
                if level>depth-1:continue
                label='Итого: '+col['key'][-1];rowspan=len(columns)-depth+1;identity=('subtotal',index)
            else:
                label=col['key'][level];rowspan=1;identity=col['key'][:level+1]
            if cells and last_index==index-1 and cells[-1]['identity']==identity and cells[-1]['rowspan']==rowspan:
                cells[-1]['colspan']+=1
            else:cells.append({'label':label,'colspan':1,'rowspan':rowspan,'kind':kind,'identity':identity})
            last_index=index
        headers.append(cells)
    if config['view']=='table':
        for row in output:
            if row['kind']=='subtotal':
                path=tuple(row['levels']);row['levels']=path[:-1]+('Итого: '+path[-1],)+('',)*(len(axes)-len(path))
            elif row['kind']=='grand':row['levels']=('Общий итог',)+('',)*(len(axes)-1)
    return {'rows':output,'columns':display_columns,'headers':headers,'header_depth':len(headers),
            'row_labels':[FIELDS[k] for k in axes], 'view':config['view'], 'column_count':len(display_columns)}
