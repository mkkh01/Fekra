// عارض الخريطة: طبقات أرضية منقوشة، غسل ألوان الدول بحواف ناعمة، جيوش متحركة
// بمؤثرات معركة حية (شرر/دخان/موجات صدمة)، وحدود مرسومة يدويًا — كل ذلك على Canvas.

import type { TerrainType } from '../sim/types';
import type { Game } from '../sim/game';

const TERRAIN_BASE: Record<TerrainType, [number, number, number]> = {
  plains: [203, 182, 121],
  forest: [88, 118, 68],
  hills: [176, 141, 92],
  mountains: [146, 138, 124],
  desert: [228, 200, 142],
  water: [58, 102, 134],
};

const PX = 4; // دقة النقش لكل خلية في طبقة الأرض

function fmtCount(n: number): string {
  n = Math.round(n);
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
}

/** ضوضاء ثابتة حتمية 0..1 — تُنتج نقوشًا لا ترمش بين الإطارات */
function hash(n: number): number {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}

function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return [200, 200, 200];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

function rgbStr(c: [number, number, number], alpha = 1): string {
  const k = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  return `rgba(${k(c[0])},${k(c[1])},${k(c[2])},${alpha})`;
}

function shade(c: [number, number, number], f: number): [number, number, number] {
  return [c[0] * f, c[1] * f, c[2] * f];
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export class MapView {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private W = 1;
  private H = 1;
  private cw = 1;
  private ch = 1;
  private scale = 10;
  private ox = 0;
  private oy = 0;
  private minScale = 5;
  onTap: ((cell: { x: number; y: number }) => void) | null = null;
  private drag = { on: false, sx: 0, sy: 0, ox: 0, oy: 0, moved: false };
  private pointers = new Map<number, { x: number; y: number }>();
  private pinch: { dist: number; scale: number; mx: number; my: number; ox: number; oy: number } | null = null;
  private terrainCache: HTMLCanvasElement | null = null;
  private terrainSrc: unknown = null;
  private wash: HTMLCanvasElement;
  private washCtx: CanvasRenderingContext2D;
  private vignette: CanvasGradient | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D غير مدعوم');
    this.ctx = ctx;
    this.wash = document.createElement('canvas');
    const wc = this.wash.getContext('2d');
    if (!wc) throw new Error('Canvas 2D غير مدعوم');
    this.washCtx = wc;
    this.attach();
  }

  setWorld(w: number, h: number): void {
    this.W = w;
    this.H = h;
    this.wash.width = w;
    this.wash.height = h;
    this.terrainCache = null;
    this.fit();
  }

  private resizeCanvas(): void {
    const p = this.canvas.parentElement!;
    const r = p.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.cw = Math.max(1, r.width);
    this.ch = Math.max(1, r.height);
    this.canvas.width = Math.max(1, Math.floor(r.width * dpr));
    this.canvas.height = Math.max(1, Math.floor(r.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.vignette = null;
  }

  fit(): void {
    this.resizeCanvas();
    this.minScale = Math.min(this.cw / this.W, this.ch / this.H);
    this.scale = this.minScale;
    this.ox = (this.cw - this.W * this.scale) / 2;
    this.oy = (this.ch - this.H * this.scale) / 2;
  }

  centerOnCell(x: number, y: number): void {
    this.ox = this.cw / 2 - x * this.scale;
    this.oy = this.ch / 2 - y * this.scale;
  }

  screenToCell(sx: number, sy: number): { x: number; y: number } | null {
    const x = Math.floor((sx - this.ox) / this.scale);
    const y = Math.floor((sy - this.oy) / this.scale);
    if (x < 0 || y < 0 || x >= this.W || y >= this.H) return null;
    return { x, y };
  }

  private attach(): void {
    const c = this.canvas;
    c.addEventListener('pointerdown', (e) => {
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        const r = c.getBoundingClientRect();
        const dist = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        this.pinch = {
          dist,
          scale: this.scale,
          mx: (a.x + b.x) / 2 - r.left,
          my: (a.y + b.y) / 2 - r.top,
          ox: this.ox,
          oy: this.oy,
        };
        this.drag.on = false;
      } else if (this.pointers.size === 1) {
        this.drag = { on: true, sx: e.clientX, sy: e.clientY, ox: this.ox, oy: this.oy, moved: false };
        c.setPointerCapture(e.pointerId);
      }
    });
    c.addEventListener('pointermove', (e) => {
      if (!this.pointers.has(e.pointerId)) return;
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.pinch && this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        const dist = Math.hypot(b.x - a.x, b.y - a.y) || 1;
        const ns = Math.min(72, Math.max(this.minScale * 0.6, (this.pinch.scale * dist) / this.pinch.dist));
        const wx = (this.pinch.mx - this.pinch.ox) / this.pinch.scale;
        const wy = (this.pinch.my - this.pinch.oy) / this.pinch.scale;
        this.scale = ns;
        this.ox = this.pinch.mx - wx * ns;
        this.oy = this.pinch.my - wy * ns;
        this.drag.moved = true;
        return;
      }
      if (!this.drag.on) return;
      const dx = e.clientX - this.drag.sx;
      const dy = e.clientY - this.drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 6) this.drag.moved = true;
      this.ox = this.drag.ox + dx;
      this.oy = this.drag.oy + dy;
    });
    const up = (e: PointerEvent) => {
      this.pointers.delete(e.pointerId);
      if (this.pointers.size < 2) this.pinch = null;
      this.drag.on = false;
      if (this.drag.moved || !this.onTap || this.pointers.size > 0) return;
      const r = c.getBoundingClientRect();
      const cell = this.screenToCell(e.clientX - r.left, e.clientY - r.top);
      if (cell) this.onTap(cell);
    };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        const r = c.getBoundingClientRect();
        const sx = e.clientX - r.left;
        const sy = e.clientY - r.top;
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        const ns = Math.min(72, Math.max(this.minScale * 0.6, this.scale * factor));
        const wx = (sx - this.ox) / this.scale;
        const wy = (sy - this.oy) / this.scale;
        this.scale = ns;
        this.ox = sx - wx * ns;
        this.oy = sy - wy * ns;
      },
      { passive: false },
    );
    new ResizeObserver(() => this.fit()).observe(c.parentElement!);
  }

  // ===== طبقة الأرض: تُبنى مرة واحدة لكل عالم (نقوش ثابتة) =====
  private buildTerrain(g: Game): HTMLCanvasElement {
    const c = document.createElement('canvas');
    c.width = this.W * PX;
    c.height = this.H * PX;
    const x2 = c.getContext('2d')!;
    const { W, H } = this;
    const isWaterAt = (x: number, y: number) =>
      x < 0 || y < 0 || x >= W || y >= H || g.cellTerrain[y * W + x] === 'water';
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const t = g.cellTerrain[i] as TerrainType;
        const base = TERRAIN_BASE[t];
        const px = x * PX;
        const py = y * PX;
        let waterNeighbors = 0;
        for (let dy = -1; dy <= 1; dy++)
          for (let dx = -1; dx <= 1; dx++)
            if ((dx || dy) && isWaterAt(x + dx, y + dy)) waterNeighbors++;
        for (let u = 0; u < PX; u++) {
          for (let v = 0; v < PX; v++) {
            const n = hash(i * 17 + u * 3.1 + v * 7.7);
            let col: [number, number, number] = base;
            let k = 0.92 + n * 0.16;
            if (t === 'water') {
              const depth = Math.min(1, (waterNeighbors / 5) * 0.9 + n * 0.12);
              col = [lerp(96, 36, depth), lerp(150, 66, depth), lerp(178, 96, depth)];
              if (n > 0.93 && depth < 0.55) k = 1.5; // بريق موج
            } else if (t === 'forest') {
              k = 0.8 + n * 0.34;
              if (n > 0.72) col = [60 + n * 26, 96 + n * 20, 48 + n * 14]; // ظلال تيجان
            } else if (t === 'hills') {
              k = v > u ? 0.78 : 1.14; // إضاءة شمالية غربية
            } else if (t === 'mountains') {
              if (v === 0) col = [228, 226, 222]; // قمم مضيئة
              else k = v > u ? 0.72 : 1.06;
            } else if (t === 'desert') {
              k = (u + v) % 3 === 0 ? 1.08 : 0.96; // كثبان مخططة
            } else {
              k = 0.94 + n * 0.12;
              if (n > 0.9) k = 1.12; // أعشاب متناثرة
            }
            x2.fillStyle = rgbStr(shade(col, k));
            x2.fillRect(px + u, py + v, 1, 1);
          }
        }
        // حافة الساحل: رمال مظللة على اليابسة المجاورة للماء
        if (t !== 'water' && waterNeighbors > 0) {
          x2.fillStyle = 'rgba(60,44,26,0.20)';
          x2.fillRect(px, py, PX, PX);
        }
      }
    }
    // زبد البحر على حافة الماء الملاصق لليابسة
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        if (g.cellTerrain[i] !== 'water') continue;
        const px = x * PX;
        const py = y * PX;
        x2.fillStyle = 'rgba(235,245,250,0.45)';
        if (!isWaterAt(x - 1, y)) x2.fillRect(px, py, 1, PX);
        if (!isWaterAt(x + 1, y)) x2.fillRect(px + PX - 1, py, 1, PX);
        if (!isWaterAt(x, y - 1)) x2.fillRect(px, py, PX, 1);
        if (!isWaterAt(x, y + 1)) x2.fillRect(px, py + PX - 1, PX, 1);
      }
    }
    // حدود المقاطعات: خطوط شعر بنية ناعمة
    x2.strokeStyle = 'rgba(58,40,24,0.5)';
    x2.lineWidth = 1;
    x2.beginPath();
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const p = g.cellProvince[i];
        if (x + 1 < W && g.cellProvince[i + 1] !== p) {
          x2.moveTo(x * PX + PX, y * PX);
          x2.lineTo(x * PX + PX, (y + 1) * PX);
        }
        if (y + 1 < H && g.cellProvince[i + W] !== p) {
          x2.moveTo(x * PX, y * PX + PX);
          x2.lineTo((x + 1) * PX, y * PX + PX);
        }
      }
    }
    x2.stroke();
    return c;
  }

  private paintWash(g: Game): void {
    const { W, H } = this;
    const wc = this.washCtx;
    wc.clearRect(0, 0, W, H);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const owner = g.provinces[g.cellProvince[y * W + x]].ownerId;
        if (owner < 0) continue;
        wc.fillStyle = g.nation(owner).color;
        wc.fillRect(x, y, 1, 1);
      }
    }
  }

  render(g: Game): void {
    const { ctx, scale: s, ox, oy } = this;
    const W = g.width;
    const H = g.height;

    // الخلفية الخارجية + إطار ناعم حول الخريطة
    ctx.fillStyle = '#191209';
    ctx.fillRect(0, 0, this.cw, this.ch);
    const mw = W * s;
    const mh = H * s;
    ctx.fillStyle = '#241a10';
    ctx.fillRect(ox - 3, oy - 3, mw + 6, mh + 6);

    if (this.terrainSrc !== g.cellTerrain || !this.terrainCache) {
      this.terrainCache = this.buildTerrain(g);
      this.terrainSrc = g.cellTerrain;
    }
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(this.terrainCache, ox, oy, mw, mh);

    // غسل الدول بحواف ريشية
    this.paintWash(g);
    ctx.globalAlpha = 0.42;
    ctx.drawImage(this.wash, ox, oy, mw, mh);
    ctx.globalAlpha = 1;

    // وميض الاشتباك فوق الخلايا المتحارب بها
    for (const p of g.provinces) {
      const age = g.tick - p.lastBattleTick;
      if (age >= 12) continue;
      const a = 0.10 + 0.09 * Math.sin(performance.now() / 150);
      ctx.fillStyle = `rgba(255,72,48,${a})`;
      for (const c of p.cells) ctx.fillRect(c.x * s + ox, c.y * s + oy, s + 0.5, s + 0.5);
    }

    // حدود الدول: ظل داكن ثم لمسة بلون الدولة على جهة صاحبها
    ctx.lineCap = 'round';
    ctx.lineWidth = Math.max(2, s * 0.16);
    ctx.strokeStyle = '#20130b';
    ctx.beginPath();
    const ownerAt = (i: number) => g.provinces[g.cellProvince[i]].ownerId;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const o = ownerAt(i);
        if (x + 1 < W && ownerAt(i + 1) !== o) {
          ctx.moveTo((x + 1) * s + ox, y * s + oy);
          ctx.lineTo((x + 1) * s + ox, (y + 1) * s + oy);
        }
        if (y + 1 < H && ownerAt(i + W) !== o) {
          ctx.moveTo(x * s + ox, (y + 1) * s + oy);
          ctx.lineTo((x + 1) * s + ox, (y + 1) * s + oy);
        }
      }
    }
    ctx.stroke();
    // لمسة اللون: لكل دولة مسار خاص بحدودها
    const nationEdges = new Map<number, Path2D>();
    const addEdge = (owner: number, x1: number, y1: number, x2: number, y2: number) => {
      if (owner < 0) return;
      let pth = nationEdges.get(owner);
      if (!pth) nationEdges.set(owner, (pth = new Path2D()));
      pth.moveTo(x1, y1);
      pth.lineTo(x2, y2);
    };
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const o = ownerAt(i);
        if (x + 1 < W) {
          const o2 = ownerAt(i + 1);
          if (o2 !== o) {
            const vx = (x + 1) * s + ox;
            addEdge(o, vx, y * s + oy, vx, (y + 1) * s + oy);
            addEdge(o2, vx, y * s + oy, vx, (y + 1) * s + oy);
          }
        }
        if (y + 1 < H) {
          const o2 = ownerAt(i + W);
          if (o2 !== o) {
            const hy = (y + 1) * s + oy;
            addEdge(o, x * s + ox, hy, (x + 1) * s + ox, hy);
            addEdge(o2, x * s + ox, hy, (x + 1) * s + ox, hy);
          }
        }
      }
    }
    ctx.lineWidth = Math.max(1, s * 0.05);
    for (const [nid, pth] of nationEdges) {
      ctx.strokeStyle = rgbStr(shade(hexToRgb(g.nation(nid).color), 1.35), 0.85);
      ctx.stroke(pth);
    }

    // فينيت خفيف حول الخريطة
    if (!this.vignette) {
      const d = Math.hypot(this.cw, this.ch);
      const gr = ctx.createRadialGradient(this.cw / 2, this.ch / 2, d * 0.28, this.cw / 2, this.ch / 2, d * 0.72);
      gr.addColorStop(0, 'rgba(0,0,0,0)');
      gr.addColorStop(1, 'rgba(0,0,0,0.34)');
      this.vignette = gr;
    }
    ctx.fillStyle = this.vignette;
    ctx.fillRect(0, 0, this.cw, this.ch);

    const now = performance.now();
    const sel = g.selection;

    // تحديد المقاطعة: نملة ذهبية متحركة + توهج
    if (sel?.type === 'province') {
      ctx.save();
      ctx.shadowColor = '#ffd94d';
      ctx.shadowBlur = 8;
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = '#ffd94d';
      ctx.setLineDash([7, 5]);
      ctx.lineDashOffset = -now / 30;
      ctx.fillStyle = 'rgba(255,217,77,0.10)';
      for (const c of g.provinces[sel.id].cells) {
        ctx.fillRect(c.x * s + ox, c.y * s + oy, s + 0.5, s + 0.5);
        ctx.strokeRect(c.x * s + ox, c.y * s + oy, s + 0.5, s + 0.5);
      }
      ctx.restore();
    }

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // المدن والأسماء
    for (const p of g.provinces) {
      if (p.terrain === 'water') continue;
      const cx = (p.center.x + 0.5) * s + ox;
      const cy = (p.center.y + 0.5) * s + oy;
      if (p.city) {
        const byy = cy - s * 0.95;
        if (p.city.isCapital) {
          const pr = s * (1.25 + 0.08 * Math.sin(now / 520));
          const gr = ctx.createRadialGradient(cx, byy, s * 0.2, cx, byy, pr);
          gr.addColorStop(0, 'rgba(255,214,110,0.5)');
          gr.addColorStop(1, 'rgba(255,214,110,0)');
          ctx.fillStyle = gr;
          ctx.beginPath();
          ctx.arc(cx, byy, pr, 0, Math.PI * 2);
          ctx.fill();
          const r = Math.max(4, s * 0.34);
          ctx.fillStyle = '#ffd94d';
          ctx.beginPath();
          ctx.arc(cx, byy, r, 0, Math.PI * 2);
          ctx.fill();
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = '#241610';
          ctx.stroke();
          if (s > 6) {
            ctx.font = `${Math.max(9, s * 0.7)}px sans-serif`;
            ctx.fillText('👑', cx, byy - r - s * 0.2);
          }
        } else {
          const r = Math.max(3, s * 0.3);
          ctx.fillStyle = '#efe3c2';
          ctx.fillRect(cx - r, byy - r * 0.6, r * 2, r * 1.4);
          ctx.fillStyle = '#a8452e';
          ctx.beginPath();
          ctx.moveTo(cx - r * 1.2, byy - r * 0.6);
          ctx.lineTo(cx, byy - r * 1.7);
          ctx.lineTo(cx + r * 1.2, byy - r * 0.6);
          ctx.closePath();
          ctx.fill();
          ctx.lineWidth = 1;
          ctx.strokeStyle = '#241610';
          ctx.stroke();
        }
      }
      if (s > 8) {
        const fs = Math.max(10, s * 0.72);
        ctx.font = p.city?.isCapital ? `bold ${fs}px sans-serif` : `${fs}px sans-serif`;
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(26,16,8,0.85)';
        ctx.strokeText(p.name, cx, cy + s * 0.95);
        ctx.fillStyle = p.city?.isCapital ? '#ffe9a8' : 'rgba(244,232,207,0.92)';
        ctx.fillText(p.name, cx, cy + s * 0.95);
      }
    }

    // مسار الجيش المحدد
    if (sel?.type === 'army') {
      const a = g.armies.find((x) => x.id === sel.id);
      if (a && a.path.length > 0) {
        ctx.save();
        ctx.strokeStyle = 'rgba(255,217,77,0.85)';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 5]);
        ctx.lineDashOffset = -now / 40;
        ctx.beginPath();
        const start = g.provinces[a.provinceId].center;
        ctx.moveTo((start.x + 0.5) * s + ox, (start.y + 0.5) * s + oy);
        for (const pid of a.path) {
          const c = g.provinces[pid].center;
          ctx.lineTo((c.x + 0.5) * s + ox, (c.y + 0.5) * s + oy);
        }
        ctx.stroke();
        ctx.restore();
      }
    }

    // ===== الجيوش: مواقع ملساء + رايات + غبار المسير =====
    for (const a of g.armies) {
      const n = g.nation(a.nationId);
      const cur = g.provinces[a.provinceId].center;
      let fx = cur.x;
      let fy = cur.y;
      const moving = a.path.length > 0 && a.progress > 0;
      if (moving) {
        const nxt = g.provinces[a.path[0]].center;
        fx = lerp(cur.x, nxt.x, Math.min(1, a.progress));
        fy = lerp(cur.y, nxt.y, Math.min(1, a.progress));
      }
      const cx = (fx + 0.5) * s + ox;
      const cy = (fy + 0.5) * s + oy - s * 0.55;
      const r = Math.max(8, s * 0.62);

      if (moving) {
        const ang = Math.atan2(fy - cur.y, fx - cur.x);
        for (let d = 1; d <= 3; d++) {
          ctx.fillStyle = `rgba(190,168,120,${0.16 - d * 0.04})`;
          ctx.beginPath();
          ctx.arc(cx - Math.cos(ang) * d * r * 0.55, cy + s * 0.5 - Math.sin(ang) * d * r * 0.2, r * (0.5 - d * 0.1), 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // ظل
      ctx.fillStyle = 'rgba(0,0,0,0.3)';
      ctx.beginPath();
      ctx.ellipse(cx, cy + r * 0.75, r * 0.9, r * 0.35, 0, 0, Math.PI * 2);
      ctx.fill();

      // جسد الدرع بتدرج
      const nrgb = hexToRgb(n.color);
      const gr = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.2, cx, cy, r);
      gr.addColorStop(0, rgbStr(shade(nrgb, 1.45)));
      gr.addColorStop(1, rgbStr(shade(nrgb, 0.75)));
      ctx.fillStyle = gr;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = '#140d07';
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(255,255,255,0.55)';
      ctx.beginPath();
      ctx.arc(cx, cy, r - 1.5, 0, Math.PI * 2);
      ctx.stroke();

      // راية تخفق
      ctx.strokeStyle = '#241610';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(cx + r * 0.8, cy - r * 0.6);
      ctx.lineTo(cx + r * 0.8, cy - r * 2.1);
      ctx.stroke();
      const wav = Math.sin(now / 160 + a.id) * r * 0.12;
      ctx.fillStyle = rgbStr(shade(nrgb, 1.2));
      ctx.beginPath();
      ctx.moveTo(cx + r * 0.8, cy - r * 2.1);
      ctx.lineTo(cx + r * 0.8 - r * 1.1, cy - r * 1.75 + wav);
      ctx.lineTo(cx + r * 0.8, cy - r * 1.3);
      ctx.closePath();
      ctx.fill();

      const isSel = sel?.type === 'army' && sel.id === a.id;
      if (isSel) {
        ctx.save();
        ctx.shadowColor = '#ffd94d';
        ctx.shadowBlur = 10;
        ctx.strokeStyle = '#ffd94d';
        ctx.lineWidth = 2.5;
        ctx.setLineDash([5, 4]);
        ctx.lineDashOffset = now / 40;
        ctx.beginPath();
        ctx.arc(cx, cy, r + 4 + Math.sin(now / 260) * 1.5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      ctx.font = `bold ${Math.max(9, r * 0.95)}px sans-serif`;
      ctx.lineWidth = 3;
      ctx.strokeStyle = 'rgba(15,10,5,0.9)';
      ctx.strokeText(fmtCount(a.soldiers), cx, cy + 1);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(fmtCount(a.soldiers), cx, cy + 1);

      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(cx - r, cy + r + 2, r * 2, 3);
      ctx.fillStyle = a.morale > 50 ? '#4caf50' : a.morale > 25 ? '#ff9800' : '#f44336';
      ctx.fillRect(cx - r, cy + r + 2, (r * 2 * a.morale) / 100, 3);
    }

    // ===== مؤثرات المعركة فوق كل شيء: موجات، شرر، دخان، لهب، سيفان =====
    for (const p of g.provinces) {
      const age = g.tick - p.lastBattleTick;
      if (age >= 12) continue;
      const cx = (p.center.x + 0.5) * s + ox;
      const cy = (p.center.y + 0.5) * s + oy;
      const fading = 1 - age / 12;

      const t1 = (now / 750) % 1;
      const t2 = (now / 750 + 0.5) % 1;
      ctx.lineWidth = 2;
      for (const t of [t1, t2]) {
        ctx.strokeStyle = `rgba(255,70,50,${(1 - t) * 0.5 * fading})`;
        ctx.beginPath();
        ctx.arc(cx, cy, s * (0.6 + t * 2.2), 0, Math.PI * 2);
        ctx.stroke();
      }

      for (let i = 0; i < 10; i++) {
        const ph = hash(p.id * 31 + i * 13.7);
        const t = (now / 600 + ph) % 1;
        const ang = ph * Math.PI * 2 + t * 2;
        const rr = s * (0.5 + t * 1.8);
        ctx.fillStyle = `rgba(${t < 0.5 ? '255,210,120' : '255,120,60'},${(1 - t) * 0.9 * fading})`;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr * 0.7, Math.max(1, s * 0.08 * (1 - t)), 0, Math.PI * 2);
        ctx.fill();
      }

      if (p.city) {
        const gr = ctx.createRadialGradient(cx, cy, 0, cx, cy, s * 1.8);
        gr.addColorStop(0, `rgba(255,140,40,${(0.28 + 0.12 * Math.sin(now / 140)) * fading})`);
        gr.addColorStop(1, 'rgba(255,140,40,0)');
        ctx.fillStyle = gr;
        ctx.beginPath();
        ctx.arc(cx, cy, s * 1.8, 0, Math.PI * 2);
        ctx.fill();
        for (let i = 0; i < 3; i++) {
          const t = (now / 1400 + i / 3 + hash(p.id + i)) % 1;
          ctx.fillStyle = `rgba(70,62,52,${(1 - t) * 0.35 * fading})`;
          ctx.beginPath();
          ctx.arc(cx + (hash(p.id * 7 + i) - 0.5) * s, cy - t * s * 2.6, s * (0.35 + t * 0.7), 0, Math.PI * 2);
          ctx.fill();
        }
      }

      ctx.save();
      ctx.translate(cx, cy - s * 1.7);
      ctx.rotate(Math.sin(now / 260) * 0.14);
      ctx.font = `${Math.max(14, s * 1.4)}px sans-serif`;
      ctx.globalAlpha = fading;
      ctx.fillText('⚔️', 0, 0);
      ctx.restore();
      ctx.globalAlpha = 1;
    }
  }
}
