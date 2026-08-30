/**
 * Three-position theme control.
 *
 * A two-state toggle cannot express "follow my OS", which is the default and
 * the state most viewers should stay in -- so this is a segmented control
 * rather than a switch, and the current position is always visible instead
 * of being inferred from an icon that changes meaning.
 */

import { Monitor, Moon, Sun } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme, type ThemeChoice } from '@/lib/theme';

const OPTIONS: { value: ThemeChoice; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'system', label: 'System', Icon: Monitor },
  { value: 'dark', label: 'Dark', Icon: Moon },
];

export function ThemeToggle() {
  const { choice, setChoice } = useTheme();

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="flex items-center gap-0.5 rounded-xl bg-surface-1 p-0.5 ring-1 ring-inset ring-line-1"
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const selected = choice === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={label}
            title={`${label} theme`}
            onClick={() => setChoice(value)}
            className={cn(
              'grid size-7 place-items-center rounded-lg transition',
              selected
                ? 'bg-surface-4 text-ink-strong ring-1 ring-inset ring-line-2'
                : 'text-ink-3 hover:bg-surface-2 hover:text-ink-strong',
            )}
          >
            <Icon className="size-3.5" />
          </button>
        );
      })}
    </div>
  );
}
