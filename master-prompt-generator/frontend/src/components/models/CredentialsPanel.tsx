import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  ExternalLink,
  KeyRound,
  Loader2,
  Trash2,
  X,
} from 'lucide-react';
import type { CredentialStatus, CredentialTestResult } from '@/types';
import { api, ApiError } from '@/services/api';
import { SectionHeader } from '@/components/ui/GlassCard';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { inputClass } from '@/components/ui/Field';
import { cn, formatRelativeTime } from '@/lib/utils';

/** One provider family's key: its state, and the controls to change it.
 *
 *  The value is never rendered, because the server never sends it. The row can
 *  show that a key exists and its last four characters — enough to answer "is
 *  the key in place the one I think it is" without the field becoming a way to
 *  read a secret back out of the deployment.
 */
function CredentialRow({
  status,
  onSaved,
}: {
  status: CredentialStatus;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(false);
  const [tested, setTested] = useState<CredentialTestResult | null>(null);

  const save = useMutation({
    mutationFn: (key: string) => api.setCredential(status.family, key),
    onSuccess: () => {
      setDraft('');
      setEditing(false);
      setTested(null);
      onSaved();
    },
  });

  const clear = useMutation({
    mutationFn: () => api.clearCredential(status.family),
    onSuccess: () => {
      setTested(null);
      onSaved();
    },
  });

  // Deliberately separate from saving. A key can be well-formed, current and
  // still answer 429 because the account is out of credit, and those have
  // opposite fixes — so the check calls the provider for real rather than
  // inferring from the shape of the string.
  const test = useMutation({
    mutationFn: () => api.testCredential(status.family),
    onSuccess: setTested,
  });

  const busy = save.isPending || clear.isPending;
  const error = save.error ?? clear.error;

  return (
    <li className="rounded-xl bg-surface-1 px-4 py-3.5 ring-1 ring-inset ring-line-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] font-medium text-ink-1">
              {status.label}
            </span>

            {!status.editable ? (
        <p className="mt-2.5 text-[11.5px] text-faint">
          {status.configured
            ? `Supplied by ${status.env_var} in your environment.`
            : `Set ${status.env_var} in .env to enable this.`}{' '}
          Models name their own variable, so this one is read here rather than
          stored — edit .env and restart the stack to change it.
        </p>
      ) : null}

      {status.needs_reentry ? (
              <Badge tone="danger">
                <AlertTriangle className="size-3" />
                re-enter
              </Badge>
            ) : status.configured ? (
              <Badge tone="success">
                <Check className="size-3" />
                {status.last4 ? `set ····${status.last4}` : 'set'}
              </Badge>
            ) : (
              <Badge tone="neutral">not set</Badge>
            )}

            {/* Which source is winning. "Configured" alone is not actionable
                once a key can come from two places: an operator editing the
                one that is being overridden sees no effect. */}
            {status.configured ? (
              <Badge tone={status.source === 'database' ? 'info' : 'neutral'}>
                {status.source === 'database' ? 'from this UI' : `from ${status.env_var}`}
              </Badge>
            ) : null}
          </div>

          <p className="mt-1 text-[11.5px] text-faint">
            {status.model_count > 0
              ? `unblocks ${status.model_count} enabled model${status.model_count === 1 ? '' : 's'}`
              : 'no enabled models use this provider yet'}
            {status.source === 'database' && status.updated_at
              ? ` · updated ${formatRelativeTime(status.updated_at)}`
              : null}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {/* The check is addressed by family, and these rows have none --
              offering the button would only produce a 404. */}
          {status.configured && status.editable ? (
            <Button
              size="sm"
              variant="ghost"
              loading={test.isPending}
              onClick={() => test.mutate()}
            >
              Test
            </Button>
          ) : null}
          {status.editable ? (
            <Button size="sm" variant="ghost" onClick={() => setEditing((v) => !v)}>
              {editing ? 'Cancel' : status.configured ? 'Replace' : 'Add key'}
            </Button>
          ) : null}
          {status.editable && status.source === 'database' ? (
            <Button
              size="sm"
              variant="ghost"
              loading={clear.isPending}
              onClick={() => clear.mutate()}
              aria-label={`Remove the stored ${status.label} key`}
            >
              <Trash2 className="size-3.5" />
            </Button>
          ) : null}
        </div>
      </div>

      {editing ? (
        <form
          className="mt-3 flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (draft.trim()) save.mutate(draft.trim());
          }}
        >
          <input
            // type=password so the value is not left legible on a shared or
            // screen-shared display while it is being pasted.
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={`Paste the ${status.label} key`}
            className={cn(inputClass, 'flex-1 font-mono text-[12px]')}
            aria-label={`${status.label} API key`}
          />
          <Button
            type="submit"
            variant="primary"
            size="sm"
            loading={save.isPending}
            disabled={draft.trim().length < 8 || busy}
          >
            Save
          </Button>
          {status.console_url ? (
            <a
              href={status.console_url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1.5 rounded-xl px-2.5 py-2 text-[12px] text-dim transition hover:bg-surface-3 hover:text-ink-strong"
            >
              Get a key
              <ExternalLink className="size-3" />
            </a>
          ) : null}
        </form>
      ) : null}

      {status.needs_reentry ? (
        <p className="mt-2.5 text-[11.5px] text-rose-400">
          A key is stored but can no longer be decrypted — JWT_SECRET_KEY was
          almost certainly rotated. Paste it again to fix this.
        </p>
      ) : null}

      {tested ? (
        <p
          className={cn(
            'mt-2.5 flex items-start gap-1.5 text-[11.5px]',
            tested.ok ? 'text-mint-400' : 'text-amber-400',
          )}
        >
          {tested.ok ? (
            <Check className="mt-px size-3.5 shrink-0" />
          ) : (
            <X className="mt-px size-3.5 shrink-0" />
          )}
          {tested.detail}
        </p>
      ) : null}

      {error ? (
        <p className="mt-2.5 text-[11.5px] text-rose-400">
          {error instanceof ApiError ? error.message : 'Could not save that key.'}
        </p>
      ) : null}
    </li>
  );
}

export function CredentialsPanel() {
  const queryClient = useQueryClient();

  const { data: credentials = [], isLoading, error } = useQuery<CredentialStatus[]>({
    queryKey: ['credentials'],
    queryFn: api.listCredentials,
  });

  /** A key change alters which models are callable, so the model list and the
   *  live catalogue are both stale the moment one is saved. */
  const invalidateEverythingKeyDependent = () => {
    queryClient.invalidateQueries({ queryKey: ['credentials'] });
    queryClient.invalidateQueries({ queryKey: ['models'] });
    queryClient.invalidateQueries({ queryKey: ['model-catalog'] });
  };

  if (error) {
    return (
      <p className="rounded-xl bg-rose-400/10 px-3 py-2 text-[12px] text-rose-400 ring-1 ring-inset ring-rose-400/25">
        {error instanceof ApiError && error.status === 403
          ? 'Only administrators can view or change provider keys.'
          : 'Could not load provider keys.'}
      </p>
    );
  }

  return (
    <div>
      <SectionHeader
        title="Provider API keys"
        subtitle="Stored encrypted and applied immediately — no restart, and no key in a git-tracked file. Keys already set in .env keep working."
        icon={<KeyRound className="size-4" />}
      />

      {isLoading ? (
        <div className="flex items-center gap-2 text-[12px] text-faint">
          <Loader2 className="size-3.5 animate-spin" />
          Loading providers…
        </div>
      ) : (
        <ul className="space-y-2.5">
          {credentials.map((status) => (
            <CredentialRow
              key={status.family}
              status={status}
              onSaved={invalidateEverythingKeyDependent}
            />
          ))}
        </ul>
      )}

      <p className="mt-4 text-[11.5px] leading-relaxed text-faint">
        A key saved here takes precedence over the matching environment
        variable, so editing it always has an effect. The value is never sent
        back to the browser — only whether one is present and its last four
        characters.
      </p>
    </div>
  );
}
