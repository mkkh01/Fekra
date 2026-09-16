export interface SpyReport {
  agentId: string;
  targetNationId: string;
  confidence: number; // 0-100
  age: number; // days
  info: string;
  isDoubleAgent: boolean;
  skill: number;
}

export class SpyNetwork {
  agents: Map<string, SpyReport> = new Map();

  deployAgent(agentId: string, targetId: string, skill: number) {
    const confidence = Math.min(100, skill * 0.8 + Math.random() * 20);
    this.agents.set(agentId, {
      agentId,
      targetNationId: targetId,
      confidence: Math.round(confidence),
      age: 0,
      info: `معلومات أولية عن ${targetId}`,
      isDoubleAgent: Math.random() < 0.1, // 10% chance of being double agent initially
      skill,
    });
  }

  gatherReport(agentId: string): SpyReport | null {
    const agent = this.agents.get(agentId);
    if (!agent) return null;
    agent.age += 1;
    agent.confidence = Math.max(10, agent.confidence - 2); // Information becomes stale
    return agent;
  }

  detectDoubleAgent(agentId: string): boolean {
    const agent = this.agents.get(agentId);
    if (!agent) return false;
    // Counter-intelligence check
    return agent.isDoubleAgent && Math.random() > 0.7;
  }
}
