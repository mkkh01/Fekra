// صنف اللعبة: يجمع العالم + الساعة + الاقتصاد + الحركة + الحفظ/التحميل.

import { Rng } from './rng';
import { Clock, HOURS_PER_DAY } from './clock';
import { generateWorld, findPath } from './worldgen';
import { tickBattles } from './battle';
import { tickAI } from './ai';
import { SAVE_VERSION } from './types';
import type { Army, Formation, Nation, NewsItem, NewsKind, Province, Stance, TerrainType } from './types';

export const MOVE_COST: Record<TerrainType, number> = {
  plains: 12,
  desert: 16,
  forest: 20,
  hills: 26,
  mountains: 36,
  water: Number.POSITIVE_INFINITY,
};

export const RECRUIT_COST = 80;
export const RECRUIT_SIZE = 600;
/** سقف حجم الجيش عبر التجنيد */
export const MAX_RECRUIT_SIZE = 4000;
export const NEW_ARMY_COST = 150;
export const NEW_ARMY_SIZE = 1000;
/** سقف عدد الجيوش للدولة الواحدة */
export const MAX_ARMIES = 5;

export type Selection = { type: 'province'; id: number } | { type: 'army'; id: number } | null;

export class Game {
  seed: number;
  rng: Rng;
  clock = new Clock();
  width = 0;
  height = 0;
  cellProvince: number[] = [];
  cellTerrain: TerrainType[] = [];
  provinces: Province[] = [];
  nations: Nation[] = [];
  armies: Army[] = [];
  nextArmyId = 1;
  news: NewsItem[] = [];
  selection: Selection = null;
  over: { winnerId: number; text: string } | null = null;

  constructor(seed: number) {
    this.seed = seed;
    this.rng = new Rng(seed);
    const w = generateWorld(seed);
    this.width = w.width;
    this.height = w.height;
    this.cellProvince = w.cellProvince;
    this.cellTerrain = w.cellTerrain;
    this.provinces = w.provinces;
    this.nations = w.nations;
    this.armies = w.armies;
    this.nextArmyId = w.nextArmyId;
    const me = this.player();
    const foe = this.nations.find((n) => !n.isPlayer);
    this.log(`🏰 بدأت الحملة — ${me.name} ضد ${foe?.name}. احتل عاصمة العدو للفوز!`, 'info');
  }

  get tick(): number {
    return this.clock.tick;
  }

  player(): Nation {
    return this.nations.find((n) => n.isPlayer)!;
  }

  nation(id: number): Nation {
    return this.nations.find((n) => n.id === id)!;
  }

  provinceAt(x: number, y: number): number {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height) return -1;
    return this.cellProvince[y * this.width + x];
  }

  armiesAt(pid: number): Army[] {
    return this.armies.filter((a) => a.provinceId === pid);
  }

  log(text: string, kind: NewsKind): void {
    this.news.unshift({ tick: this.tick, text, kind });
    if (this.news.length > 200) this.news.pop();
  }

  // ---------- الحلقة ----------

  update(): void {
    if (this.over) return;
    this.clock.advance(1);
    this.moveArmies();
    tickBattles(this, this.rng);
    if (this.over) return;
    this.supplyTick();
    this.garrisonTick();
    if (this.tick % HOURS_PER_DAY === 0) this.economyTick();
    if (this.tick % 12 === 0) tickAI(this);
  }

  private moveArmies(): void {
    for (const a of this.armies) {
      if (a.path.length === 0) continue;
      const dest = this.provinces[a.path[0]];
      let cost = MOVE_COST[dest.terrain];
      if (a.formation === 'column') cost *= 0.8;
      a.progress += 1 / cost;
      if (a.progress >= 1) {
        a.progress = 0;
        a.provinceId = a.path.shift()!;
        if (a.path.length === 0) {
          if (a.order.kind === 'retreat') {
            this.log(`🛡️ أعاد ${a.name} تنظيم صفوفه في ${this.provinces[a.provinceId].name}`, 'info');
          }
          a.order = { kind: 'hold', targetProvinceId: null };
        }
      }
    }
  }

  private supplyTick(): void {
    for (const a of this.armies) {
      const prov = this.provinces[a.provinceId];
      const inBattle = prov.lastBattleTick === this.tick;
      // حصار: وجود عدو في المقاطعة يمنع التموين حتى للمدافع (تجويع تدريجي)
      const besieged = this.armies.some((x) => x.nationId !== a.nationId && x.provinceId === a.provinceId);
      if (besieged) a.supply = Math.max(0, a.supply - 0.3);
      else if (a.nationId === prov.ownerId) a.supply = Math.min(100, a.supply + 0.4);
      else a.supply = Math.max(0, a.supply - 0.2);
      if (!inBattle && a.supply > 30 && a.morale < 100) {
        a.morale = Math.min(100, a.morale + 0.25);
      }
      if (a.supply <= 0) a.morale = Math.max(0, a.morale - 0.15);
    }
  }

  /** تجدد الحاميات تدريجيًا في المقاطعات الهادئة */
  private garrisonTick(): void {
    for (const p of this.provinces) {
      if (p.terrain === 'water' || p.ownerId < 0) continue;
      if (this.tick - p.lastBattleTick < 48) continue;
      const max = p.city ? (p.city.isCapital ? 1500 : 350) : 100;
      if (p.garrison >= max) continue;
      const rate = p.city ? (p.city.isCapital ? 3 : 1.5) : 0.75;
      p.garrison = Math.min(max, p.garrison + rate);
    }
  }

  private economyTick(): void {
    for (const n of this.nations) {
      if (!n.alive) continue;
      const owned = this.provinces.filter((p) => p.ownerId === n.id);
      const cities = owned.filter((p) => p.city).length;
      n.gold += cities * 15 + owned.length * 3;
      const soldiers =
        this.armies.filter((a) => a.nationId === n.id).reduce((s, a) => s + a.soldiers, 0) +
        owned.reduce((s, p) => s + p.garrison, 0);
      n.food += cities * 30 + owned.length * 6 - Math.ceil(soldiers / 400);
      if (n.food < 0) {
        n.food = 0;
        for (const a of this.armies) {
          if (a.nationId === n.id) {
            a.morale = Math.max(0, a.morale - 2);
            a.supply = Math.max(0, a.supply - 3);
          }
        }
        if (n.isPlayer) this.log('🍞 نقص الغذاء! معنويات جيوشك تنهار.', 'bad');
      }
    }
  }

  // ---------- واجهة المعارك ----------

  captureProvince(pid: number, byId: number): void {
    const prov = this.provinces[pid];
    const prev = prov.ownerId;
    const by = this.nation(byId);
    prov.ownerId = byId;
    prov.garrison = 0;
    this.log(
      `🚩 سقطت ${prov.name} بيد ${by.name}!`,
      by.isPlayer ? 'good' : this.player().alive ? 'bad' : 'war',
    );
    if (prov.city?.isCapital) {
      const loser = this.nation(prev);
      loser.alive = false;
      for (const p of this.provinces) {
        if (p.ownerId === loser.id) {
          p.ownerId = byId;
          p.garrison = 0;
        }
      }
      this.armies = this.armies.filter((a) => a.nationId !== loser.id);
      this.selection = null;
      const won = by.isPlayer;
      this.over = {
        winnerId: byId,
        text: won ? '🎉 النصر! سقطت عاصمة العدو.' : '💀 الهزيمة... سقطت عاصمتك.',
      };
      this.log(won ? '👑 انتصرت دولتك في الحرب!' : '🏴 سقطت دولتك في الحرب.', won ? 'good' : 'bad');
    }
  }

  disbandArmy(armyId: number): void {
    this.armies = this.armies.filter((a) => a.id !== armyId);
    if (this.selection?.type === 'army' && this.selection.id === armyId) {
      this.selection = null;
    }
  }

  // ---------- أوامر اللاعب ----------

  issueMoveOrder(armyId: number, targetId: number): boolean {
    const a = this.armies.find((x) => x.id === armyId);
    if (!a || this.over) return false;
    if (a.nationId !== this.player().id) return false;
    if (targetId < 0 || targetId === a.provinceId) return false;
    if (this.provinces[targetId].terrain === 'water') {
      this.log('🌊 لا يمكن عبور المياه.', 'info');
      return false;
    }
    const path = findPath(this.provinces, a.provinceId, targetId);
    if (path.length === 0) {
      this.log('⛔ لا يوجد طريق بري إلى هناك!', 'bad');
      return false;
    }
    a.path = path;
    a.progress = 0;
    a.order = { kind: 'move', targetProvinceId: targetId };
    const dest = this.provinces[targetId];
    const hostile = dest.ownerId >= 0 && dest.ownerId !== a.nationId;
    this.log(
      hostile ? `⚔️ ${a.name} يزحف للهجوم على ${dest.name}` : `🚶 ${a.name} يتحرك نحو ${dest.name}`,
      'info',
    );
    return true;
  }

  setStance(armyId: number, s: Stance): void {
    const a = this.armies.find((x) => x.id === armyId);
    if (a && a.nationId === this.player().id) a.stance = s;
  }

  setFormation(armyId: number, f: Formation): void {
    const a = this.armies.find((x) => x.id === armyId);
    if (a && a.nationId === this.player().id) a.formation = f;
  }

  holdArmy(armyId: number): void {
    const a = this.armies.find((x) => x.id === armyId);
    if (a && a.nationId === this.player().id) {
      a.path = [];
      a.progress = 0;
      a.order = { kind: 'hold', targetProvinceId: null };
    }
  }

  retreatArmy(armyId: number): void {
    const a = this.armies.find((x) => x.id === armyId);
    if (!a || a.nationId !== this.player().id) return;
    const prov = this.provinces[a.provinceId];
    const options = prov.neighbors.filter(
      (nb) => this.provinces[nb].terrain !== 'water' && this.provinces[nb].ownerId === a.nationId,
    );
    if (options.length === 0) {
      this.log('⛔ لا يوجد مكان آمن للانسحاب!', 'bad');
      return;
    }
    // الأقرب للعاصمة
    const cap = this.provinces[this.player().capitalProvinceId];
    options.sort((m, n) => {
      const dm = (this.provinces[m].center.x - cap.center.x) ** 2 + (this.provinces[m].center.y - cap.center.y) ** 2;
      const dn = (this.provinces[n].center.x - cap.center.x) ** 2 + (this.provinces[n].center.y - cap.center.y) ** 2;
      return dm - dn;
    });
    const dest = options[0];
    a.path = [dest];
    a.progress = 0;
    a.order = { kind: 'retreat', targetProvinceId: dest };
    this.log(`🏃 ${a.name} ينسحب إلى ${this.provinces[dest].name}`, 'info');
  }

  recruit(armyId: number): void {
    const a = this.armies.find((x) => x.id === armyId);
    const me = this.player();
    if (!a || a.nationId !== me.id) return;
    const prov = this.provinces[a.provinceId];
    if (prov.ownerId !== me.id || !prov.city) {
      this.log('⛔ التجنيد متاح فقط في مدنك.', 'bad');
      return;
    }
    if (this.tick - prov.lastBattleTick < 96) {
      this.log('⛔ لا يمكن التجنيد في مقاطعة مشتعلة — انتظر هدوءها (4 أيام).', 'bad');
      return;
    }
    if (a.soldiers >= MAX_RECRUIT_SIZE) {
      this.log(`⛔ ${a.name} مكتمل العدد (${MAX_RECRUIT_SIZE}). شكّل جيشًا جديدًا بدلًا من ذلك.`, 'info');
      return;
    }
    if (me.gold < RECRUIT_COST) {
      this.log('⛔ ذهب غير كافٍ للتجنيد!', 'bad');
      return;
    }
    me.gold -= RECRUIT_COST;
    a.soldiers += RECRUIT_SIZE;
    this.log(`➕ تم تجنيد ${RECRUIT_SIZE} جندي في ${a.name}`, 'good');
  }

  /** تشكيل جيش جديد في العاصمة (للاعب أو للذكاء الاصطناعي) */
  foundArmy(nationId: number): boolean {
    const n = this.nation(nationId);
    if (!n.alive || n.gold < NEW_ARMY_COST) return false;
    if (this.armies.filter((a) => a.nationId === nationId).length >= MAX_ARMIES) {
      if (n.isPlayer) this.log(`⛔ وصلت للحد الأقصى من الجيوش (${MAX_ARMIES}).`, 'bad');
      return false;
    }
    const cap = this.provinces[n.capitalProvinceId];
    if (cap.ownerId !== nationId) return false;
    // لا تشكيل جيوش في عاصمة محاصرة/مشتعلة
    if (this.tick - cap.lastBattleTick < 96) {
      if (n.isPlayer) this.log('⛔ لا يمكن تشكيل جيش والعاصمة مشتعلة!', 'bad');
      return false;
    }
    n.gold -= NEW_ARMY_COST;
    const count = this.armies.filter((a) => a.nationId === nationId).length + 1;
    const short = n.isPlayer ? 'الفجر' : 'الغسق';
    this.armies.push({
      id: this.nextArmyId++,
      nationId,
      provinceId: cap.id,
      soldiers: NEW_ARMY_SIZE,
      morale: 70,
      supply: 100,
      stance: 'balanced',
      formation: 'line',
      order: { kind: 'hold', targetProvinceId: null },
      path: [],
      progress: 0,
      name: `جيش ${short} ${count === 1 ? 'الأول' : count === 2 ? 'الثاني' : 'الجديد'}`,
      commander: n.isPlayer ? 'قائد جديد' : 'قائد العدو',
    });
    if (n.isPlayer) this.log(`🎖️ تم تشكيل جيش جديد في العاصمة!`, 'good');
    return true;
  }

  // ---------- الحفظ ----------

  save(): string {
    return JSON.stringify({
      version: SAVE_VERSION,
      seed: this.seed,
      rngState: this.rng.state,
      tick: this.clock.tick,
      width: this.width,
      height: this.height,
      cellProvince: this.cellProvince,
      cellTerrain: this.cellTerrain,
      provinces: this.provinces,
      nations: this.nations,
      armies: this.armies,
      nextArmyId: this.nextArmyId,
      news: this.news.slice(0, 50),
      over: this.over,
    });
  }

  static load(json: string): Game {
    const d = JSON.parse(json);
    if (d.version !== SAVE_VERSION) throw new Error('نسخة حفظ غير مدعومة');
    const g = Object.create(Game.prototype) as Game;
    g.seed = d.seed;
    g.rng = new Rng(d.seed);
    g.rng.state = d.rngState;
    g.clock = new Clock();
    g.clock.tick = d.tick;
    g.width = d.width;
    g.height = d.height;
    g.cellProvince = d.cellProvince;
    g.cellTerrain = d.cellTerrain;
    g.provinces = d.provinces;
    g.nations = d.nations;
    g.armies = d.armies;
    g.nextArmyId = d.nextArmyId;
    g.news = d.news;
    g.over = d.over ?? null;
    g.selection = null;
    g.log('💾 تم تحميل الحفظ بنجاح.', 'good');
    return g;
  }
}
