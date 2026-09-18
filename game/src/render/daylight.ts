// لون السماء حسب ساعة اللعبة — مشترك بين عارض 2D و3D.

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** مفاتيح لون السماء عبر ساعات اليوم: [الساعة، r، g، b، الشدة] */
const SKY_KEYS: [number, number, number, number, number][] = [
  [0, 14, 24, 56, 0.5],
  [4.5, 16, 28, 62, 0.45],
  [6.5, 255, 160, 90, 0.17],
  [8, 255, 210, 140, 0],
  [16.5, 255, 210, 140, 0],
  [18.5, 255, 128, 56, 0.21],
  [20.5, 58, 44, 96, 0.32],
  [22.5, 14, 24, 56, 0.5],
  [24, 14, 24, 56, 0.5],
];

export function skyTint(hour: number): [number, number, number, number] {
  const h = ((hour % 24) + 24) % 24;
  for (let i = 0; i < SKY_KEYS.length - 1; i++) {
    const [h0, r0, g0, b0, a0] = SKY_KEYS[i];
    const [h1, r1, g1, b1, a1] = SKY_KEYS[i + 1];
    if (h >= h0 && h <= h1) {
      const t = h1 === h0 ? 0 : (h - h0) / (h1 - h0);
      return [lerp(r0, r1, t), lerp(g0, g1, t), lerp(b0, b1, t), lerp(a0, a1, t)];
    }
  }
  return [0, 0, 0, 0];
}

/** كثافة الظلام الليلية 0..1 من شدة الصبغة */
export function nightAmount(ta: number): number {
  return Math.max(0, Math.min(1, (ta - 0.17) / 0.3));
}
