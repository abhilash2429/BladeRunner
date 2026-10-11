import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Check, Note } from "./motion";
import type { ReplayFile } from "./types";

function textOf(replay: ReplayFile, agent: string, type: string, category: string, field: "claim" | "reason") {
  const event = [...replay.events].reverse().find((item) => item.type === type && item.agent === agent);
  const list = event?.data.findings;
  if (!Array.isArray(list)) return "";
  for (const row of list) {
    if (!row || typeof row !== "object") continue;
    const record = row as Record<string, unknown>;
    if (record.category === category && typeof record[field] === "string") return record[field];
  }
  return "";
}

const BEATS = [
  ["24 May", "The first agent writes land."],
  ["2 June", "A volunteer moderator starts deleting pages by hand, and keeps at it for weeks."],
  ["16–22 June", "Most of the activity lands in one week."],
  ["21 June", "The operator running the agents shows up in the log."],
  ["22 June", "Agent edits drop to near zero."],
  ["4 September", "Four outside researchers publish the edit log. Until that day, nobody had said anything about it in public."],
];

function useScrollProgress<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const rect = el.getBoundingClientRect();
      const mid = window.innerHeight * 0.6;
      const p = Math.min(1, Math.max(0, (mid - rect.top) / Math.max(1, rect.height)));
      el.style.setProperty("--p", p.toFixed(3));
      el.querySelectorAll<HTMLElement>("[data-beat]").forEach((item) => {
        item.classList.toggle("lit", item.getBoundingClientRect().top < mid);
      });
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);
  return ref;
}

export function Problem() {
  const line = useScrollProgress<HTMLOListElement>();
  return (
    <section className="sec problem" id="problem">
      <div className="split">
        <header className="sec-head sticky" data-reveal>
          <p className="kicker">The problem</p>
          <h2>A 25-year-old wiki became a notepad for thousands of agents.</h2>
          <p className="lede">DSEWiki has served German software developers for 25 years. In the summer of 2026, OpenAI agents writing under more than 3,700 self-given names used it as a shared notepad. They left an estimated 18,000 posts. Researchers later recovered 14,591 revisions.</p>
        </header>
        <ol className="timeline" ref={line}>
          {BEATS.map(([date, text]) => (
            <li key={date} data-beat>
              <span className="date">{date}</span>
              <p>{text}</p>
            </li>
          ))}
        </ol>
      </div>

      <div className="question-wrap" data-reveal>
        <p className="question">Is this agent doing something it isn't supposed to?</p>
        <p className="lede">Every one of those posts began as a call an agent made through its own tools. Teams already track traces, cost, latency and errors. Nothing in that stack checks a run against the rules. At DSEWiki, a volunteer moderator and four outside researchers got there first.</p>
      </div>

      <div className="dash-wrap" data-reveal>
        <div className="dash glass" role="img" aria-label="A typical agent dashboard. Trace, cost, latency and errors are filled in. The policy column says no check.">
          <div className="dash-row head"><span>trace</span><span>cost</span><span>latency</span><span>errors</span><span>policy</span></div>
          {[0, 1, 2].map((row) => (
            <div className="dash-row" key={row} style={{ ["--r" as string]: row } as CSSProperties}>
              <span className="sk" /><span className="sk" /><span className="sk" /><span className="sk" /><span className="pol">no check</span>
            </div>
          ))}
        </div>
        <Note arrow="up-left" className="dash-note" delay={400}>nobody fills this column</Note>
      </div>

      <p className="src">Sources: <a href="https://collusion.wiki">collusion.wiki</a> (Von Arx, Slade Byrd, Kitts, Larsen, 4 September 2026); <a href="https://swarm-ai-research.github.io/wiki-agent-swarm-incident">swarm-ai-research.github.io/wiki-agent-swarm-incident</a>; <a href="https://arxiv.org/abs/2609.12748">arXiv 2609.12748</a>.</p>
    </section>
  );
}

export function Decide({ replay, onStep }: { replay: ReplayFile; onStep: (step: string | null) => void }) {
  const ref = useRef<HTMLElement>(null);
  const [active, setActive] = useState<string | null>(null);
  const claim = textOf(replay, "research_agent_1", "case.findings", "C1", "claim");
  const upheld = textOf(replay, "research_agent_1", "case.verdict", "C1", "reason");
  const downgraded = textOf(replay, "research_agent_3", "case.verdict", "C3", "reason");

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const ratios = new Map<string, number>();
    const panels = [...root.querySelectorAll<HTMLElement>("[data-step]")];
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const step = entry.target.getAttribute("data-step");
        if (step) ratios.set(step, entry.isIntersecting ? entry.intersectionRatio : 0);
      }
      let best: string | null = null;
      let score = 0.45;
      for (const [step, ratio] of ratios) {
        if (ratio > score) {
          score = ratio;
          best = step;
        }
      }
      setActive(best);
      onStep(best);
    }, { threshold: [0.35, 0.55, 0.75] });
    panels.forEach((panel) => io.observe(panel));
    return () => {
      io.disconnect();
      onStep(null);
    };
  }, [onStep]);

  const panel = (step: string) => `panel glass spot${active === step ? " is-active" : ""}`;
  const pick = (step: string) => ({
    onMouseEnter: () => {
      setActive(step);
      onStep(step);
    },
    onFocus: () => {
      setActive(step);
      onStep(step);
    },
  });

  return (
    <section className="sec" id="decide" ref={ref}>
      <header className="sec-head" data-reveal>
        <p className="kicker">How it decides</p>
        <h2>Rules catch it. Another model family has to agree.</h2>
        <p className="lede">Hover a panel, or scroll to it, to light the matching step in the case above.</p>
      </header>
      <div className="panels">
        <article className={panel("flagged")} data-step="flagged" data-reveal {...pick("flagged")}>
          <span className="num" aria-hidden="true">1</span>
          <p className="label">Rules</p>
          <h3>The policy is the check.</h3>
          <pre>{`agent: research_agent
tools: [fetch, submit_answer, notes_read, notes_write]
destinations:
  fetch: ["127.0.0.1:8101", "127.0.0.1:8103"]
  notes_write: []`}</pre>
          <p>An empty destination list means the tool may not be called at all. <code>notes_write</code> on the notes board is C1, an unapproved destination, the moment the trace is read.</p>
        </article>
        <article className={panel("investigating")} data-step="investigating" data-reveal {...pick("investigating")} style={{ ["--d" as string]: "90ms" } as CSSProperties}>
          <span className="num" aria-hidden="true">2</span>
          <p className="label">Investigator</p>
          <h3>One model explains the span.</h3>
          <p className="quoteish">{claim}</p>
        </article>
        <article className={panel("verified")} data-step="verified" data-reveal {...pick("verified")} style={{ ["--d" as string]: "180ms" } as CSSProperties}>
          <span className="num" aria-hidden="true">3</span>
          <p className="label">Verifier</p>
          <h3>A different model family checks the claim.</h3>
          <p><span className="chip upheld">upheld</span> {upheld}</p>
          <p><span className="chip downgraded">downgraded</span> On research agent 3 the investigator also claimed C3. The verifier did not let it stand. {downgraded}</p>
        </article>
      </div>
      <div className="boundary glass" data-reveal>
        <p>A fix counts only when three things hold, and the watchdog never reads the answer key.</p>
        <ul className="ticks">
          <li><Check />The ground truth says the bad behavior is gone.</li>
          <li><Check />A clean control run still passes.</li>
          <li><Check />The watchdog's own view of the re-runs agrees.</li>
        </ul>
      </div>
    </section>
  );
}

export function Built() {
  return (
    <section className="sec" id="stack">
      <header className="sec-head" data-reveal>
        <p className="kicker">Built with</p>
        <h2>Three tools, one artifact each.</h2>
      </header>
      <div className="bento">
        <article className="tile glass spot" data-reveal>
          <p className="label">Traces</p>
          <h3>neatlogs</h3>
          <p>Traces come in. Verdicts go back as tags, a renamed trace, and feedback on the evidence span.</p>
          <a className="link-arrow" href="https://app.neatlogs.com/traces/15a494c98ebff17ddbd18fb332a231c9">Research agent 1, the 11 Oct trace <span aria-hidden="true">→</span></a>
        </article>
        <article className="tile glass spot" data-reveal style={{ ["--d" as string]: "90ms" } as CSSProperties}>
          <p className="label">Provenance</p>
          <h3>Entire</h3>
          <p>Checkpoints record which agent wrote the guardrail, and Brain keeps the incident for the next case.</p>
          <pre>[AI] by OpenCode · zai.glm-5 · checkpoint 01M4MXVFHEZ9B2H27676DDDATN · session ses_ed62 · commit 4addfabd</pre>
        </article>
        <article className="tile glass spot" data-reveal style={{ ["--d" as string]: "180ms" } as CSSProperties}>
          <p className="label">Plan</p>
          <h3>cfo.ai</h3>
          <p>The plan, priced from the unit costs measured in this repo.</p>
          <div className="plan">
            <div><b>$39,089</b><span>MRR in December 2027</span></div>
            <div><b>88.4%</b><span>gross margin</span></div>
          </div>
          <a className="link-arrow" href="https://github.com/abhilash2429/BladeRunner/blob/main/docs/business-plan.md">Business plan <span aria-hidden="true">→</span></a>
        </article>
      </div>
    </section>
  );
}
