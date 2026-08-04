import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Coins, Cpu, Gauge, Timer } from 'lucide-react';
import type { Candidate, ConsensusPrompt, RunDetail } from '@/types';
import { formatCurrency, formatDuration, formatNumber } from '@/lib/utils';

interface TileProps {
  label: string;
  value: string;
  hint?: string;
  icon: ReactNode;
  delay: number;
}

function Tile({ label, value, hint, icon, delay }: TileProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay, ease: [0.22, 1, 0.36, 1] }}
      className="glass relative overflow-hidden rounded-[18px] px-4 py-3.5"
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium tracking-wide text-faint uppercase">
          {label}
        </span>
        <span className="text-aurora-300">{icon}</span>
      </div>
      <p className="mt-2 font-mono text-[22px] font-semibold leading-none tabular-nums text-white">
        {value}
      </p>
      {hint ? <p className="mt-1.5 text-[11px] text-dim">{hint}</p> : null}
    </motion.div>
  );
}

interface StatTilesProps {
  run: RunDetail | null;
  candidates: Candidate[];
  consensus: ConsensusPrompt | null;
}

export function StatTiles({ run, candidates, consensus }: StatTilesProps) {
  const succeeded = candidates.filter((candidate) => candidate.status === 'succeeded');
  const totalCost =
    run?.total_cost_usd ??
    candidates.reduce((sum, candidate) => sum + candidate.cost_usd, 0);
  const slowest = succeeded.reduce(
    (max, candidate) => Math.max(max, candidate.latency_ms),
    0,
  );
  const bestCandidate = succeeded.reduce(
    (best, candidate) => Math.max(best, candidate.overall_score ?? 0),
    0,
  );
  const improvement = consensus?.improvement_over_best ?? null;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Models"
        value={`${succeeded.length}/${candidates.length || 0}`}
        hint={`${candidates.length - succeeded.length} pending or failed`}
        icon={<Cpu className="size-4" />}
        delay={0}
      />
      <Tile
        label="Spend"
        value={formatCurrency(totalCost)}
        hint={`${formatNumber(
          (run?.total_input_tokens ?? 0) + (run?.total_output_tokens ?? 0),
        )} tokens`}
        icon={<Coins className="size-4" />}
        delay={0.05}
      />
      <Tile
        label="Wall clock"
        value={formatDuration(run?.duration_ms ?? (slowest || null))}
        hint={slowest ? `slowest model ${formatDuration(slowest)}` : 'in progress'}
        icon={<Timer className="size-4" />}
        delay={0.1}
      />
      <Tile
        label="Consensus lift"
        value={
          improvement === null
            ? '—'
            : `${improvement >= 0 ? '+' : ''}${improvement.toFixed(1)}`
        }
        hint={
          bestCandidate
            ? `best single model ${bestCandidate.toFixed(1)}`
            : 'awaiting evaluation'
        }
        icon={<Gauge className="size-4" />}
        delay={0.15}
      />
    </div>
  );
}
