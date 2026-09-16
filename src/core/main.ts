import { GameEngine } from './game.js';
import { NewsPanel } from '../ui/newsPanel.js';
import { HUDController } from '../ui/hud.js';
import { WorldRenderer } from '../rendering/worldMap.js';
import { eventBus } from './eventBus.js';
import { GameEvent } from './types.js';

// Initialize everything
const game = new GameEngine(438921);
const newsPanel = new NewsPanel('newsPanel');
const hud = new HUDController();
const renderer = new WorldRenderer('gameCanvas', game.world);

eventBus.subscribe('all', (event: GameEvent) => {
  newsPanel.addNews(event);
  console.log(`[${event.year}] ${event.description}`);
});

// Subscribe to nation updates for HUD
function updateHUD() {
  const nations = game.world.nations;
  hud.updateYear(game.clock.year);
  hud.updateNations(nations.size);

  // Aggregate stats
  let totalTreasury = 0;
  let totalFood = 0;
  let wars = 0;
  for (const n of nations.values()) {
    totalTreasury += n.state.treasury;
    totalFood += n.state.food;
  }
  hud.updateGold(Math.round(totalTreasury));
  hud.updateFood(Math.round(totalFood));
  hud.updateWars(wars);
}

// Main loop: render and update
function gameLoop() {
  if (game.running) {
    renderer.render();
    updateHUD();
    requestAnimationFrame(gameLoop);
  }
}

// Auto-start simulation
window.addEventListener('load', () => {
  game.start();
  gameLoop();
  console.log('عالم سِيَر الممالك يعمل الآن — كل دولة تفكر بشكل مستقل');
  console.log('المحاكاة بدأت بالبذرة:', 438921);
});
