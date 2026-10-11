import { useEffect, useState } from "react";
import { Dojo, Exam, type DojoFile, type ExamFile } from "./charts";
import { Hero, Mission, type Proof } from "./mission";
import { Built, Decide, Problem } from "./story";
import type { ReplayFile } from "./types";

interface Boot {
  replay: ReplayFile;
  exam: ExamFile;
  dojo: DojoFile;
}

function readLive() {
  const raw = new URLSearchParams(window.location.search).get("live");
  if (!raw) return { origin: null as string | null, error: null as string | null };
  try {
    const url = new URL(raw);
    const hostOk = url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const protoOk = url.protocol === "http:" || url.protocol === "https:";
    if (!hostOk || !protoOk) return { origin: null, error: "Live mode only accepts an address on 127.0.0.1." };
    return { origin: url.origin, error: null };
  } catch {
    return { origin: null, error: "That live address is not a URL." };
  }
}

function proofOf(boot: Boot): Proof {
  const fixed = new Set(
    boot.replay.events
      .filter((event) => event.type === "case.transition" && event.data.to === "fixed")
      .map((event) => event.case_id),
  ).size;
  const claude = boot.dojo.tallies["Claude 3.5 Sonnet"];
  return {
    fixed,
    cases: Object.keys(boot.replay.cases).length,
    precision: boot.exam.bladerunner.precision,
    claudeBefore: claude.v0[1],
    claudeAfter: claude.bladerunner[1],
  };
}

function useReveal(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const pending = () => [...document.querySelectorAll<HTMLElement>("[data-reveal]:not([data-in])")];
    if (document.documentElement.classList.contains("reduce")) {
      pending().forEach((item) => item.setAttribute("data-in", ""));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.setAttribute("data-in", "");
        io.unobserve(entry.target);
      }
    }, { threshold: 0.18, rootMargin: "0px 0px -6% 0px" });
    pending().forEach((item) => io.observe(item));
    const watch = new MutationObserver(() => pending().forEach((item) => io.observe(item)));
    watch.observe(document.body, { childList: true, subtree: true });
    return () => {
      io.disconnect();
      watch.disconnect();
    };
  }, [ready]);
}

function useSpotlight() {
  useEffect(() => {
    const move = (event: PointerEvent) => {
      const card = (event.target as Element | null)?.closest?.<HTMLElement>(".spot");
      if (!card) return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${event.clientX - rect.left}px`);
      card.style.setProperty("--my", `${event.clientY - rect.top}px`);
    };
    document.addEventListener("pointermove", move, { passive: true });
    return () => document.removeEventListener("pointermove", move);
  }, []);
}

function useScrolled() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return scrolled;
}

export function App() {
  const [boot, setBoot] = useState<Boot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const live = readLive();
  const scrolled = useScrolled();
  useReveal(boot != null);
  useSpotlight();

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.documentElement.classList.toggle("reduce", reduced);
    void document.fonts?.load("16px Excalifont");
    Promise.all([
      fetch("./replay/efd78263e6d2.json").then((res) => res.json()),
      fetch("./data/exam.json").then((res) => res.json()),
      fetch("./data/dojo.json").then((res) => res.json()),
    ]).then(([replay, exam, dojo]) => {
      if (!replay?.events || !exam?.v0 || !dojo?.dots) throw new Error("The replay files are missing fields.");
      setBoot({ replay, exam, dojo });
    }).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Could not load the replay.");
    });
  }, []);

  if (error) {
    return <main className="loading"><p>Could not load the Oct 11 replay. {error}</p></main>;
  }
  if (!boot) {
    return (
      <main className="loading">
        <span className="loader" aria-hidden="true" />
        <p className="label">Loading the 11 Oct 2026 run</p>
      </main>
    );
  }

  return (
    <>
      <a className="skip-link" href="#case">Skip to the case</a>
      <header className={`nav${scrolled ? " is-scrolled" : ""}`}>
        <a className="wordmark" href="#top"><i aria-hidden="true" />Bladerunner</a>
        <nav aria-label="Sections">
          <a href="#problem">Problem</a>
          <a href="#case">Case</a>
          <a href="#decide">How it decides</a>
          <a href="#exam">Numbers</a>
          <a href="#dojo">AgentDojo</a>
          <a href="https://github.com/abhilash2429/BladeRunner">GitHub</a>
        </nav>
        <p className={`badge${live.origin ? " live" : ""}`} aria-live="polite">
          <i aria-hidden="true" />
          {live.origin ? "LIVE" : `REPLAY · ${boot.replay.label}`}
        </p>
      </header>
      {live.error && <p className="banner">{live.error} Showing the replay.</p>}
      <main>
        <Hero replay={boot.replay} proof={proofOf(boot)} />
        <Problem />
        <Mission replay={boot.replay} liveOrigin={live.origin} focusStep={focus} />
        <Decide replay={boot.replay} onStep={setFocus} />
        <Exam exam={boot.exam} />
        <Dojo dojo={boot.dojo} />
        <Built />
      </main>
    </>
  );
}
