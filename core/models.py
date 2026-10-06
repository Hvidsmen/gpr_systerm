from django.db import models
from django.utils.translation import gettext_lazy as _


class BaseTimestampedModel(models.Model):
    created_at = models.DateTimeField(_('created at'), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        abstract = True


class BaseCompanyModel(BaseTimestampedModel):
    company = models.ForeignKey(
        'accounts.Company',
        on_delete=models.CASCADE,
        related_name='%(class)s_set',
        verbose_name=_('company')
    )

    class Meta:
        abstract = True