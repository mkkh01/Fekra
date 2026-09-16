import { describe, it, expect } from 'vitest';
import { Rng } from './rng';
import { tickBattles, type BattleWorld } from './battle';
import type { Army, Province } from './types';

function makeArmy(id: number, nationId: number, soldiers: number): Army {
  return {
    id,
    nationId,
    name: `جيش ${id}`,
    commander: 'قائد',
    provinceId: 0,
    soldiers,
    morale: 80,
    supply: 100,
    stance: 'balanced',
    formation: 'line',
    order: { kind: 'hold', targetProvinceId: null },
    path: [],
    progress: 0,
  };
}

function makeWorld(): BattleWorld & { lines: string[] } {
  const lines: string[] = [];
  const prov: Province = {
    id: 0,
    name: 'ساحة',
    cells: [{ x: 0, y: 0 }],
    center: { x: 0, y: 0 },
    terrain: 'plains',
    neighbors: [],
    ownerId: 0,
    city: null,
    garrison: 0,
    lastBattleTick: -99999,
  };
  const world: BattleWorld & { lines: string[]; tickNum: number } = {
    lines,
    provinces: [prov],
    nations: [
      { id: 0, name: 'أ' },
      { id: 1, name: 'ب' },
    ],
    armies: [makeArmy(1, 0, 3000), makeArmy(2, 1, 3000)],
    tickNum: 0,
    get tick() {
      return this.tickNum;
    },
    log: (t: string) => {
      lines.push(t);
    },
    captureProvince: (pid: number, by: number) => {
      world.provinces[pid].ownerId = by;
    },
    disbandArmy: (id: number) => {
      world.armies = world.armies.filter((a) => a.id !== id);
    },
  };
  return world;
}

function run(seed: number, ticks: number): { a: number; b: number } {
  const w = makeWorld() as BattleWorld & { lines: string[]; tickNum: number };
  const rng = new Rng(seed);
  for (let t = 1; t <= ticks; t++) {
    w.tickNum = t;
    tickBattles(w, rng);
  }
  const a = w.armies.find((x) => x.id === 1)?.soldiers ?? 0;
  const b = w.armies.find((x) => x.id === 2)?.soldiers ?? 0;
  return { a, b };
}

describe('محرك المعارك', () => {
  it('القتال يسبب خسائر للطرفين', () => {
    const { a, b } = run(7, 50);
    expect(a).toBeLessThan(3000);
    expect(b).toBeLessThan(3000);
  });

  it('حتمي: نفس البذرة = نفس النتيجة', () => {
    expect(run(42, 100)).toEqual(run(42, 100));
  });
});
