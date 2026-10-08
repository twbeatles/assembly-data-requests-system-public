// 마지막으로 보던 탭·보기 방식·정렬을 기억한다. 검색어를 입력하면 자동으로 바뀌는
// '정확도순'은 기억하지 않는다(검색어 없이 열면 의미가 없다).
const VIEW_PREFS_KEY = 'kocsc_view_prefs';
const VIEW_MODES = ['docs', 'qa', 'tables', 'ledger'];
const VIEW_SORTS = ['date-desc', 'date-asc', 'qa-count', 'table-first', 'deadline-asc'];

function sanitizeViewPrefs(raw) {
  const p = raw && typeof raw === 'object' ? raw : {};
  return {
    mode: VIEW_MODES.includes(p.mode) ? p.mode : 'docs',
    view: p.view === 'table' ? 'table' : 'card',
    sort: VIEW_SORTS.includes(p.sort) ? p.sort : 'date-desc'
  };
}

function saveViewPrefs() {
  safeStorageSet(VIEW_PREFS_KEY, sanitizeViewPrefs({ mode: currentMode, view: currentView, sort: activeFilters.sort }));
}

function syncModeTabs() {
  VIEW_MODES.forEach(mode => {
    const tab = document.getElementById(`tab-mode-${mode}`);
    if (!tab) return;
    const active = mode === currentMode;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function setView(view) {
  currentView = view === 'table' ? 'table' : 'card';
  const isTable = currentView === 'table';
  const cardBtn = document.getElementById('btn-view-card');
  const tableBtn = document.getElementById('btn-view-table');
  if (cardBtn) {
    cardBtn.classList.toggle('active', !isTable);
    cardBtn.setAttribute('aria-pressed', String(!isTable));
  }
  if (tableBtn) {
    tableBtn.classList.toggle('active', isTable);
    tableBtn.setAttribute('aria-pressed', String(isTable));
  }
  const cards = document.getElementById('card-container');
  if (cards) cards.style.display = isTable ? 'none' : 'grid';
  const table = document.getElementById('table-container');
  if (table) table.style.display = isTable ? 'block' : 'none';
}

function restoreViewPrefs() {
  const prefs = sanitizeViewPrefs(safeStorageGet(VIEW_PREFS_KEY, null));
  currentMode = document.getElementById(`tab-mode-${prefs.mode}`) ? prefs.mode : 'docs';
  activeFilters.sort = prefs.sort;
  const selSort = document.getElementById('sel-sort');
  if (selSort) selSort.value = prefs.sort;
  syncModeTabs();
  setView(prefs.view);
}

// 필터 칩·탭은 span/div라 Tab 키로 갈 수도, Enter로 누를 수도 없었다.
const KEY_CLICK_SELECTOR = '.chip[data-filter], .mode-tab, .stat-badge';

function syncChipA11y(root) {
  (root || document).querySelectorAll('.chip[data-filter]').forEach(chip => {
    chip.setAttribute('role', 'button');
    chip.setAttribute('tabindex', '0');
    chip.setAttribute('aria-pressed', chip.classList.contains('active') ? 'true' : 'false');
  });
}

function isKeyClick(e) {
  return e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar';
}

function hideAppLoading() {
  const el = document.getElementById('app-loading');
  if (el) el.hidden = true;
}

function showAppLoadingError(message) {
  const el = document.getElementById('app-loading');
  const txt = document.getElementById('app-loading-text');
  if (!el || !txt) {
    notify(message, 'error');
    return;
  }
  el.hidden = false;
  el.classList.add('error');
  el.setAttribute('role', 'alert');
  txt.textContent = message;
}

const RECENT_SEARCH_KEY = 'kocsc_recent_searches';

function safeStorageGet(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {}
}

function nextRecentSearches(items, query) {
  const q = String(query || '').trim();
  const list = Array.isArray(items) ? items.filter(x => typeof x === 'string') : [];
  if (q.length < 2) return list.slice(0, 8);
  return [q].concat(list.filter(x => x !== q)).slice(0, 8);
}

function loadRecentSearches() {
  const list = document.getElementById('recent-searches');
  if (!list) return;
  const items = nextRecentSearches(safeStorageGet(RECENT_SEARCH_KEY, []), '');
  list.innerHTML = items.map(q => `<option value="${escapeHtml(q)}"></option>`).join('');
}

function rememberSearch(query) {
  const next = nextRecentSearches(safeStorageGet(RECENT_SEARCH_KEY, []), query);
  safeStorageSet(RECENT_SEARCH_KEY, next);
  loadRecentSearches();
}

// 첫 검색이 전문 캐시를 만드느라 느려지지 않도록, 화면이 뜬 뒤 짧은 조각으로 나눠 미리 계산한다.
// Q&A 답변은 부모 문서 본문의 일부라, 두 목록을 모두 데우면 전문의 공백 제거 사본이 두 벌 생긴다.
// 기본 화면(문서)만 미리 데우고, Q&A 사본은 Q&A 검색을 처음 할 때 만든다(검색은 시간 조각으로
// 나뉘어 있어 첫 검색도 화면을 멈추지 않는다).
function warmSearchCache() {
  const lists = currentMode === 'qa' ? [QA_ITEMS] : [DOCUMENTS];
  let listIdx = 0;
  let pos = 0;
  const step = () => {
    const started = Date.now();
    while (listIdx < lists.length && Date.now() - started < 12) {
      const list = lists[listIdx] || [];
      if (pos >= list.length) {
        listIdx += 1;
        pos = 0;
        continue;
      }
      const item = list[pos++];
      const summary = item.answer_summary || '';
      const body = item.full_markdown || item.answer_full_text || item.answer_full || item.answer_markdown || '';
      const answer = body ? `${summary} ${body}` : summary;
      searchCache(item, 'answerNoSpace', answer, (x) => x.replace(/\s+/g, '').toLowerCase());
    }
    if (listIdx < lists.length) setTimeout(step, 30);
  };
  setTimeout(step, 500);
}

