"""
Phase 3 (Authentication and Authorization) — creates a UserProfile the
moment a Django auth.User is created, so `request.user.profile` is always
safe to read anywhere permissions.py checks a role, with no lazy-creation
race and no view ever needing to handle "profile doesn't exist yet".

Role on creation: ADMINISTRATOR if the User was created as a superuser
(i.e. via `manage.py createsuperuser`, or `is_superuser=True` set directly)
— the one and only account this codebase ever elevates automatically, and
only because the operator explicitly chose to create a superuser, the
same established Django convention used for the admin site itself (see
the Phase 3 report's "initial administrator setup" section). Every other
new User defaults to VIEWER, the least-privileged role — an existing
Administrator must deliberately promote them (via /admin/ or
POST /api/auth/users/), never automatic.
"""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ROLE_ADMINISTRATOR, ROLE_VIEWER, UserProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if not created:
        return
    UserProfile.objects.get_or_create(
        user=instance,
        defaults={'role': ROLE_ADMINISTRATOR if instance.is_superuser else ROLE_VIEWER},
    )
