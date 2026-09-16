import { describe, it, expect } from 'vitest';
import { generateWorld, findPath } from './worldgen';

describe('توليد العالم', () => {
  it('يولد دولتين وعاصمتين متصلتين برًّا', () => {
    const w = generateWorld(438921);
    expect(w.provinces.length).toBeGreaterThan(10);
    expect(w.nations.filter((n) => n.alive).length).toBe(2);
    const [a, b] = w.nations;
    const path = findPath(w.provinces, a.capitalProvinceId, b.capitalProvinceId);
    expect(path.length).toBeGreaterThan(0);
  });

  it('حتمي: نفس البذرة = نفس العالم', () => {
    const a = generateWorld(123);
    const b = generateWorld(123);
    expect(a.cellProvince).toEqual(b.cellProvince);
    expect(a.provinces.map((p) => p.ownerId)).toEqual(b.provinces.map((p) => p.ownerId));
  });

  it('لا توجد مقاطعة فارغة', () => {
    const w = generateWorld(777);
    for (const p of w.provinces) {
      expect(p.cells.length).toBeGreaterThan(0);
    }
  });
});
