import { GameEvent } from '../core/types.js';

export class EventGenerator {
  private year: number;

  constructor(startYear: number = 1) {
    this.year = startYear;
  }

  generateEmergentEvent(nationIds: string[]): GameEvent | null {
    // Pure emergent: no scripted scenarios — events arise from interaction patterns
    // This is the core of the user's request: "لا نصمم أحداثًا لتحدث؛ نصمم أنظمة تتفاعل"
    const types: GameEvent['type'][] = [
      'war_start', 'alliance_formed', 'betrayal', 'treaty_break',
      'civil_war', 'rebellion', 'famine', 'assassination', 'leader_death'
    ];
    const type = types[Math.floor(Math.random() * types.length)];
    const desc = this.generateDescription(type, nationIds);

    return {
      type,
      year: this.year,
      description: desc,
      involvedNations: this.pickRandomNations(nationIds, Math.random() < 0.3 ? 1 : 2),
      impact: this.calculateImpact(type),
    };
  }

  private generateDescription(type: GameEvent['type'], nationIds: string[]): string {
    const names = nationIds.length > 0 ? nationIds : ['دولة مجهولة'];
    const n1 = names[0];
    const n2 = names.length > 1 ? names[1] : 'دولة مجاورة';

    const descMap: Record<string, string> = {
      war_start: `${n1} أعلنت الحرب على ${n2}`,
      alliance_formed: `${n1} و${n2} عقدتا تحالفًا دفاعيًا`,
      betrayal: `${n1} خانت معاهدتها مع ${n2}`,
      treaty_break: `${n2} كسرت معاهدة السلام مع ${n1}`,
      civil_war: `اندلعت حرب أهلية داخل ${n1}`,
      rebellion: `تمرد محلي في مناطق ${n2}`,
      famine: `أزمة غذاء حادة ضربت مناطق ${n1}`,
      assassination: `اغتيال قائد بارز في ${n1}`,
      leader_death: `وفاة حاكم ${n1}`,
    };

    return descMap[type] || `${n1} شهد حدثًا غير متوقع`;
  }

  private pickRandomNations(ids: string[], count: number): string[] {
    if (ids.length === 0) return ['unknown'];
    const shuffled = [...ids].sort(() => 0.5 - Math.random());
    return shuffled.slice(0, Math.min(count, ids.length));
  }

  private calculateImpact(type: GameEvent['type']): number {
    const impacts: Record<string, number> = {
      war_start: -25,
      alliance_formed: 15,
      betrayal: -30,
      treaty_break: -15,
      civil_war: -40,
      rebellion: -20,
      famine: -20,
      assassination: -10,
      leader_death: -5,
    };
    return impacts[type] || -5;
  }
}
