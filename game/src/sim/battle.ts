// محرك المعارك: حسم بنبضات مع مواقف وتشكيلات وتضاريس ومعنويات.
// يعمل على واجهة BattleWorld حتى لا يعتمد على صنف Game مباشرة.

import type { Rng } from './rng';
import type { Army, Formation, NewsKind, Province, Stance, TerrainType } from './types';

export const BALANCE = {
  /** نسبة قوة المهاجم التي تتحول لخسائر في العدو كل نبضة */
  KILL_RATE: 0.0025,
  MORALE_LOSS_BASE: 0.25,
  MORALE_LOSS_FACTOR: 150,
  /** تحت هذا الحد ينسحب الجيش تلقائيًا */
  ROUT_MORALE: 12,
  TERRAIN_DEFENSE: {
    plains: 1,
    desert: 1,
    forest: 1.15,
    hills: 1.2,
    mountains: 1.35,
    water: 1,
  } as Record<TerrainType, number>,
  STANCE_ATTACK: { balanced: 1, aggressive: 1.2, defensive: 0.85 } as Record<Stance, number>,
  STANCE_DEFENSE: { balanced: 1, aggressive: 0.9, defensive: 1.25 } as Record<Stance, number>,
  FORMATION_ATTACK: { line: 1.1, column: 1.0, loose: 0.9 } as Record<Formation, number>,
  FORMATION_DEFENSE: { line: 1.0, column: 0.95, loose: 1.0 } as Record<Formation, number>,
  FORMATION_CASUALTY_TAKEN: { line: 1.0, column: 1.0, loose: 0.85 } as Record<Formation, number>,
  /** مضاعف القوة عند نفاد الإمداد */
  SUPPLY_PENALTY: 0.7,
  /** أفضلية الأرض: المدافع في مقاطعة دولته يفقد معنويات بنسبة أقل */
  HOME_MORALE_FACTOR: 0.6,
};

export interface BattleWorld {
  provinces: Province[];
  nations: { id: number; name: string }[];
  armies: Army[];
  tick: number;
  log(text: string, kind: NewsKind): void;
  captureProvince(provinceId: number, byNationId: number): void;
  disbandArmy(armyId: number): void;
}

function nationName(w: BattleWorld, id: number): string {
  return w.nations.find((n) => n.id === id)?.name ?? 'مجهول';
}

export function armyPower(a: Army, prov: Province): number {
  const moraleF = 0.5 + a.morale / 200;
  const supplyF = a.supply < 20 ? BALANCE.SUPPLY_PENALTY : 1;
  const defender = a.nationId === prov.ownerId;
  const stanceF = defender ? BALANCE.STANCE_DEFENSE[a.stance] : BALANCE.STANCE_ATTACK[a.stance];
  const formF = defender ? BALANCE.FORMATION_DEFENSE[a.formation] : BALANCE.FORMATION_ATTACK[a.formation];
  const terrF = defender ? BALANCE.TERRAIN_DEFENSE[prov.terrain] : 1;
  return a.soldiers * moraleF * supplyF * stanceF * formF * terrF;
}

export function tickBattles(w: BattleWorld, rng: Rng): void {
  const byProv = new Map<number, Army[]>();
  for (const a of w.armies) {
    const l = byProv.get(a.provinceId);
    if (l) l.push(a);
    else byProv.set(a.provinceId, [a]);
  }

  for (const [pid, list] of byProv) {
    const prov = w.provinces[pid];
    if (!prov || prov.terrain === 'water') continue;

    const sides = new Set<number>(list.map((a) => a.nationId));
    if (prov.garrison > 0 && prov.ownerId >= 0) sides.add(prov.ownerId);
    if (sides.size < 2) continue;

    // قوة كل طرف
    const power = new Map<number, number>();
    for (const a of list) {
      power.set(a.nationId, (power.get(a.nationId) ?? 0) + armyPower(a, prov));
    }
    if (prov.garrison > 0 && prov.ownerId >= 0) {
      const gp =
        prov.garrison *
        (1 + 0.5 * (prov.city?.fortLevel ?? 0)) *
        BALANCE.TERRAIN_DEFENSE[prov.terrain] *
        0.9;
      power.set(prov.ownerId, (power.get(prov.ownerId) ?? 0) + gp);
    }
    let total = 0;
    for (const p of power.values()) total += p;

    const wasActive = prov.lastBattleTick === w.tick - 1;
    prov.lastBattleTick = w.tick;
    if (!wasActive) w.log(`⚔️ اندلعت معركة في ${prov.name}!`, 'war');

    // كل طرف يتلقى ضررًا من مجموع قوى الآخرين
    for (const n of sides) {
      const incoming = total - (power.get(n) ?? 0);
      if (incoming <= 0) continue;
      const cas = incoming * BALANCE.KILL_RATE * (0.8 + rng.next() * 0.4);
      const mine = list.filter((a) => a.nationId === n);
      const gshare = prov.ownerId === n ? prov.garrison : 0;
      const pool = mine.reduce((s, a) => s + a.soldiers, 0) + gshare;
      if (pool <= 0) continue;
      for (const a of mine) {
        const share = (cas * (a.soldiers / pool)) * BALANCE.FORMATION_CASUALTY_TAKEN[a.formation];
        const before = a.soldiers;
        a.soldiers = Math.max(0, a.soldiers - share);
        const home = a.nationId === prov.ownerId ? BALANCE.HOME_MORALE_FACTOR : 1;
        a.morale = Math.max(
          0,
          a.morale -
            home * (BALANCE.MORALE_LOSS_BASE + BALANCE.MORALE_LOSS_FACTOR * (share / Math.max(1, before))),
        );
      }
      if (gshare > 0) {
        prov.garrison = Math.max(0, prov.garrison - cas * (gshare / pool));
      }
    }

    // سحق وانسحابات
    for (const a of [...list]) {
      if (a.soldiers < 1) {
        w.log(`💀 تم سحق ${a.name} (${nationName(w, a.nationId)}) في ${prov.name}`, 'war');
        w.disbandArmy(a.id);
        continue;
      }
      a.soldiers = Math.round(a.soldiers);
      if (a.morale <= BALANCE.ROUT_MORALE && a.order.kind !== 'retreat') {
        startRetreat(w, a, prov, rng);
      }
    }
    prov.garrison = Math.round(prov.garrison);

    // احتلال المقاطعة إذا لم يتبقَّ سوى طرف واحد
    const remaining = new Set<number>();
    for (const a of w.armies) {
      if (a.provinceId === pid) remaining.add(a.nationId);
    }
    if (prov.garrison > 0 && prov.ownerId >= 0) remaining.add(prov.ownerId);
    if (remaining.size === 1) {
      const winner = [...remaining][0];
      if (winner !== prov.ownerId) w.captureProvince(pid, winner);
    }
  }
}

function startRetreat(w: BattleWorld, a: Army, prov: Province, rng: Rng): void {
  const options = prov.neighbors.filter(
    (nb) => w.provinces[nb].terrain !== 'water' && w.provinces[nb].ownerId === a.nationId,
  );
  if (options.length > 0) {
    const dest = options[Math.floor(rng.next() * options.length)];
    a.path = [dest];
    a.progress = 0;
    a.order = { kind: 'retreat', targetProvinceId: dest };
    a.morale = Math.max(a.morale, 25);
    w.log(`🏃 انسحب ${a.name} من ${prov.name} إلى ${w.provinces[dest].name}`, 'war');
  } else {
    a.morale = 20;
    w.log(`🔥 ${a.name} محاصَر في ${prov.name} ويقاتل حتى النهاية!`, 'bad');
  }
}
