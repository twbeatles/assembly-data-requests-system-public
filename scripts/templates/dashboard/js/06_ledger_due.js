// ---------------------------------------------------------------------------
// 관리대장 마감 계산·동적 필터·빈 결과 안내·서버 상태·최근 검색어·검색 캐시 선계산
// ---------------------------------------------------------------------------
const LEDGER_DONE_STATUSES = new Set(['제출', '완료', '[삭제]', '업무설명']);
const DUE_SOON_DAYS = 7;

function parseLedgerDate(value) {
  const m = /^(\d{4})[-.\/](\d{1,2})[-.\/](\d{1,2})/.exec(String(value || '').trim());
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return isNaN(d.getTime()) ? null : d;
}

function startOfDay(date) {
  const d = date || new Date();
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

// 마감 상태: done(제출·완료) / overdue(기한 경과) / soon(7일 이내) / later / none(마감일 없음)
function ledgerDueInfo(item, today) {
  const status = String((item && item.status) || '').trim();
  if (LEDGER_DONE_STATUSES.has(status)) return { state: 'done', days: null };
  const deadline = parseLedgerDate(item && item.deadline);
  if (!deadline) return { state: 'none', days: null };
  const days = Math.round((deadline - startOfDay(today)) / 86400000);
  if (days < 0) return { state: 'overdue', days };
  if (days <= DUE_SOON_DAYS) return { state: 'soon', days };
  return { state: 'later', days };
}

// 진행 상태 배지 색. 카드·표·상세창이 함께 쓴다. 빈 값은 엑셀 규칙과 같이 '미제출'이다(불변조건 24).
const LEDGER_PROGRESS_STATUSES = new Set(['작성중', '검토중']);

function ledgerStatusLabel(status) {
  return String(status || '').trim() || '미제출';
}

function ledgerStatusClass(status) {
  const st = ledgerStatusLabel(status);
  if (LEDGER_DONE_STATUSES.has(st)) return 'done';
  if (LEDGER_PROGRESS_STATUSES.has(st)) return 'progress';
  if (st === '해당없음') return 'na';
  return 'todo';
}

function statusPillHtml(item) {
  const st = ledgerStatusLabel(item && item.status);
  return `<span class="status-pill ${ledgerStatusClass(st)}">${escapeHtml(st)}</span>`;
}

// 카드·표 줄 왼쪽에 마감 상태를 색 띠로 보여 줄 때 쓰는 클래스.
function dueRowClass(item, today) {
  const state = ledgerDueInfo(item, today).state;
  return state === 'overdue' ? 'due-overdue' : state === 'soon' ? 'due-soon' : '';
}

const DUE_FILTER_LABELS = { overdue: '마감 지남', soon: '7일 안에 마감', open: '아직 미제출' };

function matchesDueFilter(item, due, today) {
  const info = ledgerDueInfo(item, today);
  if (due === 'soon') return info.state === 'soon';
  if (due === 'overdue') return info.state === 'overdue';
  if (due === 'open') return info.state !== 'done';
  return true;
}

function dueBadgeHtml(item, today) {
  const info = ledgerDueInfo(item, today);
  if (info.state === 'overdue') {
    return `<span class="due-badge overdue" title="마감일이 지났는데 아직 제출하지 않았습니다">마감 ${-info.days}일 지남</span>`;
  }
  if (info.state === 'soon') {
    return `<span class="due-badge soon" title="마감까지 ${info.days}일 남았습니다">${info.days === 0 ? '오늘 마감' : `D-${info.days}`}</span>`;
  }
  return '';
}

function compareByDeadline(a, b, today) {
  const rank = { overdue: 0, soon: 1, later: 2, none: 3, done: 4 };
  const ia = ledgerDueInfo(a, today);
  const ib = ledgerDueInfo(b, today);
  if (rank[ia.state] !== rank[ib.state]) return rank[ia.state] - rank[ib.state];
  const da = parseLedgerDate(a.deadline);
  const db = parseLedgerDate(b.deadline);
  if (da && db && da - db !== 0) return da - db;
  return String(b.request_date || b.year || '').localeCompare(String(a.request_date || a.year || ''));
}

function collectFilterYears(docs, ledger) {
  const years = new Set();
  for (const list of [docs || [], ledger || []]) {
    for (const it of list) {
      const y = String((it && it.year) || '').trim();
      if (/^\d{4}$/.test(y)) years.add(y);
    }
  }
  return Array.from(years).sort((a, b) => b.localeCompare(a));
}

function collectLedgerStatuses(ledger) {
  const counts = new Map();
  for (const it of ledger || []) {
    const st = String((it && it.status) || '').trim();
    if (!st || st === '[삭제]') continue;
    counts.set(st, (counts.get(st) || 0) + 1);
  }
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
}

function rebuildChipGroup(containerId, filterKey, entries) {
  const box = document.getElementById(containerId);
  if (!box) return;
  const values = entries.map(e => e.value);
  if (activeFilters[filterKey] !== 'ALL' && !values.includes(activeFilters[filterKey])) activeFilters[filterKey] = 'ALL';
  const chips = [{ value: 'ALL', label: '전체' }].concat(entries);
  box.innerHTML = chips.map(c =>
    `<span class="chip${activeFilters[filterKey] === c.value ? ' active' : ''}" data-filter="${filterKey}" data-val="${escapeHtml(c.value)}">${escapeHtml(c.label)}</span>`
  ).join('');
}

function countLedgerDue(ledger, today) {
  const counts = { soon: 0, overdue: 0, open: 0 };
  for (const it of ledger || []) {
    const info = ledgerDueInfo(it, today);
    if (info.state === 'soon') counts.soon += 1;
    if (info.state === 'overdue') counts.overdue += 1;
    if (info.state !== 'done') counts.open += 1;
  }
  return counts;
}

function buildDynamicFilters() {
  rebuildChipGroup('year-chips', 'year', collectFilterYears(DOCUMENTS, REQUEST_LEDGER).map(y => ({ value: y, label: `${y}년` })));
  rebuildChipGroup('status-chips', 'status', collectLedgerStatuses(REQUEST_LEDGER).map(([st, n]) => ({ value: st, label: `${st} (${n})` })));
  const due = countLedgerDue(REQUEST_LEDGER);
  const labels = {
    soon: `${DUE_FILTER_LABELS.soon} (${due.soon})`,
    overdue: `${DUE_FILTER_LABELS.overdue} (${due.overdue})`,
    open: `${DUE_FILTER_LABELS.open} (${due.open})`
  };
  document.querySelectorAll('.chip[data-filter="due"]').forEach(chip => {
    const key = chip.getAttribute('data-val');
    if (labels[key]) chip.textContent = labels[key];
  });
  const ledgerTab = document.getElementById('tab-mode-ledger');
  if (ledgerTab) {
    ledgerTab.title = due.overdue
      ? `마감이 지났는데 아직 제출하지 않은 요구자료 ${due.overdue}건`
      : '접수한 요구자료의 마감·제출 상태를 관리합니다';
    ledgerTab.classList.toggle('has-overdue', due.overdue > 0);
  }
  syncChipA11y();
}

function updateModeFilters() {
  const isLedger = currentMode === 'ledger';
  const show = (id, visible) => {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  };
  show('filter-group-inst', !isLedger);
  show('filter-group-topic', !isLedger);
  show('filter-group-tables-only', !isLedger);
  show('filter-group-status', isLedger);
  show('filter-group-due', isLedger);
}

function emptyStateHints(filteredCount, totalCount) {
  const active = [];
  if (activeFilters.query) active.push(`검색어 "${activeFilters.query}"`);
  if (activeFilters.year !== 'ALL') active.push(`연도 ${activeFilters.year}`);
  if (currentMode === 'ledger') {
    if (activeFilters.status !== 'ALL') active.push(`진행상태 ${activeFilters.status}`);
    if (activeFilters.due !== 'ALL') active.push(`마감 ${DUE_FILTER_LABELS[activeFilters.due] || activeFilters.due}`);
  } else {
    if (activeFilters.inst !== 'ALL') active.push(`기관 ${activeFilters.inst}`);
    if (activeFilters.topic !== 'ALL') active.push(`주제 ${activeFilters.topic}`);
    if (activeFilters.tablesOnly) active.push('통계표 포함만');
  }
  if (activeFilters.onlyFavorites) active.push('즐겨찾기만');
  const hints = [];
  const q = String(activeFilters.query || '').trim();
  if (totalCount === 0) {
    hints.push(currentMode === 'ledger'
      ? '관리대장이 비어 있습니다. 위의 [＋ 요구자료 등록]으로 첫 요구자료를 올려 보세요.'
      : '적재된 문서가 없습니다. 새자료_투입폴더에 문서를 넣고 00_새자료_추가_및_DB동기화.bat을 실행하세요.');
  } else {
    if (active.length) hints.push('아래 버튼으로 검색어·필터를 지우거나, 검색어를 줄여 보세요.');
    const positive = q.split(/\s+/).filter(t => t && !t.startsWith('-') && t.toUpperCase() !== 'OR' && t !== '|');
    if (positive.length > 1 && !/(^|\s)(OR|or|\|)(\s|$)/.test(q)) {
      hints.push('여러 단어는 모두 들어간 자료만 찾습니다. 하나만 있어도 되면 "OR"로 이어 주세요.');
    }
    if (q && positive.length === 0 && /(^|\s)-\S/.test(q)) hints.push('제외어(-)만으로는 검색되지 않습니다. 찾을 단어를 함께 입력하세요.');
    if (q) hints.push('위쪽 다른 탭에 같은 검색어로 찾은 건수가 표시됩니다. 그 탭을 눌러 보세요.');
  }
  return { active, hints };
}

function renderEmptyState(filteredCount, totalCount) {
  const box = document.getElementById('empty-state');
  if (!box) return;
  box.style.display = filteredCount === 0 ? 'block' : 'none';
  if (filteredCount !== 0) return;
  const { active, hints } = emptyStateHints(filteredCount, totalCount);
  const reason = document.getElementById('empty-state-reason');
  if (reason) reason.textContent = active.length ? `지금 건 조건: ${active.join(' · ')}` : '';
  const list = document.getElementById('empty-state-hints');
  if (list) list.innerHTML = hints.map(h => `<li>${escapeHtml(h)}</li>`).join('');
  const clearBtn = document.getElementById('empty-clear-filters');
  if (clearBtn) clearBtn.hidden = !active.length;
}

