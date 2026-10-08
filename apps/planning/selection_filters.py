from apps.resources.models import Brigade, BrigadeGroup, BrigadeMacroGroup, EquipmentType, EquipmentCategory
from apps.works.models import ProjectWork
from apps.projects.models import Section
from .models import ResourceMonthAllocation


def planning_filters(version):
    company = version.company
    def choices(queryset):
        return [{'value':str(row.pk), 'label':row.name} for row in queryset]
    def field(name, label, options):
        return {'name':name, 'label':label, 'options':options}
    works = ProjectWork.objects.filter(company=company, section__construction_object=version.construction_object)
    brigades = Brigade.objects.filter(company=company)
    equipment = EquipmentType.objects.filter(company=company)
    fuel = ResourceMonthAllocation._meta.get_field('fuel_type').choices
    data = {
        'works': {str(row.pk): {'section':str(row.section_id), 'kind':row.kind} for row in works},
        'labor': {str(row.pk): {'group':str(row.group_id or ''), 'macro':str(row.macro_group_id or '')} for row in brigades},
        'equipment': {str(row.pk): {'category':str(row.category_id or '')} for row in equipment},
        'fuel': {key:{'fuel_type':key} for key, label in fuel},
    }
    specs = {
        'works': [field('section','Раздел',choices(Section.objects.filter(company=company, construction_object=version.construction_object).order_by('name', 'pk'))), field('kind','Вид работы',[{'value':key,'label':label} for key,label in ProjectWork.Kind.choices])],
        'labor': [field('group','Группа',choices(BrigadeGroup.objects.filter(company=company))),field('macro','Макрогруппа',choices(BrigadeMacroGroup.objects.filter(company=company)))],
        'equipment': [field('category','Категория',choices(EquipmentCategory.objects.filter(company=company)))],
        'fuel': [field('fuel_type','Вид ГСМ',[{'value':key,'label':label} for key,label in fuel])],
    }
    return data, specs
