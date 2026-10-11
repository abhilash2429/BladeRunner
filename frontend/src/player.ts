import { initialState, reduce } from "./reduce";
import { compileTimeline, realAt, type Timeline } from "./timeline";
import type { BrEvent, ViewState } from "./types";

export interface PlayerOptions {
  compress: boolean;
  prerollMs: number;
  realLimitMs: number | null;
  loop: boolean;
}

const STATE_TYPES = new Set([
  "case.opened",
  "case.transition",
  "case.failed",
  "case.patched",
  "agent.blocked",
]);

export class Player {
  readonly timeline: Timeline;
  virt = 0;
  speed = 1;
  playing = false;
  ended = false;
  state: ViewState;
  private cursor = 0;
  private raf = 0;
  private last = 0;
  private readonly listeners = new Set<() => void>();

  constructor(
    events: BrEvent[],
    private readonly agents: string[],
    private readonly options: PlayerOptions,
  ) {
    this.timeline = compileTimeline(events, options);
    this.state = initialState(agents);
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit() {
    for (const listener of this.listeners) listener();
  }

  seek(virt: number) {
    const v = Math.min(this.timeline.duration, Math.max(0, virt));
    this.virt = v;
    this.ended = false;
    this.state = initialState(this.agents);
    this.cursor = 0;
    const points = this.timeline.points;
    while (this.cursor < points.length && points[this.cursor].virt <= v) {
      this.state = reduce(this.state, points[this.cursor].event, points[this.cursor].virt);
      this.cursor += 1;
    }
    this.emit();
  }

  play() {
    if (this.playing) return;
    if (this.virt >= this.timeline.duration) this.seek(0);
    this.playing = true;
    this.ended = false;
    this.last = 0;
    this.emit();
    this.raf = requestAnimationFrame(this.frame);
  }

  pause() {
    this.playing = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.emit();
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  setSpeed(speed: number) {
    this.speed = speed;
    this.emit();
  }

  jumpNext() {
    const next = this.timeline.points.find((point) => point.virt > this.virt + 1 && STATE_TYPES.has(point.event.type));
    if (next) this.seek(next.virt);
    else this.seek(this.timeline.duration);
  }

  realNow(): number {
    return realAt(this.timeline, this.virt);
  }

  destroy() {
    this.pause();
    this.listeners.clear();
  }

  private frame = (now: number) => {
    if (!this.playing) return;
    const dt = this.last ? Math.min(50, now - this.last) : 16;
    this.last = now;
    this.virt += dt * this.speed;
    if (this.virt >= this.timeline.duration) {
      if (this.options.loop) {
        this.seek(0);
        this.playing = true;
        this.last = now;
      } else {
        this.virt = this.timeline.duration;
        this.applyDue();
        this.playing = false;
        this.ended = true;
        this.emit();
        return;
      }
    }
    this.applyDue();
    this.raf = requestAnimationFrame(this.frame);
  };

  private applyDue() {
    const points = this.timeline.points;
    let changed = false;
    while (this.cursor < points.length && points[this.cursor].virt <= this.virt) {
      this.state = reduce(this.state, points[this.cursor].event, points[this.cursor].virt);
      this.cursor += 1;
      changed = true;
    }
    if (changed) this.emit();
  }
}
