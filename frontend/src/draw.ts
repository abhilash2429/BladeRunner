import type { AgentState, ViewState } from "./types";

export const SERVICES = [
  { id: "data", name: "Data API", port: ":8101", x: 0.3, y: 0.1 },
  { id: "docs", name: "Docs", port: ":8103", x: 0.6, y: 0.04 },
  { id: "notes", name: "Notes board", port: ":8102", x: 0.86, y: 0.2 },
  { id: "unlisted", name: "Unlisted", port: ":8104", x: 0.8, y: 0.78 },
  { id: "submit", name: "Submit", port: "", x: 0.5, y: 0.92 },
] as const;

const HOMES: [number, number][] = [
  [0.42, 0.4],
  [0.64, 0.5],
  [0.48, 0.64],
];

const CAGE = { x0: 0.0, y0: 0.36, x1: 0.24, y1: 0.9 };
const CAGE_SLOTS = [0.48, 0.63, 0.78];

const TRIP_MS = 1800;
const FADE_MS = 600;
const INK = "#0b0b0c";
const MUTED = "#8a8a8f";
const YELLOW = "#ffd400";
const HAND = "'Excalifont', 'Comic Sans MS', cursive";
const MONO = "'IBM Plex Mono', ui-monospace, monospace";

function clamp(n: number, a: number, b: number) {
  return Math.min(b, Math.max(a, n));
}

function ease(t: number) {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

function field(w: number, h: number) {
  const padX = Math.max(46, w * 0.08);
  const padTop = Math.max(28, h * 0.08);
  const padBottom = Math.max(28, h * 0.08);
  return { padX, padTop, innerW: Math.max(1, w - padX * 2), innerH: Math.max(1, h - padTop - padBottom) };
}

function pt(nx: number, ny: number, w: number, h: number): [number, number] {
  const box = field(w, h);
  return [box.padX + nx * box.innerW, box.padTop + ny * box.innerH];
}

function nodeSize(w: number) {
  if (w < 520) return { nodeW: 80, nodeH: 38, nameSize: 10, portSize: 9 };
  return { nodeW: Math.min(116, Math.max(96, w * 0.13)), nodeH: 44, nameSize: 11.5, portSize: 10 };
}

function homeOf(agent: AgentState, w: number, h: number): [number, number] {
  const spot = HOMES[(agent.rerun ? agent.rerun.parentIndex : agent.index) % HOMES.length];
  const [x, y] = pt(spot[0], spot[1], w, h);
  if (!agent.rerun) return [x, y];
  const { slot, parentIndex } = agent.rerun;
  const angle = slot * (Math.PI * 2 / 12) + parentIndex;
  const r = 24 + (slot % 3) * 9;
  return [x + Math.cos(angle) * r, y + Math.sin(angle) * r];
}

// Re-run agents are on screen from their run's start until their answer lands.
export function rerunAlpha(agent: AgentState, now: number): number {
  if (!agent.rerun) return 1;
  const { spawnAt, doneAt } = agent.rerun;
  if (now < spawnAt) return 0;
  const fadeIn = clamp((now - spawnAt) / FADE_MS, 0, 1);
  if (doneAt == null) return fadeIn;
  return Math.min(fadeIn, clamp(1 - (now - doneAt - TRIP_MS) / FADE_MS, 0, 1));
}

function cageOf(index: number, w: number, h: number): [number, number] {
  return pt(0.12, CAGE_SLOTS[index % CAGE_SLOTS.length], w, h);
}

function servicePoint(id: string, w: number, h: number): [number, number] | null {
  const service = SERVICES.find((item) => item.id === id);
  return service ? pt(service.x, service.y, w, h) : null;
}

function along(u: number) {
  if (u < 0.42) return ease(u / 0.42);
  if (u < 0.58) return 1;
  return 1 - ease((u - 0.58) / 0.42);
}

export function agentPoint(agent: AgentState, w: number, h: number, now: number, reduced: boolean) {
  const [hx, hy] = homeOf(agent, w, h);
  const driftTime = agent.frozenAt != null && agent.releasedAt == null ? agent.frozenAt : now;
  const wob = reduced ? 0 : 1;
  const dx = Math.sin(driftTime / 1000 * 0.85 + agent.index) * 7 * wob;
  const dy = Math.cos(driftTime / 1000 * 0.62 + agent.index * 1.4) * 5 * wob;
  let x = hx + dx;
  let y = hy + dy;

  const trip = [...agent.trips].reverse().find((item) => now >= item.at && now <= item.at + TRIP_MS);
  const traveling = trip && (agent.frozenAt == null || now < agent.frozenAt) && (agent.cagedAt == null || now < agent.cagedAt);
  if (traveling && trip) {
    const dest = servicePoint(trip.service, w, h);
    if (dest) {
      const u = along((now - trip.at) / TRIP_MS);
      x = hx + (dest[0] - hx) * u;
      y = hy + (dest[1] - hy) * u;
    }
  }

  const [cx, cy] = cageOf(agent.index, w, h);
  if (agent.releasedAt != null && now >= agent.releasedAt) {
    const t = reduced ? 1 : clamp((now - agent.releasedAt) / 700, 0, 1);
    x = cx + (hx + dx - cx) * ease(t);
    y = cy + (hy + dy - cy) * ease(t);
  } else if (agent.cagedAt != null && now >= agent.cagedAt) {
    const t = reduced ? 1 : clamp((now - agent.cagedAt) / 700, 0, 1);
    x = x + (cx - x) * ease(t);
    y = y + (cy - y) * ease(t);
  }

  if (agent.blockedAt != null && now >= agent.blockedAt && now < agent.blockedAt + 900) {
    const notes = servicePoint("notes", w, h);
    if (notes) {
      const u = (now - agent.blockedAt) / 900;
      const bounce = Math.sin(u * Math.PI);
      x = notes[0] - 46 - bounce * 18;
      y = notes[1] + (agent.rerun ? (agent.rerun.slot % 5) - 2 : agent.index - 1) * (agent.rerun ? 8 : 16);
    }
  }

  return { x, y, hx, hy };
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function shortSha(sha: string) {
  return sha.slice(0, 7);
}

type Box = { x: number; y: number; w: number; h: number };

export function drawSwarm(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  state: ViewState,
  now: number,
  reduced: boolean,
  selected: string | null,
) {
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, w, h);

  ctx.fillStyle = "rgba(10, 10, 12, 0.09)";
  for (let x = 12; x < w; x += 24) {
    for (let y = 12; y < h; y += 24) ctx.fillRect(x, y, 1.2, 1.2);
  }

  const scanX = reduced ? w * 0.42 : ((now / 20) % (w + 120)) - 60;
  const wash = ctx.createLinearGradient(scanX - 70, 0, scanX, 0);
  wash.addColorStop(0, "rgba(255, 212, 0, 0)");
  wash.addColorStop(1, "rgba(255, 212, 0, 0.22)");
  ctx.fillStyle = wash;
  ctx.fillRect(scanX - 70, 0, 70, h);
  const edge = ctx.createLinearGradient(0, 0, 0, h);
  edge.addColorStop(0, "rgba(11, 11, 12, 0)");
  edge.addColorStop(0.5, "rgba(11, 11, 12, 0.55)");
  edge.addColorStop(1, "rgba(11, 11, 12, 0)");
  ctx.fillStyle = edge;
  ctx.fillRect(scanX, 0, 1, h);

  const agents = Object.values(state.agents).filter((agent) => rerunAlpha(agent, now) > 0);
  const size = nodeSize(w);
  const nodeBoxes: Box[] = SERVICES.map((service) => {
    const [x, y] = pt(service.x, service.y, w, h);
    return { x: x - size.nodeW / 2, y: y - size.nodeH / 2, w: size.nodeW, h: size.nodeH };
  });

  const anyCaged = agents.some((agent) => agent.cagedAt != null && (agent.releasedAt == null || now < agent.releasedAt + 700));
  if (anyCaged) {
    const [x0, y0] = pt(CAGE.x0, CAGE.y0, w, h);
    const [x1, y1] = pt(CAGE.x1, CAGE.y1, w, h);
    ctx.save();
    ctx.setLineDash([4, 5]);
    ctx.strokeStyle = "rgba(11, 11, 12, 0.35)";
    ctx.lineWidth = 1.2;
    roundRect(ctx, x0, y0, x1 - x0, y1 - y0, 14);
    ctx.stroke();
    ctx.restore();
    ctx.font = `16px ${HAND}`;
    ctx.fillStyle = INK;
    ctx.fillText("quarantine", x0 + 4, y0 - 8);
  }

  const notes = servicePoint("notes", w, h);
  for (const agent of agents) {
    if (agent.redAt == null || !notes) continue;
    const from = agentPoint(agent, w, h, now, reduced);
    const u = reduced ? 1 : clamp((now - agent.redAt) / 520, 0, 1);
    const gone = agent.releasedAt != null && now > agent.releasedAt;
    const ex = from.x + (notes[0] - from.x) * u;
    const ey = from.y + (notes[1] - from.y) * u;
    const mx = (from.x + ex) / 2;
    const my = (from.y + ey) / 2 - 18;
    ctx.strokeStyle = gone ? "rgba(11,11,12,0.16)" : "rgba(229, 72, 77, 0.85)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.quadraticCurveTo(mx, my, ex, ey);
    ctx.stroke();
  }

  const docsHot = state.plantedPulseAt != null && now - state.plantedPulseAt < 1400 && now >= state.plantedPulseAt;
  for (const service of SERVICES) {
    const [x, y] = pt(service.x, service.y, w, h);
    const hot = service.id === "docs" && docsHot;
    const left = x - size.nodeW / 2;
    const top = y - size.nodeH / 2;
    ctx.save();
    ctx.shadowColor = "rgba(11, 11, 12, 0.10)";
    ctx.shadowBlur = 18;
    ctx.shadowOffsetY = 6;
    ctx.fillStyle = hot ? "#fff6c7" : "#fff";
    roundRect(ctx, left, top, size.nodeW, size.nodeH, 10);
    ctx.fill();
    ctx.restore();
    ctx.strokeStyle = "rgba(11, 11, 12, 0.12)";
    ctx.lineWidth = 1;
    roundRect(ctx, left + 0.5, top + 0.5, size.nodeW - 1, size.nodeH - 1, 10);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(left + 11, top + size.nodeH / 2 - (service.port ? 6 : 0), 2.6, 0, Math.PI * 2);
    ctx.fillStyle = hot ? YELLOW : service.id === "notes" && state.barrier ? INK : "#d4d4d8";
    ctx.fill();

    ctx.fillStyle = INK;
    ctx.font = `500 ${size.nameSize}px ${MONO}`;
    ctx.textAlign = "left";
    ctx.fillText(service.name, left + 19, y - (service.port ? 2 : -4));
    if (service.port) {
      ctx.font = `400 ${size.portSize}px ${MONO}`;
      ctx.fillStyle = MUTED;
      ctx.fillText(service.port, left + 19, y + 12);
    }
  }

  if (state.barrier && notes) {
    const u = reduced ? 1 : clamp((now - state.barrier.at) / 500, 0, 1);
    const bw = (size.nodeW + 18) * u;
    const bh = (size.nodeH + 18) * u;
    ctx.strokeStyle = INK;
    ctx.lineWidth = 1.5;
    roundRect(ctx, notes[0] - bw / 2, notes[1] - bh / 2, bw, bh, 14 * u);
    ctx.stroke();
    if (u > 0.8) {
      ctx.font = `500 10px ${MONO}`;
      const label = `guardrail ${shortSha(state.barrier.sha)}`;
      const tw = ctx.measureText(label).width;
      const lx = notes[0] - tw / 2 - 7;
      const ly = notes[1] + bh / 2 + 6;
      ctx.fillStyle = INK;
      roundRect(ctx, lx, ly, tw + 14, 18, 9);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.fillText(label, lx + 7, ly + 12.5);
    }
  }

  for (const agent of agents) {
    const { x, y } = agentPoint(agent, w, h, now, reduced);
    if (agent.rerun) {
      ctx.save();
      ctx.globalAlpha = rerunAlpha(agent, now);
      ctx.beginPath();
      ctx.arc(x, y, selected === agent.id ? 4.5 : 3, 0, Math.PI * 2);
      if (agent.rerun.kind === "control") {
        ctx.strokeStyle = INK;
        ctx.lineWidth = 1.2;
        ctx.stroke();
      } else {
        ctx.fillStyle = "rgba(11, 11, 12, 0.62)";
        ctx.fill();
      }
      ctx.restore();
      continue;
    }
    const released = agent.releasedAt != null && now >= agent.releasedAt;
    if (agent.ringAt != null && !released) {
      const age = now - agent.ringAt;
      const pulse = age >= 0 && age < 900 ? age / 900 : 1;
      if (pulse < 1) {
        ctx.beginPath();
        ctx.arc(x, y, 10 + pulse * 18, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255, 212, 0, ${1 - pulse})`;
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(x, y, 11, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 212, 0, 0.28)";
      ctx.fill();
      ctx.strokeStyle = YELLOW;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    const radius = selected === agent.id ? 7 : 5;
    ctx.save();
    ctx.shadowColor = "rgba(11, 11, 12, 0.25)";
    ctx.shadowBlur = 8;
    ctx.shadowOffsetY = 2;
    ctx.beginPath();
    ctx.arc(x, y, radius + 2, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = released ? "#b4b4b9" : INK;
    ctx.fill();

    const label = agent.id.replace("research_", "").replaceAll("_", " ");
    ctx.font = `16px ${HAND}`;
    paintLabel(ctx, label, x, y, w, h, nodeBoxes, released);
  }
  ctx.textAlign = "left";
}

function paintLabel(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, w: number, h: number, obstacles: Box[], muted: boolean) {
  const tw = ctx.measureText(text).width;
  const th = 16;
  const options = [
    { lx: x + 13, ly: y - 8 },
    { lx: x - tw - 13, ly: y - 8 },
    { lx: x - tw / 2, ly: y - 26 },
    { lx: x - tw / 2, ly: y + 10 },
  ];
  const hits = (lx: number, ly: number) => {
    const rect = { x: lx - 2, y: ly - 1, w: tw + 4, h: th };
    if (rect.x < 4 || rect.y < 4 || rect.x + rect.w > w - 4 || rect.y + rect.h > h - 4) return true;
    return obstacles.some((box) => !(rect.x + rect.w < box.x || box.x + box.w < rect.x || rect.y + rect.h < box.y || box.y + box.h < rect.y));
  };
  const spot = options.find((option) => !hits(option.lx, option.ly)) ?? options[0];
  ctx.fillStyle = muted ? MUTED : INK;
  ctx.fillText(text, spot.lx, spot.ly + 12);
}

export function hitAgent(state: ViewState, w: number, h: number, x: number, y: number, now: number, reduced: boolean) {
  let found: string | null = null;
  for (const agent of Object.values(state.agents)) {
    if (rerunAlpha(agent, now) === 0) continue;
    const point = agentPoint(agent, w, h, now, reduced);
    if (Math.hypot(point.x - x, point.y - y) < 16) found = agent.id;
  }
  return found;
}