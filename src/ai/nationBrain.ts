import { PersonalityProfile } from '../core/types.js';
import { SeededRNG } from '../core/rng.js';

/**
 * The Independent Intelligence Brain for each nation.
 * This implements the user's core request: each nation has its own
 * intelligent foundation — a hybrid of algorithmic rules and simple
 * local adaptation, making emergent behavior possible.
 */
export class NationBrain {
  personality: PersonalityProfile;
  memory: Map<string, number> = new Map(); // event key -> emotional weight
  adaptationRate: number = 0.3;
  rng: SeededRNG;

  constructor(personality: PersonalityProfile, rng: SeededRNG) {
    this.personality = { ...personality };
    this.rng = rng;
  }

  /** Evaluate a potential action using multi-factor utility */
  evaluateUtility(action: {
    name: string;
    militaryGain: number;
    economicGain: number;
    diplomaticGain: number;
    risk: number;
    defensiveValue: number;
  }): number {
    let score = 0;

    // Personality weights — each nation values things differently
    score += action.militaryGain * (this.personality.aggression / 100);
    score += action.economicGain * (this.personality.economicGreed / 100);
    score += action.diplomaticGain * (this.personality.diplomacy / 100);
    score -= action.risk * ((100 - this.personality.riskTolerance) / 100);
    score += action.defensiveValue * (this.personality.fear / 100);

    // Memory influence: past events shift weights
    const memSum = Array.from(this.memory.values()).reduce((a, b) => a + b, 0);
    const avgMemory = this.memory.size > 0 ? memSum / this.memory.size : 0;
    score += avgMemory * 5; // Positive memories boost cooperation; negative reduce it

    // Emergent factor: if nation is very stable, it takes fewer risks
    if (this.personality.stability > 80) {
      score -= action.risk * 0.3;
    }

    return score;
  }

  /** Adapt personality slightly based on recent action outcomes */
  adapt(traitName: keyof PersonalityProfile, delta: number) {
    const current = this.personality[traitName];
    const newVal = Math.max(0, Math.min(100, current + delta * this.adaptationRate));
    (this.personality as any)[traitName] = newVal;
  }

  /** Build a strategic plan based on current state — GOAP-inspired */
  buildPlan(goals: string[], worldState: { threatLevel: number; economicHealth: number; stability: number }): string[] {
    const plan: string[] = [];

    if (worldState.threatLevel > 60) {
      plan.push('defend_borders');
      if (this.personality.aggression > 60 && worldState.stability > 50) {
        plan.push('counter_attack');
      }
    }

    if (worldState.economicHealth < 40) {
      plan.push('increase_production');
      plan.push('trade_negotiation');
    }

    if (this.personality.expansion > 70 && worldState.stability > 60) {
      plan.push('expand_territory');
    }

    if (this.personality.espionage > 50) {
      plan.push('send_spies');
    }

    // Always include at least one action — emergent behavior requires continuous action
    if (plan.length === 0) {
      plan.push('maintain_stability');
    }

    return plan;
  }
}
