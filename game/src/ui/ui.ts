// واجهة DOM: الشريط العلوي + لوحة التحديد + الأخبار + لافتة النهاية.

import { tickToDay, tickToYear } from '../sim/clock';
import { NEW_ARMY_COST, RECRUIT_COST, RECRUIT_SIZE } from '../sim/game';
import type { Game } from '../sim/game';
import type { Army, Formation, Province, Stance, TerrainType } from '../sim/types';
import { emblemFor } from '../render/emblems';

/** شعار نصي صغير يُستخدم بجانب اسم الدولة في كل اللوحات */
function crest(id: number): string {
  const c = emblemFor(id).charge;
  return c ? `${c} ` : '';
}

const TERRAIN_AR: Record<TerrainType, string> = {
  plains: 'سهول',
  forest: 'غابات',
  hills: 'تلال',
  mountains: 'جبال',
  desert: 'صحراء',
  water: 'مياه',
};

const STANCE_AR: Record<Stance, string> = {
  balanced: '⚖️ متوازن',
  aggressive: '🔥 هجومي',
  defensive: '🛡️ دفاعي',
};

const FORMATION_AR: Record<Formation, string> = {
  line: '➖ خط',
  column: '⬆️ رتل',
  loose: '🔹 منتشر',
};

const ORDER_AR = { hold: 'ثبات', move: 'تحرك', retreat: 'انسحاب' } as const;

export function fmt(n: number): string {
  return Math.round(n).toLocaleString('en-US');
}

/** الهيكل الثابت للشريط العلوي (يُبنى مرة واحدة) */
export function buildTopbarHTML(defaultSeed: number): string {
  return `
    <span id="tb-date" class="tb-date">…</span>
    <span class="tb-res">🪙 <b id="tb-gold">0</b></span>
    <span class="tb-res">🌾 <b id="tb-food">0</b></span>
    <span class="tb-speeds">
      <button data-action="speed" data-v="0" title="إيقاف">⏸️</button>
      <button data-action="speed" data-v="1" title="سرعة عادية">▶️</button>
      <button data-action="speed" data-v="2" title="سرعة ×2">⏩</button>
      <button data-action="speed" data-v="4" title="سرعة ×4">⏭️</button>
    </span>
    <span class="tb-sep"></span>
    <button data-action="mode" title="تبديل العرض ثلاثي/مسطح">🧊</button>
    <button data-action="capital" title="انتقال للعاصمة">🏰</button>
    <button data-action="save" title="حفظ">💾</button>
    <button data-action="load" title="تحميل">📂</button>
    <button data-action="mute" title="كتم/تشغيل الصوت">🔊</button>
    <span class="tb-sep"></span>
    <input id="seedInput" type="number" value="${defaultSeed}" title="بذرة العالم" />
    <button data-action="newgame" title="حملة جديدة">🆕</button>
  `;
}

export function updateTopbar(g: Game, speed: number): void {
  const set = (id: string, v: string) => {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  };
  set('tb-date', g.clock.format());
  const me = g.player();
  set('tb-gold', fmt(me.gold));
  set('tb-food', fmt(me.food));
  document.querySelectorAll<HTMLButtonElement>('[data-action="speed"]').forEach((b) => {
    b.classList.toggle('active', Number(b.dataset.v) === speed);
  });
}

function bar(label: string, v: number): string {
  const cls = v > 50 ? 'ok' : v > 25 ? 'mid' : 'low';
  return `<div class="bar-row"><span>${label}</span><div class="bar"><div class="fill ${cls}" style="width:${Math.max(0, Math.min(100, v))}%"></div></div><b>${Math.round(v)}</b></div>`;
}

function armyCard(g: Game, a: Army): string {
  const n = g.nation(a.nationId);
  const prov = g.provinces[a.provinceId];
  const own = n.isPlayer;
  const orderTxt =
    a.order.kind === 'move' && a.order.targetProvinceId !== null
      ? `${ORDER_AR.move} ← ${g.provinces[a.order.targetProvinceId].name}`
      : a.order.kind === 'retreat'
        ? ORDER_AR.retreat
        : ORDER_AR.hold;
  return `
    <div class="card">
      <div class="card-head" style="border-color:${n.color}">
        <h2>${crest(n.id)}${a.name}</h2>
        <span class="sub">${n.name} — القائد ${a.commander}</span>
      </div>
      <div class="kv"><span>الموقع</span><b>${prov.name}</b></div>
      <div class="kv"><span>الجنود</span><b>${fmt(a.soldiers)}</b></div>
      ${bar('المعنويات', a.morale)}
      ${bar('الإمداد', a.supply)}
      <div class="kv"><span>الأمر</span><b>${orderTxt}</b></div>
      ${
        own
          ? `
      <div class="btn-label">الموقف القتالي</div>
      <div class="btn-row">
        ${(['balanced', 'aggressive', 'defensive'] as Stance[])
          .map((s) => `<button data-action="stance" data-v="${s}" data-id="${a.id}" class="${a.stance === s ? 'active' : ''}">${STANCE_AR[s]}</button>`)
          .join('')}
      </div>
      <div class="btn-label">التشكيل</div>
      <div class="btn-row">
        ${(['line', 'column', 'loose'] as Formation[])
          .map((f) => `<button data-action="formation" data-v="${f}" data-id="${a.id}" class="${a.formation === f ? 'active' : ''}">${FORMATION_AR[f]}</button>`)
          .join('')}
      </div>
      <div class="btn-row">
        <button data-action="hold" data-id="${a.id}">✋ ثبات</button>
        <button data-action="retreat" data-id="${a.id}">🏃 انسحاب</button>
      </div>
      <div class="btn-row">
        <button data-action="recruit" data-id="${a.id}">➕ تجنيد ${RECRUIT_SIZE} (🪙${RECRUIT_COST})</button>
      </div>
      <p class="hint">💡 لإصدار أمر تحرك: اختر مقاطعة هدف على الخريطة.</p>`
          : `<p class="hint">👁️ جيش معادٍ — للمراقبة فقط.</p>`
      }
      <div class="btn-row"><button data-action="deselect">↩️ رجوع</button></div>
    </div>
  `;
}

function provinceCard(g: Game, p: Province): string {
  const owner = p.ownerId >= 0 ? g.nation(p.ownerId) : null;
  const here = g.armiesAt(p.id);
  const me = g.player();
  return `
    <div class="card">
      <div class="card-head" style="border-color:${owner?.color ?? '#888'}">
        <h2>🗺️ ${p.name}</h2>
        <span class="sub">${TERRAIN_AR[p.terrain]}${p.city ? (p.city.isCapital ? ' — 👑 عاصمة' : ' — 🏘️ مدينة') : ''}</span>
      </div>
      <div class="kv"><span>المالك</span><b style="color:${owner?.color ?? '#ccc'}">${owner ? crest(owner.id) + owner.name : '—'}</b></div>
      ${p.city ? `<div class="kv"><span>السكان</span><b>${fmt(p.city.population)}</b></div>
      <div class="kv"><span>التحصين</span><b>${'⭐'.repeat(p.city.fortLevel) || '—'}</b></div>` : ''}
      <div class="kv"><span>الحامية</span><b>${fmt(p.garrison)}</b></div>
      ${
        here.length > 0
          ? `<div class="btn-label">الجيوش هنا</div>
        <div class="btn-col">
          ${here.map((a) => `<button data-action="select-army" data-id="${a.id}">${crest(a.nationId)}${a.name} — ${fmt(a.soldiers)} (${g.nation(a.nationId).name})</button>`).join('')}
        </div>`
          : '<p class="hint">لا توجد جيوش هنا.</p>'
      }
      ${
        owner?.id === me.id && p.city
          ? `<div class="btn-row"><button data-action="newarmy">🎖️ تشكيل جيش جديد (🪙${NEW_ARMY_COST})</button></div>`
          : ''
      }
      <div class="btn-row"><button data-action="deselect">↩️ رجوع</button></div>
    </div>
  `;
}

export function renderPanel(g: Game): string {
  if (g.over) {
    return `<div class="card"><h2>${g.over.text}</h2><p class="hint">ابدأ حملة جديدة من الزر 🆕 بالأعلى (غيّر البذرة لعالم مختلف).</p></div>`;
  }
  const sel = g.selection;
  if (!sel) {
    return `<div class="card"><h2>🎮 كيف تلعب؟</h2>
      <p class="hint">🖱️ انقر مقاطعة لعرضها، وانقر جيشًا من جيوشك (دروع تحمل شعار ☀️) لتحديده.</p>
      <p class="hint">⚔️ والجيش محدد: انقر أي مقاطعة لتحريكه/مهاجمتها.</p>
      <p class="hint">🏆 الهدف: احتل عاصمة دولة الغسق 🌙 — تاجها الذهبي فوق درعهم الأحمر.</p>
      <p class="hint">🗺️ قرصًا للتقريب/التبعيد، والسحب للإزاحة؛ على الحاسب: عجلة الفأرة والسحب. مسافة = إيقاف مؤقت.</p></div>`;
  }
  if (sel.type === 'army') {
    const a = g.armies.find((x) => x.id === sel.id);
    if (!a) return `<div class="card"><p class="hint">انتهى هذا الجيش.</p></div>`;
    return armyCard(g, a);
  }
  return provinceCard(g, g.provinces[sel.id]);
}

export function renderNews(g: Game): string {
  return g.news
    .slice(0, 30)
    .map((n) => `<div class="n ${n.kind}"><span class="t">س${tickToYear(n.tick)} ي${tickToDay(n.tick)}</span> ${n.text}</div>`)
    .join('');
}

export function renderBanner(g: Game): string {
  if (!g.over) return '';
  const won = g.nation(g.over.winnerId).isPlayer;
  return `<div class="banner-box ${won ? 'win' : 'lose'}">${g.over.text}</div>`;
}
