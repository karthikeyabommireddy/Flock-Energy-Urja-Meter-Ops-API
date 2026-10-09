import { Suspense, useEffect, useRef } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { API_BASE } from '../api';
import { ErrorBoundary } from './ErrorBoundary';
import { Loading } from './Ui';

export function Layout() {
  const { pathname } = useLocation();
  const shown = useRef(pathname);

  // A route change is a new page: start at the top and move focus to the content, as a full page load would. It is
  // compared with the page last shown rather than "is this the first render", so that React's StrictMode running
  // effects twice in development does not count as a navigation.
  useEffect(() => {
    if (shown.current === pathname) return;
    shown.current = pathname;
    window.scrollTo(0, 0);
    document.getElementById('main')?.focus();
  }, [pathname]);

  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <span className="brand">Urja Meter Ops</span>
          <nav aria-label="Main">
            <NavLink to="/" end>
              Fleet
            </NavLink>
            <NavLink to="/meters">Meters</NavLink>
            <NavLink to="/map">Map</NavLink>
            <NavLink to="/network">Network</NavLink>
          </nav>
        </div>
      </header>
      <main id="main" className="content" tabIndex={-1}>
        {/* Both sit inside the shell: while a screen's script loads, or when it fails, the header and menu stay. */}
        <ErrorBoundary resetKey={pathname}>
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>
      <footer className="footer">
        A read-only view of the Urja Meter Ops portal, served by the API at <code>{API_BASE || window.location.origin}</code>.
      </footer>
    </div>
  );
}
