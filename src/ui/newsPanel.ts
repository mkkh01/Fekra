import { GameEvent } from '../core/types.js';

export class NewsPanel {
  private container: HTMLElement;
  private maxItems: number = 20;

  constructor(containerId: string = 'newsPanel') {
    const el = document.getElementById(containerId);
    if (!el) throw new Error(`News panel container #${containerId} not found`);
    this.container = el;
  }

  addNews(event: GameEvent) {
    const item = document.createElement('div');
    item.className = 'news-item';
    item.innerHTML = `<span class="news-time">سنة ${event.year}</span> — ${event.description}`;
    this.container.insertBefore(item, this.container.firstChild);

    // Keep only latest items
    while (this.container.children.length > this.maxItems) {
      this.container.removeChild(this.container.lastChild!);
    }
  }

  clear() {
    this.container.innerHTML = '';
  }
}
