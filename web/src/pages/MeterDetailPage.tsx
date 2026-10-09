import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { findAnomalies, summarise } from '../analysis';
import { ConsumptionChart, type ChartMode } from '../components/ConsumptionChart';
import { MeterMap, type MapPoint } from '../components/MeterMap';
import { Card, EmptyState, ErrorState, Loading, StaleBanner, StatCard, StatusBadge, TableWrap } from '../components/Ui';
import { formatNumber, formatTime, formatWithUnit, plural } from '../format';
import { usePageTitle } from '../usePageTitle';

export function MeterDetailPage() {
  const { meterId = '' } = useParams();
  usePageTitle(`Meter ${meterId}`);
  const detail = useQuery({ queryKey: ['meter', meterId], queryFn: ({ signal }) => api.meter(meterId, signal) });
  const history = useQuery({ queryKey: ['consumption', meterId], queryFn: ({ signal }) => api.consumption(meterId, signal) });
  const [mode, setMode] = useState<ChartMode>('interval');
  const [showVoltage, setShowVoltage] = useState(true);

  const analysis = useMemo(() => {
    if (!detail.data || !history.data) return null;
    const { records, interval_minutes: step } = history.data.data;
    return {
      stats: summarise(records, step),
      anomalies: findAnomalies(detail.data.data, history.data.data),
    };
  }, [detail.data, history.data]);

  // Built once per answer, so the map keeps the view the user gave it when something else on the page re-renders.
  const point = useMemo<MapPoint | null>(() => {
    const meter = detail.data?.data;
    if (!meter || meter.latitude == null || meter.longitude == null) return null;
    return { id: meter.meter_id, lat: meter.latitude, lon: meter.longitude, status: meter.status };
  }, [detail.data]);

  if (detail.isPending) return <Loading label={`Loading meter ${meterId}…`} />;
  if (detail.isError) {
    return (
      <>
        <Link className="back" to="/meters">
          ← All meters
        </Link>
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </>
    );
  }

  const meter = detail.data.data;
  const zone = history.data?.data.portal_timezone;
  const stats = analysis?.stats;
  const latest = history.data?.data.records.slice(-8).reverse() ?? [];

  return (
    <>
      <div>
        <Link className="back" to="/meters">
          ← All meters
        </Link>
        <div className="page-head">
          <h1>Meter {meter.meter_id}</h1>
          <StatusBadge status={meter.status} />
        </div>
      </div>
      <StaleBanner caches={[detail.data.cache, history.data?.cache]} />

      {meter.hierarchy.length > 0 ? (
        <nav className="crumbs" aria-label="Position in the network">
          <ol>
            {meter.hierarchy.map((step) => (
              <li key={`${step.level}:${step.code}`}>
                {step.code ? <Link to={`/network?node=${encodeURIComponent(step.code)}`}>{step.name ?? step.code}</Link> : step.name}
                <span className="sr-only"> ({step.level})</span>
              </li>
            ))}
          </ol>
        </nav>
      ) : null}

      <div className="grid grid-2">
        <Card title="Nameplate">
          <dl className="facts">
            <dt>Meter ID</dt>
            <dd>{meter.meter_id}</dd>
            <dt>Serial number</dt>
            <dd>{meter.serial_number ?? '—'}</dd>
            <dt>Make</dt>
            <dd>{meter.make ?? '—'}</dd>
            <dt>Phase type</dt>
            <dd>{meter.phase_type ?? '—'}</dd>
            <dt>Installation type</dt>
            <dd>{meter.installation_type ?? '—'}</dd>
            <dt>Transformer</dt>
            <dd>
              {meter.dt_code ? <Link to={`/meters?dt=${encodeURIComponent(meter.dt_code)}`}>{meter.dt_code}</Link> : '—'}
              {meter.location ? ` · ${meter.location}` : ''}
            </dd>
          </dl>
        </Card>

        <Card title="Location">
          {point ? (
            <>
              <MeterMap points={[point]} height={220} label={`Map showing where meter ${meter.meter_id} is`} />
              <p>
                {formatNumber(point.lat, 5)}, {formatNumber(point.lon, 5)} (latitude, longitude)
              </p>
            </>
          ) : (
            <p>The portal has no usable coordinates for this meter.</p>
          )}
        </Card>
      </div>

      <Card
        title="Consumption"
        actions={
          <div className="toolbar">
            <div className="segmented" role="radiogroup" aria-label="Chart view">
              <label>
                <input type="radio" name="mode" checked={mode === 'interval'} onChange={() => setMode('interval')} />
                Per half hour
              </label>
              <label>
                <input type="radio" name="mode" checked={mode === 'register'} onChange={() => setMode('register')} />
                Meter register
              </label>
            </div>
            <label className="checks">
              <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                <input type="checkbox" checked={showVoltage} onChange={(e) => setShowVoltage(e.target.checked)} />
                Voltage
              </span>
            </label>
          </div>
        }
      >
        {history.isPending ? <Loading label="Loading readings…" /> : null}
        {history.isError ? <ErrorState error={history.error} onRetry={() => void history.refetch()} /> : null}
        {history.isSuccess && stats && stats.readings === 0 ? (
          <EmptyState title="No readings for this meter">The portal returned no consumption readings for it.</EmptyState>
        ) : null}
        {history.isSuccess && stats && stats.readings > 0 ? (
          <>
            <dl className="grid grid-stats">
              <StatCard label="Consumed in the window" value={formatWithUnit(stats.totalKwh, 'kWh', 1)} hint={plural(stats.readings, 'reading')} />
              <StatCard label="Average per half hour" value={formatWithUnit(stats.meanKwh, 'kWh', 2)} />
              <StatCard
                label="Peak half hour"
                value={formatWithUnit(stats.peak?.kwh, 'kWh', 2)}
                hint={stats.peak ? formatTime(stats.peak.at, zone) : undefined}
              />
              <StatCard
                label="Voltage range"
                value={stats.voltage ? `${formatNumber(stats.voltage.min, 0)}–${formatNumber(stats.voltage.max, 0)} V` : '—'}
                hint={stats.voltage ? `mean ${formatNumber(stats.voltage.mean, 0)} V` : undefined}
              />
            </dl>
            <figure>
              <ConsumptionChart
                records={history.data.data.records}
                stepMinutes={history.data.data.interval_minutes}
                timeZone={zone}
                mode={mode}
                showVoltage={showVoltage}
              />
              <figcaption>
                {plural(stats.readings, 'reading')} from {stats.first !== null ? formatTime(stats.first, zone) : '—'} to{' '}
                {stats.last !== null ? formatTime(stats.last, zone) : '—'}
                {zone ? ` (${zone})` : ''}. The portal states no time zone; {zone ?? 'UTC'} is an assumption. “Per half hour” is the
                difference between consecutive readings of the meter's cumulative register.
              </figcaption>
            </figure>
          </>
        ) : null}
      </Card>

      {analysis && analysis.stats.readings > 0 ? (
        <Card title="Worth a look">
          {analysis.anomalies.length === 0 ? (
            <p>
              {analysis.stats.readings >= 2
                ? 'Nothing unusual in this window: voltage steady, no gaps, no spikes.'
                : 'Only one reading, too few to say anything about this window.'}
            </p>
          ) : (
            <ul className="anomalies">
              {analysis.anomalies.map((a) => (
                <li key={a.id} data-severity={a.severity}>
                  <strong>
                    {a.severity === 'warn' ? 'Warning: ' : 'Note: '}
                    {a.title}
                  </strong>
                  <span>{a.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      {latest.length > 0 ? (
        <Card title="Latest readings">
          <TableWrap label="The eight most recent readings">
            <table>
              <caption className="sr-only">The eight most recent readings, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Time {zone ? `(${zone})` : '(UTC)'}</th>
                  <th scope="col" className="num">Register kWh</th>
                  <th scope="col" className="num">In the half hour</th>
                  <th scope="col" className="num">kVAh</th>
                  <th scope="col" className="num">Voltage R</th>
                </tr>
              </thead>
              <tbody>
                {latest.map((r) => (
                  <tr key={r.period}>
                    <th scope="row">{formatTime(r.period, zone)}</th>
                    <td className="num">{formatNumber(r.value, 2)}</td>
                    <td className="num">{formatNumber(r.interval_kwh, 2)}</td>
                    <td className="num">{formatNumber(r.kvah, 2)}</td>
                    <td className="num">{formatNumber(r.voltage_r, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      ) : null}
    </>
  );
}
