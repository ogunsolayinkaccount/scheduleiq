"""
Phase 3 (Authentication and Authorization) — server-side role enforcement.

Permission matrix principle (documented here once; every call site below
just names a role, not a rationale):

  ADMINISTRATOR  user management, whole-project deletion, schedule-version
                 deletion/restoration, and consolidation-apply — operations
                 that destroy or irreversibly rewrite PRIMARY schedule
                 history, or that act across projects at once.
  SCHEDULER      everything else that writes: imports, project creation,
                 version metadata edits, and CRUD on records that are
                 derived/secondary (reports, cost entries, recovery
                 scenarios, risk/mitigation actions, contractual
                 milestones, documents, threshold profiles, AI
                 review/chat) and scoped to a single project.
  VIEWER         every read-only / dry-run / always-rolled-back endpoint.

Roles are ascending-privilege (VIEWER < SCHEDULER < ADMINISTRATOR, see
models.ROLE_RANK) — a required role is a FLOOR, not an exact match, so an
Administrator can do anything a Scheduler or Viewer can.
"""
from __future__ import annotations

from functools import wraps
from typing import Optional

from django.http import JsonResponse

from .models import ROLE_ADMINISTRATOR, ROLE_RANK, ROLE_SCHEDULER, ROLE_VIEWER

__all__ = ['ROLE_ADMINISTRATOR', 'ROLE_SCHEDULER', 'ROLE_VIEWER', 'require_role', 'user_role']


def user_role(user) -> Optional[str]:
    """The role of an authenticated Django user, or None if they have no
    profile (should not happen for any User created after this phase —
    see signals.py) or aren't authenticated at all."""
    if not getattr(user, 'is_authenticated', False):
        return None
    profile = getattr(user, 'profile', None)
    return profile.role if profile else None


def _meets(role: Optional[str], required: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(required, 999)


def require_role(default: Optional[str] = None, **method_roles: str):
    """Decorator. `default` is the role required for any HTTP method not
    named in `method_roles`; `method_roles` lets ONE view that branches
    internally on request.method (e.g. GET is read-only, DELETE is
    destructive) require a different role per method — see
    project_version_detail (PATCH=SCHEDULER, DELETE=ADMINISTRATOR) for why
    this matters: gating the whole view at the DELETE branch's role would
    lock Schedulers out of the routine PATCH edits they're explicitly meant
    to do.

    `default=None` with no method_roles means "authenticated, any role" —
    used by @require_role() with nothing else for endpoints that only need
    a real session, not a specific privilege floor.

    Always checks authentication (401) before role (403), and NEVER trusts
    anything from the request body/headers as an identity claim — only
    request.user, which Django's session middleware has already verified
    against the server-side session store.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({'error': 'Authentication required.'}, status=401)
            required = method_roles.get(request.method, default)
            if required is not None:
                role = user_role(request.user)
                if not _meets(role, required):
                    return JsonResponse(
                        {'error': f'This action requires the {required.title()} role or higher.'},
                        status=403,
                    )
            return view_func(request, *args, **kwargs)
        # Makes the permission matrix MACHINE-AUDITABLE rather than only
        # documented in prose: scheduler/tests/test_permission_audit.py
        # walks every URL pattern, reads this attribute off each view's
        # callback, and asserts live requests match it exactly — so "what
        # role does this endpoint require" is always answerable by running
        # code against the actual deployed decorators, never by re-reading
        # a hand-maintained table that could drift from reality.
        wrapped.role_requirements = {'default': default, **method_roles}
        return wrapped
    return decorator
