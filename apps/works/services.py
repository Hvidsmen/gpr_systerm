from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction


class WorkItemGeneratorService:
    """Автоматическое создание подработ из шаблона."""

    @staticmethod
    @transaction.atomic
    def generate_from_template(project_work):
        """
        Создать подработы (ProjectWorkItem) на основе шаблона работы.

        Формулы:
        - quantity_per_unit = template_item.quantity_per_unit (норматив)
        - planned_quantity = work.planned_quantity × quantity_per_unit (вычисляется динамически)
        - planned_value = work.planned_value × (weight / 100)
        """
        if not project_work.template:
            return []

        from django.core.exceptions import ValidationError
        if project_work.kind != 'COMPOSITE':
            raise ValidationError('Подработы создаются только для составной работы.')
        if project_work.daily_facts.exists() or project_work.monthly_plans.filter(versions__daily_plans__isnull=False).exists():
            raise ValidationError('Нельзя заменять подработы с существующим планом или фактом.')
        # Удаляем старые подработы
        project_work.items.all().delete()

        # Получаем текущую версию шаблона
        template_version = project_work.template.versions.filter(is_current=True).first()
        if not template_version:
            template_version = project_work.template.versions.order_by('-version_number').first()

        if not template_version:
            return []

        template_items = list(template_version.items.order_by('sequence'))
        if not template_items:
            return []

        company = project_work.company

        created_items = []
        sequence = 0

        for t_item in template_items:
            sequence += 1

            # Норматив подработы на единицу работы
            qty_per_unit = Decimal(str(t_item.quantity_per_unit))
            if qty_per_unit <= 0:
                raise ValidationError('Норматив шаблона должен быть больше нуля.')

            # Создаём подработу с нормативом
            item = project_work.items.create(
                company=company,
                sequence=sequence,
                name=t_item.name,
                unit=t_item.unit,
                weight=Decimal(str(t_item.weight)) if t_item.weight else Decimal('0'),
                quantity_per_unit=qty_per_unit,  # ← НОРМАТИВ
                load_profile=t_item.load_profile,
                parent=None,
                # Копируем нормативы ресурсов из шаблона
                labor_norm_per_unit=Decimal(str(t_item.labor_norm_per_unit)) if t_item.labor_norm_per_unit else Decimal(
                    '0'),
                equipment_norm_per_unit=Decimal(
                    str(t_item.equipment_norm_per_unit)) if t_item.equipment_norm_per_unit else Decimal('0'),
                fuel_norm_per_unit=Decimal(str(t_item.fuel_norm_per_unit)) if t_item.fuel_norm_per_unit else Decimal(
                    '0'),
                labor_hourly_rate=Decimal(str(t_item.labor_hourly_rate)) if t_item.labor_hourly_rate else Decimal(
                    '350'),
                equipment_hourly_rate=Decimal(
                    str(t_item.equipment_hourly_rate)) if t_item.equipment_hourly_rate else Decimal('1500'),
                fuel_price=Decimal(str(t_item.fuel_price)) if t_item.fuel_price else Decimal('65'),
            )
            created_items.append(item)

        return created_items