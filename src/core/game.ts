import { GameClock } from './clock.js';
import { SeededRNG } from './rng.js';
import { eventBus } from './eventBus.js';
import { World } from '../world/world.js';

export class GameEngine {
  clock: GameClock;
  rng: SeededRNG;
  world: World;
  running: boolean = false;
  intervalId?: number;

  constructor(seed: number = 438921) {
    this.clock = new GameClock();
    this.rng = new SeededRNG(seed);
    this.world = new World(this.rng, eventBus);
  }

  start() {
    this.running = true;
    this.world.initialize();
    // Simulation tick: 500ms per tick
    this.intervalId = window.setInterval(() => this.tick(), 500);
  }

  stop() {
    this.running = false;
    if (this.intervalId) clearInterval(this.intervalId);
  }

  tick() {
    this.clock.tick();
    this.world.updateTick(this.clock);
  }
}
