// توليد العالم: شبكة خلايا تُجمّع في مقاطعات (Voronoi على الشبكة).

import { Rng } from './rng';
import type { Army, Nation, Province, TerrainType, Vec } from './types';

export interface WorldData {
  seed: number;
  width: number;
  height: number;
  /** لكل خلية: معرف مقاطعتها */
  cellProvince: number[];
  /** لكل خلية: تضاريسها */
  cellTerrain: TerrainType[];
  provinces: Province[];
  nations: Nation[];
  armies: Army[];
  nextArmyId: number;
}

const PROVINCE_NAMES = [
  'داران', 'سيفار', 'نواعير', 'حلبان', 'كرخ', 'ميفعة', 'تدمر', 'واسط',
  'الجزيرة', 'عسقلان', 'جبلة', 'رُصافة', 'قنسرين', 'آمد', 'نصيبين', 'سنجار',
  'تكريت', 'الحيرة', 'أيلة', 'دومة', 'تيماء', 'خيبر', 'فدك', 'يثرب',
  'جُرش', 'أذرعات', 'بصرى', 'تُستَر', 'الأهواز', 'جرجان', 'طبرستان', 'قومس',
];

const COMMANDERS = [
  'فهد', 'سليم', 'طارق', 'خالد', 'عنترة', 'القعقاع', 'سعد', 'المثنى',
  'النعمان', 'جابر', 'حسان', 'زهير', 'عمرو', 'يزيد', 'مروان', 'الوليد',
];

function idx(width: number, x: number, y: number): number {
  return y * width + x;
}

export function generateWorld(seed: number, width = 56, height = 36, provinceCount = 26): WorldData {
  // نعيد المحاولة ببذور متتالية حتى نضمن اتصال العاصمتين برًّا (حتمي).
  for (let attempt = 0; attempt < 25; attempt++) {
    const w = tryGenerate(seed + attempt, width, height, provinceCount);
    const caps = w.nations.map((n) => n.capitalProvinceId);
    if (findPath(w.provinces, caps[0], caps[1]).length > 0) return w;
  }
  return tryGenerate(seed + 999, width, height, provinceCount);
}

function tryGenerate(seed: number, width: number, height: number, provinceCount: number): WorldData {
  const rng = new Rng(seed);

  // 1) بذور المقاطعات (بدون تكرار لنضمن عدم وجود مقاطعة فارغة)
  const seeds: Vec[] = [];
  const seen = new Set<string>();
  while (seeds.length < provinceCount) {
    const x = rng.int(0, width - 1);
    const y = rng.int(0, height - 1);
    const k = `${x},${y}`;
    if (!seen.has(k)) {
      seen.add(k);
      seeds.push({ x, y });
    }
  }

  // 2) إسناد كل خلية لأقرب بذرة
  const cellProvince = new Array<number>(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let best = 0;
      let bd = Infinity;
      for (let i = 0; i < seeds.length; i++) {
        const dx = x - seeds[i].x;
        const dy = y - seeds[i].y;
        const d = dx * dx + dy * dy;
        if (d < bd) {
          bd = d;
          best = i;
        }
      }
      cellProvince[idx(width, x, y)] = best;
    }
  }

  // 3) التضاريس لكل خلية
  const cellTerrain = new Array<TerrainType>(width * height).fill('plains');
  const blob = (n: number, rMin: number, rMax: number, t: TerrainType) => {
    for (let b = 0; b < n; b++) {
      const cx = rng.int(0, width - 1);
      const cy = rng.int(0, height - 1);
      const r = rMin + rng.next() * (rMax - rMin);
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          const dx = x - cx;
          const dy = y - cy;
          if (dx * dx + dy * dy <= r * r) cellTerrain[idx(width, x, y)] = t;
        }
      }
    }
  };
  blob(3, 2.5, 4.5, 'water');
  blob(6, 1, 2, 'mountains');
  blob(8, 1.5, 3, 'forest');
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = idx(width, x, y);
      if (cellTerrain[i] !== 'plains') continue;
      const r = rng.next();
      if (y > height * 0.62 && r < 0.45) cellTerrain[i] = 'desert';
      else if (r > 0.92) cellTerrain[i] = 'hills';
    }
  }

  // 4) بناء المقاطعات
  const names = rng.shuffle([...PROVINCE_NAMES]);
  const provinces: Province[] = [];
  for (let i = 0; i < provinceCount; i++) {
    const cells: Vec[] = [];
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (cellProvince[idx(width, x, y)] === i) cells.push({ x, y });
      }
    }
    let sx = 0;
    let sy = 0;
    for (const c of cells) {
      sx += c.x;
      sy += c.y;
    }
    const counts = new Map<TerrainType, number>();
    for (const c of cells) {
      const t = cellTerrain[idx(width, c.x, c.y)];
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    const waterN = counts.get('water') ?? 0;
    let terrain: TerrainType;
    if (waterN >= cells.length * 0.45) {
      terrain = 'water';
    } else {
      let bt: TerrainType = 'plains';
      let bn = -1;
      for (const [t, n] of counts) {
        if (t !== 'water' && n > bn) {
          bn = n;
          bt = t;
        }
      }
      terrain = bt;
    }
    provinces.push({
      id: i,
      name: names[i % names.length],
      cells,
      center: { x: sx / cells.length, y: sy / cells.length },
      terrain,
      neighbors: [],
      ownerId: -1,
      city: null,
      garrison: 0,
      lastBattleTick: -99999,
    });
  }

  // 5) الجوار
  const link = (a: number, b: number) => {
    if (a === b) return;
    const A = provinces[a];
    const B = provinces[b];
    if (!A.neighbors.includes(b)) A.neighbors.push(b);
    if (!B.neighbors.includes(a)) B.neighbors.push(a);
  };
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = idx(width, x, y);
      const p = cellProvince[i];
      if (x + 1 < width) link(p, cellProvince[i + 1]);
      if (y + 1 < height) link(p, cellProvince[i + width]);
    }
  }

  // 6) الدول والملكية: عاصمتان متباعدتان، وكل مقاطعة لأقرب عاصمة
  const land = provinces.filter((p) => p.terrain !== 'water');
  const capA = rng.pick(land);
  let capB = capA;
  let bestD = -1;
  for (const p of land) {
    const d = (p.center.x - capA.center.x) ** 2 + (p.center.y - capA.center.y) ** 2;
    if (d > bestD) {
      bestD = d;
      capB = p;
    }
  }
  const nations: Nation[] = [
    { id: 0, name: 'دولة الفجر', color: '#0e8a74', darkColor: '#065f50', isPlayer: true, gold: 700, food: 400, capitalProvinceId: capA.id, alive: true },
    { id: 1, name: 'دولة الغسق', color: '#c0392b', darkColor: '#7c241a', isPlayer: false, gold: 700, food: 400, capitalProvinceId: capB.id, alive: true },
  ];
  for (const p of land) {
    const da = (p.center.x - capA.center.x) ** 2 + (p.center.y - capA.center.y) ** 2;
    const db = (p.center.x - capB.center.x) ** 2 + (p.center.y - capB.center.y) ** 2;
    p.ownerId = da <= db ? 0 : 1;
  }

  // 7) المدن والحاميات
  for (const p of land) {
    const isCap = p.id === capA.id || p.id === capB.id;
    if (isCap) {
      p.city = { name: p.name, population: 20000, isCapital: true, fortLevel: 3 };
      p.garrison = 1500;
    } else if (rng.next() < 0.45) {
      p.city = { name: p.name, population: rng.int(3000, 9000), isCapital: false, fortLevel: rng.int(1, 2) };
      p.garrison = 350;
    } else {
      p.garrison = 100;
    }
  }

  // 8) الجيوش الأولى
  const cmdNames = rng.shuffle([...COMMANDERS]);
  let nextArmyId = 1;
  const mkArmy = (nationId: number, provinceId: number, soldiers: number, name: string): Army => ({
    id: nextArmyId++,
    nationId,
    provinceId,
    soldiers,
    morale: 80,
    supply: 100,
    stance: 'balanced',
    formation: 'line',
    order: { kind: 'hold', targetProvinceId: null },
    path: [],
    progress: 0,
    name,
    commander: cmdNames[(nextArmyId - 2) % cmdNames.length],
  });
  const armies: Army[] = [];
  armies.push(mkArmy(0, capA.id, 3500, 'جيش الفجر الأول'));
  armies.push(mkArmy(0, capA.id, 2200, 'جيش الفجر الثاني'));
  armies.push(mkArmy(1, capB.id, 3500, 'جيش الغسق الأول'));
  armies.push(mkArmy(1, capB.id, 2200, 'جيش الغسق الثاني'));

  return { seed, width, height, cellProvince, cellTerrain, provinces, nations, armies, nextArmyId };
}

/** أقصر مسار بري بين مقاطعتين (BFS) — يُرجع قائمة الخطوات بدون نقطة البداية. */
export function findPath(provinces: Province[], from: number, to: number): number[] {
  if (from === to) return [];
  if (provinces[to].terrain === 'water') return [];
  const prev = new Map<number, number>();
  const seen = new Set<number>([from]);
  const q: number[] = [from];
  while (q.length > 0) {
    const cur = q.shift()!;
    for (const nb of provinces[cur].neighbors) {
      if (seen.has(nb)) continue;
      if (provinces[nb].terrain === 'water') continue;
      seen.add(nb);
      prev.set(nb, cur);
      if (nb === to) {
        const path = [to];
        let c = to;
        while (prev.get(c)! !== from) {
          c = prev.get(c)!;
          path.unshift(c);
        }
        return path;
      }
      q.push(nb);
    }
  }
  return [];
}
