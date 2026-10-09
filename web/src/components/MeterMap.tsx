import { useEffect } from 'react';
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from 'react-leaflet';
import { Link } from 'react-router-dom';
import { statusLabel, statusTone, toneColor, usePalette, type Palette, type Tone } from '../format';

export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  status: string | null;
}

/** react-leaflet does not pass `aria-*` props on to the map's element, so the name is set on the element itself. */
function NameMap({ label }: { label: string }) {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    container.setAttribute('role', 'region');
    container.setAttribute('aria-label', label);
  }, [map, label]);
  return null;
}

function FitBounds({ points }: { points: readonly MapPoint[] }) {
  const map = useMap();
  // Fit again only when the set of places changes, not each time a parent re-renders with a new (equal) array.
  const key = points.map((p) => `${p.lat},${p.lon}`).join(';');
  useEffect(() => {
    if (!key) return;
    const bounds = key.split(';').map((pair) => pair.split(',').map(Number) as [number, number]);
    map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
  }, [map, key]);
  return null;
}

/** Status is also in the marker's size and outline, not only its colour: faulty is larger, decommissioned is dashed. */
function markerStyle(tone: Tone, large: boolean, colors: Palette) {
  const base = large ? 6 : 10;
  const fillColor = toneColor(colors, tone);
  switch (tone) {
    case 'bad':
      return { radius: base + 2, pathOptions: { color: colors.surface, weight: 2, fillColor, fillOpacity: 0.95 } };
    case 'muted':
      return {
        radius: base - 1,
        pathOptions: { color: colors.text, weight: 1.5, dashArray: '2 2', fillColor, fillOpacity: 0.7 },
      };
    default:
      return { radius: base, pathOptions: { color: colors.surface, weight: 1.5, fillColor, fillOpacity: 0.92 } };
  }
}

interface Props {
  /** The markers to draw. */
  points: readonly MapPoint[];
  /** The points the view is fitted to; defaults to `points`. Keeps the view still while markers are filtered. */
  fitTo?: readonly MapPoint[];
  height?: number;
  large?: boolean;
  label?: string;
}

/**
 * OpenStreetMap tiles (free, attributed, low volume) under one circle per meter. A map is no use to a screen-reader
 * user, so every page that shows one also links to the same data as a table or list.
 */
export function MeterMap({
  points,
  fitTo,
  height = 480,
  large = false,
  label = 'Map of meters, marked by installation status',
}: Props) {
  const colors = usePalette();
  return (
    <MapContainer center={[26.9, 75.8]} zoom={11} style={{ height }} scrollWheelZoom={large}>
      <NameMap label={label} />
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds points={fitTo ?? points} />
      {points.map((p) => {
        const { radius, pathOptions } = markerStyle(statusTone(p.status), large, colors);
        return (
          <CircleMarker key={p.id} center={[p.lat, p.lon]} radius={radius} pathOptions={pathOptions}>
            <Popup>
              <Link to={`/meters/${encodeURIComponent(p.id)}`}>{p.id}</Link> · {statusLabel(p.status)}
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}
