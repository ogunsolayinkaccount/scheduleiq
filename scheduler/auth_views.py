"""
Phase 3 (Authentication and Authorization) — login/logout/session-status
and minimal user management. Uses Django's own auth.User + authenticate()/
login()/logout() (django.contrib.auth) rather than a bespoke scheme, per
the instruction to use Django's established authentication framework.

These views are deliberately NOT @csrf_exempt (except csrf_bootstrap and
login — see each view's docstring for why) — CsrfViewMiddleware is already
active (seglc_backend/settings.py MIDDLEWARE) and now actually matters,
since mutating requests can carry a real session cookie.
"""
import json

from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import ROLE_CHOICES
from .permissions import ROLE_ADMINISTRATOR, require_role, user_role


def error_response(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _serialize_user(user):
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user_role(user),
        'isSuperuser': user.is_superuser,
    }


@ensure_csrf_cookie
@require_http_methods(['GET'])
def csrf_bootstrap(request):
    """The frontend calls this once on load, before any mutating request,
    purely so the browser receives a csrftoken cookie to echo back as the
    X-CSRFToken header (see frontend/main.tsx). No @csrf_exempt is needed
    here — GET is a safe method, CsrfViewMiddleware never checks it."""
    return JsonResponse({'csrfToken': get_token(request)})


@csrf_exempt  # the client cannot have a CSRF token before it has ever
# received the cookie from csrf_bootstrap — but login() itself still
# requires a valid session (none exists yet) and real credentials, so
# exempting CSRF here does not weaken authentication; it only means this
# one endpoint can be POSTed without the header. The frontend always calls
# csrf_bootstrap first regardless, so the cookie is already set by the
# time a real user reaches the login form.
@require_http_methods(['POST'])
def login_view(request):
    """Body: {username, password}. Never accepts or trusts any other
    identity claim — authenticate() is the ONLY source of truth."""
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    username = (body.get('username') or '').strip()
    password = body.get('password') or ''
    if not username or not password:
        return error_response('username and password are required', status=400)

    # ModelBackend.authenticate() already refuses an inactive user (returns
    # None, same as a wrong password) — never distinguishes "disabled
    # account" from "wrong credentials" to the caller.
    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({'error': 'Invalid username or password.'}, status=401)

    django_login(request, user)
    return JsonResponse({'user': _serialize_user(user)})


@require_http_methods(['POST'])
def logout_view(request):
    """Idempotent: logging out an already-anonymous session is a no-op
    success, never an error — the caller's goal ("I am not logged in") is
    already satisfied."""
    django_logout(request)
    return JsonResponse({'loggedOut': True})


@ensure_csrf_cookie
@require_http_methods(['GET'])
def me_view(request):
    """PUBLIC — never behind @require_role. The frontend calls this on
    load to decide whether to show the login screen or the app, so it
    must be reachable by a not-yet-authenticated browser. Also refreshes
    the CSRF cookie on every app load the same way csrf_bootstrap does."""
    get_token(request)  # ensures the csrftoken cookie, like csrf_bootstrap
    if not request.user.is_authenticated:
        return JsonResponse({'authenticated': False})
    return JsonResponse({'authenticated': True, 'user': _serialize_user(request.user)})


@require_http_methods(['GET', 'POST'])
@require_role(ROLE_ADMINISTRATOR)
def users_list(request):
    """GET: every user + role, for the Administrator's user-management
    screen (also manageable at /admin/ — see the Phase 3 report). POST
    body: {username, password, role, email?} — creates a new account.
    Never a default/placeholder password; the Administrator sets a real
    one for every account they create."""
    if request.method == 'GET':
        return JsonResponse({'users': [_serialize_user(u) for u in User.objects.all().order_by('username')]})

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    username = (body.get('username') or '').strip()
    password = body.get('password') or ''
    role = body.get('role') or ''
    valid_roles = {c[0] for c in ROLE_CHOICES}
    if not username or not password:
        return error_response('username and password are required', status=400)
    if role not in valid_roles:
        return error_response(f'role must be one of {sorted(valid_roles)}', status=400)
    if User.objects.filter(username=username).exists():
        return error_response('That username is already taken.', status=409)

    user = User.objects.create_user(username=username, password=password, email=body.get('email') or '')
    user.profile.role = role
    user.profile.save(update_fields=['role'])
    return JsonResponse({'user': _serialize_user(user)}, status=201)


@require_http_methods(['PATCH'])
@require_role(ROLE_ADMINISTRATOR)
def user_detail(request, user_id):
    """Body (any subset): {role, isActive}. Never lets an Administrator
    lock themselves out by demoting/deactivating their own last-standing
    Administrator account — see the guard below."""
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return error_response('User not found', status=404)

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return error_response('Request body must be valid JSON')

    valid_roles = {c[0] for c in ROLE_CHOICES}
    new_role = body.get('role')
    new_active = body.get('isActive')

    would_demote = new_role is not None and new_role != ROLE_ADMINISTRATOR and user.profile.role == ROLE_ADMINISTRATOR
    would_deactivate = new_active is False and user.is_active
    if (would_demote or would_deactivate) and user_role(request.user) == ROLE_ADMINISTRATOR:
        remaining_admins = User.objects.filter(profile__role=ROLE_ADMINISTRATOR, is_active=True).exclude(pk=user.pk).count()
        if remaining_admins == 0:
            return error_response(
                'Refusing: this is the last active Administrator account. Promote another account first.',
                status=409,
            )

    if new_role is not None:
        if new_role not in valid_roles:
            return error_response(f'role must be one of {sorted(valid_roles)}', status=400)
        user.profile.role = new_role
        user.profile.save(update_fields=['role'])
    if new_active is not None:
        user.is_active = bool(new_active)
        user.save(update_fields=['is_active'])

    return JsonResponse({'user': _serialize_user(user)})
