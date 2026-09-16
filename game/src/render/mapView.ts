// عارض الخريطة: رسم المقاطعات والجيوش والمدن على Canvas + تحريك/تقريب.

import type { TerrainType } from '../sim/types';
import type { Game } from '../sim/game';

const TERRAIN_COLORS: Record<TerrainType, string> = {
  plains: '#c9b477',
  forest: '#66804a',
  hills: '#b28f5e',
  mountains: '#8f8779',
  desert: '#e3c78d',
  water: '#42708f',
};

function fmtCount(n: number): string {
  n = Math.round(n);
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
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

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D غير مدعوم');
    this.ctx = ctx;
    this.attach();
  }

  setWorld(w: number, h: number): void {
    this.W = w;
    this.H = h;
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
      this.drag = { on: true, sx: e.clientX, sy: e.clientY, ox: this.ox, oy: this.oy, moved: false };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener('pointermove', (e) => {
      if (!this.drag.on) return;
      const dx = e.clientX - this.drag.sx;
      const dy = e.clientY - this.drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 6) this.drag.moved = true;
      this.ox = this.drag.ox + dx;
      this.oy = this.drag.oy + dy;
    });
    c.addEventListener('pointerup', (e) => {
      this.drag.on = false;
      if (this.drag.moved || !this.onTap) return;
      const r = c.getBoundingClientRect();
      const cell = this.screenToCell(e.clientX - r.left, e.clientY - r.top);
      if (cell) this.onTap(cell);
    });
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

  render(g: Game): void {
    const { ctx, scale: s, ox, oy } = this;
    ctx.fillStyle = '#211a13';
    ctx.fillRect(0, 0, this.cw, this.ch);

    const W = g.width;
    const H = g.height;

    // الخلايا + لون المالك
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const prov = g.provinces[g.cellProvince[i]];
        const px = x * s + ox;
        const py = y * s + oy;
        ctx.fillStyle = TERRAIN_COLORS[g.cellTerrain[i]];
        ctx.fillRect(px, py, s + 0.6, s + 0.6);
        if (prov.ownerId >= 0) {
          ctx.globalAlpha = 0.32;
          ctx.fillStyle = g.nation(prov.ownerId).color;
          ctx.fillRect(px, py, s + 0.6, s + 0.6);
          ctx.globalAlpha = 1;
        }
      }
    }

    // حدود المقاطعات
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(43,29,18,0.65)';
    ctx.beginPath();
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const p = g.cellProvince[i];
        if (x + 1 < W && g.cellProvince[i + 1] !== p) {
          const px = (x + 1) * s + ox;
          ctx.moveTo(px, y * s + oy);
          ctx.lineTo(px, (y + 1) * s + oy);
        }
        if (y + 1 < H && g.cellProvince[i + W] !== p) {
          const py = (y + 1) * s + oy;
          ctx.moveTo(x * s + ox, py);
          ctx.lineTo((x + 1) * s + ox, py);
        }
      }
    }
    ctx.stroke();

    // حدود الدول (أسمك)
    ctx.lineWidth = Math.max(2, s * 0.12);
    ctx.strokeStyle = '#241610';
    ctx.beginPath();
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        const o = g.provinces[g.cellProvince[i]].ownerId;
        if (x + 1 < W && g.provinces[g.cellProvince[i + 1]].ownerId !== o) {
          const px = (x + 1) * s + ox;
          ctx.moveTo(px, y * s + oy);
          ctx.lineTo(px, (y + 1) * s + oy);
        }
        if (y + 1 < H && g.provinces[g.cellProvince[i + W]].ownerId !== o) {
          const py = (y + 1) * s + oy;
          ctx.moveTo(x * s + ox, py);
          ctx.lineTo((x + 1) * s + ox, py);
        }
      }
    }
    ctx.stroke();

    const now = performance.now();

    // التحديد: إطار المقاطعة المحددة
    const sel = g.selection;
    if (sel?.type === 'province') {
      ctx.lineWidth = 3;
      ctx.strokeStyle = '#ffd94d';
      ctx.beginPath();
      for (const c of g.provinces[sel.id].cells) {
        ctx.strokeRect(c.x * s + ox, c.y * s + oy, s + 0.6, s + 0.6);
      }
      ctx.stroke();
    }

    // المدن + أسماء المقاطعات
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const p of g.provinces) {
      if (p.terrain === 'water') continue;
      const cx = (p.center.x + 0.5) * s + ox;
      const cy = (p.center.y + 0.5) * s + oy;
      if (p.city) {
        if (p.city.isCapital) {
          ctx.fillStyle = '#ffd94d';
          ctx.beginPath();
          ctx.arc(cx, cy - s * 0.9, Math.max(4, s * 0.35), 0, Math.PI * 2);
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = '#241610';
          ctx.stroke();
        } else {
          const r = Math.max(3, s * 0.28);
          ctx.fillStyle = '#2e2013';
          ctx.fillRect(cx - r, cy - s * 0.9 - r, r * 2, r * 2);
        }
      }
      if (s > 9) {
        ctx.font = `${Math.max(10, s * 0.75)}px sans-serif`;
        ctx.fillStyle = 'rgba(30,20,10,0.9)';
        ctx.fillText(p.name, cx, cy + s * 0.9);
      }
    }

    // مسار الجيش المحدد
    if (sel?.type === 'army') {
      const a = g.armies.find((x) => x.id === sel.id);
      if (a && a.path.length > 0) {
        ctx.strokeStyle = '#ffd94d';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        const start = g.provinces[a.provinceId].center;
        ctx.moveTo((start.x + 0.5) * s + ox, (start.y + 0.5) * s + oy);
        for (const pid of a.path) {
          const c = g.provinces[pid].center;
          ctx.lineTo((c.x + 0.5) * s + ox, (c.y + 0.5) * s + oy);
        }
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // الجيوش
    const byProv = new Map<number, number>();
    for (const a of g.armies) {
      const n = g.nation(a.nationId);
      const p = g.provinces[a.provinceId];
      const k = byProv.get(a.provinceId) ?? 0;
      byProv.set(a.provinceId, k + 1);
      const off = (k - 0.5) * s * 1.1;
      const cx = (p.center.x + 0.5) * s + ox + (k > 0 ? off : 0);
      const cy = (p.center.y + 0.5) * s + oy - s * 0.6;
      const r = Math.max(9, s * 0.7);

      // حلقة المعركة
      if (g.tick - p.lastBattleTick < 12) {
        ctx.strokeStyle = '#ff3b30';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(cx, cy, r + 4 + 2 * Math.sin(now / 180), 0, Math.PI * 2);
        ctx.stroke();
        ctx.font = `${r + 6}px sans-serif`;
        ctx.fillText('⚔️', cx, cy - r - 8);
      }

      ctx.fillStyle = n.color;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
      const isSel = sel?.type === 'army' && sel.id === a.id;
      ctx.lineWidth = isSel ? 3 : 2;
      ctx.strokeStyle = isSel ? '#ffd94d' : '#ffffff';
      ctx.stroke();
      ctx.font = `bold ${Math.max(10, r * 0.95)}px sans-serif`;
      ctx.fillStyle = '#ffffff';
      ctx.fillText(fmtCount(a.soldiers), cx, cy + 1);

      // شريط المعنويات المصغر
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(cx - r, cy + r + 2, r * 2, 3);
      ctx.fillStyle = a.morale > 50 ? '#4caf50' : a.morale > 25 ? '#ff9800' : '#f44336';
      ctx.fillRect(cx - r, cy + r + 2, (r * 2 * a.morale) / 100, 3);
    }
  }
}
