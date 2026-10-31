from datetime import date,timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Company,Role,User
from apps.projects.models import Project,ConstructionObject,Section
from apps.resources.models import Brigade,Employee,EquipmentType,Equipment
from apps.resources.position_catalog import position_for
from apps.works.models import ProjectWork
from apps.planning.models import DailySchedule,DailyScheduleEntry,WorkTask
from .models import DailyFact,EmployeeActivity,EquipmentActivity,FuelFact,LaborFact,EquipmentFact,DeviationReason,FactSectionDay

class DispatcherTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Dispatcher fact')
        cls.project=Project.objects.create(company=cls.company,name='Project')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='Object')
        cls.other=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='Other')
        section=Section.objects.create(company=cls.company,construction_object=cls.obj,name='Section')
        cls.work=ProjectWork.objects.create(company=cls.company,section=section,name='Work',unit='m',unit_price=10)
        cls.brigade=Brigade.objects.create(company=cls.company,name='Driver')
        cls.employee=Employee.objects.create(company=cls.company,last_name='Иванов',first_name='Иван',position=position_for(cls.brigade))
        cls.type=EquipmentType.objects.create(company=cls.company,name='Crane')
        cls.machine=Equipment.objects.create(company=cls.company,type=cls.type,plate_number='123',name='Crane 123')
        cls.reason=DeviationReason.objects.create(company=cls.company,name='Материалы')
        cls.day=timezone.localdate()-timedelta(days=1)
        from apps.personnel.models import Camp,CampObjectLink,CampStay,Person
        cls.housing_camp=Camp.objects.create(company=cls.company,name='ВЖГ Dispatcher')
        CampObjectLink.objects.create(company=cls.company,camp=cls.housing_camp,construction_object=cls.obj)
        cls.housing_person=Person.objects.create(company=cls.company,full_name=cls.employee.display_name,employee=cls.employee)
        CampStay.objects.create(company=cls.company,person=cls.housing_person,camp=cls.housing_camp,arrived_on=cls.day)
        cls.url=reverse('production:fact_day_workspace')
        cls.users={}
        for code in ['ADMIN','DISPATCHER','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER']:
            role=Role.objects.get(code=code)
            user=User.objects.create_user(username=code,company=cls.company,role=role)
            user.assigned_objects.add(cls.obj);cls.users[code]=user
        cls.schedule=DailySchedule.objects.create(company=cls.company,construction_object=cls.obj,month=cls.day.replace(day=1),status='APPLIED',revision=1,created_by=cls.users['ADMIN'])
        DailyScheduleEntry.objects.create(schedule=cls.schedule,key=f'work:{cls.work.pk}',kind='work',label=cls.work.name,metadata={'id':cls.work.pk,'kind':'SIMPLE'},rows=[{'date':str(cls.day),'item_id':None,'quantity':'5'}])

    def payload(self,code,section):
        self.client.force_login(self.users[code])
        response=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':section})
        self.assertEqual(response.status_code,200)
        data={'construction_object':str(self.obj.pk),'date':str(self.day),'section':section,'revision':str(response.context['status'].revision),'action':'save'}
        if section=='works':
            data['labor_revision']=str(response.context['labor_revision'])
            data['production_snapshot']=response.context['production_snapshot']
        for group in response.context['groups']:
            fs=group['formset']
            for field in fs.management_form:data[field.html_name]=field.value()
            for form in fs:
                for field in form:
                    if field.name=='DELETE':continue
                    value=field.value()
                    data[field.html_name]=value if isinstance(value,list) else str(value) if value is not None and value is not False else ''
        return data

    def test_work_deviation_confirmation_and_revision(self):
        data=self.payload('DISPATCHER','works');data['works-0-actual_quantity']='3'
        data['action']='confirm'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        fact=DailyFact.objects.get();self.assertFalse(fact.outside_smg);self.assertEqual(fact.smg_plan_quantity,5)
        self.assertTrue(FactSectionDay.objects.get(section='works').confirmed_at)
        self.assertEqual(self.client.post(self.url,data).status_code,400)

    def test_outside_smg_saves_without_reason(self):
        self.schedule.entries.all().delete()
        data=self.payload('DISPATCHER','works');data.update({'works-0-project_work':self.work.pk,'works-0-actual_quantity':'2'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertIsNone(DailyFact.objects.get().deviation_reason_id)
        self.assertTrue(DailyFact.objects.get().outside_smg)
        self.assertContains(self.client.get(reverse('production:fact_list'),{'construction_object':self.obj.pk,'start':str(self.day),'end':str(self.day)}),'Вне СМГ')

    def test_roles_and_assigned_objects(self):
        self.client.force_login(self.users['PERSONNEL_DISPATCHER'])
        self.assertEqual(self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'}).status_code,403)
        response=self.client.get(self.url,{'construction_object':self.other.pk,'date':str(self.day)})
        self.assertFalse(response.context['selector'].is_valid())
        self.assertEqual(self.client.post(reverse('production:equipment_create'),{}).status_code,403)

    def test_person_requires_work_or_reason_and_projects_counts(self):
        data=self.payload('PERSONNEL_DISPATCHER','labor');data.update({'labor-0-employee':self.employee.pk})
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        data['labor-0-work']=self.work.pk;data['action']='confirm'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,302, getattr(response,'context',None))
        self.assertEqual(EmployeeActivity.objects.get().position,self.employee.position)
        self.assertEqual(LaborFact.objects.get().actual_workers,1)
        self.assertIsNone(LaborFact.objects.get().actual_hours)

    def test_general_staff_without_work_and_no_duplicate_shift(self):
        data=self.payload('PERSONNEL_DISPATCHER','labor');data.update({'labor-0-employee':self.employee.pk,'labor-0-general':'on'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.users['PERSONNEL_DISPATCHER'].assigned_objects.add(self.other)
        EmployeeActivity.objects.filter(employee=self.employee).update(construction_object=self.other)
        data=self.payload('PERSONNEL_DISPATCHER','labor');data.update({'labor-0-employee':self.employee.pk,'labor-0-general':'on'})
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertEqual(EmployeeActivity.objects.count(),1)

    def test_repair_and_object_fuel_balance(self):
        data=self.payload('MECHANIZATION_DISPATCHER','mechanization')
        data.update({'equipment-0-equipment':self.machine.pk,'equipment-0-state':'REPAIR','equipment-0-activity':'Замена насоса'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(EquipmentActivity.objects.get().state,'REPAIR')
        self.assertEqual(EquipmentFact.objects.get().actual_count,0)
        self.assertIsNone(EquipmentFact.objects.get().machine_hours)
        self.assertFalse(FuelFact.objects.exists())
        self.client.force_login(self.users['ADMIN'])
        self.assertEqual(self.client.post(self.url,{'section':'fuel'}).status_code,403)
        self.assertFalse(FuelFact.objects.exists())

    def test_independent_confirmations_and_reports_do_not_double_count(self):
        data=self.payload('PERSONNEL_DISPATCHER','labor');data.update({'labor-0-employee':self.employee.pk,'labor-0-general':'on','action':'confirm'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        data=self.payload('DISPATCHER','works');data['works-0-actual_quantity']='5'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertIsNotNone(FactSectionDay.objects.get(section='labor').confirmed_at)
        data=self.payload('PERSONNEL_DISPATCHER','labor')
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(LaborFact.objects.count(),1);self.assertEqual(LaborFact.objects.get().actual_workers,1)

    def test_no_deleting_foreign_row_by_forged_id(self):
        other=EmployeeActivity.objects.create(company=self.company,construction_object=self.other,date=self.day,employee=self.employee,position=self.employee.position,general=True)
        data=self.payload('PERSONNEL_DISPATCHER','labor');data.update({'labor-INITIAL_FORMS':'1','labor-0-id':other.pk,'labor-0-DELETE':'on'})
        self.client.post(self.url,data)
        self.assertTrue(EmployeeActivity.objects.filter(pk=other.pk).exists())

    def test_daily_tasks_are_created_for_all_directions(self):
        from apps.planning.task_services import generate_daily,validate_fact
        from django.core.exceptions import ValidationError
        for role in ['DISPATCHER','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER']:
            self.assertEqual(generate_daily(self.users[role],today=self.day),1)
        self.assertEqual(WorkTask.objects.filter(kind='FACT').count(),3)
        task=WorkTask.objects.filter(assigned_to=self.users['PERSONNEL_DISPATCHER']).get()
        with self.assertRaises(ValidationError):validate_fact(task)

    def test_actuation_is_saved_on_the_selected_work(self):
        from .models import DayActuation
        data=self.payload('DISPATCHER','works')
        data.update({'works-0-actuation_type':'FULL_DAY','works-0-actuation_reason':'Погодные условия','works-0-actual_quantity':'5','action':'confirm'})
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,302)
        fact=DailyFact.objects.get()
        self.assertEqual(fact.actual_quantity,0)
        self.assertEqual(fact.actuation_type,'FULL_DAY')
        self.assertEqual(fact.actuation_reason,'Погодные условия')
        self.assertFalse(DayActuation.objects.exists())

    def test_legacy_totals_need_explicit_replacement(self):
        LaborFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,brigade=self.brigade,actual_workers=7)
        data=self.payload('PERSONNEL_DISPATCHER','labor')
        data.update({'labor-0-employee':self.employee.pk,'labor-0-general':'on'})
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertEqual(LaborFact.objects.get().actual_workers,7)
        data['replace_legacy']='on'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(LaborFact.objects.get().actual_workers,1)

    def test_no_work_person_reason_and_idle_reason(self):
        data=self.payload('PERSONNEL_DISPATCHER','labor')
        data.update({'labor-0-employee':self.employee.pk,'labor-0-activity':'Ожидание фронта работ'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        data=self.payload('MECHANIZATION_DISPATCHER','mechanization')
        data.update({'equipment-0-equipment':self.machine.pk,'equipment-0-state':'IDLE'})
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        data['equipment-0-activity']='Ожидание материалов'
        self.assertEqual(self.client.post(self.url,data).status_code,302)

    def test_general_flag_cannot_bypass_smg_work_assignment(self):
        from apps.planning.models import WorkResourceAssignment
        WorkResourceAssignment.objects.create(company=self.company,schedule=self.schedule,work=self.work,position=self.employee.position,start_date=self.day,end_date=self.day,count=1)
        data=self.payload('PERSONNEL_DISPATCHER','labor')
        data.update({'labor-0-employee':self.employee.pk,'labor-0-general':'on'})
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        data['labor-0-general']='';data['labor-0-work']=self.work.pk
        self.assertEqual(self.client.post(self.url,data).status_code,302)

    def test_late_generated_tasks_respect_section_confirmation(self):
        from apps.planning.task_services import generate_daily
        from datetime import timedelta
        data=self.payload('PERSONNEL_DISPATCHER','labor')
        data['action']='confirm'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        # Login middleware may already have advanced the cursor beyond this fixed fixture day.
        from apps.planning.models import DailyTaskCursor
        DailyTaskCursor.objects.filter(user=self.users['PERSONNEL_DISPATCHER'],construction_object=self.obj).update(last_date=self.day-timedelta(days=1))
        generate_daily(self.users['PERSONNEL_DISPATCHER'],today=self.day+timedelta(days=1))
        self.assertEqual(WorkTask.objects.get(kind='FACT',fact_date=self.day).status,'DONE')

    def test_mechanization_cannot_move_machine_away_from_recorded_crew(self):
        EmployeeActivity.objects.create(company=self.company,construction_object=self.other,date=self.day,shift=1,employee=self.employee,position=self.employee.position,equipment=self.machine,general=True)
        data=self.payload('MECHANIZATION_DISPATCHER','mechanization')
        data.update({'equipment-0-equipment':self.machine.pk,'equipment-0-state':'REPAIR','equipment-0-activity':'Ремонт'})
        self.assertEqual(self.client.post(self.url,data).status_code,400)
        self.assertFalse(EquipmentActivity.objects.exists())

    def test_concrete_equipment_catalog_and_company_scope(self):
        self.client.force_login(self.users['ADMIN'])
        url=reverse('resources:equipment_unit_list')
        self.assertContains(self.client.get(url),'123')
        data={'type':self.type.pk,'plate_number':'NEW-456','name':'Новая машина','is_active':'on'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,data).status_code,400)
        foreign=Company.objects.create(name='Foreign fleet')
        other_type=EquipmentType.objects.create(company=foreign,name='Foreign machine')
        data.update(type=other_type.pk,plate_number='FOREIGN')
        self.assertEqual(self.client.post(url,data).status_code,400)
        self.assertFalse(Equipment.objects.filter(plate_number='FOREIGN').exists())

        EquipmentActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment=self.machine,state='REPAIR',activity='Ремонт')
        edit=reverse('resources:equipment_unit_update',args=[self.machine.pk])
        self.assertEqual(self.client.post(edit,{'type':self.type.pk,'plate_number':'CHANGED','name':'Name','is_active':'on'}).status_code,400)
        self.machine.refresh_from_db();self.assertEqual(self.machine.plate_number,'123')

    def test_confirmed_zero_personnel_is_explicit_in_reports(self):
        DailyScheduleEntry.objects.create(schedule=self.schedule,key='labor:test',kind='labor',label='Driver',rows=[{'date':str(self.day),'brigade_id':self.brigade.pk,'planned_workers':'3'}])
        data=self.payload('PERSONNEL_DISPATCHER','labor')
        data.update({'action':'confirm','deviation_comment':'Персонал не прибыл'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(LaborFact.objects.get().actual_workers,0)
        response=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'labor'})
        self.assertFalse(response.context['legacy_exists'])

    def test_work_reason_is_absent_and_obsolete_posted_value_is_ignored(self):
        data=self.payload('DISPATCHER','works')
        page=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'})
        self.assertNotContains(page,'name="works-0-deviation_reason"')
        self.assertNotContains(page,'data-deviation-dialog')
        data['works-0-actual_quantity']='8';data['works-0-deviation_reason']='invalid-obsolete-field'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        fact=DailyFact.objects.get();self.assertEqual(fact.actual_quantity,8)
        self.assertIsNone(fact.deviation_reason_id);self.assertEqual(fact.comment,'')
