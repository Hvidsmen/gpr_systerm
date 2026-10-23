"""Explicit alternate Excel names for existing catalogue records."""
import re
import unicodedata
from django import forms
from django.core.exceptions import ValidationError


def name_key(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or ''))).strip().casefold().rstrip(' .:-')


def validate_excel_aliases(value):
    if not isinstance(value,list) or len(value)>200 or any(not isinstance(v,str) or not name_key(v) or len(v)>500 for v in value):
        raise ValidationError('Укажите не более 200 названий Excel, каждое не длиннее 500 символов.')
    if len({name_key(v) for v in value})!=len(value):
        raise ValidationError('Названия Excel не должны повторяться.')


class ExcelAliasesField(forms.CharField):
    def __init__(self, **kwargs):
        super().__init__(label='Названия в Excel', required=False,
            help_text='По одному названию в строке. Несколько названий могут соответствовать этой записи. Основное название также распознаётся автоматически.',
            widget=forms.Textarea(attrs={'class':'form-control','rows':4}), **kwargs)

    def prepare_value(self,value):
        return '\n'.join(value) if isinstance(value,list) else value

    def clean(self,value):
        value=super().clean(value)
        aliases=[v.strip() for v in value.splitlines() if v.strip()]
        validate_excel_aliases(aliases)
        return aliases


def matches_name(item, name):
    key=name_key(name)
    return bool(key) and key in {name_key(item.name),*(name_key(v) for v in getattr(item,'excel_aliases',[]))}


def alias_matches(item, name):
    return name_key(name) in {name_key(v) for v in getattr(item,'excel_aliases',[])}


def resource_match(candidates, name, section='', group_field='group'):
    matches=[item for item in candidates if matches_name(item,name)]
    if len(matches)>1 and section:
        scoped=[item for item in matches if name_key(getattr(getattr(item,group_field,None),'name',''))==name_key(section)]
        if scoped:matches=scoped
    if len(matches)>1:
        raise ValidationError(f'«{name}»: неоднозначное соответствие Excel. Исправьте названия в справочнике.')
    if not matches:return None
    target=matches[0]
    if not target.is_active:raise ValidationError(f'«{name}»: запись справочника неактивна.')
    return target
