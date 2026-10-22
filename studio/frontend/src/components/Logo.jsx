const UPPER_PATH =
  "M232 6 386 94c13 7 20 18 20 33v13c0 14-7 26-19 33L203 277c-16 9-32 10-48 1l-16-9c-3-1-3-5 1-7l75-43c13-7 14-23 1-31l-88-50c-16-10-36-1-36 19v236c0 4-3 6-7 4l-64-37C7 352 0 342 0 327V125c0-13 6-23 18-29L184 7c15-9 32-9 48-1Z";
const LOWER_PATH =
  "M396 202c1-3 3-3 6-1 10 6 16 16 16 30v56c0 15-7 28-20 36L220 421c-15 8-30 9-46 1l-31-17c-12-7-18-18-18-32v-69c0-4 2-5 5-3l27 15c17 10 34 9 53 0l169-96c8-5 14-11 17-18Z";

export function LogoMark({ size = 36, title = "Pawabase" }) {
  return (
    <svg
      viewBox="0 0 420 430"
      width={size}
      height={size}
      role="img"
      aria-label={title}
      style={{ flexShrink: 0 }}
    >
      <path d={UPPER_PATH} fill="#FF7049" />
      <path d={LOWER_PATH} fill="#9A8CFF" />
    </svg>
  );
}

export function Logo({ sub }) {
  return (
    <span className="row flex items-center gap-2 [&.wrap]:flex-wrap [&.top]:items-start [td_&]:gap-1.5" style={{ gap: 11 }}>
      <LogoMark />
      <span className="brand-name font-extrabold text-[21px] tracking-[-0.035em] [&_small]:block [&_small]:text-2xs [&_small]:font-semibold [&_small]:tracking-[0.02em] [&_small]:text-faint [&_small]:mt-[-3px]">
        pawabase
        {sub && <small>{sub}</small>}
      </span>
    </span>
  );
}
