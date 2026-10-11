import { useEffect, useRef } from "react";
import { drawSwarm, hitAgent } from "./draw";
import type { ViewState } from "./types";

export function useReducedMotion() {
  const ref = useRef(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  return ref.current;
}

export function SwarmView({
  read,
  selected = null,
  onPick,
}: {
  read: () => { state: ViewState; now: number };
  selected?: string | null;
  onPick?: (id: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const readRef = useRef(read);
  const selectedRef = useRef(selected);
  const pickRef = useRef(onPick);
  readRef.current = read;
  selectedRef.current = selected;
  pickRef.current = onPick;
  const reduced = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let on = true;
    const frame = () => {
      raf = 0;
      if (!on) return;
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
      const scene = readRef.current();
      drawSwarm(ctx, w, h, scene.state, scene.now, reduced, selectedRef.current);
      raf = requestAnimationFrame(frame);
    };
    const start = () => {
      if (!raf) raf = requestAnimationFrame(frame);
    };
    const io = new IntersectionObserver(([entry]) => {
      on = entry.isIntersecting;
      if (on) start();
    }, { threshold: 0.05 });
    io.observe(canvas);
    start();
    const onClick = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const scene = readRef.current();
      const id = hitAgent(
        scene.state,
        rect.width,
        rect.height,
        event.clientX - rect.left,
        event.clientY - rect.top,
        scene.now,
        reduced,
      );
      if (id) pickRef.current?.(id);
    };
    canvas.addEventListener("click", onClick);
    return () => {
      on = false;
      if (raf) cancelAnimationFrame(raf);
      io.disconnect();
      canvas.removeEventListener("click", onClick);
    };
  }, [reduced]);

  return <canvas ref={canvasRef} aria-hidden="true" />;
}
