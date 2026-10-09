import { useEffect } from 'react';

const APP = 'Urja Meter Ops';

/** Each screen names itself in the tab and in the history, and a screen reader announces the new title on navigation. */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · ${APP}`;
  }, [title]);
}
