import type {
  CacheStatus,
  ConsumptionHistory,
  FleetSummary,
  HierarchyNode,
  HierarchyReport,
  MeterDetail,
  MeterSummary,
  Problem,
} from './types';

/** Where the API is. Empty (`VITE_API_BASE_URL=`) means "the same origin as this page", for when both are served together. */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, '');

/** An error answer from the API (an RFC 9457 problem), or `network_error` when it could not be reached at all. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** A response body plus what the headers add: where the data came from and, for lists, how many matched. */
export interface Result<T> {
  data: T;
  cache: CacheStatus | null;
  total: number | null;
}

type Param = string | number | boolean | readonly string[] | null | undefined;

async function get<T>(path: string, params: Record<string, Param> = {}, signal?: AbortSignal): Promise<Result<T>> {
  const url = new URL(API_BASE + path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    if (typeof value === 'object') value.forEach((item) => url.searchParams.append(key, item));
    else url.searchParams.set(key, String(value)); // URLSearchParams percent-encodes: a serial such as "L&84997" is safe
  }

  let response: Response;
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' }, signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(
      0,
      'network_error',
      `Cannot reach the API at ${API_BASE || window.location.origin}. Is it running, and does it allow this origin (URJA_CORS_ALLOW_ORIGINS)?`,
    );
  }

  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as Partial<Problem> | null;
    throw new ApiError(
      response.status,
      problem?.code ?? 'http_error',
      problem?.detail ?? (response.statusText || `HTTP ${response.status}`), // statusText is empty over HTTP/2
      problem?.request_id,
    );
  }

  let data: T;
  try {
    data = (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, 'invalid_response', 'The API answered, but not with JSON. Is the API address right?');
  }
  const total = response.headers.get('X-Total-Count');
  return {
    data,
    cache: response.headers.get('X-Cache') as CacheStatus | null,
    total: total === null ? null : Number(total),
  };
}

export interface MeterQuery {
  q?: string;
  status?: string;
  make?: string;
  phase?: string;
  dt?: string;
  zone?: string;
  circle?: string;
  division?: string;
  subdivision?: string;
  substation?: string;
  feeder?: string;
  include?: readonly ('coordinates' | 'hierarchy')[];
  limit?: number;
  offset?: number;
}

export const api = {
  summary: (signal?: AbortSignal) => get<FleetSummary>('/api/v1/fleet/summary', {}, signal),
  meters: (query: MeterQuery, signal?: AbortSignal) => get<MeterSummary[]>('/api/v1/meters', { ...query }, signal),
  meter: (id: string, signal?: AbortSignal) => get<MeterDetail>(`/api/v1/meters/${encodeURIComponent(id)}`, {}, signal),
  consumption: (id: string, signal?: AbortSignal) =>
    get<ConsumptionHistory>(`/api/v1/meters/${encodeURIComponent(id)}/consumption`, {}, signal),
  hierarchy: (signal?: AbortSignal) => get<HierarchyNode[]>('/api/v1/hierarchy', {}, signal),
  issues: (signal?: AbortSignal) => get<HierarchyReport>('/api/v1/hierarchy/issues', {}, signal),
};
