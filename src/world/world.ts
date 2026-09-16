import { SeededRNG } from '../core/rng.js';
import { GameClock } from '../core/clock.js';
import { EventBus } from '../core/eventBus.js';
import { Nation } from '../nations/nation.js';

export class World {
  rng: SeededRNG;
  eventBus: EventBus;
  nations: Map<string, Nation> = new Map();
  seed: number;

  constructor(rng: SeededRNG, eventBus: EventBus, seed: number = 438921) {
    this.rng = rng;
    this.eventBus = eventBus;
    this.seed = seed;
  }

  initialize() {
    // Create 20 initial nations with unique personalities
    const names = [
      'مملكة الشمال', 'إمبراطورية الجنوب', 'دولة الشرق', 'جمهورية الغرب',
      'قبائل الجبال', 'مملكة السهول', 'دولة الموانئ', 'إمارة الصحراء',
      'اتحاد الأنهار', 'مملكة الغابات', 'دولة التلال', 'جمهورية السواحل',
      'قبائل المستنقعات', 'مملكة الحديد', 'دولة التجارة', 'إمارة الحدود',
      'مملكة الذهب', 'دولة العلم', 'جمهورية الشمال البعيد', 'إمبراطورية الوسط'
    ];

    for (let i = 0; i < 20; i++) {
      const nation = new Nation(
        `nation_${i}`,
        names[i],
        this.rng,
        this.eventBus
      );
      this.nations.set(nation.id, nation);
    }
  }

  updateTick(clock: GameClock) {
    // Each nation thinks and acts
    for (const nation of this.nations.values()) {
      nation.thinkAndAct(clock, this.nations);
    }

    // Update world-level events based on interactions
    this.generateWorldEvents(clock);
  }

  generateWorldEvents(clock: GameClock) {
    // Random world event generation based on current state
    // This is the emergent system: no scripted scenarios
  }
}
