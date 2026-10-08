// P0/P1 UX (기능_UIUX_개선_제안서): 모달 접근성·마감 요약·탭별 건수·연결 상태·폼 개선·유사 답변·이력 UI.
// 불변조건: 외부 참조 금지(단일 HTML), 검색은 filterItems 계열만(18·51), 전문은 full_markdown(정책 1~5).
// 이 파일은 마지막에 붙으므로 위 전역 함수를 감싸서 확장한다. Node 하네스에서도 읽힐 수 있게
// 최상위에서는 window/document가 없을 때 아무 일도 하지 않는다.
const _UXG = (typeof window !== 'undefined') ? window : ((typeof globalThis !== 'undefined') ? globalThis : {});
const _UXD = (typeof document !== 'undefined') ? document : null;

// ---------------------------------------------------------------------------
// P0-M1/M2. 모달 스택: Esc는 맨 위 하나만 닫고, 포커스는 이동·복귀·감금한다.
// ---------------------------------------------------------------------------
const MODAL_DEFS = [
  { id: 'add-ledger-backdrop', open: 'openAddLedgerModal', close: 'closeAddLedgerModal' },
  { id: 'edit-ledger-backdrop', open: 'openEditLedgerModal', close: 'closeEditLedgerModal' },
  { id: 'ledger-modal-backdrop', open: 'openLedgerModal', close: 'closeLedgerModal' },
  { id: 'config-modal-backdrop', open: 'openConfigModal', close: 'closeConfigModal' },
  { id: 'modal-backdrop', open: 'openDocModal', close: 'closeModal' },
  { id: 'compare-backdrop', open: 'openComparePicker', close: 'closeCompareModal' },
  { id: 'quarantine-modal-backdrop', open: 'openQuarantineModal', close: 'closeQuarantineModal' },
];
let MODAL_STACK = [];

function isModalOpen(id) {
  if (!_UXD) return false;
  const el = _UXD.getElementById(id);
  return !!(el && el.classList.contains('open'));
}

function focusablesOf(root) {
  if (!root || !root.querySelectorAll) return [];
  return Array.from(root.querySelectorAll(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )).filter(el => !!(el.offsetWidth || el.offsetHeight || el === _UXD.activeElement));
}

function trackModal(id) {
  if (!_UXD) return;
  MODAL_STACK = MODAL_STACK.filter(m => m.id !== id);
  MODAL_STACK.push({ id, opener: _UXD.activeElement });
  const el = _UXD.getElementById(id);
  const dlg = el ? el.querySelector('.modal-dialog') : null;
  const f = dlg ? focusablesOf(dlg) : [];
  const target = f.length ? f[0] : dlg;
  if (target && target.focus) {
    try { target.focus({ preventScroll: true }); } catch (e) { try { target.focus(); } catch (_e) {} }
  }
}

function untrackModal(id) {
  let opener = null;
  const idx = MODAL_STACK.map(m => m.id).lastIndexOf(id);
  if (idx !== -1) {
    opener = MODAL_STACK[idx].opener;
    MODAL_STACK.splice(idx, 1);
  }
  if (opener && opener.focus) {
    try { opener.focus({ preventScroll: true }); } catch (e) { /* opener가 사라졌으면 무시 */ }
  }
}

function modalCloser(id) {
  const def = MODAL_DEFS.find(d => d.id === id);
  if (!def) return null;
  const fn = _UXG[def.close];
  return (typeof fn === 'function') ? fn : null;
}

function closeTopModal() {
  for (let i = MODAL_STACK.length - 1; i >= 0; i--) {
    if (isModalOpen(MODAL_STACK[i].id)) {
      const fn = modalCloser(MODAL_STACK[i].id);
      if (fn) { fn(); return true; }
      return false;
    }
  }
  // 스택에 없지만 열려 있는 모달을 위에서부터 하나만 닫는다(직접 클래스를 건드린 경우 대비).
  const order = ['quarantine-modal-backdrop', 'compare-backdrop', 'modal-backdrop',
    'config-modal-backdrop', 'edit-ledger-backdrop', 'add-ledger-backdrop', 'ledger-modal-backdrop'];
  for (const id of order) {
    if (isModalOpen(id)) {
      const fn = modalCloser(id);
      if (fn) { fn(); return true; }
    }
  }
  return false;
}

function readAddLedgerForm() {
  const v = (id) => {
    const el = _UXD.getElementById(id);
    return el ? String(el.value == null ? '' : el.value).trim() : '';
  };
  return {
    seq_no: v('add-seq-no'), party: v('add-party'), requester: v('add-requester'),
    aide: v('add-aide'), title: v('add-title'), details: v('add-details'),
    deadline: v('add-deadline'), submit_date: v('add-submit-date'),
    status: v('add-status'), req_type: v('add-req-type'), note: v('add-note'),
  };
}

function isAddFormDirty() {
  const f = readAddLedgerForm();
  return !!(f.seq_no || f.party || f.requester || f.aide || f.title || f.details
    || f.deadline || f.submit_date || f.note);
}

function isEditFormDirty() {
  try {
    if (typeof readEditLedgerForm !== 'function') return false;
    const now = readEditLedgerForm();
    const before = (typeof EDIT_LEDGER_SNAPSHOT !== 'undefined' && EDIT_LEDGER_SNAPSHOT) || {};
    return Object.keys(now).some(k => String(now[k] == null ? '' : now[k]).trim()
      !== String(before[k] == null ? '' : before[k]).trim());
  } catch (e) {
    return false;
  }
}

// 입력 중 닫기는 되돌릴 수 없는 작업이라 confirm으로 확인한다(불변조건 23).
function confirmDiscardForm(backdropId) {
  let dirty = false;
  try {
    dirty = (backdropId === 'add-ledger-backdrop') ? isAddFormDirty() : isEditFormDirty();
  } catch (e) {
    dirty = false;
  }
  if (!dirty) return true;
  return confirm('적어 둔 내용이 있습니다. 저장하지 않고 닫을까요?');
}

function afterLedgerFormOpen(prefix, item) {
  try { refreshRequesterCandidates(); } catch (e) {}
  try { refreshDatePreviews(prefix); } catch (e) {}
  if (prefix === 'add') {
    try {
      const list = _UXD.getElementById('similar-list');
      if (list) list.innerHTML = '';
      const count = _UXD.getElementById('similar-count');
      if (count) count.textContent = '';
    } catch (e) {}
  }
  try { updateOfflineUi(); } catch (e) {}
  if (prefix === 'ledger') {
    try { loadLedgerHistory(item); } catch (e) {}
    try { initLedgerLinkRow(item); } catch (e) {}
  }
}

MODAL_DEFS.forEach(def => {
  const origOpen = _UXG[def.open];
  if (typeof origOpen === 'function') {
    _UXG[def.open] = function (...args) {
      const r = origOpen.apply(this, args);
      const track = () => {
        trackModal(def.id);
        if (def.id === 'add-ledger-backdrop') afterLedgerFormOpen('add');
        else if (def.id === 'edit-ledger-backdrop') afterLedgerFormOpen('edit');
        else if (def.id === 'ledger-modal-backdrop') afterLedgerFormOpen('ledger', args[0]);
      };
      if (r && typeof r.then === 'function') r.then(track, track);
      else track();
      return r;
    };
  }
  const origClose = _UXG[def.close];
  if (typeof origClose === 'function') {
    _UXG[def.close] = function (...args) {
      // 저장에 성공해 코드가 직접 닫을 때는 첫 인자로 true를 준다. 예전에는 그때도 폼이 '고친 상태'라
      // 저장 직후에 "저장하지 않고 닫을까요?"를 물었고, [취소]를 누르면 저장된 창이 그대로 남았다.
      const saved = args[0] === true;
      if (!saved && (def.id === 'add-ledger-backdrop' || def.id === 'edit-ledger-backdrop')) {
        if (!confirmDiscardForm(def.id)) return;
      }
      const r = origClose.apply(this, args);
      untrackModal(def.id);
      return r;
    };
  }
});

if (_UXD && _UXD.addEventListener) {
  // Tab 순환을 맨 위 모달 안에 가둔다.
  _UXD.addEventListener('keydown', (e) => {
    if (!e || e.key !== 'Tab' || !MODAL_STACK.length) return;
    const top = MODAL_STACK[MODAL_STACK.length - 1];
    if (!top || !isModalOpen(top.id)) return;
    const el = _UXD.getElementById(top.id);
    const dlg = el ? el.querySelector('.modal-dialog') : null;
    if (!dlg) return;
    const f = focusablesOf(dlg);
    if (!f.length) { e.preventDefault(); return; }
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && _UXD.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && _UXD.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

// ---------------------------------------------------------------------------
// P0-H3/F5. 오프라인에서는 동기화 버튼을 잠그고 등록 안내를 바꾼다.
// ---------------------------------------------------------------------------
function updateOfflineUi() {
  if (!_UXD) return;
  const offline = !((typeof window !== 'undefined' && window.location && window.location.protocol) || '').startsWith('http');
  const syncBtn = _UXD.getElementById('btn-trigger-sync');
  if (syncBtn && offline) {
    syncBtn.disabled = true;
    syncBtn.title = '서버 없이 연 화면(01번)에서는 쓸 수 없습니다. 02_웹관리서버_실행.bat으로 연 화면에서 눌러 주세요.';
    syncBtn.setAttribute('aria-disabled', 'true');
  }
  const sub = _UXD.getElementById('add-ledger-sub');
  if (sub && offline) {
    sub.textContent = '서버 없이 연 화면(01번)이라 이 브라우저에만 임시로 저장됩니다. 나중에 02번 화면에서 파일로 옮겨야 정식 등록됩니다.';
  }
}

// ---------------------------------------------------------------------------
// P1-3. 마감 현황 요약: 문서 탭에서도 기한 경과를 본다. 계산은 countLedgerDue 재사용.
// ---------------------------------------------------------------------------
function refreshDueSummary() {
  if (!_UXD || typeof countLedgerDue !== 'function') return;
  let due = null;
  try {
    due = countLedgerDue(typeof REQUEST_LEDGER !== 'undefined' ? REQUEST_LEDGER : []);
  } catch (e) {
    return;
  }
  if (!due) return;
  const set = (id, v) => {
    const el = _UXD.getElementById(id);
    if (el) el.textContent = v;
  };
  set('due-num-overdue', due.overdue || 0);
  set('due-num-soon', due.soon || 0);
  set('due-num-open', due.open || 0);
  const chip = _UXD.getElementById('due-chip-overdue');
  if (chip) chip.classList.toggle('has-overdue', (due.overdue || 0) > 0);
  const soonChip = _UXD.getElementById('due-chip-soon');
  if (soonChip) soonChip.classList.toggle('has-soon', (due.soon || 0) > 0);
}

function gotoDueFilter(due) {
  if (typeof switchMode !== 'function') return;
  switchMode('ledger');
  activeFilters.due = due;
  _UXD.querySelectorAll('.chip[data-filter="due"]').forEach(c => {
    c.classList.toggle('active', c.getAttribute('data-val') === due);
  });
  if (typeof syncChipA11y === 'function') {
    try { syncChipA11y(); } catch (e) {}
  }
  // 마감을 챙기러 들어온 것이므로 급한 것부터 보여 준다.
  if (!activeFilters.query && typeof setSortValue === 'function') setSortValue('deadline-asc');
  currentRenderLimit = 40;
  render();
}

// 주제 칩을 데이터의 topic_tags 빈도로 만든다(타 부서 배포본 고정 칩 문제 해소).
function buildTopicChips() {
  if (!_UXD || typeof rebuildChipGroup !== 'function') return;
  const freq = {};
  ((typeof DOCUMENTS !== 'undefined' && DOCUMENTS) || []).forEach(d => {
    String((d && d.topic_tags) || '').split(',').forEach(t => {
      t = t.trim();
      if (t) freq[t] = (freq[t] || 0) + 1;
    });
  });
  const entries = Object.entries(freq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([t, n]) => ({ value: t, label: `${t} (${n})` }));
  if (entries.length) rebuildChipGroup('topic-chips', 'topic', entries);
}

if (typeof _UXG.buildDynamicFilters === 'function') {
  const _buildDynamicFilters = _UXG.buildDynamicFilters;
  _UXG.buildDynamicFilters = function (...args) {
    const r = _buildDynamicFilters.apply(this, args);
    try { buildTopicChips(); } catch (e) {}
    try { refreshDueSummary(); } catch (e) {}
    return r;
  };
}

// ---------------------------------------------------------------------------
// P1-S1/S4/S5/S2. 탭별 결과 수·적용 필터 칩·모드별 정렬·검색 스피너.
// ---------------------------------------------------------------------------
function hasActiveSearch() {
  const f = (typeof activeFilters !== 'undefined' && activeFilters) || {};
  return !!(f.query || (f.year && f.year !== 'ALL') || (f.inst && f.inst !== 'ALL')
    || (f.topic && f.topic !== 'ALL') || (f.status && f.status !== 'ALL')
    || (f.due && f.due !== 'ALL') || f.tablesOnly || f.onlyFavorites);
}

function refreshSearchExtras() {
  if (!_UXD) return;
  const show = hasActiveSearch() && (typeof filterItemsFromList === 'function');
  const setTab = (id, v) => {
    const el = _UXD.getElementById(id);
    if (el) el.textContent = show ? ` · ${v}건` : '';
  };
  if (show) {
    // 같은 matchSearchItem으로 세 목록을 각각 센다(불변조건 51 — 결과 일치).
    let docs = 0;
    let qas = 0;
    let ledger = 0;
    let tables = 0;
    try {
      docs = filterItemsFromList(DOCUMENTS, false).length;
      qas = filterItemsFromList(QA_ITEMS, false).length;
      ledger = filterItemsFromList(REQUEST_LEDGER, false).length;
      tables = filterItemsFromList(
        (DOCUMENTS || []).filter(d => d && d.has_tables === 'Y'), false).length;
    } catch (e) { /* 본문 로딩 중 실패하면 0건으로 둔다 */ }
    setTab('tab-count-docs', docs);
    setTab('tab-count-qa', qas);
    setTab('tab-count-tables', tables);
    setTab('tab-count-ledger', ledger);
  } else {
    ['tab-count-docs', 'tab-count-qa', 'tab-count-tables', 'tab-count-ledger'].forEach(id => setTab(id, ''));
  }
  try { renderActiveChips(); } catch (e) {}
  try { updateSortOptions(); } catch (e) {}
  const clearBtn = _UXD.getElementById('search-clear');
  if (clearBtn) clearBtn.hidden = !((typeof activeFilters !== 'undefined' && activeFilters && activeFilters.query));
}

function renderActiveChips() {
  const box = _UXD.getElementById('active-filter-chips');
  if (!box) return;
  box.innerHTML = '';
  const f = (typeof activeFilters !== 'undefined' && activeFilters) || {};
  const chips = [];
  if (f.query) chips.push({ key: 'query', label: `검색어 “${f.query}”` });
  if (f.year && f.year !== 'ALL') chips.push({ key: 'year', label: `연도 ${f.year}` });
  // 지금 탭에서 실제로 걸리는 조건만 보여 준다(기관·주제는 답변서, 상태·마감은 관리대장).
  const isLedger = (typeof currentMode !== 'undefined' && currentMode === 'ledger');
  const dueLabels = (typeof DUE_FILTER_LABELS !== 'undefined' && DUE_FILTER_LABELS) || {};
  if (!isLedger && f.inst && f.inst !== 'ALL') chips.push({ key: 'inst', label: `기관 ${f.inst}` });
  if (!isLedger && f.topic && f.topic !== 'ALL') chips.push({ key: 'topic', label: `주제 ${f.topic}` });
  if (isLedger && f.status && f.status !== 'ALL') chips.push({ key: 'status', label: `상태 ${f.status}` });
  if (isLedger && f.due && f.due !== 'ALL') chips.push({ key: 'due', label: dueLabels[f.due] || '마감' });
  if (!isLedger && f.tablesOnly) chips.push({ key: 'tablesOnly', label: '표가 있는 자료만' });
  if (f.onlyFavorites) chips.push({ key: 'onlyFavorites', label: '즐겨찾기만' });
  chips.forEach(c => {
    const b = _UXD.createElement('button');
    b.type = 'button';
    b.className = 'active-chip';
    b.appendChild(_UXD.createTextNode(`${c.label} `));
    const x = _UXD.createElement('span');
    x.setAttribute('aria-hidden', 'true');
    x.textContent = '✕';
    b.appendChild(x);
    b.setAttribute('aria-label', `${c.label} 조건 지우기`);
    b.title = '누르면 이 조건을 지웁니다';
    b.addEventListener('click', () => clearOneFilter(c.key));
    box.appendChild(b);
  });
  if (chips.length > 1) {
    const all = _UXD.createElement('button');
    all.type = 'button';
    all.className = 'active-chip clear-all';
    all.textContent = '모두 지우기';
    all.addEventListener('click', clearAllFilters);
    box.appendChild(all);
  }
}

// 검색어가 없어지면 '검색어와 가까운 순'은 뜻이 없다. 입력창을 직접 지울 때와 같이 최신순으로 되돌린다.
function resetRelevanceSort() {
  if (activeFilters.sort === 'relevance' && typeof setSortValue === 'function') setSortValue('date-desc');
}

function clearOneFilter(key) {
  if (key === 'query') {
    activeFilters.query = '';
    const si = _UXD.getElementById('search-input');
    if (si) {
      si.value = '';
      if (si.focus) si.focus();
    }
    resetRelevanceSort();
  } else if (key === 'tablesOnly') {
    activeFilters.tablesOnly = false;
    const c = _UXD.getElementById('chk-tables-only');
    if (c) c.checked = false;
  } else if (key === 'onlyFavorites') {
    if (activeFilters.onlyFavorites && typeof filterFavorites === 'function') {
      filterFavorites();
      return;
    }
    activeFilters.onlyFavorites = false;
  } else {
    activeFilters[key] = 'ALL';
  }
  _UXD.querySelectorAll(`.chip[data-filter="${key}"]`).forEach(c => {
    c.classList.toggle('active', c.getAttribute('data-val') === 'ALL');
  });
  if (typeof syncChipA11y === 'function') {
    try { syncChipA11y(); } catch (e) {}
  }
  currentRenderLimit = 40;
  render();
}

function clearAllFilters() {
  activeFilters.query = '';
  activeFilters.year = 'ALL';
  activeFilters.inst = 'ALL';
  activeFilters.topic = 'ALL';
  activeFilters.status = 'ALL';
  activeFilters.due = 'ALL';
  activeFilters.tablesOnly = false;
  activeFilters.onlyFavorites = false;
  const si = _UXD.getElementById('search-input');
  if (si) si.value = '';
  const ct = _UXD.getElementById('chk-tables-only');
  if (ct) ct.checked = false;
  if (typeof setFavoritesOnly === 'function') setFavoritesOnly(false);
  resetRelevanceSort();
  _UXD.querySelectorAll('.chip').forEach(c => {
    c.classList.toggle('active', c.getAttribute('data-val') === 'ALL');
  });
  if (typeof syncChipA11y === 'function') {
    try { syncChipA11y(); } catch (e) {}
  }
  currentRenderLimit = 40;
  render();
}

function updateSortOptions() {
  const sel = _UXD.getElementById('sel-sort');
  if (!sel || !sel.options) return;
  // 탭마다 뜻이 있는 정렬만 남긴다(MODE_SORTS). 검색어가 없으면 '검색어와 가까운 순'도 숨긴다.
  const allowed = (typeof MODE_SORTS !== 'undefined' && typeof currentMode !== 'undefined' && MODE_SORTS[currentMode]) || null;
  const hasQuery = !!(typeof activeFilters !== 'undefined' && activeFilters && activeFilters.query);
  Array.from(sel.options).forEach(o => {
    let hide = allowed ? !allowed.includes(o.value) : false;
    if (o.value === 'relevance' && !hasQuery && sel.value !== 'relevance') hide = true;
    o.hidden = hide;
    o.disabled = hide;
  });
}

function showSearchSpinner() {
  const el = _UXD.getElementById('search-spinner');
  if (el) el.style.display = 'inline';
}

function hideSearchSpinner() {
  const el = _UXD.getElementById('search-spinner');
  if (el) el.style.display = 'none';
}

// ---------------------------------------------------------------------------
// S3. 검색 문법 칩: 누르면 예시가 입력창에 들어간다.
// ---------------------------------------------------------------------------
function initGrammarChips() {
  if (!_UXD) return;
  const box = _UXD.getElementById('grammar-chips');
  if (!box) return;
  box.addEventListener('click', (e) => {
    const btn = e.target && e.target.closest ? e.target.closest('.g-chip') : null;
    if (!btn) return;
    if (btn.hasAttribute('data-jump')) {
      const det = _UXD.getElementById(btn.getAttribute('data-jump'));
      if (det) {
        det.open = true;
        if (det.scrollIntoView) det.scrollIntoView({ block: 'nearest' });
      }
      return;
    }
    const input = _UXD.getElementById('search-input');
    if (!input) return;
    const ins = btn.getAttribute('data-insert') || '';
    const start = (input.selectionStart == null) ? input.value.length : input.selectionStart;
    const end = (input.selectionEnd == null) ? input.value.length : input.selectionEnd;
    input.value = input.value.slice(0, start) + ins + input.value.slice(end);
    let caret = start + ins.length;
    if (btn.hasAttribute('data-caret')) caret = start + ins.length + Number(btn.getAttribute('data-caret'));
    input.focus();
    try { input.setSelectionRange(caret, caret); } catch (err) {}
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

// ---------------------------------------------------------------------------
// P1-F2/F4. 날짜 정규화 미리보기 + 마감<요구 경고(저장은 허용).
// ---------------------------------------------------------------------------
function previewLedgerDate(value) {
  const s = String(value == null ? '' : value).trim();
  if (!s) return '';
  let m = /^(\d{4})[-.\/](\d{1,2})[-.\/](\d{1,2})/.exec(s);
  if (m) {
    return `→ ${m[1]}-${String(Number(m[2])).padStart(2, '0')}-${String(Number(m[3])).padStart(2, '0')} 로 저장됩니다`;
  }
  m = /^(\d{1,2})[\/.](\d{1,2})/.exec(s);
  if (m) {
    const y = new Date().getFullYear();
    return `→ ${y}-${String(Number(m[1])).padStart(2, '0')}-${String(Number(m[2])).padStart(2, '0')} 로 저장됩니다`;
  }
  return '원문 그대로 저장됩니다 (읽을 수 없는 표기)';
}

function refreshDatePreviews(prefix) {
  if (!_UXD) return;
  const get = (id) => _UXD.getElementById(id);
  const req = get(`${prefix}-req-date`);
  const dl = get(`${prefix}-deadline`);
  const sub = get(`${prefix}-submit-date`);
  [[req, `${prefix}-req-date-preview`], [dl, `${prefix}-deadline-preview`], [sub, `${prefix}-submit-date-preview`]]
    .forEach(([input, pvId]) => {
      const pv = get(pvId);
      if (pv && input) pv.textContent = previewLedgerDate(input.value);
    });
  // 마감일이 요구일보다 앞서면 경고한다. 저장은 막지 않는다.
  const wEl = get(`${prefix}-deadline-preview`);
  if (wEl && req && dl && req.value.trim() && dl.value.trim()
      && typeof parseLedgerDate === 'function') {
    const r = parseLedgerDate(req.value);
    const d = parseLedgerDate(dl.value);
    const early = !!(r && d && d < r);
    if (early) wEl.textContent += ' ⚠️ 마감일이 요구일보다 앞입니다 (저장은 됩니다)';
    if (wEl.classList) wEl.classList.toggle('warn', early);
  } else if (wEl && wEl.classList) {
    wEl.classList.remove('warn');
  }
}

function initDatePreviews() {
  if (!_UXD) return;
  ['add', 'edit'].forEach(prefix => {
    ['req-date', 'deadline', 'submit-date'].forEach(k => {
      const el = _UXD.getElementById(`${prefix}-${k}`);
      if (el) el.addEventListener('input', () => refreshDatePreviews(prefix));
    });
  });
}

// ---------------------------------------------------------------------------
// P1-4. 요구자 자동완성: 같은 사전을 등록·수정 폼이 함께 쓴다. 빈 칸만 미리 채운다.
// ---------------------------------------------------------------------------
function requesterStats() {
  const map = new Map();
  ((typeof REQUEST_LEDGER !== 'undefined' && REQUEST_LEDGER) || []).forEach(it => {
    const name = String((it && it.requester) || '').trim();
    if (!name) return;
    const e = map.get(name) || { count: 0, party: '', aide: '', latest: '' };
    e.count += 1;
    const ts = String((it && (it.updated_at || it.created_at || it.request_date)) || '');
    if (ts >= (e.latest || '')) {
      e.latest = ts;
      if (it.party) e.party = it.party;
      if (it.aide) e.aide = it.aide;
    }
    map.set(name, e);
  });
  return map;
}

function refreshRequesterCandidates() {
  if (!_UXD) return;
  const dl = _UXD.getElementById('requester-candidates');
  if (!dl) return;
  dl.innerHTML = '';
  requesterStats().forEach((e, name) => {
    const o = _UXD.createElement('option');
    o.value = name;
    o.label = `${name} (${e.count}건)`;
    dl.appendChild(o);
  });
}

function fillRequesterDefaults(prefix) {
  if (!_UXD) return;
  const reqEl = _UXD.getElementById(`${prefix}-requester`);
  if (!reqEl) return;
  const st = requesterStats().get(reqEl.value.trim());
  if (!st) return;
  const partyEl = _UXD.getElementById(`${prefix}-party`);
  const aideEl = _UXD.getElementById(`${prefix}-aide`);
  if (partyEl && !partyEl.value.trim() && st.party) partyEl.value = st.party;
  if (aideEl && !aideEl.value.trim() && st.aide) aideEl.value = st.aide;
}

function initRequesterAutocomplete() {
  if (!_UXD) return;
  ['add', 'edit'].forEach(prefix => {
    const reqEl = _UXD.getElementById(`${prefix}-requester`);
    if (reqEl) reqEl.addEventListener('change', () => fillRequesterDefaults(prefix));
  });
}

// ---------------------------------------------------------------------------
// P1-2. 신규 등록 중 과거 유사 답변 추천 (클라이언트 검색과 같은 평가 함수).
// ---------------------------------------------------------------------------
const SIM_STOP = new Set(['현황', '관련', '자료', '요청', '제출', '대해', '대한', '관한',
  '및', '또는', '최근', '3년간', '연도별', '계획', '대책', '실태', '내역', '목록', '사항', '건수']);
let SIM_TIMER = null;

function similarKeywords() {
  if (!_UXD) return [];
  const gv = (id) => {
    const el = _UXD.getElementById(id);
    return el ? String(el.value || '') : '';
  };
  const text = `${gv('add-title')} ${gv('add-details')}`;
  const toks = text.replace(/["“”'']/g, ' ')
    .split(/[\s,·/()[\]]+/)
    .map(t => t.trim())
    .filter(t => t.length >= 2 && !SIM_STOP.has(t) && !/^[0-9.\-]+$/.test(t));
  const cleaned = toks
    .map(t => t.replace(/(에서|에게|으로|로|은|는|이|가|을|를|의|에|과|와|도|만|부터|까지)$/, ''))
    .filter(t => t.length >= 2);
  return Array.from(new Set(cleaned)).slice(0, 4);
}

function initSimilarPanel() {
  if (!_UXD) return;
  ['add-title', 'add-details'].forEach(id => {
    const el = _UXD.getElementById(id);
    if (el) el.addEventListener('input', () => {
      clearTimeout(SIM_TIMER);
      SIM_TIMER = setTimeout(refreshSimilarPanel, 600);
    });
  });
}

function refreshSimilarPanel() {
  if (!_UXD) return;
  const list = _UXD.getElementById('similar-list');
  if (!list) return;
  const count = _UXD.getElementById('similar-count');
  const keys = similarKeywords();
  if (!keys.length) {
    list.innerHTML = '';
    if (count) count.textContent = '';
    return;
  }
  if (typeof bodiesLoading === 'function' && bodiesLoading()) {
    list.innerHTML = '<div class="similar-hint">답변서 본문을 불러오는 중입니다. 잠시 뒤에 더 정확하게 찾아 줍니다.</div>';
  }
  let prepared = null;
  try {
    prepared = prepareSearchQuery(keys.join(' OR '));
  } catch (e) {
    return;
  }
  const scored = [];
  const push = (item, kind) => {
    let s = -1;
    try {
      s = matchSearchItem(item, prepared, false);
    } catch (e) {
      s = -1;
    }
    if (s >= 0) scored.push({ item, kind, score: s });
  };
  ((typeof DOCUMENTS !== 'undefined' && DOCUMENTS) || []).forEach(d => push(d, 'doc'));
  ((typeof QA_ITEMS !== 'undefined' && QA_ITEMS) || []).forEach(q => push(q, 'qa'));
  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, 5);
  if (count) count.textContent = top.length ? `${top.length}건` : '';
  list.innerHTML = '';
  if (!top.length) {
    list.innerHTML = '<div class="similar-hint">비슷한 과거 답변을 찾지 못했습니다.</div>';
    return;
  }
  top.forEach(({ item, kind }) => {
    const b = _UXD.createElement('button');
    b.type = 'button';
    b.className = 'similar-item';
    const title = item.title || item.question_title || '(제목 없음)';
    b.textContent = `${kind === 'qa' ? '[질문] ' : ''}${title} — ${item.requester || ''} ${item.request_date || item.year || ''}`.trim();
    b.addEventListener('click', () => {
      if (kind === 'qa' && typeof DOCS_MAP !== 'undefined' && DOCS_MAP[item.doc_id]) {
        openDocModal(DOCS_MAP[item.doc_id], item.q_num);
      } else if (item.doc_id && typeof openDocModal === 'function') {
        openDocModal(item);
      }
    });
    list.appendChild(b);
  });
}

// ---------------------------------------------------------------------------
// P1-1. 대장 변경 이력: 02는 API, 01은 내장 페이로드(LEDGER_HISTORY).
// ---------------------------------------------------------------------------
function loadLedgerHistory(item) {
  if (!_UXD || !item) return;
  const list = _UXD.getElementById('ledger-history-list');
  if (!list) return;
  const count = _UXD.getElementById('ledger-history-count');
  const off = _UXD.getElementById('ledger-history-offline');
  list.innerHTML = '';
  if (count) count.textContent = '';
  const isHttp = ((typeof window !== 'undefined' && window.location && window.location.protocol) || '').startsWith('http');
  if (off) off.style.display = 'none';
  if (isHttp && item.ledger_id) {
    fetch(`/api/ledger/history?ledger_id=${encodeURIComponent(item.ledger_id)}&limit=50`)
      .then(res => res.json().catch(() => ({})))
      .then(d => {
        const entries = (d && d.success && Array.isArray(d.timeline)) ? d.timeline : null;
        if (!entries) {
          list.innerHTML = '<div class="history-empty">바뀐 기록을 불러오지 못했습니다.</div>';
          return;
        }
        paintHistoryEntries(entries, list, count);
      })
      .catch(() => {
        list.innerHTML = '<div class="history-empty">바뀐 기록을 불러오지 못했습니다.</div>';
      });
    return;
  }
  const all = (typeof LEDGER_HISTORY !== 'undefined' && LEDGER_HISTORY) || {};
  const entries = all[item.ledger_id] || [];
  if (!entries.length) {
    if (off) off.style.display = 'block';
    return;
  }
  paintHistoryEntries(entries, list, count);
}

function paintHistoryEntries(entries, list, count) {
  if (!entries.length) {
    list.innerHTML = '<div class="history-empty">아직 바뀐 기록이 없습니다.</div>';
    return;
  }
  if (count) count.textContent = `(${entries.length}건)`;
  entries.forEach(en => list.appendChild(renderHistoryEntry(en)));
}

// 기록을 남긴 쪽을 쉬운 말로 바꾼다. 모르는 값은 그대로 보여 준다.
const HISTORY_ACTOR_LABELS = { web: '화면에서', excel: '엑셀에서', system: '프로그램이 자동으로', pipeline: '새 문서 반영 중에' };

function historyActorLabel(actor) {
  const a = String(actor || '').trim();
  if (!a) return '';
  return HISTORY_ACTOR_LABELS[a.toLowerCase()] || a;
}

function renderHistoryEntry(en) {
  const wrap = _UXD.createElement('div');
  wrap.className = 'history-item';
  const head = _UXD.createElement('div');
  head.className = 'history-head';
  head.textContent = [en.label || en.action || '기록', historyActorLabel(en.actor), en.changed_at || '']
    .filter(Boolean).join(' · ');
  wrap.appendChild(head);
  (en.changes || []).forEach(c => {
    const row = _UXD.createElement('div');
    row.className = 'history-change';
    const f = _UXD.createElement('span');
    f.className = 'history-field';
    f.textContent = c.label || c.field || '';
    const v = _UXD.createElement('span');
    v.className = 'history-values';
    v.textContent = `${c.before || '(비어 있음)'} → ${c.after || '(비어 있음)'}`;
    row.appendChild(f);
    row.appendChild(v);
    wrap.appendChild(row);
  });
  if (en.summary && !(en.changes && en.changes.length)) {
    const s = _UXD.createElement('div');
    s.className = 'history-summary';
    s.textContent = en.summary;
    wrap.appendChild(s);
  }
  return wrap;
}

// ---------------------------------------------------------------------------
// 답변서 수동 연결/변경/해제 (DB 전용 파생값 — 서버의 linked_doc_id 경로를 탄다).
// ---------------------------------------------------------------------------
function initLedgerLinkRow(item) {
  if (!_UXD || !item) return;
  const sel = _UXD.getElementById('ledger-link-select');
  if (!sel) return;
  sel.innerHTML = '';
  const cur = item.linked_doc_id || '';
  const docs = ((typeof DOCUMENTS !== 'undefined' && DOCUMENTS) || [])
    .slice()
    .sort((a, b) => String((b && (b.request_date || b.year)) || '')
      .localeCompare(String((a && (a.request_date || a.year)) || '')))
    .slice(0, 200);
  const empty = _UXD.createElement('option');
  empty.value = '';
  empty.textContent = cur ? '(연결 끊기)' : '연결할 답변서를 고르세요 (최근 200건)';
  sel.appendChild(empty);
  if (cur && (typeof DOCS_MAP === 'undefined' || !DOCS_MAP[cur])) {
    const o = _UXD.createElement('option');
    o.value = cur;
    o.textContent = `${cur} (답변서를 찾을 수 없음)`;
    sel.appendChild(o);
  }
  docs.forEach(d => {
    const o = _UXD.createElement('option');
    o.value = d.doc_id;
    o.textContent = `${d.doc_id} · ${d.title || '(제목 없음)'}`.slice(0, 80);
    sel.appendChild(o);
  });
  sel.value = cur || '';
}

// 서버 값을 바꾸는 동작이라 연타해도 한 번만 나가게 한다(불변조건 23).
function saveLedgerDocLink() {
  return runExclusive('ledger-link', _UXD.getElementById('btn-save-ledger-link'), saveLedgerDocLinkNow);
}

async function saveLedgerDocLinkNow() {
  const item = (typeof currentSelectedLedgerItem !== 'undefined' && currentSelectedLedgerItem) || null;
  const sel = _UXD.getElementById('ledger-link-select');
  if (!item || !sel) return;
  const newId = sel.value || '';
  if (!((typeof window !== 'undefined' && window.location && window.location.protocol) || '').startsWith('http')) {
    notify('⚠️ 서버 없이 연 화면(01번)에서는 답변서 연결을 저장할 수 없습니다. 02번 화면에서 해 주세요.');
    return;
  }
  if (newId && (typeof DOCS_MAP === 'undefined' || !DOCS_MAP[newId])) {
    notify('⚠️ 연결할 답변서를 찾을 수 없습니다.');
    return;
  }
  const body = { linked_doc_id: newId };
  if (item.updated_at) body.expected_updated_at = item.updated_at;
  try {
    const res = await fetch(`/api/ledger/${encodeURIComponent(item.ledger_id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok || !d.success) {
      notify(`❌ 연결 저장 실패: ${d.error || res.statusText || '서버 오류'}`);
      return;
    }
    item.linked_doc_id = newId;
    if (d.updated_at) item.updated_at = d.updated_at;
    openLedgerModal(item);
    render();
    notify(newId ? '✅ 답변서를 연결했습니다.' : '✅ 답변서 연결을 끊었습니다.');
  } catch (e) {
    notify(`❌ 서버에 연결하지 못했습니다: ${e.message}`);
  }
}

function clearLedgerDocLink() {
  const item = (typeof currentSelectedLedgerItem !== 'undefined' && currentSelectedLedgerItem) || null;
  if (item && !item.linked_doc_id) {
    notify('연결된 답변서가 없습니다.', 'info');
    return;
  }
  const sel = _UXD.getElementById('ledger-link-select');
  if (sel) sel.value = '';
  saveLedgerDocLink();
}

// ---------------------------------------------------------------------------
// P1-N2. 연결 상태 표시등: 오프라인/서버/엑셀 대기·격리를 한 곳에.
// ---------------------------------------------------------------------------
let CONN_DETAIL = { mode: 'unknown', pending: 0, failed: 0 };

async function refreshConnStatus() {
  if (!_UXD) return;
  const el = _UXD.getElementById('conn-status');
  if (!el) return;
  const offline = !((typeof window !== 'undefined' && window.location && window.location.protocol) || '').startsWith('http');
  if (offline) {
    let n = 0;
    try {
      n = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]').length;
    } catch (e) {
      n = 0;
    }
    CONN_DETAIL = { mode: 'offline', pending: n, failed: 0 };
    el.className = 'conn-status offline';
    el.textContent = n ? `● 보기 전용 (서버 꺼짐) · 임시 등록 ${n}건` : '● 보기 전용 (서버 꺼짐)';
    el.title = '서버 없이 연 화면(01번)입니다. 검색과 열람만 됩니다. 누르면 자세히 알려 드립니다.';
    return;
  }
  try {
    const res = await fetch('/api/sync');
    if (!res.ok) throw new Error(String(res.status));
    const s = await res.json();
    const pending = Number(s.excel_pending || 0);
    const failed = Number(s.excel_failed || 0);
    CONN_DETAIL = { mode: 'online', pending, failed };
    el.className = `conn-status ${failed ? 'error' : pending ? 'warn' : 'ok'}`;
    el.textContent = failed
      ? `⚠ 엑셀에 반영 못 한 변경 ${failed}건`
      : pending ? `● 정상 · 엑셀에 적는 중 ${pending}건` : '● 정상 (서버 연결됨)';
    el.title = '누르면 연결 상태를 자세히 알려 드립니다.';
  } catch (e) {
    CONN_DETAIL = { mode: 'unknown', pending: 0, failed: 0 };
    el.className = 'conn-status error';
    el.textContent = '● 서버 응답 없음';
    el.title = '서버가 응답하지 않습니다. 02_웹관리서버_실행.bat 창이 켜져 있는지 확인하세요. 누르면 다시 확인합니다.';
  }
}

function showConnectionDetails() {
  const d = CONN_DETAIL || { mode: 'unknown' };
  if (d.mode === 'offline') {
    notify(d.pending
      ? `⚠️ 서버 없이 연 화면(01번)입니다. 임시 등록 ${d.pending}건은 [파일로 저장]한 뒤 02번 화면에서 그 파일을 골라야 정식 등록됩니다.`
      : '서버 없이 연 화면(01번)입니다. 검색과 열람은 모두 되고, 관리대장 수정·삭제와 답변서 연결은 02_웹관리서버_실행.bat으로 연 화면에서 할 수 있습니다.', d.pending ? 'warn' : 'info');
    return;
  }
  if (d.mode === 'online' && (d.failed || d.pending)) {
    notify(`${d.failed ? `대장 엑셀에 반영하지 못한 변경 ${d.failed}건` : ''}${d.failed && d.pending ? ' · ' : ''}${d.pending ? `엑셀에 아직 적지 못한 변경 ${d.pending}건 (엑셀 파일을 닫으면 자동으로 적습니다)` : ''}`, d.failed ? 'warn' : 'info');
    if (d.failed && typeof openQuarantineModal === 'function') openQuarantineModal();
    return;
  }
  if (d.mode !== 'online') {
    refreshConnStatus();
    notify('서버 상태를 다시 확인하고 있습니다. 계속 응답이 없으면 02_웹관리서버_실행.bat 창이 켜져 있는지 봐 주세요.', 'warn');
    return;
  }
  refreshConnStatus();
  notify('✅ 정상입니다. 화면에서 고친 내용은 관리대장과 대장 엑셀에 바로 반영됩니다.');
}

if (typeof _UXG.refreshServerExcelStatus === 'function') {
  const _refreshServerExcelStatus = _UXG.refreshServerExcelStatus;
  _UXG.refreshServerExcelStatus = function (...args) {
    const r = _refreshServerExcelStatus.apply(this, args);
    try { refreshConnStatus(); } catch (e) {}
    return r;
  };
}

// ---------------------------------------------------------------------------
// 부팅 시 한 번.
// ---------------------------------------------------------------------------
function initUxEnhancements() {
  updateOfflineUi();
  initGrammarChips();
  initDatePreviews();
  initRequesterAutocomplete();
  initSimilarPanel();
  refreshConnStatus();
  refreshDueSummary();
}

try {
  if (_UXD) {
    if (_UXD.readyState === 'loading' && _UXD.addEventListener) {
      _UXD.addEventListener('DOMContentLoaded', () => {
        try { initUxEnhancements(); } catch (e) { console.warn(e); }
      });
    } else {
      initUxEnhancements();
    }
  }
} catch (e) {
  if (typeof console !== 'undefined' && console.warn) console.warn(e);
}
