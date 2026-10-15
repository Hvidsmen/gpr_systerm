from django.db import models
from core.models import BaseCompanyModel


class EstimateObject(BaseCompanyModel):
    name = models.CharField('Объект смет', max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='estimate_object_name')]

    def __str__(self):
        return self.name


class EstimateCatalog(BaseCompanyModel):
    """Independent identities; deliberately no links to GPR catalogs."""
    kind = models.CharField(max_length=20, choices=[('work', 'Работы'), ('resource', 'Ресурсы'), ('unit', 'Единицы измерения')])
    identity = models.CharField(max_length=64)
    code = models.TextField(blank=True)
    name = models.TextField()
    unit = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'kind', 'identity'], name='estimate_catalog_identity')]


class EstimateDocument(BaseCompanyModel):
    estimate_object = models.ForeignKey(EstimateObject, on_delete=models.PROTECT, related_name='estimates')
    filename = models.CharField(max_length=255)
    digest = models.CharField(max_length=64)
    title = models.TextField()
    sheet = models.CharField(max_length=100)
    base_total = models.DecimalField(max_digits=24, decimal_places=2, null=True)
    current_total = models.DecimalField(max_digits=24, decimal_places=2, null=True)
    warnings = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'estimate_object', 'digest'], name='estimate_document_digest')]


class EstimateRow(models.Model):
    document = models.ForeignKey(EstimateDocument, on_delete=models.CASCADE, related_name='rows')
    source_row = models.PositiveIntegerField()
    section = models.TextField(blank=True)
    kind = models.CharField(max_length=20)
    parent_row = models.PositiveIntegerField(null=True)
    catalog = models.ForeignKey(EstimateCatalog, null=True, on_delete=models.PROTECT)
    values = models.JSONField()

    class Meta:
        ordering = ['source_row']
        constraints = [models.UniqueConstraint(fields=['document', 'source_row'], name='estimate_source_row')]
