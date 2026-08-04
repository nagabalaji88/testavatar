import { motion, type HTMLMotionProps } from 'framer-motion';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface GlassCardProps extends HTMLMotionProps<'div'> {
  children: ReactNode;
  elevated?: boolean;
  interactive?: boolean;
  grain?: boolean;
}

/** The base spatial surface: translucent stratum floating over the field. */
export function GlassCard({
  children,
  elevated = false,
  interactive = false,
  grain = false,
  className,
  ...rest
}: GlassCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      whileHover={
        interactive ? { y: -3, transition: { duration: 0.2 } } : undefined
      }
      className={cn(
        'relative overflow-hidden rounded-[22px] p-5',
        elevated ? 'glass-elevated' : 'glass',
        interactive && 'cursor-pointer transition-shadow hover:shadow-[0_20px_60px_rgba(3,6,20,0.65)]',
        grain && 'grain',
        className,
      )}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

interface SectionHeaderProps {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  action?: ReactNode;
}

export function SectionHeader({ title, subtitle, icon, action }: SectionHeaderProps) {
  return (
    <div className="mb-4 flex items-start justify-between gap-4">
      <div className="flex items-start gap-3">
        {icon ? (
          <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-white/8 text-aurora-300 ring-1 ring-white/10">
            {icon}
          </span>
        ) : null}
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight text-white">
            {title}
          </h2>
          {subtitle ? (
            <p className="mt-0.5 text-[13px] leading-relaxed text-dim">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {action}
    </div>
  );
}
