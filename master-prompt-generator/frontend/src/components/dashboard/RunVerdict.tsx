import { useMemo } from 'react';
import { Coins, Timer, TrendingUp, Trophy } from 'lucide-react';
import type { Candidate, ConsensusPrompt, RunDetail } from '@/types';
import { cn, formatCurrency, formatDuration } from '@/lib/utils';

/**
 * The four numbers a finished run is actually judged on, plus a sentence
 * saying what they mean together.
 *
 * `improvement_over_best` is the only figure that answers "was running four
 * models worth more than running the best one", which is the question the
 * whole product exists to settle -- and it used to sit in a tile among seven
 * others with no interpretation attached. A negative lift is reported as
 * plainly as a positive one: a merge that beat nothing is a real outcome, and
 * hiding it would make every other number untrustworthy.
 */
export function RunVerdict({
  run,
  candidates,
  consensus,
}: {
  run: RunDetail | null;
  candidates: Candidate[];
  consensus: ConsensusPrompt | null;
}) {
  const scored = candidates.filter((c) => c.overall_score !== null);
  const best = scored.length
    ? Math.max(...scored.map((c) => c.overall_score as number))
    : null;
  const lift = consensus?.improvement_over_best ?? null;
  const failed = candidates.filter((c) => c.status === 'failed');

  const verdict = useMemo(() => {
    if (!consensus) return null;
    const cost = formatCurrency(run?.total_cost_usd ?? 0);
    if (lift === null || best === null) {
      return `Merged from ${scored.length} model${scored.length === 1 ? '' : 's'} for ${cost}.`;
    }
    if (lift > 0.5) {
      return `The merge scored ${lift.toFixed(1)} points above the best single model, for ${cost}.`;
    }
    if (lift > -0.5) {
      return `The merge matched the best single model (${best.toFixed(1)}). The extra models cost ${cost} and changed nothing.`;
    }
    return `The best single model scored ${Math.abs(lift).toFixed(1)} points higher than the merge. Worth checking before trusting this result.`;
  }, [consensus, lift, best, scored.length, run?.total_cost_usd]);

  const liftTone =
    lift === null ? 'neutral' : lift > 0.5 ? 'good' : lift > -0.5 ? 'flat' : 'bad';

  return (
    <section className="glass rounded-[22px] p-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Figure
          icon={<Trophy className="size-4" />}
          label="Consensus score"
          value={consensus?.overall_score != null ? consensus.overall_score.toFixed(1) : '—'}
          note={best !== null ? `best model ${best.toFixed(1)}` : 'not scored yet'}
        />
        <Figure
          icon={<TrendingUp className="size-4" />}
          label="Lift over best model"
          value={lift === null ? '—' : `${lift > 0 ? '+' : ''}${lift.toFixed(1)}`}
          note={
            liftTone === 'good'
              ? 'the merge earned its cost'
              : liftTone === 'flat'
                ? 'no better than one model'
                : liftTone === 'bad'
                  ? 'a single model did better'
                  : 'pending'
          }
          tone={liftTone}
        />
        <Figure
          icon={<Coins className="size-4" />}
          label="Spend"
          value={formatCurrency(run?.total_cost_usd ?? 0)}
          note={`${scored.length} of ${candidates.length} models answered`}
          tone={failed.length ? 'bad' : 'neutral'}
        />
        <Figure
          icon={<Timer className="size-4" />}
          label="Wall clock"
          value={run?.duration_ms ? formatDuration(run.duration_ms) : '—'}
          note={
            candidates.length
              ? `slowest ${formatDuration(Math.max(...candidates.map((c) => c.latency_ms)))}`
              : 'waiting'
          }
        />
      </div>

      {verdict ? (
        <p
          className={cn(
            'mt-4 border-t border-line-1 pt-3.5 text-[14px] leading-relaxed',
            liftTone === 'bad' ? 'text-amber-400' : 'text-ink-2',
          )}
        >
          {verdict}
        </p>
      ) : null}
    </section>
  );
}

function Figure({
  icon,
  label,
  value,
  note,
  tone = 'neutral',
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  note: string;
  tone?: 'neutral' | 'good' | 'flat' | 'bad';
}) {
  return (
    <div>
      <div className="flex items-center gap-2 text-ink-3">
        {icon}
        <span className="text-[12.5px] font-medium">{label}</span>
      </div>
      <p
        className={cn(
          'mt-1.5 font-mono text-[26px] font-semibold leading-none tabular-nums',
          tone === 'good' ? 'text-mint-400' : tone === 'bad' ? 'text-amber-400' : 'text-ink-strong',
        )}
      >
        {value}
      </p>
      <p className="mt-1.5 text-[12px] text-ink-3">{note}</p>
    </div>
  );
}
