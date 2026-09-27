// The Pawabase mark: three stacked data layers — the base — struck through
// by a bolt — the pawa. Ink tile, lavender layers, peach bolt; the bolt is
// knocked out of the layers by a tile-coloured stroke so it reads at 16px.
// public favicon: resources/views/app.html carries the same paths.

export const LOGO_BARS = [7, 13.7, 20.4];
export const LOGO_BOLT = "M18.3 4.2 10.6 16.6h5.3l-2.3 11.2 7.9-12.6h-5.3l2.1-11Z";

export function LogoMark({ size = 36, title = "Pawabase" }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} role="img" aria-label={title} style={{ flexShrink: 0 }}>
      <rect width="32" height="32" rx="9" fill="#1f1b2e" />
      <g fill="#cfc4fa">
        {LOGO_BARS.map((y) => <rect key={y} x="5.5" y={y} width="21" height="4.6" rx="2.3" />)}
      </g>
      <path d={LOGO_BOLT} fill="#ffc9a8" stroke="#1f1b2e" strokeWidth="2" strokeLinejoin="round" paintOrder="stroke" />
    </svg>
  );
}

export function Logo({ sub }) {
  return (
    <span className="row" style={{ gap: 11 }}>
      <LogoMark />
      <span className="brand-name">
        pawabase
        {sub && <small>{sub}</small>}
      </span>
    </span>
  );
}
