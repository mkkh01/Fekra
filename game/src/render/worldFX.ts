// مشتقات بصرية للعالم: حقل ارتفاع + ظلال إضاءة (hillshade) + شبكة أنهار.
// كل شيء حتمي (deterministic) مشتق من التضاريس — لا يغيّر المحاكاة إطلاقًا.

import type { TerrainType } from '../sim/types';

export interface WorldFX {
  /** إضاءة شمالية-غربية لكل خلية: 1 = محايد، <1 ظل، >1 ضوء */
  shade: Float32Array;
  /** سعة النهر لكل خلية: 0 = لا نهر، ..1 = مصب عريض */
  river: Float32Array;
  /** خط كنتور ناعم فوق المرتفعات: 1 = ارسم */
  contour: Uint8Array;
}

const ELEV_BASE: Record<TerrainType, number> = {
  water: 0.02,
  desert: 0.24,
  plains: 0.34,
  forest: 0.4,
  hills: 0.64,
  mountains: 0.96,
};

function hash2(x: number, y: number): number {
  const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

export function buildWorldFX(
  W: number,
  H: number,
  terrain: TerrainType[],
): WorldFX {
  // 1) حقل ارتفاع خام = أساس التضاريس + ضوضاء متموجة
  let elev = new Float32Array(W * H);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const t = terrain[y * W + x];
      const n =
        hash2(x * 0.31, y * 0.31) * 0.1 +
        hash2(x * 0.09, y * 0.09) * 0.16;
      elev[y * W + x] = ELEV_BASE[t] + (t === 'water' ? 0 : n);
    }
  }
  // تنعيم صندوقي 3 مرات — تضاريس ناعمة لا ضجيج
  for (let pass = 0; pass < 3; pass++) {
    const out = new Float32Array(W * H);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        let sum = 0;
        let c = 0;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = x + dx;
            const ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
            sum += elev[ny * W + nx];
            c++;
          }
        }
        out[y * W + x] = sum / c;
      }
    }
    elev = out;
  }
  // المياه تظل منخفضة حتمًا بعد التنعيم
  for (let i = 0; i < W * H; i++) if (terrain[i] === 'water') elev[i] = 0;

  // 2) Hillshade: متجه ضوء NW بارتفاع 42°
  const shade = new Float32Array(W * H);
  const az = (-315 * Math.PI) / 180;
  const lz = Math.tan((42 * Math.PI) / 180);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      const xl = elev[y * W + Math.max(0, x - 1)];
      const xr = elev[y * W + Math.min(W - 1, x + 1)];
      const yu = elev[Math.max(0, y - 1) * W + x];
      const yd = elev[Math.min(H - 1, y + 1) * W + x];
      const dzdx = (xr - xl) / 2;
      const dzdy = (yd - yu) / 2;
      // تدرج سلبي باتجاه光源 يعطي تباينًا جيدًا مع NW
      const slope = Math.hypot(dzdx, dzdy) * 9;
      const aspect = Math.atan2(-dzdy, dzdx);
      const illum = Math.sin(lz) * Math.cos(Math.min(1.45, slope * 0.11)) +
        Math.cos(lz) * Math.sin(Math.min(1.45, slope * 0.11)) * Math.cos(az - aspect);
      shade[i] = 0.82 + 0.5 * Math.max(0, Math.min(1.4, illum));
    }
  }

  // 3) خطوط كنتور فوق المرتفعات فقط
  const contour = new Uint8Array(W * H);
  const LEVEL = 0.1;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      if (elev[i] < 0.5) continue;
      const q = Math.floor(elev[i] / LEVEL);
      const r = x + 1 < W ? Math.floor(elev[i + 1] / LEVEL) : q;
      const d = y + 1 < H ? Math.floor(elev[i + W] / LEVEL) : q;
      if (r !== q || d !== q) contour[i] = 1;
    }
  }

  // 4) أنهار: من القمم نزلًا لأخفض جار حتى الماء (تجمّع التدفق = سعة)
  const flow = new Float32Array(W * H);
  const starts: number[] = [];
  for (let i = 0; i < W * H; i++) if (terrain[i] === 'mountains' && hash2(i, 7) > 0.42) starts.push(i);
  for (const s0 of starts) {
    let x = s0 % W;
    let y = Math.floor(s0 / W);
    const seen = new Set<number>();
    for (let step = 0; step < 220; step++) {
      const i = y * W + x;
      if (seen.has(i)) break;
      seen.add(i);
      flow[i] += 1;
      if (terrain[i] === 'water') break;
      let bx = -1;
      let by = -1;
      let best = elev[i] - 0.004;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const nx = x + dx;
          const ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
          const e = elev[ny * W + nx] - hash2(nx * 3.7 + step, ny * 2.9) * 0.012;
          if (e < best) {
            best = e;
            bx = nx;
            by = ny;
          }
        }
      }
      if (bx < 0) break; // وعاء مغلق — النهر يموت هنا (مستنع صغير)
      x = bx;
      y = by;
    }
  }
  const maxFlow = Math.max(3, ...Array.from(flow));
  const river = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) {
    if (flow[i] >= 2 && terrain[i] !== 'water') {
      river[i] = Math.min(1, Math.pow(flow[i] / maxFlow, 0.55));
    }
  }

  return { shade, river, contour };
}
