import type { UseQueryResult } from '@tanstack/react-query';
import { Bike, ChevronRight, CircleAlert, LoaderCircle, Minus, Plus } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { t } from '../i18n';
import { cx } from '../lib';
import { Button } from './Button';

export type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'accent' | 'muted';

export function Badge({
  tone = 'neutral',
  icon,
  children,
}: {
  tone?: Tone;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <span className={cx('badge', `badge-${tone}`)}>
      {icon}
      {children}
    </span>
  );
}

export function Card({
  children,
  flush = false,
  className,
}: {
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return <div className={cx('card', flush && 'card-flush', className)}>{children}</div>;
}

export function List({ children, className }: { children: ReactNode; className?: string }) {
  return <ul className={cx('list', className)}>{children}</ul>;
}

/** One line, one fact. Rows sit directly in a card; they are never cards themselves. */
export function Row({
  title,
  subtitle,
  leading,
  trailing,
  onClick,
  disabled = false,
  chevron,
  tone,
  className,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  leading?: ReactNode;
  trailing?: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  chevron?: boolean;
  tone?: Tone;
  className?: string;
  children?: ReactNode;
}) {
  const body = (
    <>
      {leading && <span className={cx('row-lead', tone && `tone-${tone}`)}>{leading}</span>}
      <span className="row-main">
        <span className={cx('row-title', tone === 'danger' && 'tone-danger')}>{title}</span>
        {subtitle && <span className="row-sub">{subtitle}</span>}
        {children}
      </span>
      {trailing !== undefined && <span className="row-trail">{trailing}</span>}
      {(chevron ?? !!onClick) && <ChevronRight className="row-chevron" size={18} />}
    </>
  );
  return (
    <li className={cx('row-item', className)}>
      {onClick ? (
        <button type="button" className="row" onClick={onClick} disabled={disabled}>
          {body}
        </button>
      ) : (
        <div className="row">{body}</div>
      )}
    </li>
  );
}

export function Page({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <div className={cx('page', wide && 'page-wide')}>{children}</div>;
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head-text">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </div>
      {actions && <div className="page-head-actions">{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  action,
  children,
  className,
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx('section', className)}>
      {(title || action) && (
        <div className="section-head">
          {title && <h2 className="section-title">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  note,
  icon,
  accent = false,
}: {
  label: ReactNode;
  value: ReactNode;
  note?: ReactNode;
  icon?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div className={cx('stat', accent && 'stat-accent')}>
      <div className="stat-label">
        <span>{label}</span>
        {icon}
      </div>
      <strong className="stat-value tabular">{value}</strong>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}

export function StatGrid({ children, columns }: { children: ReactNode; columns?: 3 | 4 }) {
  return <div className={cx('stat-grid', columns === 3 && 'stat-grid-3')}>{children}</div>;
}

export function Meter({
  value,
  max,
  tone = 'ok',
  className,
}: {
  value: number;
  max: number;
  tone?: Tone;
  className?: string;
}) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <span className={cx('meter', `meter-${tone}`, className)} aria-hidden="true">
      <span style={{ width: `${percent}%` } as CSSProperties} />
    </span>
  );
}

export function Stepper({
  value,
  onDecrement,
  onIncrement,
  canDecrement,
  canIncrement,
  decrementLabel,
  incrementLabel,
}: {
  value: number;
  onDecrement(): void;
  onIncrement(): void;
  canDecrement: boolean;
  canIncrement: boolean;
  decrementLabel: string;
  incrementLabel: string;
}) {
  return (
    <div className="stepper">
      <Button
        size="icon"
        aria-label={decrementLabel}
        disabled={!canDecrement}
        onClick={onDecrement}
      >
        <Minus size={16} />
      </Button>
      <b className="tabular" aria-live="polite">
        {value}
      </b>
      <Button
        size="icon"
        aria-label={incrementLabel}
        disabled={!canIncrement}
        onClick={onIncrement}
      >
        <Plus size={16} />
      </Button>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  size = 'md',
  chips = false,
  className,
}: {
  value: T;
  onChange(value: NoInfer<T>): void;
  options: { value: NoInfer<T>; label: ReactNode; count?: number }[];
  label: string;
  size?: 'sm' | 'md';
  /** Wrapping filter chips rather than a single track, for longer labels. */
  chips?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cx(chips ? 'seg-chips' : 'seg', size === 'sm' && 'seg-sm', className)}
      role="group"
      aria-label={label}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          <span className="seg-label">{option.label}</span>
          {option.count !== undefined && <span className="seg-count tabular">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Empty({
  title,
  text,
  icon,
  action,
}: {
  title: ReactNode;
  text?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">{icon ?? <Bike size={24} />}</span>
      <p className="empty-title">{title}</p>
      {text && <p className="empty-text">{text}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ height = 120, className }: { height?: number; className?: string }) {
  return <div className={cx('skeleton', className)} style={{ height }} aria-hidden="true" />;
}

export function PageSkeleton() {
  return (
    <div className="page-skeleton" aria-busy="true" aria-label={t.common.loading}>
      <Skeleton height={28} className="skeleton-title" />
      <Skeleton height={96} />
      <Skeleton height={180} />
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <CircleAlert size={20} />
      <p>{error instanceof Error && error.message ? error.message : t.errors.generic}</p>
      {retry && (
        <Button size="sm" onClick={retry}>
          {t.common.retry}
        </Button>
      )}
    </div>
  );
}

export function Notice({
  tone = 'info',
  icon,
  children,
}: {
  tone?: 'info' | 'warn' | 'danger';
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={cx('notice', `notice-${tone}`)}>
      {icon}
      <div>{children}</div>
    </div>
  );
}

export function Spinner({ size = 18 }: { size?: number }) {
  return <LoaderCircle className="spin" size={size} aria-hidden="true" />;
}

/**
 * Data first: once a query has data it stays on screen through refetches and
 * transient errors, so the cabinet never falls back to a skeleton it has
 * already moved past.
 */
export function Query<T>({
  query,
  children,
  skeleton,
}: {
  query: UseQueryResult<T>;
  children(data: T): ReactNode;
  skeleton?: ReactNode;
}) {
  if (query.data !== undefined) return <>{children(query.data)}</>;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  return <>{skeleton ?? <PageSkeleton />}</>;
}

/** A labelled control; `group` for controls that are not a single input (segmented buttons). */
export function Field({
  label,
  hint,
  group = false,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  group?: boolean;
  children: ReactNode;
}) {
  const Tag = group ? 'div' : 'label';
  return (
    <Tag className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </Tag>
  );
}
