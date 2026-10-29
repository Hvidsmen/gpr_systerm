from io import BytesIO
from types import SimpleNamespace

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.accounts.models import Company, Role, User
from apps.accounts.staffing_sync import sync_states
from .models import Brigade, CatalogueReviewRequest
from .position_catalog import planning_brigades
from .zup_positions import sync_names


class ConfirmedStaffingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Confirmed outside ZUP')
        cls.user=User.objects.create_user(username='confirmed-staffing-reviewer',company=cls.company,
            role=Role.objects.get(code='PERSONNEL_CATALOG_MANAGER'))
        cls.current=Brigade.objects.create(company=cls.company,name='Актуальная из ЗУП',staffing_current=True)
        cls.manual=Brigade.objects.create(company=cls.company,name='Подтверждённый инженер')
        cls.retired=Brigade.objects.create(company=cls.company,name='Подтверждённый мастер',staffing_current=False)
        cls.pending=Brigade.objects.create(company=cls.company,name='Непроверенная должность',is_active=False)
        cls.rejected=Brigade.objects.create(company=cls.company,name='Отклонённая должность',is_active=False)
        cls.ordinary=Brigade.objects.create(company=cls.company,name='Обычная ручная должность')
        for brigade,status in [(cls.manual,'CONFIRMED'),(cls.retired,'CONFIRMED'),(cls.pending,'PENDING'),(cls.rejected,'REJECTED')]:
            CatalogueReviewRequest.objects.create(company=cls.company,kind='labor',brigade=brigade,status=status,
                created_by=cls.user,origin_label='ГПР объекта')
        other=Company.objects.create(name='Foreign confirmed staffing')
        user=User.objects.create_user(username='foreign-confirmed',company=other,role=cls.user.role)
        foreign=Brigade.objects.create(company=other,name='Чужая подтверждённая')
        CatalogueReviewRequest.objects.create(company=other,kind='labor',brigade=foreign,status='CONFIRMED',
            created_by=user,origin_label='Другой объект')

    def setUp(self):
        self.client.force_login(self.user)

    def test_confirmation_is_distinct_from_unknown_inactive_and_current_staffing(self):
        self.assertEqual(self.manual.staffing_status_label,'Подтверждена ответственным · отсутствует в ЗУП')
        self.assertEqual(self.retired.staffing_status_label,'Подтверждена ответственным · неактуальна в ЗУП')
        for brigade,label in [(self.current,'Актуальна'),(self.ordinary,'Не получено из ЗУП'),(self.pending,'Не получено из ЗУП'),(self.rejected,'Не получено из ЗУП')]:
            self.assertEqual(brigade.staffing_status_label,label)
            self.assertFalse(brigade.confirmed_outside_staffing)

    def test_default_current_view_shows_shortcut_and_filter_finds_confirmed_only(self):
        url=reverse('resources:brigade_list')
        page=self.client.get(url)
        self.assertEqual(list(page.context['brigades']),[self.current])
        self.assertEqual(page.context['position_confirmed_outside_count'],2)
        self.assertContains(page,'data-confirmed-staffing-link')
        self.assertContains(page,'staffing_current=confirmed')
        for view in ['groups','table']:
            page=self.client.get(url,{'staffing_current':'confirmed','view':view})
            self.assertEqual(set(page.context['brigades'].values_list('pk',flat=True)),{self.manual.pk,self.retired.pk})
            self.assertContains(page,self.manual.staffing_status_label)
            self.assertContains(page,self.retired.staffing_status_label)
            self.assertContains(page,'data-confirmed-staffing-help')
            self.assertNotContains(page,'Чужая подтверждённая')
        page=self.client.get(url,{'staffing_current':'confirmed','name':'инженер'})
        self.assertEqual(list(page.context['brigades']),[self.manual])

    def test_card_and_filtered_export_explain_confirmation(self):
        page=self.client.get(reverse('resources:brigade_update',args=[self.manual.pk]))
        self.assertContains(page,self.manual.staffing_status_label)
        self.assertContains(page,'Должность доступна для планирования по решению ответственного')
        response=self.client.get(reverse('resources:brigade_export'),{'staffing_current':'confirmed'})
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content))
        rows=list(book.active.values);headers=rows[0]
        names=headers.index('Название');status=headers.index('Штатное расписание ЗУП')
        self.assertEqual({row[names]:row[status] for row in rows[1:]},
            {self.manual.name:self.manual.staffing_status_label,self.retired.name:self.retired.staffing_status_label})
        book.close()

    def test_status_relation_is_loaded_once_for_list_and_export_rows(self):
        from .views import BrigadeListView
        from django.test import RequestFactory
        view=BrigadeListView();view.request=RequestFactory().get('/',{'staffing_current':''});view.request.user=self.user
        rows=list(view.get_queryset())
        with self.assertNumQueries(0):
            labels=[row.staffing_status_label for row in rows]
        self.assertIn(self.manual.staffing_status_label,labels)

    def test_sync_keeps_confirmation_and_reuses_position_when_zup_later_contains_it(self):
        config=SimpleNamespace(company_id=self.company.pk,server='server',database='zup')
        run=SimpleNamespace(connection=config,server='server',database='zup')
        sync_states(run,{self.current.name.casefold():True},complete=True)
        manual=Brigade.objects.get(pk=self.manual.pk)
        self.assertTrue(manual.is_active)
        self.assertIsNone(manual.staffing_current)
        self.assertTrue(manual.confirmed_outside_staffing)
        self.assertTrue(planning_brigades(self.company).filter(pk=manual.pk).exists())
        result,mapped=sync_names(self.company.pk,[manual.name])
        self.assertEqual(result['created'],0)
        self.assertEqual(mapped[manual.name.casefold()].pk,manual.position_mapping.position_id)
        sync_states(run,{manual.name.casefold():True},complete=True)
        manual=Brigade.objects.get(pk=manual.pk)
        self.assertEqual(manual.staffing_status_label,'Актуальна')
        self.assertFalse(manual.confirmed_outside_staffing)
        self.assertEqual(manual.catalogue_review.status,'CONFIRMED')
        # If ZUP subsequently retires it, confirmation remains explicit and usable.
        sync_states(run,{},complete=True)
        manual=Brigade.objects.get(pk=manual.pk)
        self.assertEqual(manual.staffing_status_label,'Подтверждена ответственным · неактуальна в ЗУП')
        self.assertTrue(planning_brigades(self.company).filter(pk=manual.pk).exists())
