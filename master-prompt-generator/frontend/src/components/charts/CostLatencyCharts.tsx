import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Candidate } from '@/types';
import { seriesColor, useVizTheme } from '@/lib/viz';
import { formatCurrency, formatDuration } from '@/lib/utils';

interface Datum {
  model: string;
  value: number;
  index: number;
}

function BarTooltip({
  active,
  payload,
  formatter,
  unit,
}: {
  active?: boolean;
  payload?: { payload?: Datum }[];
  formatter: (value: number) => string;
  unit: string;
}) {
  const datum = active ? payload?.[0]?.payload : undefined;
  if (!datum) return null;
  return (
    <div className="glass-elevated rounded-xl px-3 py-2 text-[12px]">
      <p className="font-medium text-ink-strong">{datum.model}</p>
      <p className="text-dim">
        {unit}: <span className="font-mono text-ink-strong">{formatter(datum.value)}</span>
      </p>
    </div>
  );
}

function MeasureChart({
  title,
  data,
  formatter,
  unit,
}: {
  title: string;
  data: Datum[];
  formatter: (value: number) => string;
  unit: string;
}) {
  const viz = useVizTheme();
  return (
    <div>
      <h3 className="mb-2 text-[12px] font-medium text-dim">{title}</h3>
      <div className="h-[188px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke={viz.GRID_COLOR} vertical={false} />
            <XAxis
              dataKey="model"
              tick={{ fill: viz.TEXT_SECONDARY, fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: viz.AXIS_COLOR }}
              interval={0}
            />
            <YAxis
              tick={{ fill: viz.TEXT_MUTED, fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={52}
              tickFormatter={(value: number) => formatter(value)}
            />
            <Tooltip
              cursor={{ fill: viz.GRID_COLOR }}
              content={<BarTooltip formatter={formatter} unit={unit} />}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={34}>
              {data.map((datum) => (
                <Cell
                  key={datum.model}
                  fill={seriesColor(datum.index)}
                  stroke={viz.SURFACE}
                  strokeWidth={2}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/**
 * Cost and latency are different measures, so they get their own axis each —
 * small multiples rather than a dual-axis chart.
 */
export function CostLatencyCharts({ candidates }: { candidates: Candidate[] }) {
  const active = candidates.filter((candidate) => candidate.status === 'succeeded');

  if (!active.length) {
    return (
      <div className="flex h-40 items-center justify-center text-[13px] text-faint">
        Telemetry appears as models return.
      </div>
    );
  }

  const cost: Datum[] = active.map((candidate, index) => ({
    model: candidate.model_name,
    value: candidate.cost_usd,
    index,
  }));
  const latency: Datum[] = active.map((candidate, index) => ({
    model: candidate.model_name,
    value: candidate.latency_ms,
    index,
  }));

  return (
    <div className="grid gap-6 sm:grid-cols-2">
      <MeasureChart
        title="Cost per candidate"
        data={cost}
        formatter={formatCurrency}
        unit="Cost"
      />
      <MeasureChart
        title="Generation latency"
        data={latency}
        formatter={(value) => formatDuration(value)}
        unit="Latency"
      />
    </div>
  );
}
