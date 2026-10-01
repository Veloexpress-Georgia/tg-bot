export function Brand({
  href = '#overview',
  compact = false,
}: {
  href?: string;
  compact?: boolean;
}) {
  return (
    <a
      className={`brand ${compact ? 'brand-compact' : ''}`}
      href={href}
      aria-label="VeloExpress — главная"
    >
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
        <small>TBILISI · SHUTTLE CLUB</small>
      </span>
    </a>
  );
}
