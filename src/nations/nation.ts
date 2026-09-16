import { SeededRNG } from '../core/rng.js';
import { GameClock } from '../core/clock.js';
import { EventBus } from '../core/eventBus.js';
import { NationState, PersonalityProfile, DoctrineType, GovernmentType, NationMemory, GameEvent } from '../core/types.js';

export class Nation {
  id: string;
  name: string;
  state: NationState;
  rng: SeededRNG;
  eventBus: EventBus;

  constructor(id: string, name: string, rng: SeededRNG, eventBus: EventBus) {
    this.id = id;
    this.name = name;
    this.rng = rng;
    this.eventBus = eventBus;
    this.state = this.generateInitialState();

    this.eventBus.subscribe('all', (e: GameEvent) => this.onWorldEvent(e));
  }

  private generateInitialState(): NationState {
    const doctrines: DoctrineType[] = ['blitz', 'fortress', 'attrition', 'encirclement', 'raid'];
    const governments: GovernmentType[] = ['monarchy', 'republic', 'oligarchy', 'tribal'];

    return {
      id: this.id,
      name: this.name,
      rulerName: `حاكم ${this.name}`,
      government: this.rng.choice(governments),
      culture: `ثقافة ${this.id}`,
      ideology: `أيديولوجيا ${this.id}`,
      treasury: this.rng.int(5000, 30000),
      population: this.rng.int(50000, 500000),
      food: this.rng.int(70, 150),
      production: this.rng.int(30, 150),
      militaryDoctrine: this.rng.choice(doctrines),
      diplomaticStyle: this.rng.choice(['عدواني', 'حذر', 'تجاري', 'انعزالي']),
      expansionDesire: this.rng.int(10, 100),
      riskTolerance: this.rng.int(10, 100),
      trustProfile: {},
      fearLevel: this.rng.int(10, 100),
      internalStability: this.rng.int(40, 100),
      intelligenceCapability: this.rng.int(20, 90),
      technologyLevel: this.rng.int(1, 5),
      territories: [`منطقة_${this.id}`],
      cities: [`مدينة_${this.id}`],
      armies: [`جيش_${this.id}`],
      treaties: [],
      enemies: [],
      friends: [],
      memory: [],
      personality: this.generatePersonality(),
      primaryGoal: this.generatePrimaryGoal(),
      secondaryGoals: this.generateSecondaryGoals(),
      secretGoals: this.generateSecretGoals(),
    };
  }

  private generatePersonality(): PersonalityProfile {
    return {
      aggression: this.rng.int(0, 100),
      expansion: this.rng.int(0, 100),
      fear: this.rng.int(0, 100),
      caution: this.rng.int(0, 100),
      trust: this.rng.int(0, 100),
      diplomacy: this.rng.int(0, 100),
      espionage: this.rng.int(0, 100),
      riskTolerance: this.rng.int(0, 100),
      economicGreed: this.rng.int(0, 100),
      stability: this.rng.int(0, 100),
      treatyRespect: this.rng.int(0, 100),
      betrayalTendency: this.rng.int(0, 100),
      revenge: this.rng.int(0, 100),
      patience: this.rng.int(0, 100),
    };
  }

  private generatePrimaryGoal(): string {
    const goals = [
      'تأمين العاصمة',
      'زيادة الغذاء',
      'السيطرة على الممر الشرقي',
      'إسقاط الدولة المجاورة',
      'بناء إمبراطورية تجارية',
      'حماية التحالفات',
      'زيادة القوة العسكرية'
    ];
    return this.rng.choice(goals);
  }

  private generateSecondaryGoals(): string[] {
    const all = [
      'زيادة الغذاء', 'بناء حصون', 'تحسين الاستخبارات',
      'عقد تحالفات', 'زيادة الإنتاج', 'حماية الحدود'
    ];
    return [this.rng.choice(all), this.rng.choice(all)].filter((v, i, a) => a.indexOf(v) === i);
  }

  private generateSecretGoals(): string[] {
    if (this.rng.next() < 0.4) return ['إضعاف الدولة المجاورة سرًا'];
    return [];
  }

  // The intelligent brain: independent decision making
  thinkAndAct(clock: GameClock, allNations: Map<string, Nation>) {
    // This is the emergent AI brain — no scripts, just system interactions
    // Utility AI evaluation
    const utilityScores: Map<string, number> = new Map();

    // Evaluate potential actions based on personality, memory, and world state
    const actions = this.generatePossibleActions(allNations);

    for (const action of actions) {
      let score = 0;

      // Personality weights
      score += action.utility * (this.state.personality.aggression / 50);
      score += action.safety * (this.state.personality.caution / 50);
      score += action.economicGain * (this.state.personality.economicGreed / 50);
      score += action.strategicValue * (this.state.expansionDesire / 50);

      // Memory influence: past betrayals increase caution, past alliances increase cooperation
      for (const mem of this.state.memory) {
        if (mem.type === 'betrayal') score -= 15;
        if (mem.type === 'alliance') score += 10;
      }

      // Emergent behavior: if fear is high, defensive actions score higher
      if (this.state.fearLevel > 70) {
        score += action.defensiveValue * 2;
      }

      // Risk tolerance affects riskier actions
      if (action.risk > this.state.riskTolerance) {
        score -= (action.risk - this.state.riskTolerance) * 0.5;
      }

      utilityScores.set(action.type, score);
    }

    // Choose the best action (or sometimes not — probability based on patience)
    let bestAction = this.selectBestAction(utilityScores);

    // Simple local adaptation (hybrid layer): adjust personality slightly based on outcomes
    this.adaptFromOutcome(bestAction, clock);

    // Execute
    this.executeAction(bestAction, clock, allNations);
  }

  private generatePossibleActions(allNations: Map<string, Nation>): Array<{
    type: string;
    utility: number;
    safety: number;
    economicGain: number;
    strategicValue: number;
    defensiveValue: number;
    risk: number;
  }> {
    const actions = [
      { type: 'attack_neighbor', utility: 30, safety: 10, economicGain: 5, strategicValue: 25, defensiveValue: 0, risk: 80 },
      { type: 'defend_border', utility: 20, safety: 90, economicGain: 0, strategicValue: 15, defensiveValue: 90, risk: 10 },
      { type: 'build_fortress', utility: 15, safety: 80, economicGain: -10, strategicValue: 20, defensiveValue: 70, risk: 20 },
      { type: 'send_spy', utility: 25, safety: 40, economicGain: 0, strategicValue: 30, defensiveValue: 0, risk: 60 },
      { type: 'propose_alliance', utility: 35, safety: 60, economicGain: 10, strategicValue: 25, defensiveValue: 20, risk: 30 },
      { type: 'increase_food', utility: 20, safety: 70, economicGain: 20, strategicValue: 10, defensiveValue: 10, risk: 15 },
      { type: 'trade_deal', utility: 15, safety: 80, economicGain: 30, strategicValue: 10, defensiveValue: 0, risk: 10 },
    ];
    return actions;
  }

  private selectBestAction(scores: Map<string, number>): string {
    let best = 'defend_border';
    let bestScore = -9999;
    for (const [type, score] of scores) {
      if (score > bestScore) {
        bestScore = score;
        best = type;
      }
    }
    return best;
  }

  private adaptFromOutcome(action: string, clock: GameClock) {
    // Simple local learning: small personality adjustments based on outcome
    // This creates the "hybrid" intelligent behavior the user requested
    const adaptationRate = 0.5; // Very small adjustments — emergent, not abrupt

    switch (action) {
      case 'attack_neighbor':
        if (this.state.treasury < 8000) {
          // If treasury is low after attacking, become more cautious
          this.state.personality.caution += adaptationRate;
        } else {
          this.state.personality.aggression += adaptationRate;
        }
        break;
      case 'propose_alliance':
        if (this.state.friends.length > 0) {
          this.state.personality.diplomacy += adaptationRate;
        }
        break;
      case 'send_spy':
        this.state.personality.espionage += adaptationRate;
        break;
      case 'defend_border':
        this.state.personality.caution += adaptationRate;
        break;
    }

    // Keep within 0-100
    for (const key of Object.keys(this.state.personality) as (keyof PersonalityProfile)[]) {
      const val = this.state.personality[key];
      this.state.personality[key] = Math.max(0, Math.min(100, val));
    }
  }

  private executeAction(action: string, clock: GameClock, allNations: Map<string, Nation>) {
    switch (action) {
      case 'attack_neighbor': {
        const targetId = this.getNeighborId(allNations);
        if (targetId) {
          this.eventBus.publish({
            type: 'war_start',
            year: clock.year,
            description: `${this.name} هاجمت ${allNations.get(targetId)?.name || 'دولة مجاورة'}`,
            involvedNations: [this.id, targetId],
            impact: -20,
          });
        }
        break;
      }
      case 'propose_alliance': {
        const targetId = this.getRandomOther(allNations);
        if (targetId && this.state.memory.filter(m => m.type === 'alliance').length < 3) {
          this.eventBus.publish({
            type: 'alliance_formed',
            year: clock.year,
            description: `${this.name} عقدت تحالفًا مع ${allNations.get(targetId)?.name}`,
            involvedNations: [this.id, targetId],
            impact: 15,
          });
          this.state.friends.push(targetId);
        }
        break;
      }
      case 'send_spy':
        this.eventBus.publish({
          type: 'betrayal',
          year: clock.year,
          description: `${this.name} أرسلت جاسوسًا لجمع معلومات`,
          involvedNations: [this.id],
          impact: 5,
        });
        break;
      case 'build_fortress':
        this.state.treasury -= 500;
        this.eventBus.publish({
          type: 'treaty_sign', // Using as event for construction
          year: clock.year,
          description: `${this.name} بنت حصنًا جديدًا`,
          involvedNations: [this.id],
          impact: 10,
        });
        break;
      case 'defend_border':
        this.state.fearLevel = Math.max(0, this.state.fearLevel - 5);
        break;
      case 'increase_food':
        this.state.food += 10;
        this.state.treasury -= 200;
        break;
      case 'trade_deal':
        this.state.treasury += 300;
        break;
    }
  }

  private getNeighborId(allNations: Map<string, Nation>): string | null {
    const others = Array.from(allNations.values()).filter(n => n.id !== this.id);
    if (others.length === 0) return null;
    return others[this.rng.int(0, others.length - 1)].id;
  }

  private getRandomOther(allNations: Map<string, Nation>): string | null {
    const others = Array.from(allNations.values()).filter(n => n.id !== this.id);
    if (others.length === 0) return null;
    return others[this.rng.int(0, others.length - 1)].id;
  }

  onWorldEvent(event: GameEvent) {
    // Each nation reacts to world events based on its personality and memory
    if (event.involvedNations.includes(this.id)) {
      this.state.memory.push({
        event: event.description,
        year: event.year,
        impact: event.impact,
        type: event.type.includes('betrayal') ? 'betrayal' :
              event.type.includes('alliance') ? 'alliance' :
              event.type.includes('war') ? 'war' : 'trade',
      });

      // Memory affects trust and fear
      if (event.impact < 0) {
        this.state.fearLevel += Math.abs(event.impact) * 0.5;
      } else {
        this.state.fearLevel = Math.max(0, this.state.fearLevel - event.impact * 0.2);
      }
    }
  }
}
