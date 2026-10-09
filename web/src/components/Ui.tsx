import { useId, type ReactNode } from 'react';
import { ApiError } from '../api';
import { statusLabel, statusTone } from '../format';
import type { CacheStatus } from '../types';

/** State is always said in words, never carried by colour alone. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  return (
    <span className={`badge badge-${statusTone(status)}`}>
      <span className="badge-dot" aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
}

export function Card({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  const headingId = useId();
  return (
    <section className="card" aria-labelledby={headingId}>
      <div className="card-head">
        <h2 id={headingId}>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: 'ok' | 'bad' | 'muted';
}) {
  return (
    <div className="stat" data-tone={tone}>
      <dt>{label}</dt>
      <dd>
        {value}
        {hint ? <span className="hint">{hint}</span> : null}
      </dd>
    </div>
  );
}

export function BarRow({
  label,
  value,
  max,
  tone,
}: {
  label: ReactNode;
  value: number;
  max: number;
  tone?: 'bad';
}) {
  return (
    <div className="bar">
      <span>{label}</span>
      <span className="bar-track" aria-hidden="true">
        <span className="bar-fill" data-tone={tone} style={{ width: `${max ? (value / max) * 100 : 0}%` }} />
      </span>
      <span>{value}</span>
    </div>
  );
}

/** `part` of `whole` as a bar, with the figure (`label`) said in words beside it; for table cells. */
export function ShareBar({ part, whole, label, tone }: { part: number; whole: number; label: string; tone?: 'bad' }) {
  const share = whole > 0 ? Math.min(1, part / whole) : 0;
  return (
    <span className="share">
      <span className="bar-track" aria-hidden="true">
        <span className="bar-fill" data-tone={tone} style={{ width: `${share * 100}%` }} />
      </span>
      <span>{label}</span>
    </span>
  );
}

/**
 * A table that scrolls sideways when the screen is narrow. A scrollable box has to be reachable by keyboard
 * (WCAG 2.1.1), and it needs a name for a screen reader to announce it as something.
 */
export function TableWrap({ label, busy, children }: { label: string; busy?: boolean; children: ReactNode }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} aria-busy={busy} tabIndex={0}>
      {children}
    </div>
  );
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;
  const message = api?.message ?? (error instanceof Error ? error.message : 'Unknown error');
  return (
    <div className="state state-error" role="alert">
      <strong>{api ? (api.status === 404 ? 'Not found' : 'The API could not answer') : 'Something went wrong'}</strong>
      <span>{message}</span>
      {api ? (
        <small>
          {api.code}
          {api.requestId ? ` · request ${api.requestId}` : ''}
        </small>
      ) : null}
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

/** "Nothing here" is a result too: announced like loading and errors are, so a screen reader hears it. */
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state" role="status">
      <div>
        <strong>{title}</strong>
        {children ? <div>{children}</div> : null}
      </div>
    </div>
  );
}

/** The API serves its last good answer, flagged STALE, when the portal is down. Say so. */
export function StaleBanner({ caches }: { caches: readonly (CacheStatus | null | undefined)[] }) {
  if (!caches.includes('STALE')) return null;
  return (
    <div className="banner banner-warn" role="status">
      <strong>Showing the last data we had.</strong> The portal is unavailable right now, so this may be a few minutes
      old.
    </div>
  );
}

export function Pagination({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  return (
    <nav className="pager" aria-label="Pagination">
      <button type="button" onClick={() => onPage(page - 1)} disabled={page <= 1}>
        Previous
      </button>
      <span>
        Page {page} of {pages}
      </span>
      <button type="button" onClick={() => onPage(page + 1)} disabled={page >= pages}>
        Next
      </button>
    </nav>
  );
}
