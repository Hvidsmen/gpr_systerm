from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company
from apps.projects.models import Project, ConstructionObject, Section
from .models import ProjectWork, ProjectWorkItem
from apps.planning import test_workspace


class WorkListFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_workspace.WorkspaceTests.setUpTestData.__func__(cls)
        cls.other_project = Project.objects.create(
            company=cls.company, name="Other project"
        )
        cls.other_obj = ConstructionObject.objects.create(
            company=cls.company, project=cls.other_project, name="Other object"
        )
        cls.other_section = Section.objects.create(
            company=cls.company, construction_object=cls.other_obj, name="Other section"
        )
        cls.other_work = ProjectWork.objects.create(
            company=cls.company,
            section=cls.other_section,
            name="Unique alternate work",
            unit="м",
        )
        cls.foreign_company = Company.objects.create(name="Foreign company")
        cls.foreign_project = Project.objects.create(
            company=cls.foreign_company, name="Foreign project"
        )
        cls.foreign_obj = ConstructionObject.objects.create(
            company=cls.foreign_company,
            project=cls.foreign_project,
            name="Foreign object",
        )

    def setUp(self):
        self.client.force_login(self.planner)

    def get(self, **params):
        response = self.client.get(reverse("works:work_list"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def ids(self, **params):
        return {work.pk for work in self.get(**params).context["works"]}

    def test_project_object_and_section_filters(self):
        for params in [
            {"project": self.obj.project_id},
            {"construction_object": self.obj.pk},
            {"section": self.simple.section_id},
        ]:
            self.assertEqual(self.ids(**params), {self.simple.pk, self.composite.pk})
        self.assertEqual(self.ids(project=self.other_project.pk), {self.other_work.pk})
        self.assertEqual(
            self.ids(
                project=self.obj.project_id,
                construction_object=self.obj.pk,
                section=self.simple.section_id,
            ),
            {self.simple.pk, self.composite.pk},
        )

    def test_work_and_subwork_search_with_combined_conditions(self):
        self.assertEqual(self.ids(work="Simple"), {self.simple.pk})
        self.assertEqual(self.ids(subwork="A"), {self.composite.pk})
        self.assertFalse(self.ids(work="Simple", subwork="A"))
        self.assertEqual(
            self.ids(work="Composite", subwork="A", project=self.obj.project_id),
            {self.composite.pk},
        )

    def test_subwork_search_does_not_duplicate_rows_or_inflate_count(self):
        ProjectWorkItem.objects.create(
            company=self.company,
            project_work=self.composite,
            name="Another A",
            unit="м",
        )
        response = self.get(subwork="A")
        self.assertEqual(len(response.context["works"]), 1)
        self.assertEqual(response.context["works"][0].items_count, 3)

    def test_cascading_choices_and_foreign_company_excluded(self):
        form = self.get(
            project=self.obj.project_id, construction_object=self.obj.pk
        ).context["filter_form"]
        self.assertEqual(
            set(
                form.fields["construction_object"].queryset.values_list("pk", flat=True)
            ),
            {self.obj.pk},
        )
        self.assertEqual(
            set(form.fields["section"].queryset.values_list("pk", flat=True)),
            {self.simple.section_id},
        )
        self.assertNotIn(
            self.foreign_project.pk,
            form.fields["project"].queryset.values_list("pk", flat=True),
        )
        self.assertFalse(self.ids(project=self.foreign_project.pk))
        self.assertFalse(self.ids(construction_object=self.foreign_obj.pk))
        self.assertFalse(
            self.ids(project=self.obj.project_id, construction_object=self.other_obj.pk)
        )
        self.assertFalse(self.ids(project="invalid"))

    def test_pagination_preserves_filters_and_reset_link(self):
        for i in range(22):
            ProjectWork.objects.create(
                company=self.company,
                section=self.simple.section,
                name=f"Page work {i}",
                unit="м",
            )
        response = self.get(project=self.obj.project_id, work="Page work")
        self.assertTrue(response.context["is_paginated"])
        self.assertContains(
            response, f"project={self.obj.project_id}&amp;work=Page+work&amp;page=2"
        )
        self.assertEqual(
            len(
                self.get(project=self.obj.project_id, work="Page work", page=2).context[
                    "works"
                ]
            ),
            2,
        )
        self.assertContains(response, "Сбросить")

    def test_russian_search_ignores_case_and_treats_punctuation_literally(self):
        ProjectWork.objects.filter(pk=self.simple.pk).update(name="Монтаж (АКЗ)")
        ProjectWorkItem.objects.filter(pk=self.a.pk).update(name="Покраска [АКЗ]")
        self.assertEqual(self.ids(work="монтаж (акз)"), {self.simple.pk})
        self.assertEqual(self.ids(subwork="покраска [акз]"), {self.composite.pk})
        self.assertFalse(self.ids(work=".*"))
