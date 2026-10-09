import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import 'leaflet/dist/leaflet.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App } from './App';
import { ApiError } from './api';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The data barely moves (the API caches it for minutes), so a minute of freshness is plenty.
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      // One retry for a flaky upstream (a 5xx); a 4xx, or an API that cannot be reached, will not get better.
      retry: (failureCount, error) => error instanceof ApiError && error.status >= 500 && failureCount < 1,
    },
  },
});

const root = document.getElementById('root');
if (!root) throw new Error('index.html has no #root element');

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
