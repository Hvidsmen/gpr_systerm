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

    def save(self, *args, **kwargs):
        from django.db import router, transaction
        from .codes import PREFIXES, allocate_code
        if self._meta.label_lower in PREFIXES and not self.code:
            using = kwargs.get('using') or router.db_for_write(type(self), instance=self)
            with transaction.atomic(using=using):
                self.code = allocate_code(self, using)
                if kwargs.get('update_fields') is not None:
                    kwargs['update_fields'] = set(kwargs['update_fields']) | {'code'}
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    class Meta:
        abstract = True

class CodeSequence(models.Model):
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE)
    model_label = models.CharField(max_length=100)
    last_number = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'model_label'], name='unique_company_code_sequence')]
