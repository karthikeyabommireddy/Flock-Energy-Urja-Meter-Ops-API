import { useMemo } from 'react';
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { toSeries } from '../analysis';
import { formatNumber, formatTick, formatTime, usePalette } from '../format';
import type { ConsumptionRecord } from '../types';

export type ChartMode = 'interval' | 'register';

interface Props {
  records: readonly ConsumptionRecord[];
  /** The readings' usual spacing in minutes, as the API states it; a longer jump between readings is drawn as a gap. */
  stepMinutes?: number | null;
  timeZone?: string | null;
  mode: ChartMode;
  showVoltage: boolean;
}

interface Row {
  t: number;
  interval: number | null;
  register: number | null;
  voltage: number | null;
}

/**
 * Half-hourly consumption. "interval" draws the kWh used in each half hour; "register" draws the meter's cumulative
 * kWh register. Missing readings stay missing: a gap in the data is a gap in the chart, never an interpolated line.
 */
export function ConsumptionChart({ records, stepMinutes, timeZone, mode, showVoltage }: Props) {
  const colors = usePalette();
  const data = useMemo(() => {
    const rows: Row[] = [];
    for (const p of toSeries(records, stepMinutes)) {
      // A row of nothing just before a reading that follows a gap: Recharts breaks a line at an empty value, so the
      // line stops where the readings stop instead of running straight across the gap.
      if (p.afterGap) rows.push({ t: p.t - 1, interval: null, register: null, voltage: null });
      rows.push({ t: p.t, interval: p.interval, register: p.register, voltage: p.voltage });
    }
    return rows;
  }, [records, stepMinutes]);
  const axis = { stroke: colors.text, fontSize: 12 };

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={colors.grid} vertical={false} />
        <XAxis
          dataKey="t"
          type="number"
          scale="time"
          domain={['dataMin', 'dataMax']}
          tickFormatter={(t: number) => formatTick(t, timeZone)}
          minTickGap={56}
          {...axis}
        />
        <YAxis
          yAxisId="left"
          width={60}
          domain={mode === 'register' ? ['dataMin', 'dataMax'] : [0, 'auto']}
          tickFormatter={(v: number) => formatNumber(v, mode === 'register' ? 0 : 2)}
          {...axis}
        />
        {showVoltage ? (
          <YAxis yAxisId="right" orientation="right" width={44} domain={[200, 260]} unit=" V" {...axis} />
        ) : null}
        <Tooltip
          labelFormatter={(label) => formatTime(Number(label), timeZone)}
          formatter={(value, name) => [typeof value === 'number' ? formatNumber(value, 2) : '—', name]}
          contentStyle={{ background: colors.surface, border: `1px solid ${colors.grid}`, borderRadius: 8 }}
        />
        {mode === 'interval' ? (
          <Bar
            yAxisId="left"
            dataKey="interval"
            name="kWh in the half hour"
            fill={colors.primary}
            fillOpacity={0.7}
            maxBarSize={6}
            isAnimationActive={false}
          />
        ) : (
          <Line
            yAxisId="left"
            dataKey="register"
            name="kWh register"
            stroke={colors.primary}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        )}
        {showVoltage ? (
          <Line
            yAxisId="right"
            dataKey="voltage"
            name="Voltage (V)"
            stroke={colors.warn}
            strokeWidth={1.75}
            dot={false}
            isAnimationActive={false}
          />
        ) : null}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
