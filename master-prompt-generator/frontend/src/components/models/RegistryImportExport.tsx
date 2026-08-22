import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Download, FileJson, Upload } from 'lucide-react';
import type { ModelWritePayload, RegistryImportResult } from '@/types';
import { api, ApiError } from '@/services/api';
import { SectionHeader } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/utils';

/** Everything parsed out of a chosen file, before anything is sent. */
interface Staged {
  filename: string;
  providers: ModelWritePayload[];
  /** Ids already in the registry, so the preview can say what changes. */
  raw: string;
}

/** Accept both shapes an operator will plausibly upload: the document this app
 *  exports, and a bare array of models someone wrote by hand. */
function parseRegistryFile(text: string): ModelWritePayload[] {
  const parsed: unknown = JSON.parse(text);

  const providers = Array.isArray(parsed)
    ? parsed
    : typeof parsed === 'object' && parsed !== null && 'providers' in parsed
      ? (parsed as { providers: unknown }).providers
      : null;

  if (!Array.isArray(providers) || providers.length === 0) {
    throw new Error(
      'Expected a JSON array of models, or an object with a non-empty "providers" array.',
    );
  }

  const missing = providers.findIndex(
    (entry) =>
      typeof entry !== 'object' ||
      entry === null ||
      !('id' in entry) ||
      !('model_key' in entry),
  );
  if (missing !== -1) {
    // Caught here rather than left to the 422, because pointing at the index
    // is far more useful than a nested pydantic path.
    throw new Error(
      `Entry ${missing + 1} is missing "id" or "model_key" — every model needs both.`,
    );
  }

  return providers as ModelWritePayload[];
}

export function RegistryImportExport({ registrySize }: { registrySize: number }) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [staged, setStaged] = useState<Staged | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [mode, setMode] = useState<'merge' | 'replace'>('merge');
  const [result, setResult] = useState<RegistryImportResult | null>(null);

  const apply = useMutation({
    mutationFn: (payload: { providers: ModelWritePayload[]; mode: 'merge' | 'replace' }) =>
      api.importModels(payload.providers, payload.mode),
    onSuccess: (imported) => {
      setResult(imported);
      setStaged(null);
      queryClient.invalidateQueries({ queryKey: ['models'] });
      queryClient.invalidateQueries({ queryKey: ['model-catalog'] });
    },
  });

  const onFile = async (file: File) => {
    setParseError(null);
    setResult(null);
    try {
      const raw = await file.text();
      setStaged({ filename: file.name, providers: parseRegistryFile(raw), raw });
    } catch (error) {
      setStaged(null);
      setParseError(
        error instanceof Error ? error.message : 'That file is not valid JSON.',
      );
    }
  };

  /** The export is fetched through the API client rather than linked directly,
   *  because the endpoint is authenticated — a plain href carries no bearer
   *  token and would download a 401 body as a file. */
  const download = useMutation({
    mutationFn: api.exportModels,
    onSuccess: (text) => {
      const url = URL.createObjectURL(
        new Blob([text], { type: 'application/json' }),
      );
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'models.json';
      anchor.click();
      URL.revokeObjectURL(url);
    },
  });

  return (
    <div>
      <SectionHeader
        title="Import / export JSON"
        subtitle="Drive the whole registry from a file. Export gives you a valid import payload to edit and send back."
        icon={<FileJson className="size-4" />}
        action={
          <Button
            size="sm"
            variant="ghost"
            loading={download.isPending}
            onClick={() => download.mutate()}
          >
            <Download className="size-3.5" />
            Export {registrySize} models
          </Button>
        }
      />

      <div
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          const file = event.dataTransfer.files[0];
          if (file) void onFile(file);
        }}
        className="rounded-xl border border-dashed border-white/15 bg-white/[0.02] px-4 py-6 text-center"
      >
        <Upload className="mx-auto mb-2 size-5 text-white/30" />
        <p className="text-[12.5px] text-dim">
          Drop a <code className="font-mono text-[11.5px]">models.json</code> here, or{' '}
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="text-aurora-300 underline decoration-aurora-300/40 transition hover:text-aurora-200"
          >
            choose a file
          </button>
          .
        </p>
        <p className="mt-1 text-[11px] text-faint">
          A bare array of models works too.
        </p>
        <input
          ref={fileInput}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void onFile(file);
            // Cleared so choosing the same file twice re-triggers onChange.
            event.target.value = '';
          }}
        />
      </div>

      {parseError ? (
        <p className="mt-3 rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
          {parseError}
        </p>
      ) : null}

      {staged ? (
        <div className="mt-3 rounded-xl bg-white/[0.03] px-4 py-3.5 ring-1 ring-inset ring-white/10">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[12.5px] text-white/90">
              <code className="font-mono text-[11.5px]">{staged.filename}</code>
              <span className="ml-2 text-faint">
                {staged.providers.length} model
                {staged.providers.length === 1 ? '' : 's'}
              </span>
            </span>
            <Button size="sm" variant="ghost" onClick={() => setStaged(null)}>
              Discard
            </Button>
          </div>

          <ul className="mt-2.5 max-h-40 space-y-1 overflow-y-auto">
            {staged.providers.map((provider, index) => (
              <li
                key={`${provider.id}-${index}`}
                className="flex flex-wrap items-baseline gap-x-2 text-[11.5px] text-faint"
              >
                <code className="font-mono text-white/70">{provider.id}</code>
                <span>{provider.model_key}</span>
              </li>
            ))}
          </ul>

          <fieldset className="mt-3.5">
            <legend className="mb-1.5 text-[12px] font-medium text-dim">
              Apply as
            </legend>
            <div className="flex flex-wrap gap-2">
              {(['merge', 'replace'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={mode === option}
                  onClick={() => setMode(option)}
                  className={cn(
                    'rounded-xl px-3 py-2 text-left text-[12px] ring-1 ring-inset transition',
                    mode === option
                      ? 'bg-aurora-500/20 text-white ring-aurora-400/45'
                      : 'bg-white/[0.04] text-dim ring-white/10 hover:bg-white/8',
                  )}
                >
                  <span className="block font-medium capitalize">{option}</span>
                  <span className="mt-0.5 block text-[11px] text-faint">
                    {option === 'merge'
                      ? 'Add and update; keep everything else'
                      : `Delete the other ${Math.max(registrySize - staged.providers.length, 0)} and use only this file`}
                  </span>
                </button>
              ))}
            </div>
          </fieldset>

          {mode === 'replace' ? (
            <p className="mt-2.5 flex items-start gap-1.5 text-[11.5px] text-amber-400">
              <AlertTriangle className="mt-px size-3.5 shrink-0" />
              Replace removes every model this file does not list, including any
              still selected on the dashboard.
            </p>
          ) : null}

          {apply.isError ? (
            <p className="mt-2.5 text-[11.5px] text-rose-400">
              {apply.error instanceof ApiError
                ? apply.error.message
                : 'The import was rejected.'}
            </p>
          ) : null}

          <Button
            className="mt-3"
            variant="primary"
            size="sm"
            loading={apply.isPending}
            onClick={() => apply.mutate({ providers: staged.providers, mode })}
          >
            Apply {mode}
          </Button>
        </div>
      ) : null}

      {result ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl bg-white/[0.03] px-4 py-3 ring-1 ring-inset ring-white/10">
          <Badge tone="success">{result.added.length} added</Badge>
          <Badge tone="info">{result.updated.length} updated</Badge>
          {result.removed.length ? (
            <Badge tone="warning">{result.removed.length} removed</Badge>
          ) : null}
          <span className="text-[11.5px] text-faint">
            {result.total} models in the registry
          </span>
        </div>
      ) : null}
    </div>
  );
}
