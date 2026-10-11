import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { formatClock } from "./timeline";

type ClockSource = { timeline: { t0: number }; realNow: () => number } | null;

export function LiveClock({ player }: { player: ClockSource }) {
  const clock = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      if (clock.current) clock.current.textContent = player ? formatClock(player.timeline.t0, player.realNow()) : "—";
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [player]);
  return <span className="clock" ref={clock}>—</span>;
}

export function WindowBar({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="window-bar">
      <span className="lights" aria-hidden="true"><i /><i /><i /></span>
      <span className="window-title">{title}</span>
      <span className="window-tools">{children}</span>
    </div>
  );
}

const ARROWS = {
  "down-left": ["M74 4 C 70 26, 46 40, 10 42", "M10 42 L 21 34 M10 42 L 22 48"],
  "down-right": ["M6 4 C 10 26, 34 40, 70 42", "M70 42 L 59 34 M70 42 L 58 48"],
  down: ["M40 2 C 32 16, 48 30, 40 46", "M40 46 L 32 38 M40 46 L 48 39"],
  left: ["M76 24 C 56 12, 30 34, 6 24", "M6 24 L 16 16 M6 24 L 15 32"],
  "up-left": ["M74 46 C 66 22, 44 10, 10 8", "M10 8 L 20 2 M10 8 L 19 15"],
} as const;

export type ArrowKind = keyof typeof ARROWS;

export function Note({
  children,
  arrow,
  className = "",
  delay = 0,
}: {
  children: ReactNode;
  arrow?: ArrowKind;
  className?: string;
  delay?: number;
}) {
  return (
    <span className={`note${arrow ? ` note-${arrow}` : ""} ${className}`} data-reveal style={{ ["--d" as string]: `${delay}ms` } as CSSProperties}>
      <span className="note-text">{children}</span>
      {arrow && (
        <svg className="note-arrow" viewBox="0 0 80 50" aria-hidden="true">
          <path d={ARROWS[arrow][0]} pathLength={1} />
          <path d={ARROWS[arrow][1]} pathLength={1} className="head" />
        </svg>
      )}
    </span>
  );
}

export function Scribble({ children }: { children: ReactNode }) {
  return (
    <span className="scribble">
      {children}
      <svg viewBox="0 0 200 22" preserveAspectRatio="none" aria-hidden="true">
        <path d="M4 14 C 42 6, 88 18, 128 10 S 184 8, 196 13" pathLength={1} />
      </svg>
    </span>
  );
}

export function Check() {
  return (
    <svg className="check" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="10.5" />
      <path d="M7 12.5 L10.5 16 L17 8.5" pathLength={1} />
    </svg>
  );
}

function formatCount(n: number, digits: number, suffix: string) {
  const body = digits === 0 ? String(Math.round(n)) : n.toFixed(digits);
  return `${body}${suffix}`;
}

export function CountUp({
  value,
  digits = 2,
  suffix = "",
  play,
}: {
  value: number;
  digits?: number;
  suffix?: string;
  play: boolean;
}) {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    if (!play) return;
    if (document.documentElement.classList.contains("reduce")) {
      setShown(value);
      return;
    }
    const started = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / 1300);
      const eased = 1 - (1 - t) ** 4;
      setShown(value * eased);
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [play, value]);

  return <>{formatCount(shown, digits, suffix)}</>;
}
