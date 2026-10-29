"""Display the confirmed ZUP label format: job /department/."""
import re


# A slash inside an abbreviation (п/п, м/к) belongs to the job name.
# A spaced slash inside the job prefix would be another department boundary,
# so ambiguous multi-segment labels are left untouched.
STAFFING_LABEL = re.compile(r'\s*((?:[^/]|(?<=\S)/(?=\S))+?)\s*/\s*([^/]+?)\s*/\s*')


def staffing_parts(label):
    match = STAFFING_LABEL.fullmatch(label or '')
    if match and all(part.strip() for part in match.groups()):
        return tuple(part.strip() for part in match.groups())
    return (label or '').strip(), ''
