export class Army {
  id: string;
  name: string;
  size: number;
  morale: number;
  experience: number;
  supplyLevel: number;
  location: string;

  constructor(id: string, name: string, size: number = 5000) {
    this.id = id;
    this.name = name;
    this.size = size;
    this.morale = 70 + Math.random() * 30;
    this.experience = Math.random() * 50;
    this.supplyLevel = 100;
    this.location = 'home';
  }

  moveTo(location: string) {
    this.location = location;
    this.supplyLevel = Math.max(20, 100 - Math.abs(Math.random() * 30));
  }

  battleResult(opponentSize: number): { victory: boolean; casualties: number } {
    const power = this.size * (this.morale / 100) * (1 + this.experience / 100);
    const oppPower = opponentSize * 0.7; // Simplified
    const victory = power > oppPower;
    const casualties = Math.floor(this.size * (victory ? 0.05 : 0.2));
    this.size = Math.max(0, this.size - casualties);
    this.morale += victory ? 5 : -10;
    return { victory, casualties };
  }
}
