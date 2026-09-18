// نقطة الدخول: ربط المحاكاة + العرض (2D/3D) + الواجهة + حلقة اللعبة.

import './style.css';
import { Game } from './sim/game';
import { MapView } from './render/mapView';
import { MapView3D } from './render/gl3d';
import { buildTopbarHTML, renderBanner, renderNews, renderPanel, updateTopbar } from './ui/ui';
import { Sound } from './ui/sound';

interface ViewLike {
  setWorld(w: number, h: number): void;
  fit?(): void;
  centerOnCell(x: number, y: number): void;
  render(g: Game): void;
  setSpeed(v: number): void;
  onTap: ((cell: { x: number; y: number }) => void) | null;
}

const SAVE_KEY = 'siyar-save-v1';
const MODE_KEY = 'siyar-mode';
const DEFAULT_SEED = 438921; // بذرة الوثيقة
const BASE_MS_PER_TICK = 120; // مدة النبضة بالمللي ثانية عند السرعة 1

function seedFromURL(): number | null {
  const m = new URLSearchParams(window.location.search).get('seed');
  if (!m) return null;
  const n = Number(m);
  return Number.isFinite(n) ? Math.floor(n) : null;
}

let game = new Game(seedFromURL() ?? DEFAULT_SEED);
let speed = 1;

const canvas = document.getElementById('map') as HTMLCanvasElement;
const glCanvas = document.createElement('canvas');
glCanvas.id = 'map3d';
document.getElementById('mapWrap')!.appendChild(glCanvas);
const panelEl = document.getElementById('panel')!;
const newsEl = document.getElementById('news')!;
const bannerEl = document.getElementById('banner')!;
const topbarEl = document.getElementById('topbar')!;
topbarEl.innerHTML = buildTopbarHTML(game.seed);

// ===== العارضات: تبديل حي بين 2D و3D =====
let view: ViewLike;
let view3d: ViewLike | null = null;
let view2d: ViewLike | null = null;
let mode3d = false;

function focusCapital(v: ViewLike): void {
  const c = game.provinces[game.player().capitalProvinceId].center;
  v.centerOnCell(c.x, c.y);
}

function makeView(mode: '2d' | '3d'): ViewLike {
  const v: ViewLike =
    mode === '3d' ? new MapView3D(glCanvas) : new MapView(canvas);
  v.setWorld(game.width, game.height);
  v.setSpeed(speed);
  focusCapital(v);
  return v;
}

function wireTap(v: ViewLike): void {
  v.onTap = (cell) => {
    sound.click();
    if (game.over) return;
    const pid = game.provinceAt(cell.x, cell.y);
    if (pid < 0) return;
    const me = game.player();
    const sel = game.selection;
    if (sel?.type === 'army') {
      const a = game.armies.find((x) => x.id === sel.id);
      if (a && a.nationId === me.id) {
        if (pid === a.provinceId) {
          game.selection = { type: 'province', id: pid };
          renderUI();
          return;
        }
        game.issueMoveOrder(a.id, pid);
        renderUI();
        return;
      }
    }
    const own = game.armies.find((x) => x.provinceId === pid && x.nationId === me.id);
    if (own) game.selection = { type: 'army', id: own.id };
    else {
      const foe = game.armies.find((x) => x.provinceId === pid);
      if (foe) game.selection = { type: 'army', id: foe.id };
      else game.selection = { type: 'province', id: pid };
    }
    renderUI();
  };
}

function applyMode(m: boolean): void {
  mode3d = m;
  localStorage.setItem(MODE_KEY, m ? '3d' : '2d');
  canvas.style.display = m ? 'none' : 'block';
  glCanvas.style.display = m ? 'block' : 'none';
  if (m) {
    view3d ??= makeView('3d');
    view = view3d;
  } else {
    view2d ??= makeView('2d');
    view = view2d;
  }
  wireTap(view);
  updateModeBtn();
}

function refreshViewsWorld(): void {
  for (const v of [view3d, view2d]) {
    if (!v) continue;
    v.setWorld(game.width, game.height);
    v.setSpeed(speed);
    focusCapital(v);
  }
}

function updateModeBtn(): void {
  const b = topbarEl.querySelector('[data-action="mode"]');
  if (b) b.textContent = mode3d ? '🗺️' : '🧊';
}

const sound = new Sound();
// أول تفاعل للمستخدم يفعّل WebAudio (سياسة المتصفحات)
const unlockAudio = () => {
  sound.click();
  window.removeEventListener('pointerdown', unlockAudio);
  window.removeEventListener('keydown', unlockAudio);
};
window.addEventListener('pointerdown', unlockAudio);
window.addEventListener('keydown', unlockAudio);

function renderUI(): void {
  updateTopbar(game, speed);
  panelEl.innerHTML = renderPanel(game);
  newsEl.innerHTML = renderNews(game);
  const b = renderBanner(game);
  bannerEl.innerHTML = b;
  bannerEl.classList.toggle('hidden', b === '');
}

// زر التبديل + فحص أن جهاز المستخدم يدعم WebGL2 قبل تفعيل 3D
function safeInitialMode(): boolean {
  if (!(localStorage.getItem(MODE_KEY) !== '2d' && MapView3D.supported())) return false;
  try {
    const c = document.createElement('canvas');
    return !!c.getContext('webgl2');
  } catch {
    return false;
  }
}

// كل أزرار الواجهة عبر التفويض
document.addEventListener('click', (e) => {
  const el = (e.target as HTMLElement).closest('[data-action]') as HTMLElement | null;
  if (!el) return;
  sound.click();
  const action = el.dataset.action!;
  const v = el.dataset.v;
  const id = el.dataset.id ? Number(el.dataset.id) : 0;

  switch (action) {
    case 'speed':
      speed = Number(v);
      view.setSpeed(speed);
      break;
    case 'newgame': {
      const inp = document.getElementById('seedInput') as HTMLInputElement;
      const s = Number(inp.value);
      game = new Game(Number.isFinite(s) ? Math.floor(s) : DEFAULT_SEED);
      speed = 1;
      refreshViewsWorld();
      view.setSpeed(1);
      break;
    }
    case 'save':
      localStorage.setItem(SAVE_KEY, game.save());
      game.log('💾 تم حفظ اللعبة.', 'good');
      break;
    case 'load': {
      const raw = localStorage.getItem(SAVE_KEY);
      if (!raw) {
        game.log('📂 لا يوجد حفظ بعد.', 'info');
        break;
      }
      try {
        game = Game.load(raw);
        refreshViewsWorld();
      } catch {
        game.log('⛔ ملف الحفظ تالف أو من نسخة قديمة.', 'bad');
      }
      break;
    }
    case 'capital': {
      const c = game.provinces[game.player().capitalProvinceId].center;
      view.centerOnCell(c.x, c.y);
      game.selection = { type: 'province', id: game.player().capitalProvinceId };
      break;
    }
    case 'mode':
      applyMode(!mode3d);
      break;
    case 'select-army':
      game.selection = { type: 'army', id };
      break;
    case 'deselect':
      game.selection = null;
      break;
    case 'stance':
      game.setStance(id, (v as 'balanced' | 'aggressive' | 'defensive'));
      break;
    case 'formation':
      game.setFormation(id, (v as 'line' | 'column' | 'loose'));
      break;
    case 'hold':
      game.holdArmy(id);
      break;
    case 'retreat':
      game.retreatArmy(id);
      break;
    case 'recruit':
      game.recruit(id);
      break;
    case 'newarmy':
      game.foundArmy(game.player().id);
      break;
    case 'mute': {
      const m = sound.toggle();
      el.textContent = m ? '🔇' : '🔊';
      break;
    }
  }
  renderUI();
});

// لوحة المفاتيح
document.addEventListener('keydown', (e) => {
  if ((e.target as HTMLElement).tagName === 'INPUT') return;
  if (e.code === 'Space') {
    e.preventDefault();
    speed = speed === 0 ? 1 : 0;
    view.setSpeed(speed);
    renderUI();
  } else if (e.key === '1' || e.key === '2' || e.key === '3') {
    speed = e.key === '1' ? 1 : e.key === '2' ? 2 : 4;
    view.setSpeed(speed);
    renderUI();
  } else if (e.key === 'Escape') {
    game.selection = null;
    renderUI();
  } else if (e.key === 't' || e.key === 'T') {
    applyMode(!mode3d);
    renderUI();
  }
});

// ===== تهيئة العارض الأول (مع شبكة أمان: لو فشل 3D نرجع للـ2D) =====
try {
  applyMode(safeInitialMode());
} catch (err) {
  console.warn('تعذر تفعيل العرض ثلاثي الأبعاد، رجوع إلى 2D', err);
  applyMode(false);
}

// حلقة اللعبة
let last = performance.now();
let acc = 0;
let lastUI = 0;
let dirty = true;

function frame(now: number): void {
  acc += now - last;
  last = now;
  if (speed > 0 && !game.over) {
    const step = BASE_MS_PER_TICK / speed;
    let n = 0;
    while (acc >= step && n < 80) {
      game.update();
      acc -= step;
      n++;
      dirty = true;
    }
    if (n >= 80) acc = 0;
  } else {
    acc = 0;
  }
  try {
    view.render(game);
  } catch (err) {
    // لو انكسر 3D أثناء التشغيل → سقوط آمن للـ2D
    if (mode3d) {
      console.warn('خطأ في العرض ثلاثي الأبعاد، تحويل تلقائي إلى 2D', err);
      applyMode(false);
    } else {
      throw err;
    }
  }
  sound.update(game);
  if (dirty && now - lastUI > 300) {
    lastUI = now;
    dirty = false;
    renderUI();
  }
  requestAnimationFrame(frame);
}

renderUI();
requestAnimationFrame(frame);
