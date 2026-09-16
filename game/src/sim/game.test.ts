import { describe, it, expect } from 'vitest';
import { Game } from './game';

describe('اللعبة الكاملة', () => {
  it('تعمل 5000 نبضة بدون أخطاء', () => {
    const g = new Game(438921);
    // أمر هجومي للاعب لاختبار الحركة والمعارك
    const myArmy = g.armies.find((a) => a.nationId === g.player().id)!;
    const foeCap = g.player().id === 0 ? 1 : 0;
    g.issueMoveOrder(myArmy.id, g.nations[foeCap].capitalProvinceId);
    for (let i = 0; i < 5000 && !g.over; i++) g.update();
    expect(g.news.length).toBeGreaterThan(0);
    expect(g.tick).toBeGreaterThan(0);
  });

  it('الحفظ والتحميل يحافظان على الحالة ونفس المستقبل (حتمية)', () => {
    const g = new Game(99);
    for (let i = 0; i < 300 && !g.over; i++) g.update();
    const h = Game.load(g.save());
    expect(h.tick).toBe(g.tick);
    expect(h.armies.length).toBe(g.armies.length);
    expect(h.provinces.map((p) => p.ownerId)).toEqual(g.provinces.map((p) => p.ownerId));
    // تقدّم متوازٍ: يجب أن يتطابق المستقبل تمامًا
    for (let i = 0; i < 200 && !g.over; i++) {
      g.update();
      h.update();
    }
    expect(h.tick).toBe(g.tick);
    expect(h.armies.map((a) => Math.round(a.soldiers))).toEqual(
      g.armies.map((a) => Math.round(a.soldiers)),
    );
    expect(h.provinces.map((p) => p.ownerId)).toEqual(g.provinces.map((p) => p.ownerId));
    expect(h.over?.winnerId ?? null).toBe(g.over?.winnerId ?? null);
  });
});
