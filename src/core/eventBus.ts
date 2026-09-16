import { GameEvent } from './types.js';

export class EventBus {
  private listeners: Map<string, ((event: GameEvent) => void)[]> = new Map();

  subscribe(eventType: string, callback: (event: GameEvent) => void) {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, []);
    }
    this.listeners.get(eventType)!.push(callback);
  }

  publish(event: GameEvent) {
    const allCallbacks = this.listeners.get('all') || [];
    allCallbacks.forEach(cb => cb(event));

    const specific = this.listeners.get(event.type) || [];
    specific.forEach(cb => cb(event));
  }

  unsubscribe(eventType: string, callback: (event: GameEvent) => void) {
    const arr = this.listeners.get(eventType);
    if (arr) {
      const idx = arr.indexOf(callback);
      if (idx > -1) arr.splice(idx, 1);
    }
  }
}

export const eventBus = new EventBus();
