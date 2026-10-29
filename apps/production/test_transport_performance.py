"""Guard roster query cost against growth with every visible machine."""
from collections import Counter
from time import perf_counter
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.resources.models import Equipment
from .transport_models import EquipmentCampStay


class TransportPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_transport import TransportTests
        TransportTests.setUpTestData.__func__(cls)
    def test_queries_for_fifty_machines(self):
        self.client.force_login(self.operator)
        machines=[self.machine,*Equipment.objects.bulk_create([Equipment(company=self.company,type=self.type,name='Machine '+str(i),inventory_number='PERF-'+str(i)) for i in range(49)])]
        EquipmentCampStay.objects.bulk_create([EquipmentCampStay(company=self.company,camp=self.camp,equipment=machine,arrived_on=self.day) for machine in machines])
        for tab in ['state','day','calendar','report']:
            data={'camp':self.camp.pk,'date':str(self.day),'tab':tab}
            self.client.get(self.board,data)
            start=perf_counter()
            with CaptureQueriesContext(connection) as queries:response=self.client.get(self.board,data)
            self.assertEqual(response.status_code,200)
            self.assertLessEqual(len(queries),55,'Roster SQL queries must not grow per machine')
            print('TRANSPORT PROFILE',tab,len(queries),'queries',round(perf_counter()-start,3),'seconds',len(response.content),'bytes',flush=True)
            counts=Counter(query['sql'].split(' FROM ')[1].split(' ')[0] for query in queries if ' FROM ' in query['sql'])
            print(counts.most_common(8),flush=True)
