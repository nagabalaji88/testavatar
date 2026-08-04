import { useMemo } from 'react';
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import type { Candidate, ConsensusPrompt, MetricDefinition } from '@/types';
import {
  AXIS_COLOR,
  CONSENSUS_COLOR,
  GRID_COLOR,
  MAX_SERIES,
  TEXT_MUTED,
  TEXT_SECONDARY,
  seriesColor,
} from '@/lib/viz';
import { metricLabel } from '@/lib/utils';

interface ScoreRadarProps {
  candidates: Candidate[];
  consensus: ConsensusPrompt | null;
  definitions: MetricDefinition[];
}

interface RadarRow {
  metric: string;
  fullLabel: string;
  [series: string]: string | number;
}

interface TooltipEntry {
  name?: string;
  value?: number | string;
  color?: string;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass-elevated rounded-xl px-3 py-2 text-[12px]">
      <p className="mb-1.5 font-medium text-white">{label}</p>
      <ul className="space-y-1">
        {payload.map((entry) => (
          <li key={String(entry.name)} className="flex items-center gap-2">
            <span
              className="size-2 rounded-full"
              style={{ backgroundColor: entry.color }}
              aria-hidden
            />
            <span className="text-dim">{entry.name}</span>
            <span className="ml-auto font-mono text-white">
              {Number(entry.value ?? 0).toFixed(1)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Rubric profile across all fifteen criteria.
 *
 * Candidates are capped at the validated three-slot series limit — the
 * remaining models stay in the leaderboard and heatmap rather than adding
 * unvalidated hues here.
 */
export function ScoreRadar({ candidates, consensus, definitions }: ScoreRadarProps) {
  const ranked = useMemo(
    () =>
      [...candidates]
        .filter((candidate) => candidate.metrics)
        .sort((a, b) => (b.overall_score ?? 0) - (a.overall_score ?? 0))
        .slice(0, MAX_SERIES),
    [candidates],
  );

  const rows = useMemo<RadarRow[]>(() => {
    const metrics = definitions.length
      ? definitions.map((definition) => definition.key)
      : Object.keys(ranked[0]?.metrics ?? consensus?.metrics ?? {});

    return metrics.map((key) => {
      const row: RadarRow = {
        metric: metricLabel(key)
          .replace(' Accuracy', '')
          .replace('Hallucination Prevention', 'Grounding'),
        fullLabel: metricLabel(key),
      };
      for (const candidate of ranked) {
        row[candidate.model_name] = candidate.metrics?.[key] ?? 0;
      }
      if (consensus?.metrics) {
        row.Consensus = consensus.metrics[key] ?? 0;
      }
      return row;
    });
  }, [definitions, ranked, consensus]);

  if (!rows.length) {
    return (
      <div className="flex h-[320px] items-center justify-center text-[13px] text-faint">
        Rubric scores appear once the AI Judge has evaluated the candidates.
      </div>
    );
  }

  return (
    <div>
      <div className="h-[340px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={rows} outerRadius="72%">
            <PolarGrid stroke={GRID_COLOR} strokeWidth={1} />
            <PolarAngleAxis
              dataKey="metric"
              tick={{ fill: TEXT_SECONDARY, fontSize: 10.5 }}
              tickLine={false}
            />
            <PolarRadiusAxis
              domain={[0, 100]}
              tickCount={5}
              axisLine={{ stroke: AXIS_COLOR }}
              tick={{ fill: TEXT_MUTED, fontSize: 9 }}
              angle={90}
            />
            {ranked.map((candidate, index) => (
              <Radar
                key={candidate.model_id}
                name={candidate.model_name}
                dataKey={candidate.model_name}
                stroke={seriesColor(index)}
                strokeWidth={2}
                fill={seriesColor(index)}
                fillOpacity={0.1}
                dot={{ r: 2.5, strokeWidth: 0, fill: seriesColor(index) }}
                isAnimationActive={false}
              />
            ))}
            {consensus?.metrics ? (
              <Radar
                name="Consensus"
                dataKey="Consensus"
                stroke={CONSENSUS_COLOR}
                strokeWidth={3}
                strokeDasharray="6 3"
                fill={CONSENSUS_COLOR}
                fillOpacity={0.06}
                dot={{ r: 3, strokeWidth: 0, fill: CONSENSUS_COLOR }}
                isAnimationActive={false}
              />
            ) : null}
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: AXIS_COLOR }} />
          </RadarChart>
        </ResponsiveContainer>
      </div>

      <ul className="mt-3 flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
        {ranked.map((candidate, index) => (
          <li
            key={candidate.model_id}
            className="flex items-center gap-2 text-[12px] text-dim"
          >
            <span
              className="h-0.5 w-4 rounded-full"
              style={{ backgroundColor: seriesColor(index) }}
              aria-hidden
            />
            {candidate.model_name}
          </li>
        ))}
        {consensus?.metrics ? (
          <li className="flex items-center gap-2 text-[12px] font-medium text-white">
            <span
              className="h-[3px] w-4 rounded-full"
              style={{
                backgroundImage: `repeating-linear-gradient(90deg, ${CONSENSUS_COLOR} 0 6px, transparent 6px 9px)`,
              }}
              aria-hidden
            />
            Consensus
          </li>
        ) : null}
      </ul>

      {candidates.length > MAX_SERIES ? (
        <p className="mt-2 text-center text-[11px] text-faint">
          Showing the top {MAX_SERIES} candidates by score — all models remain in the
          heatmap and leaderboard.
        </p>
      ) : null}
    </div>
  );
}
