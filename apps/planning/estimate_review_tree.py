"""Catalogue identities, shared by GPR and SMG estimator reviews."""

def hierarchy(works, editing_work_id=None):
    sections={}
    for index,work in enumerate(works):
        work.review_open=str(editing_work_id)==str(work.pk) or index==0 and not work.review_valid
        work.review_search=' '.join([work.section.name,work.complex_object.name if work.complex_object_id else '',work.name,work.unit]+[item.name+' '+item.unit for item in work.items.all()])
        section=sections.setdefault(work.section_id,{'id':work.section_id,'name':work.section.name,'objects':{},'count':0,'checked':0})
        obj=section['objects'].setdefault(work.complex_object_id,{'id':work.complex_object_id,'name':work.complex_object.name if work.complex_object_id else 'Без комплексного объекта','works':[],'count':0,'checked':0})
        obj['works'].append(work)
        for node in [section,obj]:
            node['count']+=1;node['checked']+=bool(work.review_valid)
    result=sorted(sections.values(),key=lambda node:(node['name'].casefold(),node['id']))
    for section in result:
        section['objects']=sorted(section['objects'].values(),key=lambda node:(node['id'] is None,node['name'].casefold(),node['id'] or 0))
    return result
