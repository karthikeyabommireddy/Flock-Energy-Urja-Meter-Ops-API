// The API's response shapes. They mirror the schemas in ../../openapi.json (the source of truth). An optional
// field may be absent from a response: the geo and hierarchy fields appear only when asked for (include=, or a
// geo filter), and the others are optional here so that a leaner answer, from an older API, still type-checks.

export type CacheStatus = 'HIT' | 'MISS' | 'STALE';

export interface MeterSummary {
  meter_id: string;
  serial_number: string | null;
  status: string | null;
  location: string | null;
  make?: string | null;
  phase_type?: string | null;
  dt_code?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  distance_m?: number | null;
  hierarchy_path?: string[] | null;
}

export interface HierarchyLevel {
  level: string;
  name: string | null;
  code: string | null;
}

export interface MeterDetail {
  meter_id: string;
  serial_number: string | null;
  status: string | null;
  location: string | null;
  installed_date: string | null;
  make: string | null;
  phase_type: string | null;
  installation_type: string | null;
  dt_code: string | null;
  latitude: number | null;
  longitude: number | null;
  hierarchy: HierarchyLevel[];
  raw_fields: Record<string, string>;
}

export interface ConsumptionRecord {
  period: string;
  /** The meter's cumulative kWh register, not the consumption in the interval. */
  value: number | null;
  unit: string | null;
  kvah?: number | null;
  voltage_r?: number | null;
  /** kWh consumed since the reading one step earlier; null for the first reading, after a gap and where the register fell. */
  interval_kwh?: number | null;
}

export interface ConsumptionHistory {
  meter_id: string;
  records: ConsumptionRecord[];
  interval_minutes?: number | null;
  portal_timezone?: string | null;
}

export interface HierarchyNode {
  node_id: string;
  name: string | null;
  node_type: string | null;
  children: HierarchyNode[];
  meter_count?: number | null;
  capacity_kva?: number | null;
}

export interface HierarchyIssue {
  type: string;
  id: string;
  detail: string;
}

export interface HierarchyReport {
  checked: Record<string, number>;
  issues: HierarchyIssue[];
}

export interface FleetSummary {
  total_meters: number;
  by_status: Record<string, number>;
  by_make: Record<string, number>;
  by_phase: Record<string, number>;
  by_dt: Record<string, number>;
  transformers: number;
}

/** RFC 9457 problem details, as the API sends every error. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string;
  request_id?: string;
}
