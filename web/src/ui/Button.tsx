import type { ComponentProps } from 'react';
import { cx } from '../lib';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'accent';

export function Button({
  variant = 'secondary',
  size = 'md',
  block = false,
  className,
  type = 'button',
  ...props
}: ComponentProps<'button'> & {
  variant?: ButtonVariant;
  size?: 'sm' | 'md' | 'lg' | 'icon';
  block?: boolean;
}) {
  return (
    <button
      type={type}
      className={cx(
        'btn',
        `btn-${variant}`,
        size !== 'md' && `btn-${size}`,
        block && 'btn-block',
        className,
      )}
      {...props}
    />
  );
}
