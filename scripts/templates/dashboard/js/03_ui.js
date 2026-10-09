function init() {
  document.getElementById('stat-docs').innerText = DOCUMENTS.length;
  document.getElementById('stat-qa').innerText = QA_ITEMS.length;
  document.getElementById('stat-tables').innerText = DOCUMENTS.filter(d => d.has_tables === 'Y').length;
  document.getElementById('count-mode-docs').innerText = DOCUMENTS.length;
  document.getElementById('count-mode-qa').innerText = QA_ITEMS.length;
  document.getElementById('count-mode-tables').innerText = DOCUMENTS.filter(d => d.has_tables === 'Y').length;
  const cntLedger = document.getElementById('count-mode-ledger');
  if (cntLedger) cntLedger.innerText = REQUEST_LEDGER.length;
  restoreViewPrefs();
  updateFavCount();
  buildCharts();
  buildDynamicFilters();
  updateModeFilters();
  updateModeUi();
  loadRecentSearches();
  render();
  warmSearchCache();

  applyDeepLink();
  window.addEventListener('hashchange', applyDeepLink);
}

function applyDeepLink() {
  const raw = window.location.hash || '';
  let targetId = '';
  try {
    targetId = decodeURIComponent(raw.replace(/^#/, ''));
  } catch (e) {
    targetId = raw.replace(/^#/, '');
  }
  if (targetId.startsWith('doc=')) {
    const id = targetId.slice(4);
    const found = DOCS_MAP[id];
    if (found) {
      setTimeout(() => openDocModal(found), 200);
    } else {
      console.warn('딥링크 문서를 찾을 수 없습니다:', id);
    }
  } else if (targetId.startsWith('qa=')) {
    const id = targetId.slice(3);
    const qa = QA_MAP[id];
    if (qa) {
      const found = DOCS_MAP[qa.doc_id || qa.parent_id];
      if (found) setTimeout(() => openDocModal(found, qa.q_num), 200);
    }
  } else if (targetId.startsWith('ledger=') || targetId.startsWith('req=')) {
    const id = targetId.replace(/^ledger=/, '').replace(/^req=/, '');
    switchMode('ledger');
    const found = LEDGER_MAP[id];
    if (found) setTimeout(() => openLedgerModal(found), 200);
  }
}

function updateFavCount() {
  document.getElementById('stat-favs').innerText = favorites.length;
}

// 탭마다 쓸 수 있는 정렬이 다르다. 질문 수·표 우선은 답변서에만, 마감순은 관리대장에만 뜻이 있다.
const MODE_SORTS = {
  docs: ['date-desc', 'date-asc', 'relevance', 'qa-count', 'table-first'],
  tables: ['date-desc', 'date-asc', 'relevance', 'qa-count'],
  qa: ['date-desc', 'date-asc', 'relevance', 'table-first'],
  ledger: ['date-desc', 'date-asc', 'relevance', 'deadline-asc']
};
const MODE_PLACEHOLDERS = {
  docs: '답변서 제목·본문·의원명으로 검색 (Ctrl+K)',
  tables: '표가 있는 답변서를 제목·본문·의원명으로 검색 (Ctrl+K)',
  qa: '질문 제목·답변 내용·의원명으로 검색 (Ctrl+K)',
  ledger: '요구자료 제목·세부내역·의원명·연번으로 검색 (Ctrl+K)'
};

function setSortValue(val) {
  activeFilters.sort = val;
  const sel = document.getElementById('sel-sort');
  if (sel) sel.value = val;
}

// 탭이 바뀔 때 화면 부속을 그 탭에 맞춘다: 검색창 안내, 전체 선택(답변서 전용), 정렬 선택지.
function updateModeUi() {
  const input = document.getElementById('search-input');
  if (input) input.placeholder = MODE_PLACEHOLDERS[currentMode] || MODE_PLACEHOLDERS.docs;
  const canSelect = currentMode === 'docs' || currentMode === 'tables';
  const selWrap = document.getElementById('select-all-wrap');
  if (selWrap) selWrap.hidden = !canSelect;
  const selAll = document.getElementById('chk-select-all');
  if (selAll) selAll.checked = false;
  const allowed = MODE_SORTS[currentMode] || MODE_SORTS.docs;
  if (!allowed.includes(activeFilters.sort)) {
    setSortValue(activeFilters.query ? 'relevance' : 'date-desc');
  }
  if (typeof updateSortOptions === 'function') updateSortOptions();
}

function switchMode(mode) {
  currentMode = mode;
  currentRenderLimit = 40;
  // 즐겨찾기는 답변서에만 있다. 관리대장으로 가면서 켜 두면 결과가 늘 0건이 된다.
  if (mode === 'ledger' && activeFilters.onlyFavorites) setFavoritesOnly(false);
  syncModeTabs();
  updateModeFilters();
  updateModeUi();
  render();
  saveViewPrefs();
}

function changeSort(val) {
  activeFilters.sort = val;
  currentRenderLimit = 40;
  render();
  saveViewPrefs();
}

function loadMoreItems() {
  currentRenderLimit += 40;
  render();
}

function changeFontSize(delta) {
  currentModalFontSize = Math.max(11, Math.min(24, currentModalFontSize + delta));
  const formatted = document.getElementById('modal-view-formatted');
  const text = document.getElementById('modal-text-content');
  const qa = document.getElementById('modal-view-qa');
  if (formatted) formatted.style.fontSize = `${currentModalFontSize}px`;
  if (text) text.style.fontSize = `${currentModalFontSize}px`;
  if (qa) qa.style.fontSize = `${currentModalFontSize}px`;
}

function toggleModalMaximize() {
  const dlg = document.getElementById('main-modal-dialog');
  const btn = document.getElementById('btn-modal-max');
  if (dlg) {
    const isMax = dlg.classList.toggle('maximized');
    btn.innerText = isMax ? '원래 크기로' : '크게 보기';
  }
}

function setFavoritesOnly(on) {
  activeFilters.onlyFavorites = !!on;
  const btn = document.getElementById('btn-show-favs');
  if (btn) {
    btn.classList.toggle('is-on', activeFilters.onlyFavorites);
    btn.setAttribute('aria-pressed', activeFilters.onlyFavorites ? 'true' : 'false');
    btn.innerText = activeFilters.onlyFavorites ? '★ 즐겨찾기만 보는 중' : '★ 즐겨찾기만';
  }
}

function filterFavorites() {
  const turnOn = !activeFilters.onlyFavorites;
  if (turnOn && !favorites.length) {
    notify('즐겨찾기한 답변서가 아직 없습니다. 답변서 카드의 ★을 누르면 즐겨찾기에 담깁니다.', 'info');
    return;
  }
  setFavoritesOnly(turnOn);
  currentRenderLimit = 40;
  // 즐겨찾기는 답변서에만 있으므로 관리대장에서 켜면 답변서 탭으로 옮긴다.
  if (turnOn && currentMode === 'ledger') {
    switchMode('docs');
    return;
  }
  render();
}

function toggleAnalytics() {
  const section = document.getElementById('analytics-section');
  const isHidden = (section.style.display === 'none' || !section.style.display);
  section.style.display = isHidden ? 'block' : 'none';
  const btn = document.getElementById('btn-toggle-analytics');
  if (btn) {
    btn.innerText = isHidden ? '통계 접기' : '통계 보기';
    btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
  }
}

// 검색창 값을 바꾸고 입력 이벤트를 흘려, 직접 친 것과 같은 경로(정렬 전환·지연 렌더)를 타게 한다.
function setSearchQuery(text) {
  const input = document.getElementById('search-input');
  if (!input) return;
  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function buildCharts() {
  // 1. Requesters TOP 8
  const reqCounts = {};
  DOCUMENTS.forEach(d => {
    reqCounts[d.requester] = (reqCounts[d.requester] || 0) + 1;
  });
  const sortedReqs = Object.entries(reqCounts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const maxReq = sortedReqs[0] ? sortedReqs[0][1] : 1;
  const reqContainer = document.getElementById('chart-requesters');
  reqContainer.innerHTML = '';
  sortedReqs.forEach(([req, count]) => {
    const pct = (count / maxReq) * 100;
    const row = document.createElement('div');
    row.className = 'chart-bar-row';
    row.title = `‘${req}’(으)로 검색`;
    row.onclick = () => {
      currentRenderLimit = 40;
      setSearchQuery(req);
    };
    row.innerHTML = `
      <span class="chart-bar-label" title="${escapeHtml(req)}">${escapeHtml(req)}</span>
      <div class="chart-bar-track">
        <div class="chart-bar-fill" style="width: ${pct}%;"></div>
      </div>
      <span class="chart-bar-val">${count}건</span>
    `;
    reqContainer.appendChild(row);
  });

  // 2. Topics TOP 8
  const topCounts = {};
  DOCUMENTS.forEach(d => {
    if (d.topic_tags) {
      d.topic_tags.split(',').forEach(t => {
        const tr = t.trim();
        if (tr) topCounts[tr] = (topCounts[tr] || 0) + 1;
      });
    }
  });
  const sortedTops = Object.entries(topCounts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const maxTop = sortedTops[0] ? sortedTops[0][1] : 1;
  const topContainer = document.getElementById('chart-topics');
  topContainer.innerHTML = '';
  sortedTops.forEach(([top, count]) => {
    const pct = (count / maxTop) * 100;
    const row = document.createElement('div');
    row.className = 'chart-bar-row';
    row.title = `주제 ‘${top}’만 보기`;
    row.onclick = () => {
      activeFilters.topic = top;
      document.querySelectorAll('.chip[data-filter="topic"]').forEach(c => {
        c.classList.toggle('active', c.getAttribute('data-val') === top);
      });
      syncChipA11y();
      currentRenderLimit = 40;
      // 주제는 답변서에만 있다. 관리대장을 보던 중이면 답변서 탭으로 옮겨 결과를 보여 준다.
      if (currentMode === 'ledger') switchMode('docs');
      else render();
    };
    row.innerHTML = `
      <span class="chart-bar-label" title="${escapeHtml(top)}">${escapeHtml(top)}</span>
      <div class="chart-bar-track">
        <div class="chart-bar-fill alt" style="width: ${pct}%;"></div>
      </div>
      <span class="chart-bar-val">${count}건</span>
    `;
    topContainer.appendChild(row);
  });
}

function getDocMdUrl(relPath) {
  if (!relPath) return '#';
  if (window.location.protocol.startsWith('http')) {
    return '/api/file?path=' + encodeURIComponent(relPath);
  }
  return relPath;
}

function getDocOrigUrl(relPath) {
  if (!relPath) return '#';
  if (window.location.protocol.startsWith('http')) {
    return '/api/download?path=' + encodeURIComponent(relPath);
  }
  return relPath;
}

async function openFileLocation(relPath) {
  if (!relPath) {
    notify('원본 파일 경로가 없습니다.', 'error');
    return;
  }
  if (!window.location.protocol.startsWith('http')) {
    notify('파일이 있는 폴더를 열려면 02_웹관리서버_실행.bat으로 연 화면에서 눌러 주세요.', 'warn');
    return;
  }
  try {
    const res = await fetch('/api/open?path=' + encodeURIComponent(relPath) + '&mode=explorer');
    const data = await res.json();
    if (res.ok && data.success) {
      notify('원본 파일이 있는 폴더를 열었습니다.', 'success');
    } else {
      notify('폴더를 열지 못했습니다: ' + ((data && data.error) || '파일을 찾을 수 없습니다.'), 'error');
    }
  } catch (err) {
    notify('서버에 연결하지 못했습니다: ' + err.message, 'error');
  }
}

function handleOrigClick(event, relPath) {
  if (window.location.protocol.startsWith('http')) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    openFileLocation(relPath);
    return false;
  }
  return true;
}


