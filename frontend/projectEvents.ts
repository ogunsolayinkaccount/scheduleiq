import { useEffect, useState } from "react";

// The backend Project/ScheduleUpload tables are the single source of truth.
// Any screen that creates/deletes a project or version calls
// notifyProjectsChanged(); every project/version selector subscribes via
// useProjectsChangedTick() and refetches — no reload needed, no second list
// to keep in sync.
const EVENT = "scheduleiq:projects-changed";

export function notifyProjectsChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

export function useProjectsChangedTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const h = () => setTick((t) => t + 1);
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);
  return tick;
}
