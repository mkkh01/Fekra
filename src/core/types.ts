export type TerrainType = 'plains' | 'hills' | 'mountains' | 'forest' | 'desert' | 'swamp' | 'river' | 'coast';
export type DoctrineType = 'blitz' | 'fortress' | 'attrition' | 'encirclement' | 'raid' | 'naval';
export type GovernmentType = 'monarchy' | 'republic' | 'oligarchy' | 'theocracy' | 'tribal';

export interface NationMemory {
  event: string;
  year: number;
  impact: number; // -100 إلى 100
  type: 'betrayal' | 'alliance' | 'war' | 'aid' | 'assassination' | 'trade';
}

export interface PersonalityProfile {
  aggression: number;       // 0-100
  expansion: number;        // 0-100
  fear: number;             // 0-100
  caution: number;          // 0-100
  trust: number;            // 0-100
  diplomacy: number;        // 0-100
  espionage: number;        // 0-100
  riskTolerance: number;    // 0-100
  economicGreed: number;    // 0-100
  stability: number;        // 0-100
  treatyRespect: number;    // 0-100
  betrayalTendency: number; // 0-100
  revenge: number;          // 0-100
  patience: number;         // 0-100
}

export interface NationState {
  id: string;
  name: string;
  rulerName: string;
  government: GovernmentType;
  culture: string;
  ideology: string;
  treasury: number;
  population: number;
  food: number;
  production: number;
  militaryDoctrine: DoctrineType;
  diplomaticStyle: string;
  expansionDesire: number;
  riskTolerance: number;
  trustProfile: Record<string, number>; // تجاه دول أخرى
  fearLevel: number;
  internalStability: number;
  intelligenceCapability: number;
  technologyLevel: number;
  territories: string[];
  cities: string[];
  armies: string[];
  treaties: string[];
  enemies: string[];
  friends: string[];
  memory: NationMemory[];
  personality: PersonalityProfile;
  primaryGoal: string;
  secondaryGoals: string[];
  secretGoals: string[];
}

export interface GameEvent {
  type: 'war_start' | 'war_end' | 'treaty_sign' | 'treaty_break' | 'betrayal' | 'assassination' | 'civil_war' | 'famine' | 'rebellion' | 'leader_death' | 'alliance_formed' | 'economic_collapse';
  year: number;
  description: string;
  involvedNations: string[];
  impact: number;
}
