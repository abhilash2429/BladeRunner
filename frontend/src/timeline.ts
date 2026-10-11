import type { BrEvent } from "./types";

export interface TimelinePoint {
  virt: number;
  real: number;
  event: BrEvent;
}

export interface Skip {
  at: number;
  span: number;
  gap: number;
}

export interface Timeline {
  points: TimelinePoint[];
  skips: Skip[];
  duration: number;
  t0: number;
}

const GAP = 20_000;
const COMPRESSED = 2_000;

export function sortEvents(events: BrEvent[]): BrEvent[] {
  return [...events].sort((a, b) => {
    const dt = Date.parse(a.ts) - Date.parse(b.ts);
    if (dt) return dt;
    const ds = a.source.localeCompare(b.source);
    if (ds) return ds;
    return a.seq - b.seq;
  });
}

export function compileTimeline(
  events: BrEvent[],
  opts: { compress: boolean; prerollMs: number; realLimitMs: number | null },
): Timeline {
  const sorted = sortEvents(events);
  if (!sorted.length) {
    return { points: [], skips: [], duration: opts.prerollMs, t0: 0 };
  }
  const t0 = Date.parse(sorted[0].ts);
  const limited = opts.realLimitMs == null
    ? sorted
    : sorted.filter((event) => Date.parse(event.ts) - t0 <= opts.realLimitMs!);
  let virt = opts.prerollMs;
  let prevReal = 0;
  const points: TimelinePoint[] = [];
  const skips: Skip[] = [];
  for (const event of limited) {
    const real = Date.parse(event.ts) - t0;
    const gap = real - prevReal;
    if (opts.compress && gap > GAP) {
      skips.push({ at: virt, span: COMPRESSED, gap });
      virt += COMPRESSED;
    } else {
      virt += gap;
    }
    points.push({ virt, real, event });
    prevReal = real;
  }
  let duration = virt;
  if (!opts.compress && opts.realLimitMs != null) {
    duration = opts.prerollMs + opts.realLimitMs;
  } else {
    duration += 800;
  }
  return { points, skips, duration, t0 };
}

export function formatSkip(gapMs: number): string {
  if (gapMs >= 60_000) {
    const minutes = Math.round(gapMs / 60_000);
    return `${minutes} min skipped`;
  }
  return `${Math.round(gapMs / 1000)}s skipped`;
}

export function formatClock(t0: number, realMs: number): string {
  if (!t0) return "—";
  return new Date(t0 + realMs).toISOString().slice(11, 19) + "Z";
}

export function realAt(timeline: Timeline, virt: number): number {
  let prevVirt = 0;
  let prevReal = 0;
  for (const point of timeline.points) {
    if (point.virt >= virt) {
      const span = point.virt - prevVirt || 1;
      const t = Math.min(1, Math.max(0, (virt - prevVirt) / span));
      return prevReal + (point.real - prevReal) * t;
    }
    prevVirt = point.virt;
    prevReal = point.real;
  }
  return prevReal;
}
