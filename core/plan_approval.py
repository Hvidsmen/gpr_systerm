"""Parallel plan departments shared by GPR, SMG and task queues."""
DEPARTMENTS = (
    ('PROJECT', 'Руководитель проекта / начальник участка', 'MANAGER', 'approve_project'),
    ('PRODUCTION', 'Работы', 'PRODUCTION_HEAD', 'approve_production'),
    ('HR', 'Должность', 'HR_HEAD', 'approve_hr'),
    ('TECH', 'Техника и ГСМ', 'TECH_HEAD', 'approve_tech'),
    ('SECURITY', 'Служба безопасности', 'SECURITY_HEAD', 'approve_security'),
    ('SBO', 'Служба СБО', 'SBO_HEAD', 'approve_sbo'),
)
REQUIRED_SECTIONS = frozenset(section for section, _, _, _ in DEPARTMENTS)
SECTION_ROLES = {
    section: ({'MANAGER', 'SITE_CHIEF'} if section == 'PROJECT' else {role})
    for section, _, role, _ in DEPARTMENTS
}
DEPARTMENT_ROLES = frozenset(role for roles in SECTION_ROLES.values() for role in roles)
ROLE_SECTIONS = {role: section for section, roles in SECTION_ROLES.items() for role in roles}
ROLE_ACTIONS = {
    role: action
    for section, _, _, action in DEPARTMENTS
    for role in SECTION_ROLES[section]
}
SECTION_ACTIONS = {section: action for section, _, _, action in DEPARTMENTS}
ACTION_ROLES = {
    action: SECTION_ROLES[section]
    for section, _, _, action in DEPARTMENTS
}
