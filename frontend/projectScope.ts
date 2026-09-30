// Stale-response protection for every version-aware screen.
//
// A screen calls setProjectScope(projectId) while rendering (module state,
// idempotent). sfetch() remembers the scope at the moment a request STARTS and,
// when the response - headers AND body - finally arrives, drops it if the user
// has since switched to another project: the returned promise then simply never
// settles, so none of the caller's .then/.catch/.finally handlers can repaint
// the screen with Project A's data (or Project A's error) after Project B was
// selected.
//
// A SECOND, independent guard protects the same-project "out-of-order
// response" case: two GETs to the exact same URL (e.g. a version list
// re-fetched because a mutation elsewhere - a backend consolidation apply,
// a role change - changed the data) can resolve in the opposite order they
// were sent. Without this, an older, slower response for that URL could
// overwrite state with stale data *after* the newer, correct response
// already rendered - the project never "changed" for the project-scope
// guard above to catch it. Only the response to the MOST RECENTLY STARTED
// request for a given URL is ever allowed to resolve; every earlier one for
// that same URL is dropped, whichever settles first.
//
// Only GETs are guarded. Mutations (POST/PATCH/DELETE) pass through untouched:
// they have already happened server-side, and their follow-up refetch is itself
// a guarded GET.

let activeScope = "";

export function setProjectScope(projectId: string | null | undefined): void {
  activeScope = projectId || "";
}

export function getProjectScope(): string {
  return activeScope;
}

const NEVER = <T,>(): Promise<T> => new Promise<T>(() => { /* stale response: intentionally never settles */ });

const BODY_READERS = new Set(["text", "json", "blob", "arrayBuffer", "formData"]);

function urlKey(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

const latestSeqByUrl = new Map<string, number>();

export function sfetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const method = (init?.method || "GET").toUpperCase();
  if (method !== "GET") return fetch(input, init);

  const startedIn = activeScope;
  const key = urlKey(input);
  const mySeq = (latestSeqByUrl.get(key) || 0) + 1;
  latestSeqByUrl.set(key, mySeq);
  const stale = () => activeScope !== startedIn || latestSeqByUrl.get(key) !== mySeq;

  return fetch(input, init).then(
    (res) => {
      if (stale()) return NEVER<Response>();
      // The body is read AFTER the headers arrive - the user can switch project in between.
      return new Proxy(res, {
        get(target, prop, receiver) {
          const value = Reflect.get(target, prop, target);
          if (typeof prop === "string" && BODY_READERS.has(prop) && typeof value === "function") {
            return (...args: unknown[]) =>
              (value as (...a: unknown[]) => Promise<unknown>).apply(target, args).then(
                (body) => (stale() ? NEVER() : body),
                (err) => (stale() ? NEVER() : Promise.reject(err)),
              );
          }
          return typeof value === "function" ? value.bind(target) : value;
        },
      });
    },
    (err) => (stale() ? NEVER<Response>() : Promise.reject(err)),
  );
}
