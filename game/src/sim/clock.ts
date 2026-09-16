// ساعة المحاكاة: نبضة ثابتة = ساعة واحدة. اليوم = 24 نبضة. السنة = 360 يومًا.

export const HOURS_PER_DAY = 24;
export const DAYS_PER_YEAR = 360;

export class Clock {
  /** عدد النبضات منذ بداية اللعبة */
  tick = 0;

  advance(n = 1): void {
    this.tick += n;
  }

  get hour(): number {
    return this.tick % HOURS_PER_DAY;
  }

  get day(): number {
    return (Math.floor(this.tick / HOURS_PER_DAY) % DAYS_PER_YEAR) + 1;
  }

  get year(): number {
    return Math.floor(this.tick / (HOURS_PER_DAY * DAYS_PER_YEAR)) + 1;
  }

  format(): string {
    const h = String(this.hour).padStart(2, '0');
    return `السنة ${this.year} — اليوم ${this.day} — ${h}:00`;
  }

  shortFormat(): string {
    return `س${this.year} ي${this.day}`;
  }
}

export function tickToDay(tick: number): number {
  return (Math.floor(tick / HOURS_PER_DAY) % DAYS_PER_YEAR) + 1;
}

export function tickToYear(tick: number): number {
  return Math.floor(tick / (HOURS_PER_DAY * DAYS_PER_YEAR)) + 1;
}
