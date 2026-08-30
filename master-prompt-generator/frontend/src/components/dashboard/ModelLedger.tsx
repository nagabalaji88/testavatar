import { useMemo } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import type { Candidate, ConsensusPrompt } from '@/types';
import { Badge } from '@/components/ui/Badge';
import { cn, formatCurrency, formatDuration, formatNumber } from '@/lib/utils';

/**
 * One row per model, ordered by score, answering a question the old candidate
 * cards could not: which of these was worth paying for.
 *
 * "Sections won" is the load-bearing column. A model can score well and still
 * contribute nothing, because the merge takes the best version of each section
 * -- so a model whose every section lost to another is pure cost. That is
 * computable from section_provenance, which the API already returns and the
 * previous layout only used to draw a legend.
 *
 * Failures keep their row and show the provider's own words rather than being
 * dropped or collapsed into "0", since "no key configured" and "rate limited"
 * need opposite fixes.
 */
export function ModelLedger({
  candidates,
  consensus,
}: {
  candidates: Candidate[];
  consensus: ConsensusPrompt | null;
}) {
  const wins = useMemo(() => {
    const tally = new Map<string, number>();
    for (const section of consensus?.section_provenance ?? []) {
      tally.set(section.source_model_id, (tally.get(section.source_model_id) ?? 0) + 1);
    }
    return tally;
  }, [consensus]);

  const totalSections = consensus?.section_provenance.length ?? 0;
  const ranked = useMemo(
    () => [...candidates].sort((a, b) => (b.overall_score ?? -1) - (a.overall_score ?? -1)),
    [candidates],
  );

  if (!ranked.length) {
    return (
      <div className="glass rounded-[22px] px-5 py-10 text-center text-[13px] text-faint">
        Models appear here the moment the fan-out begins.
      </div>
    );
  }

  return (
    <div className="glass overflow-hidden rounded-[22px]">
      <div className="flex items-baseline gap-3 px-5 pb-1 pt-5">
        <h2 className="text-[15px] font-semibold tracking-tight text-ink-strong">Models</h2>
        <p className="text-[12.5px] text-ink-3">
          {totalSections
            ? `which of them earned a place in the ${totalSections} merged sections`
            : 'scored once every candidate is in'}
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] border-collapse">
          <thead>
            <tr>
              {['Model', 'Score', 'Sections won', 'Cost', 'Latency', 'Tokens'].map((head, i) => (
                <th
                  key={head}
                  className={cn(
                    'px-5 py-2.5 text-[12px] font-medium text-ink-3',
                    i === 0 ? 'text-left' : 'text-right',
                  )}
                >
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ranked.map((candidate) => {
              const won = wins.get(candidate.model_id) ?? 0;
              const share = totalSections ? won / totalSections : 0;
              const dead = candidate.status === 'failed';
              return (
                <tr key={candidate.model_id} className="border-t border-line-1">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      <span className="text-[13.5px] font-medium text-ink-strong">
                        {candidate.model_name}
                      </span>
                      <span className="text-[12px] text-ink-3">{candidate.provider}</span>
                      {candidate.status === 'running' ? (
                        <Loader2 className="size-3.5 animate-spin text-aurora-300" />
                      ) : null}
                      {totalSections > 0 && won === 0 && !dead ? (
                        <Badge tone="warning">nothing merged</Badge>
                      ) : null}
                    </div>
                    {dead && candidate.error ? (
                      <p className="mt-1 flex items-start gap-1.5 text-[12px] leading-snug text-rose-400">
                        <AlertTriangle className="mt-px size-3.5 shrink-0" />
                        <span className="line-clamp-2">{candidate.error}</span>
                      </p>
                    ) : null}
                  </td>

                  <td className="px-5 py-3 text-right font-mono text-[13.5px] tabular-nums text-ink-strong">
                    {candidate.overall_score != null ? candidate.overall_score.toFixed(1) : '—'}
                  </td>

                  <td className="px-5 py-3">
                    <div className="flex items-center justify-end gap-2.5">
                      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-3">
                        <div
                          className="h-full rounded-full bg-aurora-500"
                          style={{ width: `${Math.round(share * 100)}%` }}
                        />
                      </div>
                      <span className="w-10 text-right font-mono text-[13px] tabular-nums text-ink-2">
                        {totalSections ? `${won}/${totalSections}` : '—'}
                      </span>
                    </div>
                  </td>

                  <td className="px-5 py-3 text-right font-mono text-[13px] tabular-nums text-ink-2">
                    {formatCurrency(candidate.cost_usd)}
                  </td>
                  <td className="px-5 py-3 text-right font-mono text-[13px] tabular-nums text-ink-2">
                    {candidate.latency_ms ? formatDuration(candidate.latency_ms) : '—'}
                  </td>
                  <td className="px-5 py-3 text-right font-mono text-[13px] tabular-nums text-ink-3">
                    {formatNumber(candidate.input_tokens + candidate.output_tokens)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
