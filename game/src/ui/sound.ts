// صوت مُركَّب بالكامل بـ WebAudio — لا ملفات صوتية، لا الشبكة.
// طبول معارك تتسارع مع حدّتها، بوق حرب عند الأخبار السيئة، رنينٌ خفيف عند الجيدة.

import type { Game } from '../sim/game';

const MUTE_KEY = 'siyar-mute';

export class Sound {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  muted = localStorage.getItem(MUTE_KEY) === '1';
  private lastBeat = 0;
  private lastHorn = 0;
  private lastNewsSeen = -1;
  private lastClick = 0;

  private ensure(): AudioContext | null {
    if (this.ctx) return this.ctx;
    try {
      const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AC) return null;
      this.ctx = new AC();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.22;
      this.master.connect(this.ctx.destination);
    } catch {
      this.ctx = null;
    }
    return this.ctx;
  }

  toggle(): boolean {
    this.muted = !this.muted;
    localStorage.setItem(MUTE_KEY, this.muted ? '1' : '0');
    return this.muted;
  }

  private tone(
    freq: number,
    dur: number,
    type: OscillatorType = 'sine',
    vol = 0.5,
    slideTo?: number,
    delay = 0,
  ): void {
    const ctx = this.ctx;
    const master = this.master;
    if (!ctx || !master) return;
    const t0 = ctx.currentTime + delay;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0);
    if (slideTo) osc.frequency.exponentialRampToValueAtTime(Math.max(20, slideTo), t0 + dur);
    g.gain.setValueAtTime(vol, t0);
    g.gain.exponentialRampToValueAtTime(0.001, t0 + dur);
    osc.connect(g).connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  }

  /** نقرة واجهة قصيرة */
  click(): void {
    if (this.muted) return;
    const now = performance.now();
    if (now - this.lastClick < 80) return;
    this.lastClick = now;
    if (!this.ensure()) return;
    this.tone(620, 0.05, 'triangle', 0.25, 760);
  }

  /** يُستدعى كل إطار — ينظم إيقاعه بنفسه */
  update(g: Game): void {
    if (this.muted) return;
    const now = performance.now();

    // طبل المعركة: عدد الجبهات المشتعلة يحدد الكثافة
    if (now - this.lastBeat > 260) {
      const fronts = g.provinces.reduce((n, p) => n + (g.tick - p.lastBattleTick < 12 ? 1 : 0), 0);
      if (fronts > 0) {
        if (!this.ensure()) return;
        this.lastBeat = now;
        const heat = Math.min(3, fronts);
        this.tone(150, 0.16, 'sine', 0.5 + heat * 0.12, 46); // دقة منخفض
        if (heat >= 2) this.tone(150, 0.13, 'sine', 0.34, 50, 0.22);
        if (heat >= 3) this.tone(96, 0.1, 'square', 0.12, 60, 0.11);
      }
    }

    // أخبار العالم: بوقٌ للسيء، رنينٌ للجيد
    const n0 = g.news[0];
    if (n0 && n0.tick !== this.lastNewsSeen) {
      this.lastNewsSeen = n0.tick;
      if (n0.kind === 'war' || n0.kind === 'bad') {
        if (now - this.lastHorn > 1300) {
          this.lastHorn = now;
          if (this.ensure()) {
            const ctx = this.ctx!;
            const lp = ctx.createBiquadFilter();
            lp.type = 'lowpass';
            lp.frequency.value = 720;
            lp.connect(this.master!);
            for (const f of [196, 294]) {
              const osc = ctx.createOscillator();
              const g2 = ctx.createGain();
              osc.type = 'sawtooth';
              osc.frequency.value = f;
              g2.gain.setValueAtTime(0.2, ctx.currentTime);
              g2.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 1.1);
              osc.connect(g2).connect(lp);
              osc.start();
              osc.stop(ctx.currentTime + 1.15);
            }
          }
        }
      } else if (n0.kind === 'good') {
        if (this.ensure()) {
          this.tone(880, 0.16, 'triangle', 0.22);
          this.tone(1320, 0.12, 'triangle', 0.12, undefined, 0.09);
        }
      }
    }
  }
}
