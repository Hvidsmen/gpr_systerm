from django import template
from core.object_tree import options_for

register = template.Library()


@register.inclusion_tag('forms/object_options.html')
def object_options(objects, selected=None):
    return {'options':options_for(objects,selected)}
