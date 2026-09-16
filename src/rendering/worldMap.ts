import { World } from '../world/world.js';

export class WorldRenderer {
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  world: World;

  constructor(canvasId: string, world: World) {
    const el = document.getElementById(canvasId) as HTMLCanvasElement;
    if (!el) throw new Error('Canvas not found');
    this.canvas = el;
    this.ctx = el.getContext('2d')!;
    this.world = world;
    this.resize();
    window.addEventListener('resize', () => this.resize());
  }

  resize() {
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight;
  }

  render() {
    this.ctx.fillStyle = '#1a1510';
    this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

    // Draw a simple world map representation
    this.ctx.save();
    this.ctx.translate(this.canvas.width / 2, this.canvas.height / 2);

    // Draw territories as circles
    for (const [id, nation] of this.world.nations) {
      const angle = (parseInt(id.replace('nation_', '')) / 20) * Math.PI * 2;
      const radius = Math.min(this.canvas.width, this.canvas.height) * 0.35;
      const x = Math.cos(angle) * radius * (0.3 + Math.random() * 0.2);
      const y = Math.sin(angle) * radius * (0.3 + Math.random() * 0.2);

      // Color based on personality traits
      const aggression = nation.state.personality.aggression;
      const r = Math.min(255, 100 + aggression * 1.5);
      const g = Math.min(255, 50 + (100 - aggression) * 1.5);
      const b = 50;

      this.ctx.beginPath();
      this.ctx.arc(x, y, 15 + (nation.state.population / 50000), 0, Math.PI * 2);
      this.ctx.fillStyle = `rgb(${Math.round(r)}, ${Math.round(g)}, ${b})`;
      this.ctx.fill();
      this.ctx.strokeStyle = '#8b6f3a';
      this.ctx.lineWidth = 1;
      this.ctx.stroke();

      // Label
      this.ctx.fillStyle = '#e8dcc8';
      this.ctx.font = '10px sans-serif';
      this.ctx.fillText(nation.name, x, y - 20);
    }

    this.ctx.restore();
  }
}
