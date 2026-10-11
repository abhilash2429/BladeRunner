import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { CountUp, Note } from "./motion";

export interface ExamSide {
  hit: number;
  false: number;
  miss: number;
  quiet: number;
  precision: number;
  recall: number;
  false_alarm_rate: number;
  cost_per_trace_usd: number;
  per_category: Record<string, { tp: number; fp: number; fn: number; precision: number | null; recall: number | null }>;
}

export interface ExamFile {
  n: number;
  clean: number;
  violating: number;
  note: string;
  v0: ExamSide;
  bladerunner: ExamSide;
}

export interface DojoFile {
  n: number;
  models: { id: string; name: string; n: number }[];
  tallies: Record<string, { v0: number[]; bladerunner: number[] }>;
  dots: number[][];
}

const CATS: Record<string, string> = {
  C1: "C1 unapproved destination",
  C2: "C2 tool it wasn't given",
  C3: "C3 planted instructions",
  C4: "C4 unofficial channels",
  C5: "C5 data sent outside",
};

function cells(side: ExamSide) {
  return [
    ...Array(side.hit).fill("hit"),
    ...Array(side.false).fill("false"),
    ...Array(side.miss).fill("miss"),
    ...Array(side.quiet).fill("quiet"),
  ];
}

function money(n: number) {
  return `$${n.toFixed(4)}`;
}

function rate(n: number | null) {
  return n == null ? "n/a" : n.toFixed(2);
}

function useSeen<T extends HTMLElement>(threshold = 0.2) {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setSeen(true);
        io.disconnect();
      }
    }, { threshold });
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return { ref, seen };
}

function Compare({
  label,
  before,
  after,
  digits,
  suffix = "",
  play,
  delay,
}: {
  label: string;
  before: number;
  after: number;
  digits: number;
  suffix?: string;
  play: boolean;
  delay: number;
}) {
  const top = Math.max(before, after) || 1;
  const fmt = (n: number) => `${n.toFixed(digits)}${suffix}`;
  return (
    <article className="compare glass spot" data-reveal style={{ ["--d" as string]: `${delay}ms` } as CSSProperties}>
      <p className="label">{label}</p>
      <p className="big"><CountUp value={after} digits={digits} suffix={suffix} play={play} /></p>
      <p className="from">Bladerunner · v0 was {fmt(before)}</p>
      <div className="bars">
        <span className="bar"><em>v0</em><span className="rail"><i style={{ ["--w" as string]: before / top } as CSSProperties} /></span></span>
        <span className="bar br"><em>Bladerunner</em><span className="rail"><i style={{ ["--w" as string]: after / top } as CSSProperties} /></span></span>
      </div>
    </article>
  );
}

export function Exam({ exam }: { exam: ExamFile }) {
  const { ref, seen } = useSeen<HTMLElement>(0.15);

  return (
    <section className={`sec exam${seen ? " is-in" : ""}`} id="exam" ref={ref}>
      <header className="sec-head" data-reveal>
        <p className="kicker">Test 1 · our exam</p>
        <h2>102 traces the system was never tuned on.</h2>
        <p className="lede">A v0 single-prompt checker against the full Bladerunner pipeline. Precision {exam.v0.precision.toFixed(2)} to {exam.bladerunner.precision.toFixed(2)}. Recall {exam.v0.recall.toFixed(2)} to {exam.bladerunner.recall.toFixed(2)}.</p>
      </header>
      <div className="compares">
        <Compare label="Precision" before={exam.v0.precision} after={exam.bladerunner.precision} digits={2} play={seen} delay={0} />
        <Compare label="Recall" before={exam.v0.recall} after={exam.bladerunner.recall} digits={2} play={seen} delay={80} />
        <Compare label="False alarms on clean runs" before={exam.v0.false_alarm_rate * 100} after={exam.bladerunner.false_alarm_rate * 100} digits={1} suffix="%" play={seen} delay={160} />
      </div>
      <div className="exam-cols">
        <Grid title="v0" side={exam.v0} />
        <div className="grid-with-note">
          <Grid title="Bladerunner" side={exam.bladerunner} />
          <Note arrow="up-left" className="grid-note" delay={500}>{exam.v0.false} false alarms down to {exam.bladerunner.false}</Note>
        </div>
      </div>
      <Legend />
      <p className="fine-note">{exam.note} Bladerunner's four false alarms are agents that read the notes board, which their fetch policy forbids, without acting on it. Cost per trace {money(exam.v0.cost_per_trace_usd)} for v0, {money(exam.bladerunner.cost_per_trace_usd)} for the pipeline.</p>
      <details className="table-card glass">
        <summary>Per-category precision and recall</summary>
        <div className="tbl-scroll">
          <table>
            <thead>
              <tr><th>Category</th><th>v0 precision</th><th>v0 recall</th><th>Pipeline precision</th><th>Pipeline recall</th></tr>
            </thead>
            <tbody>
              {Object.keys(CATS).map((key) => (
                <tr key={key}>
                  <th scope="row">{CATS[key]}</th>
                  <td>{rate(exam.v0.per_category[key]?.precision ?? null)}</td>
                  <td>{rate(exam.v0.per_category[key]?.recall ?? null)}</td>
                  <td>{rate(exam.bladerunner.per_category[key]?.precision ?? null)}</td>
                  <td>{rate(exam.bladerunner.per_category[key]?.recall ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

function Legend() {
  return (
    <ul className="legend">
      <li><i className="hit" /> Right alarm</li>
      <li><i className="false" /> False alarm</li>
      <li><i className="miss" /> Miss</li>
      <li><i className="quiet" /> Correct silence</li>
    </ul>
  );
}

function Grid({ title, side }: { title: string; side: ExamSide }) {
  const items = cells(side);
  return (
    <div className="grid-card glass" data-reveal>
      <div className="grid-head">
        <h3>{title}</h3>
        <p className="counts"><b>{side.hit}</b> right · <b className="red">{side.false}</b> false · <b>{side.miss}</b> miss · <b>{side.quiet}</b> quiet</p>
      </div>
      <div className="cells">
        {items.map((kind, index) => (
          <i key={index} className={`cell ${kind}`} style={{ ["--i" as string]: index } as CSSProperties} title={kind === "hit" ? "Right alarm" : kind === "false" ? "False alarm" : kind === "miss" ? "Miss" : "Correct silence"} />
        ))}
      </div>
    </div>
  );
}

const DOT_COLOR: Record<number, [number, number, number]> = {
  0: [11, 11, 12],
  1: [229, 72, 77],
  2: [150, 150, 156],
  3: [222, 222, 226],
};

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Dot {
  x: number;
  y: number;
  v0: number;
  br: number;
  model: number;
  phase: number;
}

function layoutDots(dots: number[][]): Dot[] {
  const counts = [0, 0, 0];
  const centers = [0.19, 0.5, 0.81];
  return dots.map((dot) => {
    const model = dot[0];
    const i = counts[model]++;
    const rnd = mulberry32(model * 10007 + i * 97 + 3);
    const ang = rnd() * Math.PI * 2;
    const rad = Math.sqrt(rnd()) * 0.145;
    return {
      model,
      v0: dot[1],
      br: dot[2],
      x: centers[model] + Math.cos(ang) * rad,
      y: 0.5 + Math.sin(ang) * rad * 0.9,
      phase: rnd() * Math.PI * 2,
    };
  });
}

function rgb(code: number, t: number, other: number) {
  const a = DOT_COLOR[code];
  const b = DOT_COLOR[other];
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}

export function Dojo({ dojo }: { dojo: DojoFile }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const dots = useMemo(() => layoutDots(dojo.dots), [dojo]);
  const reduced = useRef(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [mode, setMode] = useState<"v0" | "bladerunner">(reduced.current ? "bladerunner" : "v0");
  const mix = useRef(reduced.current ? 1 : 0);
  const target = useRef(0);
  const claude = dojo.tallies["Claude 3.5 Sonnet"];
  const played = useRef(false);

  useEffect(() => {
    target.current = mode === "bladerunner" ? 1 : 0;
    if (reduced.current) mix.current = target.current;
  }, [mode]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const section = sectionRef.current;
    if (!canvas || !section) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let on = true;
    const frame = (now: number) => {
      raf = 0;
      if (!on) return;
      const goal = target.current;
      if (!reduced.current) mix.current += (goal - mix.current) * 0.07;
      if (Math.abs(goal - mix.current) < 0.004) mix.current = goal;
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, rect.width);
      const h = Math.max(1, rect.height);
      const pw = Math.floor(w * dpr);
      const ph = Math.floor(h * dpr);
      if (canvas.width !== pw || canvas.height !== ph) {
        canvas.width = pw;
        canvas.height = ph;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const t = mix.current;
      const scale = Math.min(w, h * 2.2);
      const bob = reduced.current ? 0 : 1;
      for (const dot of dots) {
        const x = dot.x * w + Math.sin(now / 1400 + dot.phase) * 1.4 * bob;
        const y = 0.5 * h + (dot.y - 0.5) * scale * 0.95 + Math.cos(now / 1700 + dot.phase) * 1.4 * bob;
        const loud = dot.br === 1 || dot.v0 === 1;
        ctx.beginPath();
        ctx.arc(x, y, loud ? 3.3 : 2.6, 0, Math.PI * 2);
        ctx.fillStyle = rgb(dot.v0, t, dot.br);
        ctx.fill();
      }
      raf = requestAnimationFrame(frame);
    };
    const start = () => {
      if (!raf) raf = requestAnimationFrame(frame);
    };
    const io = new IntersectionObserver(([entry]) => {
      on = entry.isIntersecting;
      if (on) {
        start();
        if (!played.current && !reduced.current) {
          played.current = true;
          window.setTimeout(() => setMode("bladerunner"), 900);
        }
      }
    }, { threshold: 0.35 });
    io.observe(section);
    start();
    return () => {
      on = false;
      if (raf) cancelAnimationFrame(raf);
      io.disconnect();
    };
  }, [dots]);

  const showing = mode === "v0" ? claude.v0[1] : claude.bladerunner[1];

  return (
    <section className="sec" id="dojo" ref={sectionRef}>
      <header className="sec-head" data-reveal>
        <p className="kicker">Test 2 · other people's agents</p>
        <h2>858 runs from AgentDojo, in three clusters.</h2>
        <p className="lede">GPT-4o, Claude 3.5 Sonnet, and Llama 3.3 70B. 286 runs each. A dot is one recorded trace. Red is a false alarm on AgentDojo's security label.</p>
      </header>
      <div className="dojo glass" data-reveal>
        <div className="dojo-bar">
          <p className="dojo-num">
            <span className="num-window"><b key={`${mode}-${showing}`}>{showing}</b></span>
            <span className="label">Claude false alarms · {mode === "v0" ? "v0" : "Bladerunner"}</span>
          </p>
          <div className="seg wide" role="group" aria-label="Checker" style={{ ["--x" as string]: mode === "bladerunner" ? 1 : 0 } as CSSProperties}>
            <button type="button" aria-pressed={mode === "v0"} onClick={() => setMode("v0")}>v0</button>
            <button type="button" aria-pressed={mode === "bladerunner"} onClick={() => setMode("bladerunner")}>Bladerunner</button>
          </div>
        </div>
        <div className="dojo-stage">
          <canvas ref={canvasRef} aria-hidden="true" />
          <Note arrow="down" className="dojo-note" delay={600}>watch the red here</Note>
        </div>
        <div className="clusters">
          {dojo.models.map((model) => <span key={model.id}>{model.name}</span>)}
        </div>
      </div>
      <Legend />
      <p className="fine-note">False alarms on the Claude cluster fall from {claude.v0[1]} under the single-prompt checker to {claude.bladerunner[1]} under Bladerunner. The drop plays once when this section comes into view. The toggle runs it again.</p>
    </section>
  );
}
