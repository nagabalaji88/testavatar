import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { History } from 'lucide-react';
import type { RunStats, RunSummary } from '@/types';
import { api } from '@/services/api';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { RunLauncher } from '@/components/dashboard/RunLauncher';
import { RunList } from '@/components/dashboard/RunList';
import { formatCurrency, formatDuration } from '@/lib/utils';

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
                <p className="mt-1.5 font-mono text-[20px] font-semibold tabular-nums text-ink-strong">
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
          subtitle="Score and lift over the best single model."
          icon={<History className="size-4" />}
        />
        <RunList runs={runs} />
      </GlassCard>
    </div>
  );
}
