"""Display the confirmed ZUP label format: job /department/."""
import re


STAFFING_LABEL = re.compile(r'\s*([^/]+?)\s*/\s*([^/]+?)\s*/\s*')


def staffing_parts(label):
    match = STAFFING_LABEL.fullmatch(label or '')
    if match and all(part.strip() for part in match.groups()):
        return tuple(part.strip() for part in match.groups())
    return (label or '').strip(), ''
