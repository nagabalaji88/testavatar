import { Check, Loader2, X } from 'lucide-react';
import type { StageState } from '@/store/useRunStore';
import type { Candidate, RunStatus } from '@/types';
import { cn, formatDuration } from '@/lib/utils';

/**
 * The five stages as a stepper, which is only interesting while a run is
 * moving. Once it finishes it collapses to a single line: a finished run's
 * progress is a fact about the past, and it was previously occupying a
 * 300px-tall graph at the top of every completed page.
 */
export function RunProgress({
  stages,
  status,
  candidates,
  runDurationMs,
}: {
  stages: StageState[];
  status: RunStatus;
  candidates: Candidate[];
  runDurationMs?: number | null;
}) {
  const settled = status === 'completed' || status === 'failed';
  const done = stages.filter((s) => s.status === 'done').length;
  // Per-stage durations only exist when the run was watched live; fall back to
  // the run's own wall clock rather than reporting a total of zero.
  const timed = stages.filter((s) => s.durationMs);
  const total = timed.reduce((ms, s) => ms + (s.durationMs as number), 0);
  const elapsed = total || runDurationMs || 0;

  if (settled) {
    return (
      <div className="glass flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-2xl px-4 py-2.5 text-[12.5px]">
        <span
          className={cn(
            'grid size-4 place-items-center rounded-full',
            status === 'completed' ? 'bg-mint-400/20 text-mint-400' : 'bg-rose-400/20 text-rose-400',
          )}
        >
          {status === 'completed' ? <Check className="size-3" /> : <X className="size-3" />}
        </span>
        <span className="text-ink-2">
          {status === 'completed' ? 'All 5 stages' : `${done} of ${stages.length} stages`}
          {elapsed ? ` in ${formatDuration(elapsed)}` : ''}
        </span>
        {timed.length ? (
          <span className="text-ink-3">
            {timed
              .map((s) => `${s.label} ${formatDuration(s.durationMs as number)}`)
              .join(' · ')}
          </span>
        ) : null}
      </div>
    );
  }

  const live = candidates.filter((c) => c.status === 'running');

  return (
    <div className="glass rounded-2xl px-5 py-4">
      <ol className="flex flex-wrap items-center gap-y-3">
        {stages.map((stage, index) => (
          <li key={stage.key} className="flex flex-1 items-center gap-2.5">
            <span
              className={cn(
                'grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold tabular-nums',
                stage.status === 'done'
                  ? 'bg-aurora-500 text-white'
                  : stage.status === 'active'
                    ? 'bg-surface-1 text-aurora-300 ring-2 ring-aurora-400'
                    : stage.status === 'failed'
                      ? 'bg-rose-400/20 text-rose-400 ring-1 ring-rose-400/40'
                      : 'bg-surface-3 text-ink-3',
              )}
            >
              {stage.status === 'done' ? <Check className="size-3.5" /> : index + 1}
            </span>
            <span
              className={cn(
                'whitespace-nowrap text-[13px]',
                stage.status === 'pending' ? 'text-ink-3' : 'font-medium text-ink-strong',
              )}
            >
              {stage.label}
            </span>
            {index < stages.length - 1 ? (
              <span
                className={cn(
                  'mx-1 h-px min-w-4 flex-1',
                  stage.status === 'done' ? 'bg-aurora-500' : 'bg-line-2',
                )}
              />
            ) : null}
          </li>
        ))}
      </ol>

      {live.length ? (
        <p className="mt-3.5 flex items-center gap-2 border-t border-line-1 pt-3 text-[12.5px] text-ink-2">
          <Loader2 className="size-3.5 animate-spin text-aurora-300" />
          Waiting on {live.map((c) => c.model_name).join(', ')}
        </p>
      ) : null}
    </div>
  );
}
