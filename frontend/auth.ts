// Phase 3 (Authentication and Authorization) — CSRF-token plumbing for the
// global fetch wrapper. Pure, DOM-input-taking functions here (testable via
// node --test, matching dateFormat.ts/importProtection.ts); the actual
// window.fetch monkey-patch is the thin imperative shell at the bottom,
// installed once from main.tsx before App renders.
//
// Why a global wrapper instead of editing every fetch() call: this app has
// no shared fetch helper (ImportCenter.tsx, ProjectControls.tsx, Dashboard.tsx
// etc. each call fetch()/sfetch() directly — sfetch itself is a thin
// staleness guard around fetch, see projectScope.ts), so a wrapper installed
// once is the only way to add real CSRF protection without touching every
// one of those call sites individually.

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);

export function readCookie(name: string, cookieString: string): string | null {
  const escaped = name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1');
  const match = cookieString.match(new RegExp('(?:^|; )' + escaped + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}

export function needsCsrfToken(method: string | undefined): boolean {
  return !SAFE_METHODS.has((method || 'GET').toUpperCase());
}

/** True for a same-origin (or relative) URL only — never attach the token
 * to a request aimed at a different origin, which would leak it. */
export function isSameOriginUrl(url: string, currentOrigin: string): boolean {
  if (!url) return true;
  if (url.startsWith('/') && !url.startsWith('//')) return true; // relative path
  try {
    return new URL(url, currentOrigin).origin === currentOrigin;
  } catch {
    return false;
  }
}

// Dispatched on any 401 from a same-origin /api/ call that ISN'T the
// session-status check itself (see the exclusion below) — AuthContext.tsx
// listens for this to drop back to the login screen immediately, instead
// of leaving every open view showing a raw "Authentication required."
// banner after a session expires or is ended in another tab.
export const SESSION_EXPIRED_EVENT = 'scheduleiq:session-expired';

export function notifySessionExpired(): void {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

// /api/auth/me/ and /api/auth/login/ legitimately return 401 as part of
// normal operation (checking "am I logged in" / a failed login attempt) —
// never treat those as a session-expiry event.
const SESSION_CHECK_PATHS = ['/api/auth/me/', '/api/auth/login/'];

let installed = false;

export function installCsrfFetchWrapper(): void {
  if (installed) return;
  installed = true;
  const originalFetch = window.fetch.bind(window);

  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : (input as Request).url || String(input);
    const method = init?.method || (typeof input !== 'string' ? (input as Request).method : undefined) || 'GET';
    const sameOrigin = isSameOriginUrl(url, window.location.origin);

    const run = (req: RequestInfo | URL, reqInit?: RequestInit) =>
      originalFetch(req, reqInit).then((resp) => {
        if (sameOrigin && resp.status === 401 && !SESSION_CHECK_PATHS.some((p) => url.includes(p))) {
          notifySessionExpired();
        }
        return resp;
      });

    if (!needsCsrfToken(method) || !sameOrigin) return run(input, init);
    const token = readCookie('csrftoken', document.cookie);
    if (!token) return run(input, init);

    const headers = new Headers(init?.headers ?? (typeof input !== 'string' ? (input as Request).headers : undefined));
    headers.set('X-CSRFToken', token);
    return run(input, { ...init, headers });
  };
}
