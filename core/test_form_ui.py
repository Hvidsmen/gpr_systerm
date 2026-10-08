from django import forms
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class FormLayoutTests(SimpleTestCase):
    class ExampleForm(forms.Form):
        token = forms.CharField(widget=forms.HiddenInput)
        title = forms.CharField(label="Название", help_text="Укажите название")
        description = forms.CharField(required=False, widget=forms.Textarea)
        objects = forms.MultipleChoiceField(choices=[("1", "Объект")], required=False)
        active = forms.BooleanField(required=False)

    def test_preserves_hidden_values_labels_and_help(self):
        html = render_to_string("forms/fields.html", {"form": self.ExampleForm(initial={"token": "opaque"})})
        self.assertIn('type="hidden" name="token" value="opaque"', html)
        self.assertIn('for="id_title"', html)
        self.assertIn('id="id_title_helptext"', html)
        self.assertIn('aria-describedby="id_title_helptext"', html)
        self.assertEqual(html.count("app-form-wide"), 2)
        self.assertIn('class="app-form-check"', html)

    def test_exposes_bound_errors_and_keeps_entered_values(self):
        form = self.ExampleForm({"token": "opaque", "description": "Мой комментарий"})
        self.assertFalse(form.is_valid())
        html = render_to_string("forms/fields.html", {"form": form})
        self.assertIn('id="id_title_error" role="alert"', html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn("app-field-invalid", html)
        self.assertIn("Мой комментарий", html)
