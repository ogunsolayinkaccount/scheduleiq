"""
Phase 3 (Authentication and Authorization) — the Django admin site
(/admin/, already routed in seglc_backend/urls.py) is the primary
user-management surface: create accounts, set roles, deactivate a user.
POST/PATCH /api/auth/users/ (auth_views.py) cover the same ground for the
React app's own Administrator screen; both operate on the same User/
UserProfile rows, so either one is always available if the other is
unreachable for some reason — see the Phase 3 report's "initial
administrator setup" section for the bootstrap procedure
(`manage.py createsuperuser`, no default credentials ever created).
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Role'


class ScheduleIQUserAdmin(UserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'role', 'is_active', 'is_superuser')

    def role(self, obj):
        return getattr(obj, 'profile', None) and obj.profile.role
    role.short_description = 'Role'


admin.site.unregister(User)
admin.site.register(User, ScheduleIQUserAdmin)
