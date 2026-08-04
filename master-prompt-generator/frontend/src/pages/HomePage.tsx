import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ArrowUpRight, History, Trophy } from 'lucide-react';
import type { RunStats, RunSummary } from '@/types';
import { api } from '@/services/api';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { RunStatusBadge } from '@/components/ui/Badge';
import { RunLauncher } from '@/components/dashboard/RunLauncher';
import { formatCurrency, formatDuration, formatRelativeTime } from '@/lib/utils';

export function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: runs = [] } = useQuery<RunSummary[]>({
    queryKey: ['runs'],
    queryFn: () => api.listRuns(20),
    refetchInterval: 15_000,
  });

  const { data: stats } = useQuery<RunStats>({
    queryKey: ['run-stats'],
    queryFn: api.stats,
    refetchInterval: 30_000,
  });

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-5">
        <RunLauncher
          onLaunched={(accepted) => {
            queryClient.invalidateQueries({ queryKey: ['runs'] });
            navigate(`/runs/${accepted.run_id}`);
          }}
        />

        {stats ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: 'Runs', value: String(stats.total_runs) },
              { label: 'Spend', value: formatCurrency(stats.total_cost_usd) },
              { label: 'Avg duration', value: formatDuration(stats.avg_duration_ms) },
              { label: 'Best score', value: stats.best_score.toFixed(1) },
            ].map((tile, index) => (
              <motion.div
                key={tile.label}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.04 }}
                className="glass rounded-[18px] px-4 py-3"
              >
                <p className="text-[11px] tracking-wide text-faint uppercase">
                  {tile.label}
                </p>
                <p className="mt-1.5 font-mono text-[20px] font-semibold tabular-nums text-white">
                  {tile.value}
                </p>
              </motion.div>
            ))}
          </div>
        ) : null}
      </div>

      <GlassCard className="flex h-fit flex-col">
        <SectionHeader
          title="Recent Runs"
          subtitle="Every orchestration, with its cost and outcome."
          icon={<History className="size-4" />}
        />
        <ul className="space-y-2">
          {runs.map((run) => (
            <li key={run.id}>
              <Link
                to={`/runs/${run.id}`}
                className="glass-inset group flex items-center gap-3 rounded-xl px-3 py-2.5 transition hover:bg-white/[0.06]"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium text-white/90">
                    {run.title}
                  </p>
                  <p className="mt-0.5 truncate text-[11px] text-faint">
                    {run.target_domain} · {formatRelativeTime(run.created_at)} ·{' '}
                    {formatCurrency(run.total_cost_usd)}
                  </p>
                </div>
                <RunStatusBadge status={run.status} />
                <ArrowUpRight className="size-4 shrink-0 text-white/30 transition group-hover:text-white/70" />
              </Link>
            </li>
          ))}
          {!runs.length ? (
            <li className="grid place-items-center py-10 text-center">
              <Trophy className="mb-2 size-6 text-white/20" />
              <p className="text-[12.5px] text-faint">
                No runs yet. Launch one to build your first Elite Prompt.
              </p>
            </li>
          ) : null}
        </ul>
      </GlassCard>
    </div>
  );
}
