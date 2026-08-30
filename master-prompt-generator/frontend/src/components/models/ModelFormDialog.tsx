import { useEffect, useState, type FormEvent } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import type { ModelWritePayload, ProviderConfig } from '@/types';
import { api, ApiError } from '@/services/api';
import { Button } from '@/components/ui/Button';
import { Field, inputClass } from '@/components/ui/Field';
import { cn } from '@/lib/utils';

/** Families the backend can resolve a key for, plus the local runtimes that
 *  need none. Offered as a list because `provider` is matched against these
 *  names to find the credential — free text that misses them produces an entry
 *  that can never authenticate. */
const PROVIDER_OPTIONS = [
  'OpenAI',
  'Anthropic',
  'Google',
  'Groq',
  'OpenRouter',
  'Together',
  'HuggingFace',
  'Ollama',
  'vLLM',
] as const;

const BLANK: ModelWritePayload = {
  id: '',
  name: '',
  provider: 'OpenAI',
  model_key: '',
  max_tokens: 4096,
  cost_per_1k_input: 0,
  cost_per_1k_output: 0,
  enabled: true,
  temperature: 0.4,
  supports_json_mode: true,
  api_base: null,
  api_key_env: null,
  weight: 1,
};

function toDraft(model: ProviderConfig): ModelWritePayload {
  const {
    credential_available: _a,
    credential_env_var: _b,
    is_local_runtime: _c,
    credential_family: _d,
    credential_source: _e,
    ...writable
  } = model;
  return writable;
}

export function ModelFormDialog({
  open,
  onOpenChange,
  editing,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Null adds a new entry; a model edits that one in place. */
  editing: ProviderConfig | null;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ModelWritePayload>(BLANK);

  // Reset whenever the dialog opens, so a cancelled edit does not leak its
  // values into the next add.
  useEffect(() => {
    if (open) setDraft(editing ? toDraft(editing) : BLANK);
  }, [open, editing]);

  const save = useMutation({
    mutationFn: (payload: ModelWritePayload) => api.upsertModel(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['models'] });
      queryClient.invalidateQueries({ queryKey: ['model-catalog'] });
      onOpenChange(false);
    },
  });

  const set = <K extends keyof ModelWritePayload>(
    key: K,
    value: ModelWritePayload[K],
  ) => setDraft((current) => ({ ...current, [key]: value }));

  const valid =
    draft.id.trim().length >= 2 &&
    draft.name.trim().length >= 2 &&
    draft.model_key.trim().length >= 2 &&
    draft.max_tokens > 0;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    save.mutate({
      ...draft,
      id: draft.id.trim(),
      name: draft.name.trim(),
      model_key: draft.model_key.trim(),
      // Empty strings would fail the server's URL validation; the field means
      // "unset" when blank.
      api_base: draft.api_base?.trim() ? draft.api_base.trim() : null,
      api_key_env: draft.api_key_env?.trim() ? draft.api_key_env.trim() : null,
    });
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim backdrop-blur-sm" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(94vw,640px)] -translate-x-1/2 -translate-y-1/2',
            'glass-elevated max-h-[88vh] overflow-y-auto rounded-[22px] p-5',
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-[15px] font-semibold tracking-tight text-ink-strong">
                {editing ? `Edit ${editing.name}` : 'Add a model'}
              </Dialog.Title>
              <Dialog.Description className="mt-0.5 text-[13px] text-dim">
                {editing
                  ? 'Changes apply immediately, including to queued runs.'
                  : 'The model key is what LiteLLM routes on — e.g. gpt-4o, anthropic/claude-sonnet-5, groq/openai/gpt-oss-120b.'}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button size="icon" variant="ghost" aria-label="Close">
                <X className="size-4" />
              </Button>
            </Dialog.Close>
          </div>

          <form onSubmit={submit} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Display name" htmlFor="model-name">
                <input
                  id="model-name"
                  value={draft.name}
                  onChange={(event) => set('name', event.target.value)}
                  placeholder="GPT-4o"
                  className={inputClass}
                  required
                />
              </Field>
              <Field
                label="Registry id"
                htmlFor="model-id"
                hint={editing ? 'renaming creates a new entry' : 'unique'}
              >
                <input
                  id="model-id"
                  value={draft.id}
                  onChange={(event) => set('id', event.target.value)}
                  placeholder="openai-gpt4o"
                  className={cn(inputClass, 'font-mono text-[12px]')}
                  required
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Provider" htmlFor="model-provider">
                <select
                  id="model-provider"
                  value={draft.provider}
                  onChange={(event) => set('provider', event.target.value)}
                  className={inputClass}
                >
                  {PROVIDER_OPTIONS.map((option) => (
                    <option key={option} value={option} className="bg-void-900 text-ink-strong">
                      {option}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Model key" htmlFor="model-key">
                <input
                  id="model-key"
                  value={draft.model_key}
                  onChange={(event) => set('model_key', event.target.value)}
                  placeholder="gpt-4o"
                  className={cn(inputClass, 'font-mono text-[12px]')}
                  required
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Max output tokens" htmlFor="model-max">
                <input
                  id="model-max"
                  type="number"
                  min={1}
                  max={200000}
                  value={draft.max_tokens}
                  onChange={(event) => set('max_tokens', Number(event.target.value))}
                  className={inputClass}
                />
              </Field>
              <Field label="$ / 1k input" htmlFor="model-cost-in">
                <input
                  id="model-cost-in"
                  type="number"
                  min={0}
                  step="0.00001"
                  value={draft.cost_per_1k_input}
                  onChange={(event) =>
                    set('cost_per_1k_input', Number(event.target.value))
                  }
                  className={inputClass}
                />
              </Field>
              <Field label="$ / 1k output" htmlFor="model-cost-out">
                <input
                  id="model-cost-out"
                  type="number"
                  min={0}
                  step="0.00001"
                  value={draft.cost_per_1k_output}
                  onChange={(event) =>
                    set('cost_per_1k_output', Number(event.target.value))
                  }
                  className={inputClass}
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Temperature" htmlFor="model-temp">
                <input
                  id="model-temp"
                  type="number"
                  min={0}
                  max={2}
                  step="0.05"
                  value={draft.temperature}
                  onChange={(event) => set('temperature', Number(event.target.value))}
                  className={inputClass}
                />
              </Field>
              <Field label="Consensus weight" htmlFor="model-weight">
                <input
                  id="model-weight"
                  type="number"
                  min={0.01}
                  step="0.05"
                  value={draft.weight}
                  onChange={(event) => set('weight', Number(event.target.value))}
                  className={inputClass}
                />
              </Field>
              <Field
                label="API base"
                htmlFor="model-base"
                hint="optional"
              >
                <input
                  id="model-base"
                  value={draft.api_base ?? ''}
                  onChange={(event) => set('api_base', event.target.value)}
                  placeholder="https://…"
                  className={cn(inputClass, 'font-mono text-[12px]')}
                />
              </Field>
            </div>

            <Field
              label="Dedicated key variable"
              htmlFor="model-key-env"
              hint="optional — overrides the family key for this entry only"
            >
              <input
                id="model-key-env"
                value={draft.api_key_env ?? ''}
                onChange={(event) => set('api_key_env', event.target.value)}
                placeholder="OLLAMA_CLOUD_API_KEY"
                className={cn(inputClass, 'font-mono text-[12px]')}
              />
            </Field>

            <div className="flex flex-wrap gap-5">
              <label className="flex items-center gap-2 text-[12px] text-dim">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => set('enabled', event.target.checked)}
                  className="size-3.5 accent-aurora-500"
                />
                Enabled
              </label>
              <label className="flex items-center gap-2 text-[12px] text-dim">
                <input
                  type="checkbox"
                  checked={draft.supports_json_mode}
                  onChange={(event) =>
                    set('supports_json_mode', event.target.checked)
                  }
                  className="size-3.5 accent-aurora-500"
                />
                Native JSON mode
              </label>
            </div>

            {save.isError ? (
              <p className="rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
                {save.error instanceof ApiError
                  ? save.error.message
                  : 'Could not save the model.'}
              </p>
            ) : null}

            <div className="flex justify-end gap-2 pt-1">
              <Dialog.Close asChild>
                <Button variant="ghost">Cancel</Button>
              </Dialog.Close>
              <Button
                type="submit"
                variant="primary"
                loading={save.isPending}
                disabled={!valid}
              >
                {editing ? 'Save changes' : 'Add model'}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
