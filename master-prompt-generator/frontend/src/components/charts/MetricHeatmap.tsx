import { useState } from 'react';
import { motion } from 'framer-motion';
import type { Candidate, ConsensusPrompt, MetricDefinition } from '@/types';
import { SEQUENTIAL_LEGEND, inkOn, sequentialContinuous } from '@/lib/viz';
import { metricLabel } from '@/lib/utils';

interface MetricHeatmapProps {
  candidates: Candidate[];
  consensus: ConsensusPrompt | null;
  definitions: MetricDefinition[];
}

interface HoverCell {
  row: string;
  metric: string;
  value: number;
  weight: number;
}

/** Model × criterion magnitude grid on a single-hue sequential ramp. */
export function MetricHeatmap({
  candidates,
  consensus,
  definitions,
}: MetricHeatmapProps) {
  const [hover, setHover] = useState<HoverCell | null>(null);

  const scored = candidates.filter((candidate) => candidate.metrics);
  const metricKeys = definitions.length
    ? definitions.map((definition) => definition.key)
    : Object.keys(scored[0]?.metrics ?? consensus?.metrics ?? {});

  if (!metricKeys.length || (!scored.length && !consensus)) {
    return (
      <div className="flex h-40 items-center justify-center text-[13px] text-faint">
        The heatmap fills in as each model is scored.
      </div>
    );
  }

  const rows: { label: string; metrics: Record<string, number>; hero: boolean }[] = [
    ...scored.map((candidate) => ({
      label: candidate.model_name,
      metrics: candidate.metrics ?? {},
      hero: false,
    })),
  ];
  if (consensus?.metrics) {
    rows.push({ label: 'Consensus', metrics: consensus.metrics, hero: true });
  }

  const weightOf = (key: string) =>
    definitions.find((definition) => definition.key === key)?.weight ?? 0;

  return (
    <div className="relative">
      <div className="overflow-x-auto pb-1">
        <table className="w-full min-w-[720px] border-separate border-spacing-[2px]">
          <caption className="sr-only">
            Rubric score for each model across every evaluation criterion
          </caption>
          <thead>
            <tr>
              <th
                scope="col"
                className="w-40 pb-2 pr-3 text-left text-[11px] font-medium text-faint"
              >
                Model
              </th>
              {metricKeys.map((key) => (
                <th
                  key={key}
                  scope="col"
                  className="pb-2 text-center align-bottom text-[10px] font-medium text-faint"
                >
                  <span className="inline-block max-w-[64px] leading-tight">
                    {metricLabel(key).replace('Hallucination Prevention', 'Grounding')}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <th
                  scope="row"
                  className={`pr-3 text-left text-[12px] font-medium ${
                    row.hero ? 'text-ink-strong' : 'text-dim'
                  }`}
                >
                  {row.label}
                </th>
                {metricKeys.map((key) => {
                  const value = row.metrics[key] ?? 0;
                  return (
                    <td key={key} className="p-0">
                      <motion.div
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ duration: 0.25 }}
                        onMouseEnter={() =>
                          setHover({
                            row: row.label,
                            metric: metricLabel(key),
                            value,
                            weight: weightOf(key),
                          })
                        }
                        onMouseLeave={() => setHover(null)}
                        className="grid h-9 cursor-default place-items-center rounded-[6px] font-mono text-[11px] tabular-nums transition-transform hover:scale-[1.08]"
                        style={{
                          backgroundColor: sequentialContinuous(value),
                          color: inkOn(value),
                          boxShadow: row.hero
                            ? 'inset 0 0 0 1.5px rgba(233,237,250,0.55)'
                            : undefined,
                        }}
                        title={`${row.label} — ${metricLabel(key)}: ${value.toFixed(1)}`}
                      >
                        {value.toFixed(0)}
                      </motion.div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-[11px] text-faint">
          <span>0</span>
          <div className="flex overflow-hidden rounded-full">
            {SEQUENTIAL_LEGEND.map((step) => (
              <span
                key={step}
                className="h-2 w-6"
                style={{ backgroundColor: step }}
                aria-hidden
              />
            ))}
          </div>
          <span>100</span>
        </div>
        <p className="min-h-[16px] text-[11px] text-dim">
          {hover
            ? `${hover.row} · ${hover.metric}: ${hover.value.toFixed(1)} (rubric weight ${(
                hover.weight * 100
              ).toFixed(0)}%)`
            : 'Hover a cell for the weighted detail.'}
        </p>
      </div>
    </div>
  );
}
