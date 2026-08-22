import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/** The shared control surface. Exported rather than duplicated because the
 *  launcher, the model form and the key panel all need identical inputs, and a
 *  drifting copy is immediately visible when two of them sit on one page. */
export const inputClass =
  'w-full rounded-xl border border-white/10 bg-white/[0.05] px-3 py-2.5 text-[13px] text-white placeholder:text-white/30 outline-none transition focus:border-aurora-400/60 focus:bg-white/[0.07] disabled:opacity-50';

export function Field({
  label,
  hint,
  htmlFor,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 block text-[12px] font-medium text-dim"
      >
        {label}
        {hint ? <span className="ml-2 font-normal text-faint">{hint}</span> : null}
      </label>
      {children}
    </div>
  );
}

export function TextInput({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(inputClass, className)} />;
}
