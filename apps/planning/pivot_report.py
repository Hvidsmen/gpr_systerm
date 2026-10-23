"""Scoped pivot report with personal, per-section saved configurations."""
import json
from calendar import monthrange
from datetime import date
from io import BytesIO
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from core.permissions import READ_ROLES, require_roles, scope_queryset
from .models import GlobalPlanVersion, DailySchedule, PivotReportPreset
from .report_forms import MatrixReportForm
from .report_services import build_matrix, Source
from .pivot_engine import FIELDS, BASE, DEFAULT, validate_config, pivot_grid


def read_config(raw):
    try:return validate_config(json.loads(raw))
    except (ValueError,TypeError,RecursionError):raise ValidationError('Не удалось прочитать настройки сводной таблицы.')


class PivotReport(View):
    source_kind='plan_fact'
    def owner(self,request,pk):
        if self.source_kind not in {'gpr','smg'}:return None
        model=DailySchedule if self.source_kind=='smg' else GlobalPlanVersion
        return get_object_or_404(scope_queryset(model.objects.all(),request.user),pk=pk)

    def presets(self,user):
        return PivotReportPreset.objects.filter(company=user.company,user=user,section=self.source_kind)

    def post(self,request,pk=None):
        require_roles(request.user,READ_ROLES);self.owner(request,pk)
        query=self.presets(request.user);action=request.POST.get('action')
        try:
            if action not in {'save','default','delete'}:raise ValidationError('Неизвестное действие.')
            with transaction.atomic():
                # Serialize default changes, including concurrent first saves.
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                preset=get_object_or_404(query,pk=request.POST.get('preset')) if request.POST.get('preset','').isdigit() else None
                if action in {'default','delete'} and preset is None:raise ValidationError('Выберите сохранённую настройку.')
                if action=='delete':preset.delete()
                elif action=='default':
                    query.update(is_default=False);preset.is_default=True;preset.save(update_fields=['is_default','updated_at'])
                else:
                    config=read_config(request.POST.get('config',''))
                    name=request.POST.get('name','').strip()
                    if not name or len(name)>120:raise ValidationError('Укажите название настройки до 120 символов.')
                    conflict=query.filter(name=name)
                    if preset:conflict=conflict.exclude(pk=preset.pk)
                    if conflict.exists():raise ValidationError('Настройка с таким названием уже существует.')
                    if preset is None:preset=PivotReportPreset(company=request.user.company,user=request.user,section=self.source_kind)
                    preset.name=name;preset.config=config
                    if request.POST.get('make_default')=='1':query.update(is_default=False);preset.is_default=True
                    preset.save()
            messages.success(request,'Личная настройка отчёта сохранена.' if action!='delete' else 'Настройка удалена.')
        except ValidationError as exc:messages.error(request,' '.join(exc.messages))
        if action in {'save','default'} and 'preset' in locals() and preset and preset.pk:
            return redirect(request.path+'?preset='+str(preset.pk))
        return redirect(request.path)

    def get(self,request,pk=None):
        require_roles(request.user,READ_ROLES);owner=self.owner(request,pk)
        params=request.GET.copy();today=timezone.localdate()
        start,end=date(today.year,1,1),date(today.year,12,31)
        if owner:
            if self.source_kind=='smg':start=owner.month;end=start.replace(day=monthrange(start.year,start.month)[1])
            else:start,end=owner.start_date,owner.end_date
            params.setlist('objects',[str(owner.construction_object_id)])
            params.pop('consolidated',None);params.pop('project',None)
            for key in list(params):
                if key.startswith('version_'):params.pop(key)
        for key,value in {'start':start.isoformat(),'end':end.isoformat(),'mode':'latest'}.items():params.setdefault(key,value)
        if 'sections_set' not in params:params.setlist('sections',['works'])
        form=MatrixReportForm(params,user=request.user)
        presets=self.presets(request.user);selected=None;config={**DEFAULT,'measures':['fact','real_fact','estimated_fact']} if self.source_kind=='facts' else {**DEFAULT,'measures':['plan','real_plan','estimated_plan']} if owner else DEFAULT
        if request.GET.get('preset','').isdigit():selected=get_object_or_404(presets,pk=request.GET['preset'])
        elif 'config' not in request.GET:selected=presets.filter(is_default=True).first()
        try:
            config=read_config(request.GET['config']) if 'config' in request.GET else validate_config(selected.config if selected else config)
        except ValidationError as exc:form.add_error(None,exc)
        context={'form':form,'owner':owner,'source_kind':self.source_kind,'presets':presets,'selected_preset':selected,
                 'pivot_config':config,'pivot_fields':FIELDS,'pivot_measures':BASE}
        # is_valid must run before constructing the scoped sources.
        if form.is_valid():
            filters={**form.cleaned_data,'daily':'date' in config['rows']+config['columns'],'flat_objects':True,'work_levels':'','month':None}
            if owner and (filters['start']<start or filters['end']>end):form.add_error(None,'Период должен находиться внутри выбранной версии плана.')
            else:
                try:
                    overrides=None
                    if owner:
                        filters['objects']=form.fields['objects'].queryset.filter(pk=owner.construction_object_id)
                        if self.source_kind=='gpr':
                            from .global_services import build_snapshot
                            snapshot=build_snapshot(owner,validate_inputs=False) if owner.status in ['DRAFT','REJECTED'] else owner.snapshot
                        else:
                            from .daily_schedule import overlay_schedules
                            snapshot=overlay_schedules({'works':[],'resources':{}},[owner],start,end)
                        overrides={owner.construction_object_id:[Source(owner,snapshot,start,end)]}
                    if self.source_kind=='facts':
                        from .journal_services import journal_matrix
                        report=journal_matrix(request.user,filters,'facts')
                    else:report=build_matrix(request.user,filters,source_overrides=overrides,plan_only=bool(owner))
                    grid=pivot_grid(records(report),report,config)
                    context.update(grid=grid,warnings=[w for obj in report['objects'] for w in obj.get('warnings',[])])
                    if request.GET.get('export')=='xlsx':return pivot_workbook(grid,filters,owner)
                except ValidationError as exc:form.add_error(None,exc)
        return render(request,'planning/pivot_report.html',context)

def records(report):
    """Only leaves contribute; priced package totals are never added twice."""
    result=[]
    def walk(nodes, obj, kind, section):
        for node in nodes:
            for parent in node['rows']:
                dims=parent.get('decomposition', {})
                for row in parent.get('children') or [parent]:
                    composite=dims.get('kind')=='COMPOSITE'
                    result.append({'building':str(obj['object'].project)+' · '+obj['name'],
                        'complex_object':dims.get('complex_object_name') or ('Без комплексного объекта' if composite else 'Общие работы и ресурсы'),
                        'package':dims.get('package_name') if composite else ('Самостоятельные работы' if kind=='works' else section),
                        'work':row['label'], 'kind':kind, 'row':row})
            walk(node.get('children',[]),obj,kind,section)
    for obj in report['objects']:
        for section in obj['sections']:walk(section['groups'],obj,section['kind'],section['label'])
    return result



def pivot_workbook(grid,filters,owner=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    book=Workbook();sheet=book.active;sheet.title='Сводная таблица'
    sheet.append(['Сводная таблица',str(filters['start'])+' — '+str(filters['end'])])
    if owner:sheet.append(['Версия',str(owner)])
    sheet.append(grid['row_labels']+['Показатель','Ед.','Тип объёма']+[c['label'] for c in grid['columns']]);header=sheet.max_row
    for row in grid['rows']:sheet.append(list(row['levels'])+[row['label'],row['unit'],row['indicator']]+row['values'])
    for row in sheet:
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
            cell.font=Font(name='Calibri',size=11,color='273B53')
            if cell.row==header:cell.fill=PatternFill('solid',fgColor='EDF5FC');cell.font=Font(name='Calibri',size=11,bold=True,color='355C7C')
            if cell.row>header and cell.column>len(grid['row_labels'])+3:cell.number_format='#,##0.###'
    for i in range(1,sheet.max_column+1):sheet.column_dimensions[get_column_letter(i)].width=32 if i<=len(grid['row_labels'])+1 else 18
    sheet.freeze_panes=get_column_letter(len(grid['row_labels'])+4)+str(header+1);sheet.print_title_rows=f'1:{header}';sheet.sheet_view.showGridLines=False
    stream=BytesIO();book.save(stream)
    response=HttpResponse(stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']='attachment; filename="pivot-report.xlsx"';return response
