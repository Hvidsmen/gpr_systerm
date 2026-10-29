from django.forms.renderers import DjangoTemplates


class ObjectTreeFormRenderer(DjangoTemplates):
    def render(self, template_name, context, request=None):
        if template_name == 'django/forms/widgets/select.html':
            from .object_tree import tree_options
            widget = context.get('widget', {})
            groups = widget.get('optgroups', [])
            from apps.resources.catalogue_search import alias_options
            alias_options([option for _, choices, _ in groups for option in choices])
            # Ungrouped Select emits one optgroup per option. Build one tree
            # across them so siblings share ancestors and catalogue lookup.
            if groups and all(label is None for label, _, _ in groups):
                options = [option for _, choices, _ in groups for option in choices]
                widget['optgroups'] = [(None, tree_options(options), 0)]
            else:
                widget['optgroups'] = [(label, tree_options(options), index) for label, options, index in groups]
        return super().render(template_name, context, request=request)
