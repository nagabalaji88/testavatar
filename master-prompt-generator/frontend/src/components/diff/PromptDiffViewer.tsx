import { DiffEditor } from '@monaco-editor/react';
import { useMemo, useState } from 'react';
import { GitCompare } from 'lucide-react';
import type { Candidate, ConsensusPrompt } from '@/types';
import { GlassCard, SectionHeader } from '@/components/ui/GlassCard';
import { Button } from '@/components/ui/Button';
import { useTheme } from '@/lib/theme';
import { DIFF_THEME, defineEditorThemes, editorTheme } from '@/lib/monacoTheme';

interface PromptDiffViewerProps {
  candidates: Candidate[];
  consensus: ConsensusPrompt | null;
}

/** Side-by-side comparison of any candidate against the Elite Consensus Prompt. */
export function PromptDiffViewer({ candidates, consensus }: PromptDiffViewerProps) {
  const available = useMemo(
    () => candidates.filter((candidate) => Boolean(candidate.content)),
    [candidates],
  );
  const [leftId, setLeftId] = useState<string>(available[0]?.model_id ?? '');
  const [inline, setInline] = useState(false);
  const { resolved } = useTheme();

  const left = available.find((candidate) => candidate.model_id === leftId) ?? available[0];

  if (!available.length || !consensus) {
    return (
      <GlassCard>
        <SectionHeader
          title="Prompt Diff"
          subtitle="Compare any model's draft against the synthesized Elite Prompt."
          icon={<GitCompare className="size-4" />}
        />
        <p className="py-10 text-center text-[13px] text-faint">
          The diff unlocks once generation and synthesis have completed.
        </p>
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-0">
      <div className="p-5 pb-3">
        <SectionHeader
          title="Prompt Diff"
          subtitle="Green marks text the consensus engine kept or introduced; red marks what it dropped."
          icon={<GitCompare className="size-4" />}
          action={
            <div className="flex items-center gap-2">
              <select
                value={left?.model_id ?? ''}
                onChange={(event) => setLeftId(event.target.value)}
                aria-label="Candidate to compare"
                className="h-8 rounded-lg border border-line-2 bg-surface-2 px-2 text-[12px] text-ink-strong outline-none focus-visible:border-aurora-400"
              >
                {available.map((candidate) => (
                  <option
                    key={candidate.model_id}
                    value={candidate.model_id}
                    className="bg-void-900 text-ink-strong"
                  >
                    {candidate.model_name}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="ghost" onClick={() => setInline((value) => !value)}>
                {inline ? 'Side by side' : 'Inline'}
              </Button>
            </div>
          }
        />
      </div>

      <div className="glass-inset h-[520px] overflow-hidden rounded-b-[22px]">
        <DiffEditor
          height="100%"
          language="markdown"
          original={left?.content ?? ''}
          modified={consensus.content}
          beforeMount={defineEditorThemes}
          theme={editorTheme(DIFF_THEME, resolved)}
          options={{
            renderSideBySide: !inline,
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 12.5,
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
            lineNumbers: 'on',
            wordWrap: 'on',
            scrollBeyondLastLine: false,
            padding: { top: 14, bottom: 14 },
            renderOverviewRuler: false,
            smoothScrolling: true,
          }}
        />
      </div>
    </GlassCard>
  );
}
