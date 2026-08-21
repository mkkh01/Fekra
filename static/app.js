const state = {
  symbol: 'BTCUSDT',
  interval: '15m',
  overview: [],
  chart: null,
  candleSeries: null,
  lineSeries: null,
  ws: null,
  signal: null,
  requestId: 0,
  overviewLoading: false,
  activeLoading: false,
  refreshTimer: null,
  liveBar: null,
  symbolMenuOpen: false,
  lastCycleSummary: null,
  cycleStateLoading: false,
  cycleSummaryLoading: false,
  cycleStateRequestId: 0,
  cycleSummaryRequestId: 0,
  pushRegistration: null,
  pushConfig: null,
  pushSubscription: null
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const fmt = (value) => value === undefined || value === null || Number.isNaN(Number(value))
  ? '—'
  : Number(value).toLocaleString('en-US', { maximumFractionDigits: 8 });
const pct = (value) => `${Number(value || 0) >= 0 ? '+' : ''}${Number(value || 0).toFixed(2)}%`;
const intervalSeconds = { '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 };

function toast(text) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 2400);
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options, headers: { Accept: 'application/json', 'Cache-Control': 'no-cache', ...(options.headers || {}) } });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function pushButton(text, enabled = false, disabled = false) {
  const button = $('#push-btn');
  if (!button) return;
  button.textContent = text;
  button.disabled = disabled;
  button.classList.toggle('push-enabled', enabled);
}

function urlBase64ToUint8Array(value) {
  const padding = '='.repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((character) => character.charCodeAt(0)));
}

async function syncPushSubscription(subscription) {
  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) throw new Error('اشتراك الهاتف ناقص');
  await api('/api/push/subscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      endpoint: json.endpoint,
      p256dh: json.keys.p256dh,
      auth: json.keys.auth,
      expiration_time: subscription.expirationTime,
      user_agent: navigator.userAgent
    })
  });
  state.pushSubscription = subscription;
}

async function enablePushNotifications() {
  const button = $('#push-btn');
  if (!button || button.disabled) return;
  if (state.pushSubscription) {
    toast('إشعارات الهاتف مفعلة بالفعل');
    return;
  }
  try {
    if (!state.pushRegistration || !state.pushConfig?.public_key) throw new Error('إشعارات الهاتف غير مهيأة');
    if (!('Notification' in window) || !('PushManager' in window)) throw new Error('هذا المتصفح لا يدعم إشعارات Push');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('تم رفض إذن الإشعارات من الهاتف');
    const subscription = await state.pushRegistration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(state.pushConfig.public_key)
    });
    await syncPushSubscription(subscription);
    pushButton('الإشعارات مفعلة', true);
    toast('تم تفعيل إشعارات فتح وإغلاق الصفقات');
  } catch (error) {
    pushButton('تفعيل إشعارات الهاتف');
    toast(error.message || 'تعذر تفعيل إشعارات الهاتف');
  }
}

async function initPushNotifications() {
  const button = $('#push-btn');
  if (!button) return;
  button.onclick = enablePushNotifications;
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    pushButton('Push غير مدعوم', false, true);
    return;
  }
  try {
    state.pushConfig = await api(`/api/push/config?push=${Date.now()}`);
    if (!state.pushConfig.enabled || !state.pushConfig.public_key) {
      pushButton('Push غير مهيأ', false, true);
      return;
    }
    const legacyRegistrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(legacyRegistrations
      .filter((registration) => registration.active?.scriptURL.includes('/static/push-sw.js'))
      .map((registration) => registration.unregister()));
    state.pushRegistration = await navigator.serviceWorker.register('/push-sw.js', { scope: '/' });
    const existing = await state.pushRegistration.pushManager.getSubscription();
    if (existing && Notification.permission === 'granted') {
      await syncPushSubscription(existing);
      pushButton('الإشعارات مفعلة', true);
    } else {
      pushButton('تفعيل إشعارات الهاتف');
    }
  } catch (_) {
    pushButton('تعذر تجهيز الإشعارات', false, true);
  }
}

function renderWatchlist() {
  const query = ($('#symbol-search')?.value || '').trim().toUpperCase();
  const rows = state.overview.filter((item) => item.symbol.includes(query));
  const watchlist = $('#watchlist');
  const existingSymbols = $$('.watch-item').map((element) => element.dataset.symbol).join(',');
  const nextSymbols = rows.map((item) => item.symbol).join(',');
  if (existingSymbols !== nextSymbols) {
    watchlist.innerHTML = rows.length
      ? rows.map((item) => `<button class="watch-item" data-symbol="${item.symbol}" type="button">
          <span><span class="watch-symbol">${item.symbol}</span><span class="watch-sub"></span></span>
          <span><span class="watch-price"></span><span class="watch-change"></span></span>
        </button>`).join('')
      : '<div class="empty-state">لا توجد عملة مطابقة</div>';
    $$('.watch-item').forEach((element) => {
      element.addEventListener('click', () => selectSymbol(element.dataset.symbol));
    });
  }
  rows.forEach((item) => updateWatchItem(item));
  const mobileSelect = $('#mobile-symbol-select');
  if (mobileSelect) {
    const selectSymbols = [...mobileSelect.options].map((option) => option.value).join(',');
    const overviewSymbols = state.overview.map((item) => item.symbol).join(',');
    if (!state.symbolMenuOpen && selectSymbols !== overviewSymbols) {
      mobileSelect.innerHTML = state.overview.map((item) => `<option value="${item.symbol}">${item.symbol}</option>`).join('');
    }
    if (!state.symbolMenuOpen && mobileSelect.value !== state.symbol) mobileSelect.value = state.symbol;
  }
  renderMobileSymbolPicker();
}

function renderMobileSymbolPicker() {
  const menu = $('#mobile-symbol-menu');
  const label = $('#mobile-symbol-label');
  if (!menu || !label) return;
  label.textContent = state.symbol;
  const symbols = state.overview.map((item) => item.symbol);
  const existing = [...menu.querySelectorAll('.mobile-symbol-option')].map((element) => element.dataset.symbol);
  if (existing.join(',') !== symbols.join(',')) {
    menu.innerHTML = symbols.map((symbol) => `<button type="button" class="mobile-symbol-option" role="option" data-symbol="${symbol}">${symbol}</button>`).join('');
  }
  menu.querySelectorAll('.mobile-symbol-option').forEach((element) => {
    element.classList.toggle('active', element.dataset.symbol === state.symbol);
    element.setAttribute('aria-selected', element.dataset.symbol === state.symbol ? 'true' : 'false');
  });
}

function setMobileSymbolMenu(open) {
  const picker = $('#mobile-symbol-picker');
  const trigger = $('#mobile-symbol-trigger');
  const menu = $('#mobile-symbol-menu');
  if (!picker || !trigger || !menu) return;
  state.symbolMenuOpen = open;
  picker.classList.toggle('open', open);
  trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
  menu.hidden = !open;
  if (open) {
    renderMobileSymbolPicker();
    menu.querySelector('.mobile-symbol-option.active')?.scrollIntoView({ block: 'nearest' });
  }
}

function updateWatchItem(item) {
  const element = document.querySelector(`.watch-item[data-symbol="${item.symbol}"]`);
  if (!element) return;
  const ticker = item.ticker || {};
  const change = Number(ticker.change || 0);
  element.classList.toggle('active', item.symbol === state.symbol);
  element.querySelector('.watch-sub').textContent = `${item.signal || 'NO TRADE'} · ${item.confidence || 0}%`;
  element.querySelector('.watch-price').textContent = fmt(ticker.price ?? item.price);
  const changeElement = element.querySelector('.watch-change');
  changeElement.textContent = pct(change);
  changeElement.className = `watch-change ${change >= 0 ? 'positive' : 'negative'}`;
}

function initChart() {
  const container = $('#chart');
  state.chart = LightweightCharts.createChart(container, {
    layout: { background: { color: '#0b1621' }, textColor: '#7890a2' },
    grid: { vertLines: { color: '#132534' }, horzLines: { color: '#132534' } },
    rightPriceScale: { borderColor: '#22394a' },
    timeScale: { borderColor: '#22394a', timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal }
  });
  state.candleSeries = state.chart.addCandlestickSeries({ upColor: '#33d69a', downColor: '#ff6b78', borderVisible: false, wickUpColor: '#33d69a', wickDownColor: '#ff6b78' });
  state.lineSeries = state.chart.addLineSeries({ color: '#54a9ff', lineWidth: 2, visible: false });
  new ResizeObserver(() => state.chart.applyOptions({ width: container.clientWidth })).observe(container);
}

async function loadChart(requestId = state.requestId) {
  const data = await api(`/api/market/candles?symbol=${encodeURIComponent(state.symbol)}&interval=${encodeURIComponent(state.interval)}&limit=250`);
  if (requestId !== state.requestId) return;
  const candles = data.candles || [];
  state.candleSeries.setData(candles.map((candle) => ({ time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close })));
  state.lineSeries.setData(candles.map((candle) => ({ time: candle.time, value: candle.close })));
  state.liveBar = candles.length ? { ...candles[candles.length - 1] } : null;
  state.chart.timeScale().fitContent();
}

function renderSignal(item) {
  if (!item) return;
  state.signal = item;
  const signal = item.signal || 'NO TRADE';
  const ticker = item.ticker || {};
  const badge = $('#active-signal');
  badge.textContent = signal;
  badge.className = `signal-badge ${signal === 'LONG' ? 'long' : signal === 'SHORT' ? 'short' : 'neutral'}`;
  $('#active-symbol').textContent = item.symbol;
  $('#active-price').textContent = fmt(ticker.price ?? item.price);
  $('#active-regime').textContent = item.regime || '—';
  $('#active-confidence').textContent = `${item.confidence || 0}%`;
  $('#active-rr').textContent = item.rr ? `1:${item.rr}` : '—';
  $('#active-change').textContent = pct(ticker.change);
  $('#active-change').className = Number(ticker.change || 0) >= 0 ? 'positive' : 'negative';
  const color = signal === 'LONG' ? 'positive' : signal === 'SHORT' ? 'negative' : 'neutral';
  const context = [item.asset_profile_label, item.regime].filter(Boolean).join(' · ') || 'TRANSITION';
  const timeframeSummary = item.timeframes
    ? Object.entries(item.timeframes).map(([interval, frame]) => `<span class="reason">${interval}: ${frame.bias || frame.signal || 'NEUTRAL'}</span>`).join('')
    : '';
  $('#signal-content').innerHTML = `<div class="signal-main"><div><span class="eyebrow">${context}</span><div class="signal-word ${color}">${signal}</div></div><div><span class="eyebrow">CONFIDENCE</span><div class="confidence">${item.confidence || 0}</div></div></div><div class="reasons">${timeframeSummary}${(item.reasons || [item.reason || 'لا توجد أسباب كافية']).map((reason) => `<span class="reason">✓ ${reason}</span>`).join('')}</div><div class="levels"><div class="level"><small>ENTRY</small><strong>${fmt(item.entry)}</strong></div><div class="level"><small>STOP LOSS</small><strong class="negative">${fmt(item.stop_loss)}</strong></div><div class="level"><small>TP1</small><strong class="positive">${fmt(item.take_profit_1)}</strong></div><div class="level"><small>TP2</small><strong class="positive">${fmt(item.take_profit_2)}</strong></div></div>`;
}

function updateLiveChart(candle) {
  if (!state.candleSeries || !candle || (candle.symbol && candle.symbol !== state.symbol)) return;
  const seconds = intervalSeconds[state.interval] || 900;
  const bucketTime = Math.floor(Number(candle.time) / seconds) * seconds;
  if (!state.liveBar || state.liveBar.time !== bucketTime) {
    state.liveBar = { time: bucketTime, open: candle.open, high: candle.high, low: candle.low, close: candle.close };
  } else {
    state.liveBar.high = Math.max(state.liveBar.high, candle.high);
    state.liveBar.low = Math.min(state.liveBar.low, candle.low);
    state.liveBar.close = candle.close;
  }
  state.candleSeries.update({ time: state.liveBar.time, open: state.liveBar.open, high: state.liveBar.high, low: state.liveBar.low, close: state.liveBar.close });
  state.lineSeries.update({ time: state.liveBar.time, value: state.liveBar.close });
}

function updateLivePrice(price, updatedAt = Math.floor(Date.now() / 1000)) {
  const value = Number(price);
  if (!state.candleSeries || !Number.isFinite(value)) return;
  const seconds = intervalSeconds[state.interval] || 900;
  const bucketTime = Math.floor(Number(updatedAt) / seconds) * seconds;
  if (!state.liveBar || state.liveBar.time !== bucketTime) {
    state.liveBar = { time: bucketTime, open: value, high: value, low: value, close: value };
  } else {
    state.liveBar.high = Math.max(state.liveBar.high, value);
    state.liveBar.low = Math.min(state.liveBar.low, value);
    state.liveBar.close = value;
  }
  state.candleSeries.update({ time: state.liveBar.time, open: state.liveBar.open, high: state.liveBar.high, low: state.liveBar.low, close: state.liveBar.close });
  state.lineSeries.update({ time: state.liveBar.time, value });
}

function activeOverviewItem() {
  return state.overview.find((item) => item.symbol === state.symbol);
}

async function selectSymbol(symbol) {
  if (!symbol || symbol === state.symbol && state.activeLoading) return;
  state.symbol = symbol.toUpperCase();
  const requestId = ++state.requestId;
  renderWatchlist();
  const active = activeOverviewItem();
  if (active) renderSignal(active);
  state.activeLoading = true;
  try {
    const signal = await api(`/api/signals/${encodeURIComponent(state.symbol)}?interval=${encodeURIComponent(state.interval)}`);
    if (requestId !== state.requestId) return;
    renderSignal({ ...signal, ticker: activeOverviewItem()?.ticker });
    $('#last-update').textContent = new Date().toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' });
  } catch (error) {
    if (requestId === state.requestId) toast(`تعذر تحميل ${state.symbol}`);
  } finally {
    if (requestId === state.requestId) state.activeLoading = false;
  }
}

async function loadOverview() {
  if (state.overviewLoading) return;
  state.overviewLoading = true;
  try {
    const data = await api(`/api/market/overview?interval=${encodeURIComponent(state.interval)}`);
    const previousSymbol = state.symbol;
    state.overview = Array.isArray(data) ? data : [];
    if (!state.overview.some((item) => item.symbol === state.symbol)) state.symbol = state.overview[0]?.symbol || state.symbol;
    renderWatchlist();
    if (previousSymbol !== state.symbol || !state.signal) {
      await selectSymbol(state.symbol);
    } else {
      const active = activeOverviewItem();
      if (active) renderSignal(active);
    }
  } catch (error) {
    toast('تعذر الاتصال بمصدر السوق');
  } finally {
    state.overviewLoading = false;
  }
}

async function refreshActive() {
  if (state.activeLoading || !state.symbol) return;
  const requestId = ++state.requestId;
  state.activeLoading = true;
  try {
    const signal = await api(`/api/signals/${encodeURIComponent(state.symbol)}?interval=${encodeURIComponent(state.interval)}`);
    if (requestId !== state.requestId) return;
    renderSignal({ ...signal, ticker: activeOverviewItem()?.ticker });
    renderWatchlist();
    $('#last-update').textContent = new Date().toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' });
  } catch (error) {
    // Live websocket remains available even when a refresh request fails.
  } finally {
    if (requestId === state.requestId) state.activeLoading = false;
  }
}

function cycleTime(value) {
  if (!value) return '—';
  const numeric = Number(value);
  const date = Number.isFinite(numeric) && numeric < 100000000000 ? new Date(numeric * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function exitDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('ar-EG', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderCycleState(data, includeTradeCounts = false) {
  const cycle = data.cycle || {};
  const health = data.health || {};
  const trades = data.trades || {};
  const statusLabels = { CHECKING: 'يفحص الآن', IDLE: 'جاهز للدورة التالية', WAITING_STORAGE: 'ينتظر التخزين', ERROR: 'خطأ في الدورة', STARTING: 'يبدأ' };
  const status = cycle.status || 'STARTING';
  const statusEl = $('#cycle-status');
  statusEl.textContent = statusLabels[status] || status;
  statusEl.className = `cycle-status ${status === 'IDLE' ? 'positive' : status === 'ERROR' || status === 'WAITING_STORAGE' ? 'negative' : 'neutral'}`;
  $('#cycle-storage').textContent = health.persistent_storage ? 'PostgreSQL متصل' : 'غير جاهز';
  $('#cycle-auto').textContent = health.auto_signal_enabled ? 'مفعّل' : 'متوقف';
  $('#cycle-scanned').textContent = cycle.scanned_symbols ? `${cycle.scanned_symbols} عملة` : '—';
  $('#cycle-ready').textContent = `${cycle.ready_signals || 0}`;
  $('#cycle-count').textContent = `${cycle.completed_cycles || 0}`;
  $('#cycle-saved').textContent = cycle.saved_trades ? `${cycle.saved_trades} · ${(cycle.last_saved_symbols || []).join(', ')}` : 'لا جديد';
  $('#cycle-next').textContent = cycleTime(cycle.next_run_at);
  if (includeTradeCounts) {
    $('#cycle-open').textContent = `${trades.open || 0}`;
    $('#cycle-closed').textContent = `${trades.closed || 0}`;
  }
  return { cycle, health, trades };
}

async function loadCycleState() {
  if (state.cycleStateLoading) return;
  const requestId = ++state.cycleStateRequestId;
  state.cycleStateLoading = true;
  try {
    const data = await api(`/api/summary/cycle/state?live=${Date.now()}`);
    if (requestId === state.cycleStateRequestId) renderCycleState(data);
  } catch (_) {
    // The full summary retains the last good values; this fast heartbeat retries automatically.
  } finally {
    if (requestId === state.cycleStateRequestId) state.cycleStateLoading = false;
  }
}

async function loadCycleSummary() {
  if (state.cycleSummaryLoading) return;
  const requestId = ++state.cycleSummaryRequestId;
  state.cycleSummaryLoading = true;
  try {
    const data = await api(`/api/summary/cycle?summary=${Date.now()}`);
    if (requestId !== state.cycleSummaryRequestId) return;
    const { cycle, health, trades } = renderCycleState(data, true);
    const error = cycle.last_error || health.storage_last_error;
    const warnings = (data.warnings || []).join('، ');
    const readySymbols = (cycle.ready_symbols || []).join('، ') || 'لا توجد';
    $('#cycle-note').textContent = error || warnings
      ? `تنبيه: ${error || warnings}`
      : `الدورة #${cycle.run_id || cycle.completed_cycles || 0}: ${cycleTime(cycle.finished_at)} · المدة: ${cycle.last_run_duration_seconds ?? '—'}ث · الإشارات: ${readySymbols} · المفتوحة: ${(trades.latest_open || []).join('، ') || 'لا توجد'}`;
    state.lastCycleSummary = data;
  } catch (error) {
    const statusEl = $('#cycle-status');
    const note = $('#cycle-note');
    if (state.lastCycleSummary) {
      statusEl.textContent = 'إعادة المحاولة';
      statusEl.className = 'cycle-status neutral';
      note.textContent = `تعذر تحديث الملخص مؤقتًا؛ آخر دورة ناجحة: ${cycleTime(state.lastCycleSummary.cycle?.finished_at)}`;
    } else {
      statusEl.textContent = 'في انتظار أول تحديث';
      statusEl.className = 'cycle-status neutral';
      note.textContent = 'سيُعاد الاتصال تلقائيًا';
    }
  } finally {
    if (requestId === state.cycleSummaryRequestId) state.cycleSummaryLoading = false;
  }
}

async function loadTrades(status = 'open') {
  try {
    const rows = await api(`/api/trades?status=${status === 'open' ? 'OPEN' : 'CLOSED_OR_STOPPED'}`);
    $('#trades-body').innerHTML = rows.length
      ? rows.map((trade) => {
        const result = trade.result === 'WIN' || trade.status === 'CLOSED' ? 'فوز' : trade.result === 'LOSS' || trade.status === 'STOPPED' ? 'خسارة' : 'قيد التشغيل';
        const resultClass = result === 'فوز' ? 'positive' : result === 'خسارة' ? 'negative' : 'neutral';
        const reason = trade.exit_reason === 'TAKE_PROFIT_1' ? 'الهدف الأول' : trade.exit_reason === 'STOP_LOSS' ? 'وقف الخسارة' : '';
        const exitTime = trade.closed_at ? exitDateTime(trade.closed_at) : '—';
        return `<tr><td>${trade.symbol}</td><td class="${trade.direction === 'LONG' ? 'positive' : 'negative'}">${trade.direction}</td><td>${fmt(trade.entry)}</td><td>${fmt(trade.stop_loss)}</td><td>${fmt(trade.take_profit_1)}</td><td>${trade.exit_price == null ? '—' : fmt(trade.exit_price)}</td><td>${exitTime}</td><td>${trade.status}</td><td><span class="trade-result ${resultClass}" title="${reason}">${result}</span></td></tr>`;
      }).join('')
      : `<tr><td colspan="9" class="neutral">لا توجد صفقات ${status === 'open' ? 'مفتوحة' : 'منتهية'} بعد</td></tr>`;
  } catch (error) {
    toast('تعذر تحميل سجل الصفقات');
  }
}

function connectWS() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  state.ws = new WebSocket(`${protocol}://${location.host}/ws`);
  state.ws.onopen = () => { $('#connection-dot').style.background = 'var(--green)'; };
  state.ws.onclose = () => { $('#connection-dot').style.background = 'var(--red)'; setTimeout(connectWS, 3000); };
  state.ws.onerror = () => state.ws.close();
  state.ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (!data.symbol) return;
    const item = state.overview.find((entry) => entry.symbol === data.symbol);
    if (!item) return;
    if (data.type === 'ticker') {
      item.price = data.price;
      item.ticker = data.ticker;
      updateWatchItem(item);
      if (data.symbol === state.symbol) {
        updateLivePrice(data.price, data.ticker?.updated_at);
        $('#active-price').textContent = fmt(data.price);
        $('#active-change').textContent = pct(data.ticker?.change);
        $('#active-change').className = Number(data.ticker?.change || 0) >= 0 ? 'positive' : 'negative';
      }
      return;
    }
    if (data.type !== 'candle' || !data.candle) return;
    item.price = data.ticker?.price ?? data.candle.close;
    item.ticker = data.ticker;
    updateWatchItem(item);
    if (data.symbol === state.symbol) {
      updateLiveChart({ ...data.candle, symbol: data.symbol });
      const ticker = data.ticker || {};
      $('#active-price').textContent = fmt(ticker.price ?? data.candle.close);
      $('#active-change').textContent = pct(ticker.change);
      $('#active-change').className = Number(ticker.change || 0) >= 0 ? 'positive' : 'negative';
    }
  };
}

$('#symbol-search').oninput = renderWatchlist;
$('#mobile-symbol-trigger').onclick = () => setMobileSymbolMenu(!state.symbolMenuOpen);
$('#mobile-symbol-menu').onclick = (event) => {
  const option = event.target.closest('.mobile-symbol-option');
  if (!option) return;
  setMobileSymbolMenu(false);
  selectSymbol(option.dataset.symbol);
};
document.addEventListener('click', (event) => {
  const picker = $('#mobile-symbol-picker');
  if (state.symbolMenuOpen && picker && !picker.contains(event.target)) setMobileSymbolMenu(false);
});
$('#refresh-btn').onclick = async () => {
  await loadOverview();
  await loadTrades(document.querySelector('.trade-tabs button.active')?.dataset.status || 'open');
  await loadCycleSummary();
  toast('تم تحديث بيانات اللوحة عند الطلب');
};
$('#price-btn').onclick = () => refreshActive();
$('#cycle-refresh-btn').onclick = () => loadCycleSummary();
$('#system-refresh-btn').onclick = async () => {
  await loadCycleSummary();
  const health = await api(`/api/health?manual=${Date.now()}`);
  toast(health.status === 'ok' ? 'حالة النظام سليمة' : 'توجد مشكلة في حالة النظام');
};

$('#add-symbol').onclick = () => toast('يمكن تتبع أي زوج موجود في قائمة SYMBOLS من إعدادات Render');
$('#settings-btn').onclick = () => toast('الإعدادات تحفظ عبر Supabase عند توفير SUPABASE_URL وSUPABASE_KEY');
$('#explain-btn').onclick = () => toast((state.signal?.reasons || []).join(' · ') || 'لا يوجد تفسير متاح');

$$('#timeframes button').forEach((button) => button.onclick = async () => {
  $$('#timeframes button').forEach((element) => element.classList.remove('selected'));
  button.classList.add('selected');
  state.interval = button.dataset.interval;
  await loadOverview();
});

$$('.trade-tabs button').forEach((button) => button.onclick = () => {
  $$('.trade-tabs button').forEach((element) => element.classList.remove('active'));
  button.classList.add('active');
  loadTrades(button.dataset.status);
});

$$('.chart-toolbar input[data-layer]').forEach((input) => input.onchange = () => toast(`طبقة ${input.parentElement.textContent.trim()} ${input.checked ? 'مفعلة' : 'متوقفة'} — ستظهر التفاصيل مع الإشارات القادمة`));

(async () => {
  initPushNotifications();
  await loadOverview();
  await loadTrades();
  await loadCycleSummary();
})();
