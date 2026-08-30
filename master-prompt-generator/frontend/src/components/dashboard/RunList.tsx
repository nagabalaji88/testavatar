import { Link } from 'react-router-dom';
import { ArrowUpRight, Trophy } from 'lucide-react';
import type { RunSummary } from '@/types';
import { RunStatusBadge } from '@/components/ui/Badge';
import { cn, formatCurrency, formatRelativeTime } from '@/lib/utils';

/**
 * Past runs, each showing what it produced rather than only what it cost.
 *
 * The list used to carry domain, age and spend -- three facts that cannot
 * answer the question someone scanning their history actually has, which is
 * "which of these was any good". Score and lift now come down with the list
 * itself (one outer join, see list_runs), so the row can say whether running
 * several models beat running one, which is the only reason to look at an old
 * run at all.
 */
export function RunList({ runs }: { runs: RunSummary[] }) {
  if (!runs.length) {
    return (
      <div className="grid place-items-center py-12 text-center">
        <Trophy className="mb-2 size-6 text-ink-4" />
        <p className="text-[12.5px] text-faint">
          No runs yet. Launch one to build your first Elite Prompt.
        </p>
      </div>
    );
  }

  return (
    <ul className="space-y-1.5">
      {runs.map((run) => {
        const lift = run.improvement_over_best;
        return (
          <li key={run.id}>
            <Link
              to={`/runs/${run.id}`}
              className="group flex items-center gap-3 rounded-xl px-3 py-2.5 transition hover:bg-surface-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13.5px] font-medium text-ink-1">{run.title}</p>
                <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px] text-faint">
                  <span>{formatRelativeTime(run.created_at)}</span>
                  <span aria-hidden>·</span>
                  <span>
                    {run.model_count} model{run.model_count === 1 ? '' : 's'}
                  </span>
                  <span aria-hidden>·</span>
                  <span className="font-mono tabular-nums">
                    {formatCurrency(run.total_cost_usd)}
                  </span>
                </p>
              </div>

              {run.consensus_score != null ? (
                <div className="shrink-0 text-right">
                  <p className="font-mono text-[15px] font-semibold leading-none tabular-nums text-ink-strong">
                    {run.consensus_score.toFixed(1)}
                  </p>
                  {lift != null ? (
                    <p
                      className={cn(
                        'mt-1 font-mono text-[11px] tabular-nums',
                        lift > 0.5 ? 'text-mint-400' : lift > -0.5 ? 'text-ink-3' : 'text-amber-400',
                      )}
                      title={
                        lift > 0.5
                          ? 'the merge beat the best single model'
                          : lift > -0.5
                            ? 'the merge matched the best single model'
                            : 'a single model scored higher than the merge'
                      }
                    >
                      {lift > 0 ? '+' : ''}
                      {lift.toFixed(1)}
                    </p>
                  ) : null}
                </div>
              ) : (
                <RunStatusBadge status={run.status} />
              )}

              <ArrowUpRight className="size-4 shrink-0 text-ink-4 transition group-hover:text-ink-2" />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
