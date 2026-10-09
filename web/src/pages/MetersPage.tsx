import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type MeterQuery } from '../api';
import { Card, EmptyState, ErrorState, Loading, Pagination, StaleBanner, StatusBadge, TableWrap } from '../components/Ui';
import { formatNumber, statusLabel } from '../format';
import { usePageTitle } from '../usePageTitle';

const PAGE_SIZE = 25;
/** The API refuses a longer search with a 422, so the box does not let one be typed. */
const MAX_SEARCH = 64;
const BRANCH_KEYS = ['zone', 'circle', 'division', 'subdivision', 'substation', 'feeder'] as const;

export function MetersPage() {
  usePageTitle('Meters');
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1);
  const [text, setText] = useState(q);

  // The typing delay below outlives the render that started it, so what it changes must be applied to the URL as it
  // is when the timer fires, not as it was then (a filter picked meanwhile would be lost). The router's own
  // `setParams(previous => ...)` has the same flaw in this version, so the latest values are kept in a ref.
  const latest = useRef({ params, setParams });
  latest.current = { params, setParams };
  const update = useCallback((changes: Record<string, string | null>) => {
    const { params: current, setParams: apply } = latest.current;
    const next = new URLSearchParams(current);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    apply(next, { replace: true });
  }, []);

  // The URL is the source of truth for filters (so a filtered list can be linked to); the box follows it, and
  // typing waits a moment before it asks the API anything.
  useEffect(() => setText(q), [q]);
  useEffect(() => {
    const wanted = text.trim();
    if (wanted === q) return;
    const timer = window.setTimeout(() => update({ q: wanted || null, page: null }), 300);
    return () => window.clearTimeout(timer);
  }, [text, q, update]);

  const query: MeterQuery = {
    q: q || undefined,
    status: params.get('status') ?? undefined,
    make: params.get('make') ?? undefined,
    phase: params.get('phase') ?? undefined,
    dt: params.get('dt') ?? undefined,
    zone: params.get('zone') ?? undefined,
    circle: params.get('circle') ?? undefined,
    division: params.get('division') ?? undefined,
    subdivision: params.get('subdivision') ?? undefined,
    substation: params.get('substation') ?? undefined,
    feeder: params.get('feeder') ?? undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };

  const options = useQuery({ queryKey: ['summary'], queryFn: ({ signal }) => api.summary(signal) });
  const meters = useQuery({
    queryKey: ['meters', query],
    queryFn: ({ signal }) => api.meters(query, signal),
    placeholderData: keepPreviousData,
  });

  const fleet = options.data?.data;
  const total = meters.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const branch = BRANCH_KEYS.flatMap((key) => {
    const value = params.get(key);
    return value ? [{ key, value }] : [];
  });

  // A bookmarked page that no longer exists (the list shrank, or the address was edited): go to the last real page.
  useEffect(() => {
    if (meters.isSuccess && !meters.isPlaceholderData && page > pages) update({ page: pages > 1 ? String(pages) : null });
  }, [meters.isSuccess, meters.isPlaceholderData, page, pages, update]);

  // "UNKNOWN" is how the summary counts a missing value; the list cannot filter on it, so it is not offered.
  const choices = (counts: Record<string, number> | undefined) => Object.keys(counts ?? {}).filter((v) => v !== 'UNKNOWN');
  const select = (key: 'status' | 'make' | 'phase' | 'dt', label: string, values: string[], show = (v: string) => v) => (
    <label className="field">
      {label}
      <select value={params.get(key) ?? ''} onChange={(e) => update({ [key]: e.target.value || null, page: null })}>
        <option value="">Any</option>
        {values.map((value) => (
          <option key={value} value={value}>
            {show(value)}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Meters</h1>
          <p>
            <span role="status">
              {meters.isError ? 'Could not load the meters' : meters.data ? `${formatNumber(total, 0)} match` : 'Searching…'}
            </span>
            {' · '}
            <Link to="/map">see them on the map</Link>
          </p>
        </div>
      </div>
      <StaleBanner caches={[meters.data?.cache]} />

      <Card title="Find a meter">
        <div className="toolbar">
          <label className="field grow">
            Search by meter number or serial
            <input
              type="search"
              value={text}
              maxLength={MAX_SEARCH}
              onChange={(e) => setText(e.target.value)}
              placeholder="J100001 or GE84132"
            />
          </label>
          {select('status', 'Status', choices(fleet?.by_status), statusLabel)}
          {select('make', 'Make', choices(fleet?.by_make))}
          {select('phase', 'Phase', choices(fleet?.by_phase))}
          {select('dt', 'Transformer', choices(fleet?.by_dt).sort())}
        </div>
        {options.isError ? <p className="note">The lists of makes and transformers could not be loaded, so only “Any” is offered.</p> : null}
        {branch.length > 0 ? (
          <div className="chips" role="group" aria-label="Network filters">
            {branch.map(({ key, value }) => (
              <span className="chip" key={key}>
                {key} {value}
                <button type="button" aria-label={`Remove the ${key} filter`} onClick={() => update({ [key]: null, page: null })}>
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}
      </Card>

      {meters.isPending ? <Loading label="Loading meters…" /> : null}
      {meters.isError ? <ErrorState error={meters.error} onRetry={() => void meters.refetch()} /> : null}
      {meters.isSuccess && meters.data.data.length === 0 ? (
        <EmptyState title="No meter matches">Try a shorter search, or clear a filter.</EmptyState>
      ) : null}
      {meters.isSuccess && meters.data.data.length > 0 ? (
        <>
          <TableWrap label="Meters" busy={meters.isFetching}>
            <table>
              <caption className="sr-only">Meters, {PAGE_SIZE} per page</caption>
              <thead>
                <tr>
                  <th scope="col">Meter</th>
                  <th scope="col">Serial</th>
                  <th scope="col">Make</th>
                  <th scope="col">Phase</th>
                  <th scope="col">Status</th>
                  <th scope="col">Transformer</th>
                </tr>
              </thead>
              <tbody>
                {meters.data.data.map((meter) => (
                  <tr key={meter.meter_id}>
                    <th scope="row">
                      <Link to={`/meters/${encodeURIComponent(meter.meter_id)}`}>{meter.meter_id}</Link>
                    </th>
                    <td>{meter.serial_number ?? '—'}</td>
                    <td>{meter.make ?? '—'}</td>
                    <td>{meter.phase_type ?? '—'}</td>
                    <td>
                      <StatusBadge status={meter.status} />
                    </td>
                    <td>{meter.dt_code ? <Link to={`/network?node=${encodeURIComponent(meter.dt_code)}`}>{meter.dt_code}</Link> : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <Pagination page={page} pages={pages} onPage={(next) => update({ page: next > 1 ? String(next) : null })} />
        </>
      ) : null}
    </>
  );
}
