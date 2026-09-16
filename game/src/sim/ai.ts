// ذكاء اصطناعي للعدو (النموذج الأولي):
// - النصف الأقوى للهجوم، والباقي لحماية العاصمة.
// - تراجع ذكي: أمام تفوق ساحق قريب، الانسحاب للعاصمة والتكتل بدل الانتحار.
// - استدعاء عام عند اقتراب العدو من العاصمة (ضمن قفزتين).
// - انقضاض مباشر على عاصمة اللاعب إذا كانت ضعيفة.
// ملاحظة: يستخدم معلومات كاملة مؤقتًا — الضباب ونظام المعتقدات في مرحلة لاحقة.

import { findPath } from './worldgen';
import type { Army, Nation, NewsKind, Province } from './types';

export interface AIWorld {
  provinces: Province[];
  nations: Nation[];
  armies: Army[];
  tick: number;
  log(text: string, kind: NewsKind): void;
  foundArmy(nationId: number): boolean;
}

const AI_REINFORCE_COST = 100;
const AI_REINFORCE_SIZE = 800;
/** مدى الاستشعار حول العاصمة (بالقفزات) */
const THREAT_RADIUS = 2;
/** نسبة التفوق التي تستدعي التراجع */
const FALLBACK_RATIO = 1.3;
/** مدة التكتل في العاصمة بعد التراجع (نبضة) */
const REGROUP_TICKS = 240;

/** جيوش في فترة تكتل: id الجيش ← تنتهي في نبضة */
const regroupUntil = new Map<number, number>();

export function tickAI(w: AIWorld): void {
  // تنظيف دوري لجيوش زالت
  if (regroupUntil.size > 40) {
    const alive = new Set(w.armies.map((a) => a.id));
    for (const id of [...regroupUntil.keys()]) {
      if (!alive.has(id)) regroupUntil.delete(id);
    }
  }

  for (const n of w.nations) {
    if (n.isPlayer || !n.alive) continue;
    const foe = w.nations.find((x) => x.id !== n.id && x.alive);
    if (!foe) continue;

    const myArmies = w.armies.filter((a) => a.nationId === n.id);
    if (myArmies.length === 0) continue;

    // توزيع الأدوار: النصف الأقوى للهجوم، والباقي لحماية العاصمة
    const sorted = [...myArmies].sort((a, b) => b.soldiers - a.soldiers);
    const attackers = new Set<number>();
    const nAttack = Math.max(1, Math.ceil(sorted.length / 2));
    for (let i = 0; i < nAttack; i++) attackers.add(sorted[i].id);

    const depths = depthsFrom(w, n.capitalProvinceId);
    const capitalThreatened = w.armies.some(
      (a) => a.nationId !== n.id && (depths.get(a.provinceId) ?? 99) <= THREAT_RADIUS,
    );

    const foeCap = w.provinces[foe.capitalProvinceId];
    const foeCapDefense =
      w.armies
        .filter((a) => a.provinceId === foeCap.id && a.nationId !== n.id)
        .reduce((s, a) => s + a.soldiers, 0) + (foeCap.ownerId !== n.id ? foeCap.garrison : 0);

    for (const a of myArmies) {
      const prov = w.provinces[a.provinceId];
      const enemiesHere = w.armies.some((x) => x.nationId !== n.id && x.provinceId === a.provinceId);
      const hostileGarrison = prov.ownerId !== n.id && prov.ownerId >= 0 && prov.garrison > 0;

      // قتال: من يتعرض للهجوم يقاتل مكانه أيًّا كان دوره
      if (enemiesHere || hostileGarrison) {
        a.order = { kind: 'hold', targetProvinceId: null };
        a.path = [];
        a.stance = prov.ownerId === n.id ? 'defensive' : 'aggressive';
        continue;
      }

      // فترة تكتل: ثبات دفاعي في العاصمة حتى انتهاؤها
      if (a.provinceId === n.capitalProvinceId && (regroupUntil.get(a.id) ?? 0) > w.tick) {
        a.path = [];
        a.order = { kind: 'hold', targetProvinceId: null };
        a.stance = 'defensive';
        if (a.soldiers < 2500 && n.gold >= AI_REINFORCE_COST) {
          a.soldiers += AI_REINFORCE_SIZE;
          n.gold -= AI_REINFORCE_COST;
        }
        continue;
      }

      // تراجع ذكي: تفوق ساحق على بعد قفزة أو أقل ← انسحاب للعاصمة
      if (a.provinceId !== n.capitalProvinceId) {
        const nearSpots = [a.provinceId, ...prov.neighbors];
        let maxFoe = 0;
        for (const x of w.armies) {
          if (x.nationId !== n.id && nearSpots.includes(x.provinceId)) {
            maxFoe = Math.max(maxFoe, x.soldiers);
          }
        }
        if (maxFoe > a.soldiers * FALLBACK_RATIO) {
          if (a.path.length === 0 || a.order.targetProvinceId !== n.capitalProvinceId) {
            const path = findPath(w.provinces, a.provinceId, n.capitalProvinceId);
            if (path.length > 0) {
              a.path = path;
              a.order = { kind: 'move', targetProvinceId: n.capitalProvinceId };
              regroupUntil.set(a.id, w.tick + REGROUP_TICKS);
              w.log(`⚠️ ${a.name} يتراجع إلى العاصمة أمام تفوق العدو!`, 'info');
            }
          }
          a.stance = 'defensive';
          continue;
        }
      }

      // تعزيز في المدن الصديقة الهادئة (لا تعزيز تحت الحصار)
      if (
        prov.ownerId === n.id &&
        prov.city &&
        w.tick - prov.lastBattleTick >= 96 &&
        a.soldiers < 2500 &&
        n.gold >= AI_REINFORCE_COST
      ) {
        a.soldiers += AI_REINFORCE_SIZE;
        n.gold -= AI_REINFORCE_COST;
      }

      // دور الحماية: العودة للعاصمة والثبات دفاعيًا
      if (!attackers.has(a.id)) {
        a.stance = 'defensive';
        if (a.provinceId !== n.capitalProvinceId && a.path.length === 0) {
          const path = findPath(w.provinces, a.provinceId, n.capitalProvinceId);
          if (path.length > 0) {
            a.path = path;
            a.order = { kind: 'move', targetProvinceId: n.capitalProvinceId };
          }
        } else if (a.provinceId === n.capitalProvinceId) {
          a.path = [];
          a.order = { kind: 'hold', targetProvinceId: null };
        }
        continue;
      }

      if (a.path.length > 0 && !capitalThreatened) continue; // يتحرك already

      // استدعاء عام: العودة للدفاع عند تهديد العاصمة
      if (capitalThreatened && a.provinceId !== n.capitalProvinceId) {
        const path = findPath(w.provinces, a.provinceId, n.capitalProvinceId);
        if (path.length > 0) {
          a.path = path;
          a.order = { kind: 'move', targetProvinceId: n.capitalProvinceId };
          a.stance = 'defensive';
          continue;
        }
      }

      // الهجوم: انقضاض على عاصمة ضعيفة، وإلا أقرب هدف
      let target = nearestTarget(w, a.provinceId, n.id);
      if (!capitalThreatened && foeCapDefense < a.soldiers * 0.9) {
        target = foe.capitalProvinceId;
      }
      if (target >= 0 && target !== a.provinceId) {
        const path = findPath(w.provinces, a.provinceId, target);
        if (path.length > 0) {
          a.path = path;
          a.order = { kind: 'move', targetProvinceId: target };
        }
      }

      // الموقف حسب ميزان القوى المحلي
      const mine = w.armies
        .filter((x) => x.nationId === n.id && x.provinceId === a.provinceId)
        .reduce((s, x) => s + x.soldiers, 0);
      const theirs = w.armies
        .filter((x) => x.nationId !== n.id && x.provinceId === a.provinceId)
        .reduce((s, x) => s + x.soldiers, 0);
      a.stance = mine >= theirs ? 'aggressive' : 'defensive';
    }

    // تشكيل جيش جديد عند الثراء (بوتيرة مضبوطة)
    if (n.gold > 900 && myArmies.length < 4) {
      w.foundArmy(n.id);
    }
  }
}

/** أقرب مقاطعة فيها عدو أو مملوكة للعدو (BFS بري). */
function nearestTarget(w: AIWorld, from: number, me: number): number {
  const seen = new Set<number>([from]);
  const q: number[] = [from];
  while (q.length > 0) {
    const cur = q.shift()!;
    if (cur !== from) {
      const p = w.provinces[cur];
      const enemyHere = w.armies.some((a) => a.provinceId === cur && a.nationId !== me);
      if (enemyHere || (p.ownerId >= 0 && p.ownerId !== me)) return cur;
    }
    for (const nb of w.provinces[cur].neighbors) {
      if (!seen.has(nb) && w.provinces[nb].terrain !== 'water') {
        seen.add(nb);
        q.push(nb);
      }
    }
  }
  return -1;
}

/** المسافة (بالقفزات البرية) من نقطة انطلاق لكل مقاطعة. */
function depthsFrom(w: AIWorld, from: number): Map<number, number> {
  const d = new Map<number, number>([[from, 0]]);
  const q: number[] = [from];
  while (q.length > 0) {
    const cur = q.shift()!;
    for (const nb of w.provinces[cur].neighbors) {
      if (d.has(nb) || w.provinces[nb].terrain === 'water') continue;
      d.set(nb, d.get(cur)! + 1);
      q.push(nb);
    }
  }
  return d;
}
