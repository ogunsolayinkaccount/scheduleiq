"""
Phase 3 (Authentication and Authorization) — TEST-ONLY convenience.

Retrofitting real authentication onto ~1160 pre-existing tests that were
all written as anonymous requests would otherwise mean touching every one
of those test files just to keep exercising the business logic they
actually test. Instead: when settings.AUTO_AUTH_TEST_USER is True (set
automatically in settings.py only when running under `manage.py test` —
see TESTING there), this middleware transparently logs in a dedicated
Administrator test-fixture account for any request that doesn't already
have an authenticated session, using Django's REAL session login() (not a
bypass of permissions.require_role — every check still runs normally
against a real, real-authenticated user).

This is NEVER active outside a test run: AUTO_AUTH_TEST_USER is only ever
True when TESTING is True, which is only ever True when 'test' appears on
sys.argv. Tests that need to verify REAL unauthenticated/role-restricted
behavior (scheduler/tests/test_authentication.py) explicitly disable this
per-test with @override_settings(AUTO_AUTH_TEST_USER=False).
"""
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User

from .models import ROLE_ADMINISTRATOR

AUTO_AUTH_TEST_USERNAME = '_autotest_admin'


def _get_or_create_test_admin():
    user, created = User.objects.get_or_create(
        username=AUTO_AUTH_TEST_USERNAME,
        defaults={'is_staff': True},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    if user.profile.role != ROLE_ADMINISTRATOR:
        user.profile.role = ROLE_ADMINISTRATOR
        user.profile.save(update_fields=['role'])
    return user


class AutoAuthTestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, 'AUTO_AUTH_TEST_USER', False) and not request.user.is_authenticated:
            login(request, _get_or_create_test_admin(), backend='django.contrib.auth.backends.ModelBackend')
        return self.get_response(request)
