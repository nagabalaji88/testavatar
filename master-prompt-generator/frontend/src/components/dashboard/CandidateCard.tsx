import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { Candidate } from '@/types';
import { GlassCard } from '@/components/ui/GlassCard';
import { CandidateStatusBadge, RiskBadge, ScoreBadge } from '@/components/ui/Badge';
import { cn, formatCurrency, formatDuration, formatNumber } from '@/lib/utils';
import { seriesColor } from '@/lib/viz';

interface CandidateCardProps {
  candidate: Candidate;
  rank: number;
  isLeader: boolean;
}

export function CandidateCard({ candidate, rank, isLeader }: CandidateCardProps) {
  const [expanded, setExpanded] = useState(false);
  const evaluation = candidate.evaluation;
  const accent = seriesColor(rank);

  return (
    <GlassCard
      elevated={isLeader}
      className={cn('p-0', isLeader && 'ring-1 ring-line-3')}
    >
      <div className="flex items-start gap-3 p-4">
        <span
          className="mt-1 h-9 w-1 shrink-0 rounded-full"
          style={{ backgroundColor: accent }}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-[14px] font-semibold text-ink-strong">
              {candidate.model_name}
            </h3>
            {isLeader ? (
              <span className="rounded-full bg-surface-4 px-2 py-0.5 text-[10px] font-medium tracking-wide text-ink-strong uppercase">
                Leader
              </span>
            ) : null}
            <CandidateStatusBadge status={candidate.status} />
          </div>
          <p className="mt-0.5 text-[11.5px] text-faint">{candidate.provider || '—'}</p>

          <dl className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
            <div>
              <dt className="text-faint">Latency</dt>
              <dd className="font-mono tabular-nums text-ink-1">
                {formatDuration(candidate.latency_ms)}
              </dd>
            </div>
            <div>
              <dt className="text-faint">Cost</dt>
              <dd className="font-mono tabular-nums text-ink-1">
                {formatCurrency(candidate.cost_usd)}
              </dd>
            </div>
            <div>
              <dt className="text-faint">Tokens</dt>
              <dd className="font-mono tabular-nums text-ink-1">
                {formatNumber(candidate.input_tokens + candidate.output_tokens)}
              </dd>
            </div>
          </dl>
        </div>

        <div className="flex flex-col items-end gap-2">
          {candidate.overall_score !== null ? (
            <ScoreBadge score={candidate.overall_score} />
          ) : (
            <span className="text-[11px] text-faint">unscored</span>
          )}
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            className="grid size-7 place-items-center rounded-lg text-ink-3 transition hover:bg-surface-3 hover:text-ink-strong"
          >
            <ChevronDown
              className={cn('size-4 transition-transform', expanded && 'rotate-180')}
            />
          </button>
        </div>
      </div>

      {candidate.error ? (
        <p className="mx-4 mb-4 rounded-lg bg-rose-400/10 px-3 py-2 text-[11.5px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
          {candidate.error}
        </p>
      ) : null}

      <AnimatePresence initial={false}>
        {expanded && evaluation ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="hairline mx-4" />
            <div className="space-y-3 p-4 text-[12px]">
              {evaluation.rationale ? (
                <p className="text-dim">{evaluation.rationale}</p>
              ) : null}

              <div className="flex flex-wrap gap-2">
                <RiskBadge
                  label="Injection"
                  level={evaluation.security_assessment.injection_risk}
                />
                <RiskBadge
                  label="PII"
                  level={evaluation.security_assessment.pii_leakage_risk}
                />
              </div>

              {evaluation.strengths.length ? (
                <div>
                  <h4 className="mb-1 flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-mint-400 uppercase">
                    <ShieldCheck className="size-3.5" /> Strengths
                  </h4>
                  <ul className="space-y-1 text-dim">
                    {evaluation.strengths.slice(0, 5).map((item) => (
                      <li key={item} className="flex gap-2">
                        <span className="text-mint-400">·</span>
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {evaluation.missing_elements.length ? (
                <div>
                  <h4 className="mb-1 flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-amber-400 uppercase">
                    <TriangleAlert className="size-3.5" /> Missing
                  </h4>
                  <ul className="space-y-1 text-dim">
                    {evaluation.missing_elements.slice(0, 5).map((item) => (
                      <li key={item} className="flex gap-2">
                        <span className="text-amber-400">·</span>
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </GlassCard>
  );
}
