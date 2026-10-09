import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Bar, BarChart, Cell, LabelList, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { BarRow, Card, ErrorState, Loading, ShareBar, StaleBanner, StatCard, TableWrap } from '../components/Ui';
import { formatNumber, formatPercent, toneColor, usePalette, type Tone } from '../format';
import { usePageTitle } from '../usePageTitle';

const STATUS_ROWS: { key: string; label: string; tone: Tone }[] = [
  { key: 'INSTALLED', label: 'Installed', tone: 'ok' },
  { key: 'FAULTY', label: 'Faulty', tone: 'bad' },
  { key: 'DECOMMISSIONED', label: 'Decommissioned', tone: 'muted' },
];

/** The meter list is read in one request of at most this many rows. */
const LIST_LIMIT = 500;
/** What the meter list calls a meter that has no transformer. It is not a transformer, so it is never linked to. */
const UNASSIGNED = 'unassigned';

export function FleetPage() {
  usePageTitle('Fleet health');
  const palette = usePalette();
  const summary = useQuery({ queryKey: ['summary'], queryFn: ({ signal }) => api.summary(signal) });
  const meters = useQuery({ queryKey: ['meters', 'all'], queryFn: ({ signal }) => api.meters({ limit: LIST_LIMIT }, signal) });
  const issues = useQuery({ queryKey: ['issues'], queryFn: ({ signal }) => api.issues(signal) });

  // Which transformers carry the most faulty meters: where a crew should look first.
  const attention = useMemo(() => {
    const perDt = new Map<string, { total: number; faulty: number }>();
    for (const meter of meters.data?.data ?? []) {
      const key = meter.dt_code ?? UNASSIGNED;
      const row = perDt.get(key) ?? { total: 0, faulty: 0 };
      row.total += 1;
      if (meter.status?.toUpperCase() === 'FAULTY') row.faulty += 1;
      perDt.set(key, row);
    }
    return [...perDt.entries()]
      .filter(([, row]) => row.faulty > 0)
      .map(([dt, row]) => ({ dt, ...row, share: row.faulty / row.total }))
      .sort((a, b) => b.share - a.share || b.faulty - a.faulty)
      .slice(0, 6);
  }, [meters.data]);

  if (summary.isPending) return <Loading label="Loading the fleet…" />;
  if (summary.isError) return <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />;

  const fleet = summary.data.data;
  const total = fleet.total_meters;
  const count = (key: string) => fleet.by_status[key] ?? 0;
  const statusData = STATUS_ROWS.map((row) => ({ name: row.label, value: count(row.key), fill: toneColor(palette, row.tone) }));
  const makes = Object.entries(fleet.by_make)
    .map(([make, value]) => ({ make, value }))
    .sort((a, b) => b.value - a.value);
  const phases = Object.entries(fleet.by_phase);
  const maxPhase = Math.max(0, ...phases.map(([, value]) => value));
  const issueList = issues.data?.data.issues ?? [];
  const listed = meters.data?.data.length ?? 0;
  const truncated = (meters.data?.total ?? 0) > listed;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Fleet health</h1>
          <p>
            {formatNumber(total, 0)} meters on {fleet.transformers} transformers.
          </p>
        </div>
      </div>
      <StaleBanner caches={[summary.data.cache, meters.data?.cache, issues.data?.cache]} />

      <dl className="grid grid-stats">
        <StatCard label="Meters" value={formatNumber(total, 0)} />
        <StatCard label="Installed" value={formatNumber(count('INSTALLED'), 0)} tone="ok" hint={formatPercent(count('INSTALLED'), total)} />
        <StatCard label="Faulty" value={formatNumber(count('FAULTY'), 0)} tone="bad" hint={formatPercent(count('FAULTY'), total)} />
        <StatCard
          label="Decommissioned"
          value={formatNumber(count('DECOMMISSIONED'), 0)}
          tone="muted"
          hint={formatPercent(count('DECOMMISSIONED'), total)}
        />
        <StatCard label="Transformers" value={fleet.transformers} />
      </dl>

      <div className="grid grid-2">
        <Card title="Status mix">
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={statusData} dataKey="value" nameKey="name" innerRadius={56} outerRadius={88} paddingAngle={2} stroke={palette.surface} isAnimationActive={false}>
                  {statusData.map((entry) => (
                    <Cell key={entry.name} fill={entry.fill} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ background: palette.surface, border: `1px solid ${palette.grid}`, borderRadius: 8 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="legend">
            {STATUS_ROWS.map((row, i) => (
              <li key={row.key}>
                <span className="swatch" style={{ background: statusData[i]?.fill }} aria-hidden="true" />
                <Link to={`/meters?status=${row.key}`}>{row.label}</Link> {formatNumber(count(row.key), 0)} ({formatPercent(count(row.key), total)})
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Make and phase">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={makes} layout="vertical" margin={{ left: 8, right: 40 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="make" width={72} stroke={palette.text} fontSize={13} tickLine={false} axisLine={false} />
                <Tooltip cursor={{ fill: palette.grid }} contentStyle={{ background: palette.surface, border: `1px solid ${palette.grid}`, borderRadius: 8 }} />
                <Bar dataKey="value" name="Meters" fill={palette.primary} barSize={16} radius={4} isAnimationActive={false}>
                  {/* The figures are printed, not only shown in a tooltip a keyboard or screen-reader user never opens. */}
                  <LabelList dataKey="value" position="right" fill={palette.text} fontSize={13} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="bars">
            {phases.map(([phase, value]) => (
              <BarRow key={phase} label={`${phase} phase`} value={value} max={maxPhase} />
            ))}
          </div>
        </Card>
      </div>

      <div className="grid grid-2">
        <Card title="Transformers needing attention">
          {meters.isPending ? <Loading label="Counting faulty meters…" /> : null}
          {meters.isError ? <ErrorState error={meters.error} onRetry={() => void meters.refetch()} /> : null}
          {meters.isSuccess && attention.length === 0 ? <p>No transformer carries a faulty meter.</p> : null}
          {attention.length > 0 ? (
            <TableWrap label="Transformers ranked by the share of their meters that are faulty">
              <table>
                <caption className="sr-only">Transformers ranked by the share of their meters that are faulty</caption>
                <thead>
                  <tr>
                    <th scope="col">Transformer</th>
                    <th scope="col" className="num">Meters</th>
                    <th scope="col" className="num">Faulty</th>
                    <th scope="col">Share</th>
                  </tr>
                </thead>
                <tbody>
                  {attention.map((row) => (
                    <tr key={row.dt}>
                      <th scope="row">
                        {row.dt === UNASSIGNED ? (
                          'No transformer'
                        ) : (
                          <Link to={`/meters?dt=${encodeURIComponent(row.dt)}&status=FAULTY`}>{row.dt}</Link>
                        )}
                      </th>
                      <td className="num">{row.total}</td>
                      <td className="num">{row.faulty}</td>
                      <td>
                        <ShareBar part={row.faulty} whole={row.total} label={formatPercent(row.faulty, row.total)} tone="bad" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          ) : null}
          {truncated ? (
            <p className="note">
              Counted from the first {formatNumber(listed, 0)} of {formatNumber(meters.data?.total, 0)} meters.
            </p>
          ) : null}
        </Card>

        <Card title="Data quality" actions={<Link to="/network">Open the network</Link>}>
          {issues.isPending ? (
            <Loading label="Checking the network data (the first check reads one meter per transformer)…" />
          ) : null}
          {issues.isError ? <ErrorState error={issues.error} onRetry={() => void issues.refetch()} /> : null}
          {issues.isSuccess ? (
            <>
              <p>
                Read {issues.data.data.checked.meters ?? 0} meters, {issues.data.data.checked.transformers ?? 0} transformers and{' '}
                {issues.data.data.checked.breadcrumbs ?? 0} breadcrumbs:{' '}
                <strong>{issueList.length === 0 ? 'nothing to report' : `${issueList.length} issue${issueList.length === 1 ? '' : 's'}`}</strong>.
              </p>
              {issueList.length > 0 ? (
                <ul className="anomalies">
                  {issueList.slice(0, 4).map((issue) => (
                    <li key={`${issue.type}:${issue.id}`} data-severity="warn">
                      <strong>{issue.type.replaceAll('_', ' ')}</strong>
                      <span>{issue.detail}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {issueList.length > 4 ? (
                <p className="note">
                  and {issueList.length - 4} more, <Link to="/network">on the Network screen</Link>.
                </p>
              ) : null}
            </>
          ) : null}
        </Card>
      </div>
    </>
  );
}
