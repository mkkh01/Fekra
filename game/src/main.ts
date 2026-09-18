// نقطة الدخول: ربط المحاكاة + العرض + الواجهة + حلقة اللعبة.

import './style.css';
import { Game } from './sim/game';
import { MapView } from './render/mapView';
import { buildTopbarHTML, renderBanner, renderNews, renderPanel, updateTopbar } from './ui/ui';
import { Sound } from './ui/sound';

const SAVE_KEY = 'siyar-save-v1';
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
const panelEl = document.getElementById('panel')!;
const newsEl = document.getElementById('news')!;
const bannerEl = document.getElementById('banner')!;
const topbarEl = document.getElementById('topbar')!;
topbarEl.innerHTML = buildTopbarHTML(game.seed);

const view = new MapView(canvas);
view.setWorld(game.width, game.height);
view.centerOnCell(game.provinces[game.player().capitalProvinceId].center.x, game.provinces[game.player().capitalProvinceId].center.y);

const sound = new Sound();
// أول تفاعل للمستخدم يفعّل WebAudio (سياسة المتصفحات)
const unlockAudio = () => {
  sound.click();
  window.removeEventListener('pointerdown', unlockAudio);
  window.removeEventListener('keydown', unlockAudio);
};
window.addEventListener('pointerdown', unlockAudio);
window.addEventListener('keydown', unlockAudio);
if (sound.muted) {
  const mb = topbarEl.querySelector('[data-action="mute"]');
  if (mb) mb.textContent = '🔇';
}

function renderUI(): void {
  updateTopbar(game, speed);
  panelEl.innerHTML = renderPanel(game);
  newsEl.innerHTML = renderNews(game);
  const b = renderBanner(game);
  bannerEl.innerHTML = b;
  bannerEl.classList.toggle('hidden', b === '');
}

// النقر على الخريطة
view.onTap = (cell) => {
  sound.click();
  if (game.over) return;
  const pid = game.provinceAt(cell.x, cell.y);
  if (pid < 0) return;
  const me = game.player();
  const sel = game.selection;

  // جيش محدد مسبقًا → النقر = أمر تحرك (إلا على مقاطعته الحالية = عرض المقاطعة)
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

  // تحديد: جيشي أولًا، ثم جيش العدو (للمراقبة)، ثم المقاطعة
  const own = game.armies.find((x) => x.provinceId === pid && x.nationId === me.id);
  if (own) {
    game.selection = { type: 'army', id: own.id };
  } else {
    const foe = game.armies.find((x) => x.provinceId === pid);
    if (foe) game.selection = { type: 'army', id: foe.id };
    else game.selection = { type: 'province', id: pid };
  }
  renderUI();
};

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
      break;
    case 'newgame': {
      const inp = document.getElementById('seedInput') as HTMLInputElement;
      const s = Number(inp.value);
      game = new Game(Number.isFinite(s) ? Math.floor(s) : DEFAULT_SEED);
      view.setWorld(game.width, game.height);
      view.centerOnCell(
        game.provinces[game.player().capitalProvinceId].center.x,
        game.provinces[game.player().capitalProvinceId].center.y,
      );
      speed = 1;
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
        view.setWorld(game.width, game.height);
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
    renderUI();
  } else if (e.key === '1' || e.key === '2' || e.key === '3') {
    speed = e.key === '1' ? 1 : e.key === '2' ? 2 : 4;
    renderUI();
  } else if (e.key === 'Escape') {
    game.selection = null;
    renderUI();
  }
});

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
  view.render(game);
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
