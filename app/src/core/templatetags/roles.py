from django import template

from core import models

register = template.Library()


@register.simple_tag()
def user_has_role(request, role):
    if not request.user.is_authenticated:
        return None
    return request.user.check_role(request.journal, role)


@register.simple_tag
def role_users(request, role_slug):
    role_holders = models.AccountRole.objects.filter(role__slug=role_slug)
    return [holder.user for holder in role_holders]


@register.simple_tag
def role_id(request, role_slug):
    try:
        role = models.Role.objects.get(slug=role_slug)
        return role.pk
    except models.Role.DoesNotExist:
        return 0
