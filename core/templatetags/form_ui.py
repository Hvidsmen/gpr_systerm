"""Presentation hints for the shared form layout."""
from django import forms, template

register = template.Library()


@register.filter
def wide_field(field):
    return isinstance(field.field.widget, (forms.Textarea, forms.SelectMultiple, forms.CheckboxSelectMultiple))


@register.filter
def checkbox_field(field):
    return isinstance(field.field.widget, forms.CheckboxInput)
