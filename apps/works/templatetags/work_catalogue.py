import re
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def highlight(value, query):
    text = str(value or '')
    query = str(query or '').strip()
    if not query:return escape(text)
    parts, position = [], 0
    for match in re.finditer(re.escape(query), text, re.IGNORECASE):
        parts.extend([str(escape(text[position:match.start()])), '<mark>', str(escape(match.group())), '</mark>'])
        position = match.end()
    parts.append(str(escape(text[position:])))
    return mark_safe(''.join(parts))
