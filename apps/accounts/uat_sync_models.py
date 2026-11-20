"""Persistent, company-scoped mirror of the read-only UAT source."""
from django.db import models
from core.models import BaseCompanyModel


MATCH_STATES = [
    ('MATCHED', 'Сопоставлено'),
    ('CREATED', 'Создано из УАТ — требует проверки'),
    ('UNMATCHED', 'Не найдено'),
    ('AMBIGUOUS', 'Несколько совпадений'),
]
FACT_STATES = [
    ('CREATED', 'Добавлено в факт'),
    ('PENDING', 'Требует сопоставления'),
    ('CONFLICT', 'Конфликт с ручным фактом'),
    ('NOT_POSTED', 'Не проведён'),
    ('EMPTY', 'Нет заданий'),
]
DRIVER_STATES = [
    ('MATCHED', 'Найден по табельному номеру'),
    ('NOT_FOUND', 'Сотрудник не найден'),
    ('AMBIGUOUS', 'Несколько сотрудников'),
    ('EMPTY', 'Не указан'),
]


class UatDepartment(BaseCompanyModel):
    source_id = models.CharField(max_length=64)
    code = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=300)
    construction_object = models.ForeignKey(
        'projects.ConstructionObject', on_delete=models.PROTECT, null=True, blank=True,
        related_name='uat_departments', verbose_name='Строительный объект портала',
    )
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['company', 'source_id'], name='unique_uat_department_source')]

    def __str__(self):
        return self.name


class UatVehicle(BaseCompanyModel):
    source_id = models.CharField(max_length=64)
    name = models.CharField(max_length=300)
    plate = models.CharField(max_length=80, blank=True)
    vin = models.CharField(max_length=120, blank=True)
    garage_number = models.CharField(max_length=80, blank=True)
    equipment = models.ForeignKey(
        'resources.Equipment', on_delete=models.PROTECT, null=True, blank=True,
        related_name='uat_sources', verbose_name='Техника портала',
    )
    match_status = models.CharField(max_length=12, choices=MATCH_STATES, default='UNMATCHED')
    match_basis = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['company', 'source_id'], name='unique_uat_vehicle_source')]

    def __str__(self):
        return self.name


class UatWaybill(BaseCompanyModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='uat_waybills')
    source_id = models.CharField(max_length=64)
    number = models.CharField(max_length=80, blank=True)
    document_date = models.DateField()
    posted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    vehicle = models.ForeignKey(UatVehicle, on_delete=models.PROTECT, null=True, blank=True, related_name='waybills')
    vehicle_name = models.CharField(max_length=300, blank=True)
    plate = models.CharField(max_length=80, blank=True)
    driver_1_ref = models.CharField(max_length=64, blank=True)
    driver_1_number = models.CharField(max_length=80, blank=True)
    driver_1_name = models.CharField(max_length=300, blank=True)
    driver_1 = models.ForeignKey('resources.Employee', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    driver_1_status = models.CharField(max_length=12, choices=DRIVER_STATES, default='EMPTY')
    driver_2_ref = models.CharField(max_length=64, blank=True)
    driver_2_number = models.CharField(max_length=80, blank=True)
    driver_2_name = models.CharField(max_length=300, blank=True)
    driver_2 = models.ForeignKey('resources.Employee', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    driver_2_status = models.CharField(max_length=12, choices=DRIVER_STATES, default='EMPTY')
    departure = models.DateTimeField(null=True, blank=True)
    returned = models.DateTimeField(null=True, blank=True)
    department = models.ForeignKey(UatDepartment, on_delete=models.PROTECT, null=True, blank=True, related_name='waybills')
    department_name = models.CharField(max_length=300, blank=True)
    project_name = models.CharField(max_length=300, blank=True)
    site_name = models.CharField(max_length=300, blank=True)
    object_name = models.CharField(max_length=300, blank=True)
    object_code = models.CharField(max_length=80, blank=True)
    organisation = models.CharField(max_length=300, blank=True)
    odometer_departure = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    odometer_return = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    comment = models.TextField(blank=True)
    fact_status = models.CharField(max_length=12, choices=FACT_STATES, default='PENDING')
    fact_message = models.CharField(max_length=500, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-document_date', '-number', '-pk']
        constraints = [models.UniqueConstraint(fields=['company', 'source_id'], name='unique_uat_waybill_source')]
        indexes = [models.Index(fields=['company', 'document_date'], name='uat_waybill_company_date')]


class UatWaybillTask(BaseCompanyModel):
    waybill = models.ForeignKey(UatWaybill, on_delete=models.CASCADE, related_name='tasks')
    line_number = models.PositiveIntegerField()
    department = models.ForeignKey(UatDepartment, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    department_name = models.CharField(max_length=300, blank=True)
    project_name = models.CharField(max_length=300, blank=True)
    site_name = models.CharField(max_length=300, blank=True)
    object_name = models.CharField(max_length=300, blank=True)
    object_code = models.CharField(max_length=80, blank=True)
    departure_address = models.CharField(max_length=500, blank=True)
    arrival_address = models.CharField(max_length=500, blank=True)
    completed = models.BooleanField(default=False)
    trips = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    loaded_km = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    empty_km = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    tonnes = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    hours = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    fact = models.ForeignKey('production.EquipmentFact', on_delete=models.SET_NULL, null=True, blank=True, related_name='uat_tasks')

    class Meta:
        ordering = ['waybill_id', 'line_number']
        constraints = [models.UniqueConstraint(fields=['waybill', 'line_number'], name='unique_uat_waybill_task')]


class UatRepairSheet(BaseCompanyModel):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE, related_name='uat_repair_sheets')
    source_id = models.CharField(max_length=64)
    number = models.CharField(max_length=80, blank=True)
    document_date = models.DateField()
    posted = models.BooleanField(default=False)
    completed = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    vehicle = models.ForeignKey(UatVehicle, on_delete=models.PROTECT, null=True, blank=True, related_name='repair_sheets')
    vehicle_name = models.CharField(max_length=300, blank=True)
    plate = models.CharField(max_length=80, blank=True)
    service_kind = models.CharField(max_length=300, blank=True)
    started = models.DateTimeField(null=True, blank=True)
    finished = models.DateTimeField(null=True, blank=True)
    master = models.CharField(max_length=300, blank=True)
    organisation = models.CharField(max_length=300, blank=True)
    department = models.ForeignKey(UatDepartment, on_delete=models.PROTECT, null=True, blank=True, related_name='repair_sheets')
    department_name = models.CharField(max_length=300, blank=True)
    repair_place = models.CharField(max_length=300, blank=True)
    reason = models.TextField(blank=True)
    comment = models.TextField(blank=True)
    works = models.JSONField(default=list, blank=True)
    materials = models.JSONField(default=list, blank=True)
    executors = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-document_date', '-number', '-pk']
        constraints = [models.UniqueConstraint(fields=['company', 'source_id'], name='unique_uat_repair_source')]
        indexes = [models.Index(fields=['company', 'document_date'], name='uat_repair_company_date')]
