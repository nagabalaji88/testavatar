import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-xl text-[13px] font-medium tracking-tight transition-all duration-200 disabled:pointer-events-none disabled:opacity-45 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-aurora-400',
  {
    variants: {
      variant: {
        primary:
          'bg-gradient-to-b from-aurora-400 to-aurora-600 text-white shadow-[0_6px_20px_rgba(31,62,245,0.45)] hover:brightness-110 active:brightness-95',
        glass:
          'glass text-ink-strong hover:bg-surface-4 active:bg-surface-2',
        ghost:
          'text-dim hover:bg-surface-3 hover:text-ink-strong',
        danger:
          'bg-rose-400/15 text-rose-400 ring-1 ring-inset ring-rose-400/30 hover:bg-rose-400/25',
      },
      size: {
        sm: 'h-8 px-3',
        md: 'h-10 px-4',
        lg: 'h-12 px-6 text-sm',
        icon: 'size-9',
      },
    },
    defaultVariants: { variant: 'glass', size: 'md' },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant, size, asChild = false, loading = false, children, disabled, ...props },
    ref,
  ) => {
    // Radix's Slot requires exactly one element child: it clones its props
    // onto that child rather than rendering its own DOM node. Injecting a
    // loading-spinner sibling here works for a real <button>, but a second
    // child (even one that evaluates to null) makes Slot's child count != 1
    // and it throws. asChild callers hand rendering control to the wrapped
    // element entirely, so they get children only, with no spinner slot.
    if (asChild) {
      return (
        <Slot
          ref={ref}
          className={cn(buttonVariants({ variant, size }), className)}
          {...props}
        >
          {children}
        </Slot>
      );
    }

    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
        {children}
      </button>
    );
  },
);
Button.displayName = 'Button';

export { buttonVariants };
