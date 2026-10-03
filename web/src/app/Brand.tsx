import { cx } from '../lib';

export function Brand({ href, compact = false }: { href?: string; compact?: boolean }) {
  const Tag = href ? 'a' : 'span';
  return (
    <Tag className={cx('brand', compact && 'brand-compact')} {...(href ? { href } : {})}>
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 48 48" fill="none">
          <path
            d="M10 13L22 35L35 13"
            stroke="currentColor"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M27 24L37 24M30 30L38 30"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
          />
          <circle cx="9" cy="33" r="2" fill="currentColor" />
        </svg>
      </span>
      <span className="brand-type">
        <b className="wordmark">
          Velo<span>Express</span>
        </b>
        {!compact && <small>Tbilisi · Shuttle club</small>}
      </span>
    </Tag>
  );
}
