import { useState } from 'react';
import Editor, { type Monaco } from '@monaco-editor/react';
import { motion } from 'framer-motion';
import { Check, Copy, Download, Merge, Scale, ShieldPlus, Sparkles } from 'lucide-react';
import type { ConsensusPrompt } from '@/types';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { ScoreRing } from '@/components/charts/ScoreRing';
import { api } from '@/services/api';
import { formatNumber } from '@/lib/utils';

const EXPORT_FORMATS = ['markdown', 'json', 'yaml', 'xml', 'python', 'typescript'] as const;

const STRATEGY_TONE: Record<string, 'neutral' | 'info' | 'success' | 'accent' | 'warning'> = {
  adopted: 'neutral',
  deduplicated: 'neutral',
  merged: 'accent',
  unanimous: 'success',
  reinforced: 'info',
  synthesized: 'warning',
};

function defineTheme(monaco: Monaco): void {
  monaco.editor.defineTheme('mpg-glass-single', {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#00000000',
      'editor.lineHighlightBackground': '#ffffff08',
      'editorLineNumber.foreground': '#ffffff2e',
      'editorGutter.background': '#00000000',
      'scrollbarSlider.background': '#ffffff18',
    },
  });
}

export function ConsensusPanel({
  consensus,
  runId,
}: {
  consensus: ConsensusPrompt | null;
  runId: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);

  if (!consensus) {
    return (
      <GlassCard elevated>
        <SectionHeader
          title="Elite Consensus Prompt"
          subtitle="Synthesized from the strongest sections of every model."
          icon={<Sparkles className="size-4" />}
        />
        <div className="grid h-56 place-items-center">
          <div className="text-center">
            <div className="animate-shimmer mx-auto h-2 w-40 rounded-full" />
            <p className="mt-4 text-[13px] text-faint">
              Waiting for evaluation to finish before synthesis begins.
            </p>
          </div>
        </div>
      </GlassCard>
    );
  }

  const copy = async () => {
    await navigator.clipboard.writeText(consensus.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  const download = async (format: string) => {
    if (!runId) return;
    setExporting(format);
    try {
      const artifact = await api.exportRun(runId, format);
      const blob = new Blob([artifact.content], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = artifact.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(null);
    }
  };

  const report = consensus.optimization_report;

  return (
    <GlassCard elevated className="p-0">
      <div className="flex flex-col gap-5 p-5 lg:flex-row lg:items-center">
        <ScoreRing
          score={consensus.overall_score ?? 0}
          label="Consensus"
          className="shrink-0"
        />
        <div className="min-w-0 flex-1">
          <SectionHeader
            title="Elite Consensus Prompt"
            subtitle="Assembled from the highest-scoring section of each model, with conflicts resolved by weighted authority."
            icon={<Sparkles className="size-4" />}
          />
          <div className="flex flex-wrap gap-2">
            <Badge tone="info">
              <Merge className="size-3" />
              {consensus.section_provenance.length} sections merged
            </Badge>
            <Badge tone="warning">
              <Scale className="size-3" />
              {consensus.conflicts.length} conflicts resolved
            </Badge>
            {consensus.reinforcements.length ? (
              <Badge tone="accent">
                <ShieldPlus className="size-3" />
                {consensus.reinforcements.length} gaps closed
              </Badge>
            ) : null}
            <Badge tone="neutral">{formatNumber(consensus.token_count)} tokens</Badge>
            {consensus.tokens_saved > 0 ? (
              <Badge tone="success">
                −{formatNumber(consensus.tokens_saved)} tokens optimized
              </Badge>
            ) : null}
            {consensus.improvement_over_best !== null ? (
              <Badge
                tone={consensus.improvement_over_best >= 0 ? 'success' : 'danger'}
              >
                {consensus.improvement_over_best >= 0 ? '+' : ''}
                {consensus.improvement_over_best.toFixed(1)} vs best model
              </Badge>
            ) : null}
          </div>
        </div>
      </div>

      <div className="hairline mx-5" />

      <div className="flex flex-wrap items-center gap-2 px-5 py-3">
        <Button size="sm" variant="primary" onClick={copy}>
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? 'Copied' : 'Copy prompt'}
        </Button>
        {EXPORT_FORMATS.map((format) => (
          <Button
            key={format}
            size="sm"
            variant="ghost"
            loading={exporting === format}
            onClick={() => download(format)}
          >
            <Download className="size-3.5" />
            {format}
          </Button>
        ))}
      </div>

      <div className="glass-inset mx-5 mb-5 h-[420px] overflow-hidden rounded-2xl">
        <Editor
          height="100%"
          language="markdown"
          value={consensus.content}
          beforeMount={defineTheme}
          theme="mpg-glass-single"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 13,
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
            wordWrap: 'on',
            scrollBeyondLastLine: false,
            padding: { top: 16, bottom: 16 },
            renderLineHighlight: 'none',
            lineNumbers: 'off',
          }}
        />
      </div>

      {consensus.section_provenance.length ? (
        <div className="px-5 pb-5">
          <h3 className="mb-2 text-[12px] font-medium text-dim">Section provenance</h3>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-[12px]">
              <thead>
                <tr className="text-[11px] text-faint">
                  <th scope="col" className="pb-2 font-medium">Section</th>
                  <th scope="col" className="pb-2 font-medium">Winning model</th>
                  <th scope="col" className="pb-2 font-medium">Score</th>
                  <th scope="col" className="pb-2 font-medium">Strategy</th>
                  <th scope="col" className="pb-2 font-medium">Directives</th>
                  <th scope="col" className="pb-2 font-medium">Merged from</th>
                </tr>
              </thead>
              <tbody>
                {consensus.section_provenance.map((item, index) => (
                  <motion.tr
                    key={item.section}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: index * 0.03 }}
                    className="border-t border-white/6"
                  >
                    <td className="py-2 pr-3 text-white/90">{item.section}</td>
                    <td className="py-2 pr-3 text-dim">{item.source_model_name}</td>
                    <td className="py-2 pr-3 font-mono tabular-nums text-dim">
                      {item.score.toFixed(1)}
                    </td>
                    <td className="py-2 pr-3">
                      <Badge tone={STRATEGY_TONE[item.strategy] ?? 'neutral'}>
                        {item.strategy}
                      </Badge>
                    </td>
                    <td className="py-2 pr-3 text-faint">
                      {item.directive_count}
                      {item.unanimous_count > 0 ? (
                        <span className="ml-1.5 text-mint-400">
                          ({item.unanimous_count} agreed)
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 text-faint">
                      {item.merged_from.length ? item.merged_from.join(', ') : '—'}
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {consensus.conflicts.length ? (
        <div className="px-5 pb-5">
          <h3 className="mb-2 text-[12px] font-medium text-dim">Resolved conflicts</h3>
          <ul className="space-y-2">
            {consensus.conflicts.slice(0, 8).map((conflict, index) => (
              <li
                key={`${conflict.section}-${index}`}
                className="glass-inset rounded-xl p-3 text-[12px]"
              >
                <div className="mb-1 flex items-center gap-2">
                  <Badge
                    tone={
                      conflict.kind === 'semantic'
                        ? 'danger'
                        : conflict.kind === 'syntactic'
                          ? 'warning'
                          : 'neutral'
                    }
                  >
                    {conflict.kind}
                  </Badge>
                  <span className="font-medium text-white/90">{conflict.section}</span>
                </div>
                <p className="text-dim">{conflict.description}</p>
                <p className="mt-1 text-[11.5px] text-faint">→ {conflict.resolution}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {consensus.reinforcements.length ? (
        <div className="px-5 pb-5">
          <h3 className="mb-1 text-[12px] font-medium text-dim">
            Engine reinforcements
          </h3>
          <p className="mb-2 text-[11.5px] text-faint">
            Production directives no candidate model supplied — merging alone could
            not have produced these.
          </p>
          <ul className="space-y-2">
            {consensus.reinforcements.map((item) => (
              <li key={item.id} className="glass-inset rounded-xl p-3 text-[12px]">
                <div className="mb-1 flex items-center gap-2">
                  <Badge tone="accent">
                    <ShieldPlus className="size-3" />
                    {item.id.replace(/_/g, ' ')}
                  </Badge>
                  <span className="font-medium text-white/90">{item.section}</span>
                </div>
                <p className="text-dim">{item.rationale}</p>
                <p className="mt-1 font-mono text-[11px] leading-relaxed text-faint">
                  {item.directive}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {report ? (
        <div className="px-5 pb-5 text-[11.5px] text-faint">
          Optimizer: {formatNumber(report.original_tokens)} →{' '}
          {formatNumber(report.optimized_tokens)} tokens (ratio{' '}
          {report.compression_ratio.toFixed(2)}), {report.removed_duplicate_lines}{' '}
          duplicate lines removed, {report.normalized_headings} headings normalised.
        </div>
      ) : null}
    </GlassCard>
  );
}
