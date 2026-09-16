export class GameClock {
  year: number = 1;
  tickCount: number = 0;
  speed: number = 1; // 1 = normal, 2 = fast, 5 = very fast, 10 = simulation

  tick() {
    this.tickCount++;
    if (this.tickCount % 12 === 0) {
      this.year++;
    }
  }

  getMonth(): number {
    return (this.tickCount % 12) + 1;
  }
}
