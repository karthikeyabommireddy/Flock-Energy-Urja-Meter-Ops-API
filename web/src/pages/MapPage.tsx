import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { MeterMap, type MapPoint } from '../components/MeterMap';
import { Card, ErrorState, Loading, StaleBanner } from '../components/Ui';
import { formatNumber, statusLabel, statusTone, toneColor, usePalette } from '../format';
import { usePageTitle } from '../usePageTitle';

const STATUSES = ['INSTALLED', 'FAULTY', 'DECOMMISSIONED'] as const;

export function MapPage() {
  usePageTitle('Meter map');
  const palette = usePalette();
  const [shown, setShown] = useState<Record<string, boolean>>({ INSTALLED: true, FAULTY: true, DECOMMISSIONED: true });

  // Coordinates cost the API one portal request per meter the first time; afterwards it serves them from memory.
  const meters = useQuery({
    queryKey: ['meters', 'map'],
    queryFn: ({ signal }) => api.meters({ include: ['coordinates'], limit: 500 }, signal),
    staleTime: 10 * 60_000,
  });

  const all = useMemo<MapPoint[]>(
    () =>
      (meters.data?.data ?? []).flatMap((m) =>
        m.latitude != null && m.longitude != null
          ? [{ id: m.meter_id, lat: m.latitude, lon: m.longitude, status: m.status }]
          : [],
      ),
    [meters.data],
  );
  const visible = useMemo(() => all.filter((p) => shown[(p.status ?? '').toUpperCase()] !== false), [all, shown]);
  const total = meters.data?.data.length ?? 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Meter map</h1>
          <p>
            {meters.isSuccess
              ? `${formatNumber(all.length, 0)} of ${formatNumber(total, 0)} meters have usable coordinates.`
              : 'Where the meters are, marked by installation status.'}{' '}
            Faulty meters are drawn larger and decommissioned ones with a dashed outline. <Link to="/meters">Prefer a table?</Link>
          </p>
        </div>
      </div>
      <StaleBanner caches={[meters.data?.cache]} />

      <Card
        title="Where the meters are"
        actions={
          <div className="checks" role="group" aria-label="Show meters with status">
            {STATUSES.map((status) => (
              <label key={status}>
                <input
                  type="checkbox"
                  checked={shown[status] !== false}
                  onChange={(e) => setShown((current) => ({ ...current, [status]: e.target.checked }))}
                />
                <span className="swatch" style={{ background: toneColor(palette, statusTone(status)) }} aria-hidden="true" />
                {statusLabel(status)}
              </label>
            ))}
          </div>
        }
      >
        {meters.isPending ? (
          <Loading label="Fetching coordinates from the portal. The first load makes one request per meter (about 400) and can take several seconds; later loads are instant." />
        ) : null}
        {meters.isError ? <ErrorState error={meters.error} onRetry={() => void meters.refetch()} /> : null}
        {meters.isSuccess && all.length === 0 ? <p>The portal returned no usable coordinates.</p> : null}
        {meters.isSuccess && all.length > 0 ? <MeterMap points={visible} fitTo={all} height={560} large /> : null}
      </Card>
    </>
  );
}
