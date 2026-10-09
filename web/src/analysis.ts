import { formatNumber, formatTime, plural } from './format';
import type { ConsumptionHistory, ConsumptionRecord, MeterDetail } from './types';

export interface Anomaly {
  id: string;
  severity: 'warn' | 'info';
  title: string;
  detail: string;
}

/** One reading on the time axis, with what the chart and the checks need to know about its neighbour before it. */
export interface SeriesPoint {
  /** Milliseconds since the epoch. */
  t: number;
  register: number | null;
  voltage: number | null;
  /** kWh in the interval ending here. The API leaves it empty for the first reading, after a gap and where the register fell. */
  interval: number | null;
  /** Readings are missing between this one and the one before it. */
  afterGap: boolean;
}

export interface ConsumptionStats {
  readings: number;
  first: number | null;
  last: number | null;
  /** Consumption over the complete intervals: an interval that spans a gap is not counted. */
  totalKwh: number;
  meanKwh: number | null;
  peak: { kwh: number; at: number } | null;
  voltage: { min: number; max: number; mean: number } | null;
}

/** Nominal single-phase supply voltage, and how far a reading may stray before it is worth flagging. */
const NOMINAL_VOLTS = 230;
const VOLTAGE_TOLERANCE = 0.1;
const IDLE_RUN = 4; // this many intervals in a row with no consumption at all
/** A reading more than this many steps after the previous one means readings are missing in between. */
const GAP_STEPS = 1.5;

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? (sorted[middle] ?? 0) : ((sorted[middle - 1] ?? 0) + (sorted[middle] ?? 0)) / 2;
}

/** The readings in time order, with the ones whose time cannot be read left out, and gaps marked. */
export function toSeries(records: readonly ConsumptionRecord[], stepMinutes?: number | null): SeriesPoint[] {
  const step = (stepMinutes ?? 30) * 60_000;
  const timed = records
    .map((record) => ({ record, t: Date.parse(record.period) }))
    .filter((reading) => !Number.isNaN(reading.t))
    .sort((a, b) => a.t - b.t);
  return timed.map(({ record, t }, i) => {
    const before = timed[i - 1];
    return {
      t,
      register: record.value ?? null,
      voltage: record.voltage_r ?? null,
      interval: record.interval_kwh ?? null,
      afterGap: before !== undefined && t - before.t > step * GAP_STEPS,
    };
  });
}

export function summarise(records: readonly ConsumptionRecord[], stepMinutes?: number | null): ConsumptionStats {
  const series = toSeries(records, stepMinutes);
  const intervals = series.flatMap((p) => (p.interval === null ? [] : [{ kwh: p.interval, at: p.t }]));
  const volts = series.flatMap((p) => (p.voltage === null ? [] : [p.voltage]));
  const totalKwh = intervals.reduce((sum, i) => sum + i.kwh, 0);
  const peak = intervals.reduce<{ kwh: number; at: number } | null>(
    (best, i) => (best === null || i.kwh > best.kwh ? i : best),
    null,
  );
  return {
    readings: series.length,
    first: series[0]?.t ?? null,
    last: series[series.length - 1]?.t ?? null,
    totalKwh,
    meanKwh: intervals.length ? totalKwh / intervals.length : null,
    peak,
    voltage: volts.length
      ? { min: Math.min(...volts), max: Math.max(...volts), mean: volts.reduce((a, b) => a + b, 0) / volts.length }
      : null,
  };
}

/** What a field engineer would want to be told about a meter, computed from what the API returns. */
export function findAnomalies(detail: MeterDetail, history: ConsumptionHistory): Anomaly[] {
  const zone = history.portal_timezone;
  const series = toSeries(history.records, history.interval_minutes);
  const stats = summarise(history.records, history.interval_minutes);
  const step = history.interval_minutes ?? 30;
  const found: Anomaly[] = [];

  if (detail.status?.toUpperCase() === 'DECOMMISSIONED' && stats.totalKwh > 0) {
    found.push({
      id: 'decommissioned-consuming',
      severity: 'warn',
      title: 'Decommissioned, yet still reporting consumption',
      detail: `${formatNumber(stats.totalKwh, 1)} kWh over ${plural(stats.readings, 'reading')}. Either the status or the readings are stale.`,
    });
  }
  if (detail.status?.toUpperCase() === 'FAULTY') {
    found.push({
      id: 'faulty',
      severity: 'info',
      title: 'The portal marks this meter as faulty',
      detail: 'Treat its readings with care.',
    });
  }

  const low = NOMINAL_VOLTS * (1 - VOLTAGE_TOLERANCE);
  const high = NOMINAL_VOLTS * (1 + VOLTAGE_TOLERANCE);
  const outside = series.filter((p) => p.voltage !== null && (p.voltage < low || p.voltage > high));
  if (outside.length > 0) {
    found.push({
      id: 'voltage',
      severity: 'warn',
      title: `Voltage outside ${Math.round(low)}–${Math.round(high)} V`,
      detail: `${plural(outside.length, 'reading')}, the first at ${formatTime(outside[0]?.t ?? 0, zone)}.`,
    });
  }

  const gaps = series.flatMap((p, i) => (p.afterGap ? [series[i - 1]?.t ?? p.t] : []));
  if (gaps.length > 0) {
    found.push({
      id: 'gaps',
      severity: 'warn',
      title: 'Missing readings',
      detail: `${plural(gaps.length, 'gap')} longer than ${step} minutes, the first after ${formatTime(gaps[0] ?? 0, zone)}. Gaps are shown as gaps, never filled in, and the interval after one is left out of the totals.`,
    });
  }

  const intervals = series.flatMap((p) => (p.interval === null ? [] : [p.interval]));
  const typical = intervals.length ? median(intervals) : 0;
  const spikes = typical > 0 ? intervals.filter((kwh) => kwh > typical * 3).length : 0;
  if (spikes > 0) {
    found.push({
      id: 'spikes',
      severity: 'warn',
      title: 'Consumption spikes',
      detail: `${plural(spikes, 'interval')} above three times the typical ${formatNumber(typical, 2)} kWh.`,
    });
  }

  let run = 0;
  let idleRuns = 0;
  for (const p of series) {
    run = p.interval === 0 ? run + 1 : 0;
    if (run === IDLE_RUN) idleRuns += 1;
  }
  if (idleRuns > 0) {
    found.push({
      id: 'idle',
      severity: 'info',
      title: 'Idle stretches',
      detail: `${plural(idleRuns, 'stretch', 'stretches')} of ${IDLE_RUN} or more intervals with no consumption.`,
    });
  }

  // A fall: a readable register straight after a readable one, yet no interval. (The API leaves the interval empty
  // only for the first reading, after a missing or unreadable one, or when the register fell below the last.)
  let falls = 0;
  let previous: number | null = null;
  for (const p of series) {
    if (p.register !== null && previous !== null && p.interval === null && !p.afterGap) falls += 1;
    previous = p.register;
  }
  if (falls > 0) {
    found.push({
      id: 'register-fell',
      severity: 'warn',
      title: 'The kWh register went down',
      detail: `${plural(falls, 'reading')}: a reset or rollover, not negative consumption.`,
    });
  }
  return found;
}
