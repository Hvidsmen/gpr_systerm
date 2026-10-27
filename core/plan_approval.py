"""Parallel plan departments shared by GPR, SMG and task queues."""
DEPARTMENTS = (
    ('PRODUCTION', 'Работы', 'PRODUCTION_HEAD', 'approve_production'),
    ('HR', 'Должность', 'HR_HEAD', 'approve_hr'),
    ('TECH', 'Техника и ГСМ', 'TECH_HEAD', 'approve_tech'),
    ('SECURITY', 'Служба безопасности', 'SECURITY_HEAD', 'approve_security'),
    ('SBO', 'Служба СБО', 'SBO_HEAD', 'approve_sbo'),
)
REQUIRED_SECTIONS = frozenset(section for section, _, _, _ in DEPARTMENTS)
DEPARTMENT_ROLES = frozenset(role for _, _, role, _ in DEPARTMENTS)
ROLE_SECTIONS = {role: section for section, _, role, _ in DEPARTMENTS}
ROLE_ACTIONS = {role: action for _, _, role, action in DEPARTMENTS}
SECTION_ACTIONS = {section: action for section, _, _, action in DEPARTMENTS}
ACTION_ROLES = {action: {role} for _, _, role, action in DEPARTMENTS}
