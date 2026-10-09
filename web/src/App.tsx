import { lazy } from 'react';
import { Link, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { MetersPage } from './pages/MetersPage';
import { NetworkPage } from './pages/NetworkPage';
import { usePageTitle } from './usePageTitle';

// The pages that draw charts or maps pull in Recharts or Leaflet; load them on demand so the shell and the list pages
// stay light. (The Suspense boundary that waits for them lives in Layout, inside the page shell.)
const FleetPage = lazy(() => import('./pages/FleetPage').then((m) => ({ default: m.FleetPage })));
const MapPage = lazy(() => import('./pages/MapPage').then((m) => ({ default: m.MapPage })));
const MeterDetailPage = lazy(() => import('./pages/MeterDetailPage').then((m) => ({ default: m.MeterDetailPage })));

function NotFound() {
  usePageTitle('Page not found');
  return (
    <>
      <h1>Page not found</h1>
      <p>
        <Link to="/">Back to the fleet overview</Link>
      </p>
    </>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<FleetPage />} />
        <Route path="meters" element={<MetersPage />} />
        <Route path="meters/:meterId" element={<MeterDetailPage />} />
        <Route path="map" element={<MapPage />} />
        <Route path="network" element={<NetworkPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
