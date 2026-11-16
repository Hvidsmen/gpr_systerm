import json
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from . import entry_concurrency


class FakeMutex:
    def __init__(self,acquired):
        self.acquired=acquired
        self.released=False

    def acquire(self,timeout=None):
        self.timeout=timeout
        return self.acquired

    def release(self):
        self.released=True


class SqliteSerializedTests(SimpleTestCase):
    def setUp(self):
        self.factory=RequestFactory()

    def test_busy_writer_returns_retryable_json_without_calling_view(self):
        mutex=FakeMutex(False)
        view=Mock(return_value=HttpResponse('unexpected'))
        request=self.factory.post('/production/save/',data='{}',content_type='application/json')
        with patch.object(entry_concurrency,'connection',Mock(vendor='sqlite')), patch.object(entry_concurrency,'_sqlite_mutex',mutex):
            response=entry_concurrency.sqlite_serialized(view)(request)
        self.assertEqual(response.status_code,429)
        self.assertEqual(response['Retry-After'],'2')
        self.assertTrue(json.loads(response.content)['retryable'])
        self.assertEqual(mutex.timeout,1)
        view.assert_not_called()

    def test_acquired_writer_is_released(self):
        mutex=FakeMutex(True)
        view=Mock(return_value=HttpResponse('ok'))
        request=self.factory.get('/production/day/')
        with patch.object(entry_concurrency,'connection',Mock(vendor='sqlite')), patch.object(entry_concurrency,'_sqlite_mutex',mutex):
            response=entry_concurrency.sqlite_serialized(view)(request)
        self.assertEqual(response.status_code,200)
        self.assertTrue(mutex.released)
        view.assert_called_once_with(request)
