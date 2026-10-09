let searchDebounceTimer = null;
let recentSearchTimer = null;
document.addEventListener('DOMContentLoaded', async () => {
  initAppConfig();
  try {
    await loadAndDecompressDatabase();
    init();
  } catch (err) {
    console.error('대시보드 초기화 실패:', err);
    showAppLoadingError(`자료를 불러오지 못했습니다.\n${(err && err.message) || err}\n\n00_새자료_추가_및_DB동기화.bat으로 화면을 다시 만들거나, 최신 Chrome·Edge에서 열어 주세요.`);
    return;
  }
  hideAppLoading();
  // 대용량 모드면 문서 전문 블록을 화면이 뜬 뒤에 푼다(기다리지 않는다).
  loadDeferredBodies();

  document.getElementById('search-input').addEventListener('input', (e) => {
    activeFilters.query = e.target.value.trim();
    if (activeFilters.query && (!activeFilters.sort || activeFilters.sort === 'date-desc')) {
      activeFilters.sort = 'relevance';
      const selSort = document.getElementById('sel-sort');
      if (selSort) selSort.value = 'relevance';
    } else if (!activeFilters.query && activeFilters.sort === 'relevance') {
      activeFilters.sort = 'date-desc';
      const selSort = document.getElementById('sel-sort');
      if (selSort) selSort.value = 'date-desc';
    }
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
      render();
    }, 200);
    clearTimeout(recentSearchTimer);
    recentSearchTimer = setTimeout(() => rememberSearch(activeFilters.query), 1500);
  });

  // 연도·진행상태 칩은 데이터로 다시 만들어지므로 개별 등록 대신 이벤트 위임을 쓴다.
  document.querySelector('.control-panel').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip[data-filter]');
    if (!chip) return;
    const type = chip.getAttribute('data-filter');
    const val = chip.getAttribute('data-val');
    chip.parentElement.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    syncChipA11y(chip.parentElement);
    activeFilters[type] = val;
    currentRenderLimit = 40;
    render();
  });

  document.addEventListener('keydown', (e) => {
    if (!isKeyClick(e) || !e.target || !e.target.closest) return;
    const target = e.target.closest(KEY_CLICK_SELECTOR);
    if (!target) return;
    e.preventDefault();
    target.click();
  });

  document.getElementById('chk-tables-only').addEventListener('change', (e) => {
    activeFilters.tablesOnly = e.target.checked;
    render();
  });

  document.getElementById('chk-synonyms').addEventListener('change', () => {
    render();
  });

  document.getElementById('btn-view-card').addEventListener('click', () => {
    setView('card');
    saveViewPrefs();
  });

  document.getElementById('btn-view-table').addEventListener('click', () => {
    setView('table');
    saveViewPrefs();
  });

  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('modal-backdrop').addEventListener('click', (e) => {
    if (e.target.id === 'modal-backdrop') closeModal();
  });
  document.getElementById('compare-backdrop').addEventListener('click', (e) => {
    if (e.target.id === 'compare-backdrop') closeCompareModal();
  });
  // 읽기만 하는 창은 바깥(어두운 부분)을 눌러도 닫힌다. 입력 폼(등록·수정)은 실수로 닫히지 않게 뺀다.
  // 창 안에서 글자를 끌어 선택하다가 바깥에서 손을 떼는 경우는 닫지 않는다(누른 곳도 바깥이어야 한다).
  [['ledger-modal-backdrop', () => closeLedgerModal()],
    ['quarantine-modal-backdrop', () => closeQuarantineModal()],
    ['config-modal-backdrop', () => closeConfigModal()]].forEach(([id, close]) => {
    const bd = document.getElementById(id);
    if (!bd) return;
    let pressedOnBackdrop = false;
    bd.addEventListener('mousedown', (e) => { pressedOnBackdrop = e.target === bd; });
    bd.addEventListener('click', (e) => {
      if (e.target === bd && pressedOnBackdrop) close();
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (typeof closeTopModal === 'function' && closeTopModal()) return;
      closeModal();
      closeCompareModal();
      if (typeof closeQuarantineModal === 'function') closeQuarantineModal();
    }
    if ((e.ctrlKey && e.key.toLowerCase() === 'k') || (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA')) {
      e.preventDefault();
      const sInput = document.getElementById('search-input');
      if (sInput) {
        sInput.focus();
        sInput.select();
      }
    }
  });

  // Global CSV Export Function for Ledger
  window.exportLedgerToCsv = function(items) {
    const list = items || filterItems();
    let csv = '\uFEFF';
    csv += '관리번호,연도,연번,소속,요구자,보좌관,요구자료명,세부요구내역,요구일자,제출기한,제출일자,담당부서,진행상태,요청형태,비고,연계문서\n';
    list.forEach(l => {
      csv += csvRow([
        csvCell(l.ledger_id), csvCell(l.year), csvCell(l.seq_no), csvCell(l.party),
        csvCell(l.requester), csvCell(l.aide), csvCell(l.title), csvCell(l.details, 1000),
        csvCell(l.request_date), csvCell(l.deadline), csvCell(l.submit_date),
        csvCell(l.department), csvCell(l.status), csvCell(l.request_type),
        csvCell(l.note), csvCell(l.linked_doc_id)
      ]);
    });
    downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8;' }),
      `국회_요구자료_관리대장_${new Date().toISOString().slice(0,10)}.csv`);
  };

  // Export CSV for current filtered items
  document.getElementById('btn-export').addEventListener('click', () => {
    const filtered = filterItems();
    let csv = '\uFEFF';
    if (currentMode === 'docs' || currentMode === 'tables') {
      // 화면 데이터에는 엑셀용 잘림본(answer_full_text)이 없어 이 칸이 늘 비어 있었다. 전문 앞부분을 넣는다.
      csv += '문서ID,일자,연도,기관,요구자,관리번호,제목,답변내용(앞 1000자),표포함,주제태그,원본경로,마크다운경로\n';
      filtered.forEach(d => {
        csv += csvRow([
          csvCell(d.doc_id), csvCell(d.request_date), csvCell(d.year), csvCell(d.institution),
          csvCell(d.requester), csvCell(d.doc_number), csvCell(d.title),
          csvCell(d.full_markdown || d.answer_full_text || d.answer_summary, 1000),
          csvCell(d.has_tables), csvCell(d.topic_tags),
          csvCell(d.original_path), csvCell(d.parsed_md_path)
        ]);
      });
    } else if (currentMode === 'ledger') {
      window.exportLedgerToCsv(filtered);
      return;
    } else {
      csv += 'QA_ID,문서ID,일자,연도,기관,요구자,질의번호,질의제목,답변내용(앞 1000자),표포함,원본경로\n';
      filtered.forEach(q => {
        csv += csvRow([
          csvCell(q.qa_id), csvCell(q.doc_id), csvCell(q.request_date), csvCell(q.year),
          csvCell(q.institution), csvCell(q.requester), csvCell(q.q_num),
          csvCell(q.question_title),
          csvCell(q.answer_full || q.answer_markdown, 1000),
          csvCell(q.has_tables), csvCell(q.original_path)
        ]);
      });
    }
    downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8;' }),
      `국회_대외기관_자료요구_${currentMode}_${new Date().toISOString().slice(0,10)}.csv`);
  });

  // 실시간 양방향 동기화: 엑셀 수기 수정 또는 백엔드 DB 변경 자동 감지
  let _lastDataVersion = null;
  async function checkLiveSyncVersion() {
    if (!window.location.protocol.startsWith('http')) return;
    try {
      const res = await fetch('/api/ledger/version');
      if (res.ok) {
        const d = await res.json();
        if (d.success && d.version) {
          if (_lastDataVersion !== d.version) {
            console.log('[실시간 동기화] 엑셀/DB 변경 감지 ➔ 화면 자동 갱신');
            if (!await reloadLedgerDataFromServer()) return;
          }
          _lastDataVersion = d.version;
        }
      }
    } catch (e) {}
  }

  async function reloadLedgerDataFromServer() {
    try {
      const serverResp = await fetch('/api/ledger');
      if (serverResp.ok) {
        const sdata = await serverResp.json();
        if (sdata && sdata.success && Array.isArray(sdata.data)) {
          applyServerLedger(sdata.data);
          buildDynamicFilters();
          render();
          return true;
        }
      }
    } catch (err) {}
    return false;
  }

  // 탭이 보이지 않을 때는 주기 확인을 건너뛴다(서버 부하·전력 절약). 돌아오면 즉시 한 번 확인한다.
  const visibleCheckLiveSync = () => { if (document.visibilityState !== 'hidden') checkLiveSyncVersion(); };
  const visibleRefreshExcel = () => { if (document.visibilityState !== 'hidden') refreshServerExcelStatus(); };
  // 창 포커스(사용자가 엑셀 저장 후 브라우저 복귀 시) 및 4초 주기 감지
  window.addEventListener('focus', visibleCheckLiveSync);
  setInterval(visibleCheckLiveSync, 4000);
  // 엑셀 반영 대기·격리 건수는 30초마다와 창 포커스 때 확인한다.
  visibleRefreshExcel();
  window.addEventListener('focus', visibleRefreshExcel);
  setInterval(visibleRefreshExcel, 30000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') { visibleCheckLiveSync(); visibleRefreshExcel(); }
  });
});
