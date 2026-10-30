"""Resolve sex only from named 1C enum declarations and matching SQL refs."""
from uuid import UUID
from django.core.exceptions import ValidationError
from .one_c_metadata import parse,unpack,walk,guid


def enum_members(blob,owner):
    tree=parse(unpack(blob));headers=[];members={}
    for node in walk(tree):
        if len(node)<3 or not isinstance(node[1],list) or len(node[1])<3:continue
        identity=guid(node[1][2]);name=node[2]
        if identity==owner:headers.append(name)
        if identity and name in {'Мужской','Женский'}:
            code={'Мужской':'M','Женский':'F'}[name]
            if code in members and members[code]!=identity:raise ValueError('Ambiguous sex enum member.')
            members[code]=identity
    if headers!=['ПолФизическогоЛица']:raise ValueError('Unexpected enumeration metadata.')
    if set(members)!={'M','F'} or len(set(members.values()))!=2:raise ValueError('Sex enum declarations are incomplete.')
    return {code:reference_candidates(identity) for code,identity in members.items()}


def reference_candidates(identity):
    value=UUID(identity);raw=value.bytes
    # SQL storage layouts are checked against actual enum rows, never ordinal
    # positions or a guessed male/female order.
    return sorted({raw.hex().upper(),value.bytes_le.hex().upper(),(raw[8:]+raw[6:8]+raw[4:6]+raw[:4]).hex().upper()})


def import_gender(item,report):
    value=item.get('gender')
    if value is None:return None
    if report.get('gender_source')!='ФизическоеЛицо.Пол':raise ValidationError('Не подтверждён источник пола сотрудника ЗУП.')
    if not isinstance(value,str) or value not in {'','M','F'}:raise ValidationError('Некорректное значение пола сотрудника ЗУП.')
    return value or None
