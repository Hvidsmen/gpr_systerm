"""Exercise the upgrade on an isolated database, including ambiguous assignments."""

from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path
from django.test import SimpleTestCase
from django.db import connection
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor


class ResourceMigrationTests(SimpleTestCase):
    databases = {"default"}

    def test_upgrade_preserves_all_originals_and_quarantines_ambiguous_and_colliding_rows(
        self,
    ):
        with TemporaryDirectory() as directory:
            settings = {
                **connection.settings_dict,
                "NAME": str(Path(directory) / "migration.sqlite3"),
            }
            db = DatabaseWrapper(settings, alias="migration_probe")
            from django.db import connections

            connections["migration_probe"] = db
            try:
                old_targets = [
                    ("production", "0006_alter_equipmentfact_options_and_more"),
                    ("works", "0003_projectworkitem_quantity_per_unit_and_more"),
                    ("planning", "0002_initial"),
                ]
                executor = MigrationExecutor(db)
                executor.migrate(old_targets)
                state = executor.loader.project_state(old_targets).apps

                def create(app, model_name, **values):
                    model = state.get_model(app, model_name)
                    # Historical managers use their connection alias; route to the isolated wrapper.
                    return model.objects.using("migration_probe").create(**values)

                company = create("accounts", "Company", name="Migration")
                project = create(
                    "projects", "Project", company_id=company.pk, code="one", name="one"
                )
                obj = create(
                    "projects",
                    "ConstructionObject",
                    company_id=company.pk,
                    project_id=project.pk,
                    code="o",
                    name="o",
                )
                section = create(
                    "projects",
                    "Section",
                    company_id=company.pk,
                    construction_object_id=obj.pk,
                    code="s",
                    name="s",
                )
                a = create(
                    "works",
                    "ProjectWork",
                    company_id=company.pk,
                    section_id=section.pk,
                    code="a",
                    name="a",
                    unit="m",
                )
                b = create(
                    "works",
                    "ProjectWork",
                    company_id=company.pk,
                    section_id=section.pk,
                    code="b",
                    name="b",
                    unit="m",
                )
                brigade = create(
                    "resources", "Brigade", company_id=company.pk, code="b", name="b"
                )
                create(
                    "production",
                    "LaborPlan",
                    company_id=company.pk,
                    project_id=project.pk,
                    brigade_id=brigade.pk,
                    date=date(2026, 1, 1),
                    planned_workers=7,
                )
                ambiguous = create(
                    "projects", "Project", company_id=company.pk, code="two", name="two"
                )
                for code in ["x", "y"]:
                    create(
                        "projects",
                        "ConstructionObject",
                        company_id=company.pk,
                        project_id=ambiguous.pk,
                        code=code,
                        name=code,
                    )
                create(
                    "production",
                    "FuelPlan",
                    company_id=company.pk,
                    project_id=ambiguous.pk,
                    date=date(2026, 1, 1),
                    planned_liters=42,
                )
                for work in [a, b]:
                    create(
                        "production",
                        "FuelPlan",
                        company_id=company.pk,
                        project_id=project.pk,
                        project_work_id=work.pk,
                        date=date(2026, 1, 2),
                        planned_liters=10,
                    )
                executor = MigrationExecutor(db)
                latest = executor.loader.graph.leaf_nodes()
                executor.migrate(latest)
                new = executor.loader.project_state(latest).apps
                archive = new.get_model(
                    "production", "LegacyResourceRecord"
                ).objects.using("migration_probe")
                self.assertEqual(archive.count(), 4)
                self.assertEqual(archive.filter(resolved=True).count(), 1)
                labor = (
                    new.get_model("production", "LaborPlan")
                    .objects.using("migration_probe")
                    .get()
                )
                self.assertEqual(labor.construction_object_id, obj.pk)
                self.assertEqual(labor.planned_workers, 7)
                self.assertFalse(
                    new.get_model("production", "FuelPlan")
                    .objects.using("migration_probe")
                    .exists()
                )
                self.assertEqual(
                    sorted(
                        float(r.payload["planned_liters"])
                        for r in archive.filter(source_model="FuelPlan")
                    ),
                    [10, 10, 42],
                )
                self.assertEqual(
                    archive.filter(reason__contains="Совпадают").count(), 2
                )
            finally:
                db.close()
                from django.db import connections

                if hasattr(connections._connections, "migration_probe"):
                    del connections["migration_probe"]
