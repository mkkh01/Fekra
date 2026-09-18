// شعارات الدول: دروع heraldic إجرائية — رمز مميز لكل دولة يثبت عبر اللعبة كلها.
// كل شعار = خلفية بلون الدولة + نمط درع + رمز مركزي (شعار).

export interface Emblem {
  /** الرمز المركزي (إيموجي) */
  charge: string;
  /** نمط الدرع */
  pattern: 'solid' | 'pale' | 'bands' | 'chevron' | 'cross' | 'base';
}

/** الدولتان المؤسستان لهما شعار موضوعي متوافق مع اسمهما */
const THEMED: Record<number, Emblem> = {
  0: { charge: '☀️', pattern: 'pale' }, // دولة الفجر — عمود شروق
  1: { charge: '🌙', pattern: 'bands' }, // دولة الغسق — حزمتا غروب
};
const CHARGES = ['⭐', '🦁', '🦅', '', '🔥', '⚔️', '🏹', '🐉', '🦌', '🛡️', '🗡️', '🐎'];
const PATTERNS: Emblem['pattern'][] = ['solid', 'chevron', 'cross', 'base'];

export function emblemFor(id: number): Emblem {
  return (
    THEMED[id] ?? {
      charge: CHARGES[(id * 5 + 2) % CHARGES.length],
      pattern: PATTERNS[(id * 3 + 1) % PATTERNS.length],
    }
  );
}

function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return [200, 200, 200];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

export function drawEmblem(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  color: string,
  darkColor: string,
  em: Emblem,
): void {
  ctx.save();
  ctx.translate(x, y);

  const path = () => {
    ctx.beginPath();
    ctx.moveTo(-r, -r * 0.95);
    ctx.lineTo(r, -r * 0.95);
    ctx.lineTo(r, r * 0.15);
    ctx.quadraticCurveTo(r * 0.85, r * 0.78, 0, r * 1.08);
    ctx.quadraticCurveTo(-r * 0.85, r * 0.78, -r, r * 0.15);
    ctx.closePath();
  };

  // خلفية متدرجة بلون الدولة
  const rgb = hexToRgb(color);
  const k = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  const g = ctx.createLinearGradient(0, -r, 0, r * 1.08);
  g.addColorStop(0, `rgb(${k(rgb[0] * 1.22)},${k(rgb[1] * 1.22)},${k(rgb[2] * 1.22)})`);
  g.addColorStop(1, `rgb(${k(rgb[0] * 0.72)},${k(rgb[1] * 0.72)},${k(rgb[2] * 0.72)})`);
  path();
  ctx.fillStyle = g;
  ctx.fill();

  // النمط الوراثي
  ctx.save();
  path();
  ctx.clip();
  ctx.fillStyle = darkColor;
  ctx.strokeStyle = darkColor;
  switch (em.pattern) {
    case 'pale':
      ctx.fillRect(-r * 0.22, -r, r * 0.44, 2 * r);
      break;
    case 'bands':
      ctx.fillRect(-r, -r * 0.8, 2 * r, r * 0.34);
      ctx.fillRect(-r, r * 0.08, 2 * r, r * 0.34);
      break;
    case 'chevron':
      ctx.lineWidth = r * 0.3;
      ctx.beginPath();
      ctx.moveTo(-r * 0.75, -r * 0.2);
      ctx.lineTo(0, r * 0.5);
      ctx.lineTo(r * 0.75, -r * 0.2);
      ctx.stroke();
      break;
    case 'cross':
      ctx.fillRect(-r * 0.17, -r, r * 0.34, 2 * r);
      ctx.fillRect(-r, -r * 0.45, 2 * r, r * 0.34);
      break;
    case 'base':
      ctx.beginPath();
      ctx.moveTo(-r, r * 0.1);
      ctx.lineTo(r, r * 0.1);
      ctx.lineTo(r, r * 0.15);
      ctx.quadraticCurveTo(r * 0.85, r * 0.78, 0, r * 1.08);
      ctx.quadraticCurveTo(-r * 0.85, r * 0.78, -r, r * 0.15);
      ctx.closePath();
      ctx.fill();
      break;
    case 'solid':
      break;
  }
  ctx.restore();

  // الرمز المركزي
  if (em.charge) {
    ctx.font = `${Math.max(8, r * 1.02)}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(em.charge, 0, -r * 0.18);
  }

  // لمعة علوية + حافة داكنة
  path();
  ctx.lineWidth = Math.max(1.4, r * 0.16);
  ctx.strokeStyle = '#14100a';
  ctx.stroke();
  ctx.beginPath();
  ctx.ellipse(-r * 0.32, -r * 0.55, r * 0.5, r * 0.22, -0.45, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(255,255,255,0.16)';
  ctx.fill();

  ctx.restore();
}
