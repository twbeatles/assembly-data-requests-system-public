// --- System & Department Config Functions ---
// 렌더러가 설정 원본값을 JSON으로 넣는다. 예전에는 부서명이 이미 붙은 HTML 이스케이프 제목을 넣어,
// 오프라인(01) 화면 제목에 부서명이 두 번 나오고 부제에 "&amp;"가 글자 그대로 보였다.
const EMBEDDED_APP_CONFIG = /* __APP_CONFIG_JSON__ */ null;
let appConfig = Object.assign({
  department_name: '자료요구담당부서',
  agency_name: '공공기관',
  system_title: '국회·대외기관 자료요구 스마트 관리 및 검색 시스템',
  system_subtitle: '공문서 답변 전문 & 지능형 검색·공유 협업 플랫폼'
}, EMBEDDED_APP_CONFIG || {});

function initAppConfig() {
  const savedDept = localStorage.getItem('user_dept_name');
  const savedAgency = localStorage.getItem('user_agency_name');
  const savedTitle = localStorage.getItem('user_sys_title');
  const savedSub = localStorage.getItem('user_sys_sub');
  if (savedDept) appConfig.department_name = savedDept;
  if (savedAgency) appConfig.agency_name = savedAgency;
  if (savedTitle) appConfig.system_title = savedTitle;
  if (savedSub) appConfig.system_subtitle = savedSub;
  applyConfigToUI();

  // 파일로 연 화면(01번)에는 서버가 없다. 부를 곳이 없는 요청으로 콘솔에 오류를 남기지 않는다.
  if (!window.location.protocol.startsWith('http')) return;
  fetch('/api/config')
    .then(r => r.json())
    .then(res => {
      if (res && res.success && res.data) {
        if (res.data.department_name) appConfig.department_name = res.data.department_name;
        if (res.data.agency_name) appConfig.agency_name = res.data.agency_name;
        if (res.data.system_title) appConfig.system_title = res.data.system_title;
        if (res.data.system_subtitle) appConfig.system_subtitle = res.data.system_subtitle;
        applyConfigToUI();
      }
    }).catch(() => {});
}

function applyConfigToUI() {
  const h1 = document.getElementById('header-main-title');
  const hp = document.getElementById('header-sub-title');
  const cleanTitle = (appConfig.system_title || '').replace(/^🛡️\s*/, '');
  if (h1) h1.textContent = `🛡️ ${appConfig.department_name} ${cleanTitle}`;
  if (hp) hp.textContent = `${appConfig.agency_name} ${appConfig.department_name} | ${appConfig.system_subtitle}`;
  document.title = `${appConfig.department_name} ${cleanTitle}`;
  // 엑셀 파일 이름은 설정(excel_filename)을 따른다. 범용 배포본은 이름이 달라 고정 링크가 열리지 않았다.
  const excelLink = document.getElementById('link-excel-db');
  if (excelLink && appConfig.excel_filename) excelLink.setAttribute('href', appConfig.excel_filename);
}

function openConfigModal() {
  document.getElementById('cfg-dept').value = appConfig.department_name || '';
  document.getElementById('cfg-agency').value = appConfig.agency_name || '';
  document.getElementById('cfg-title').value = (appConfig.system_title || '').replace(/^🛡️\s*/, '') || '';
  document.getElementById('cfg-subtitle').value = appConfig.system_subtitle || '';
  const bd = document.getElementById('config-modal-backdrop');
  if (bd) { bd.style.display = 'flex'; bd.classList.add('open'); }
}

function closeConfigModal() {
  const bd = document.getElementById('config-modal-backdrop');
  if (bd) { bd.style.display = 'none'; bd.classList.remove('open'); }
}

function handleConfigSubmit(e) {
  if (e) e.preventDefault();
  return runExclusive('config-save', submitButtonOf('config-form'), () => handleConfigSubmitNow());
}

async function handleConfigSubmitNow(e) {
  if (e) e.preventDefault();
  const d = document.getElementById('cfg-dept').value.trim();
  const a = document.getElementById('cfg-agency').value.trim();
  const t = document.getElementById('cfg-title').value.trim();
  const s = document.getElementById('cfg-subtitle').value.trim();

  appConfig.department_name = d;
  appConfig.agency_name = a;
  appConfig.system_title = t;
  appConfig.system_subtitle = s;

  localStorage.setItem('user_dept_name', d);
  localStorage.setItem('user_agency_name', a);
  localStorage.setItem('user_sys_title', t);
  localStorage.setItem('user_sys_sub', s);

  applyConfigToUI();

  if (window.location.protocol.startsWith('http')) {
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          department_name: d,
          agency_name: a,
          system_title: t,
          system_subtitle: s
        })
      });
      if (res.ok) {
        notify('설정을 저장했습니다. 화면을 새로 고칩니다.');
        closeConfigModal();
        setTimeout(() => location.reload(), 700);
        return;
      }
      // 서버가 거절한 경우(권한·파일 오류 등)를 "로컬에 반영됨"으로 덮지 않는다. (감사 R4-15c)
      const data = await res.json().catch(() => ({}));
      notify(`⚠️ 설정을 서버에 저장하지 못했습니다: ${data.error || res.statusText}\n이 브라우저에서만 임시로 적용됩니다.`);
      closeConfigModal();
      return;
    } catch (err) {
      notify(`⚠️ 서버에 연결하지 못해 설정을 저장하지 못했습니다: ${err.message}\n이 브라우저에서만 임시로 적용됩니다.`);
      closeConfigModal();
      return;
    }
  }
  notify('이 브라우저에서만 바뀌었습니다. 모든 사용자에게 적용하려면 02_웹관리서버_실행.bat으로 연 화면에서 저장하세요.', 'warn');
  closeConfigModal();
}

// --- Request Ledger UI Functions ---

// 오늘 날짜(이 컴퓨터 시각). toISOString()은 UTC라 한국 시각 오전 9시 전에는 어제 날짜가 나온다.
function localDateString(date) {
  const d = date || new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function openAddLedgerModal() {
  const form = document.getElementById('form-add-ledger');
  if (form) form.reset();
  const yearInput = document.getElementById('add-year');
  if (yearInput) yearInput.value = new Date().getFullYear();
  const deptInput = document.getElementById('add-dept');
  if (deptInput) deptInput.value = appConfig.department_name || '__DEFAULT_DEPT__';
  const todayStr = localDateString();
  const reqDateInput = document.getElementById('add-req-date');
  if (reqDateInput) reqDateInput.value = todayStr;
  document.getElementById('add-ledger-backdrop').classList.add('open');
}

function closeAddLedgerModal() {
  document.getElementById('add-ledger-backdrop').classList.remove('open');
}

function submitNewLedgerItem() {
  return runExclusive('ledger-add', submitButtonOf('form-add-ledger'), submitNewLedgerItemNow);
}

let pendingRegistration = null;
function registrationKey(payload) {
  const fingerprint = JSON.stringify(payload);
  try { pendingRegistration = JSON.parse(sessionStorage.getItem('pending-ledger-registration')) || pendingRegistration; } catch (e) {}
  if (!pendingRegistration || pendingRegistration.fingerprint !== fingerprint) {
    pendingRegistration = { fingerprint, key: 'web-' + (crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random()) };
    try { sessionStorage.setItem('pending-ledger-registration', JSON.stringify(pendingRegistration)); } catch (e) {}
  }
  return pendingRegistration.key;
}
function clearRegistrationKey() {
  pendingRegistration = null;
  try { sessionStorage.removeItem('pending-ledger-registration'); } catch (e) {}
}

async function submitNewLedgerItemNow() {
  const year = document.getElementById('add-year').value.trim();
  const seq_no = document.getElementById('add-seq-no').value.trim();
  const party = document.getElementById('add-party').value.trim();
  const requester = document.getElementById('add-requester').value.trim();
  const aide = document.getElementById('add-aide').value.trim();
  const dept = document.getElementById('add-dept').value.trim();
  const title = document.getElementById('add-title').value.trim();
  const details = document.getElementById('add-details').value.trim();
  const req_date = document.getElementById('add-req-date').value.trim();
  const deadline = document.getElementById('add-deadline').value.trim();
  const submit_date = document.getElementById('add-submit-date').value.trim();
  const status = document.getElementById('add-status').value;
  const req_type = document.getElementById('add-req-type').value;
  const note = document.getElementById('add-note').value.trim();

  if (!title || !requester) {
    notify('요구자료 제목과 의원명(요구자)은 꼭 적어야 합니다.');
    return;
  }

  const payload = {
    year, seq_no, party, requester, aide, department: dept,
    title, details, request_date: req_date, deadline,
    submit_date, status, request_type: req_type, note
  };

  let savedItem = null;

  let excelSyncMsg = '';

  const isHttp = window.location.protocol.startsWith('http');

  // Keep the key across response loss, including a page reload.
  if (isHttp) payload.idempotency_key = registrationKey(payload);
  // Try API first
  if (isHttp) {
    try {
      const res = await fetch('/api/ledger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        notify(`❌ 등록 실패: ${data.error || res.statusText || '서버 오류'}`);
        return;
      }
      if (data.item) {
        savedItem = data.item;
        clearRegistrationKey();
        if (data.excel_message) {
          excelSyncMsg = '\n\n' + data.excel_message;
        }
      }
    } catch (err) {
      notify(`❌ 서버에 연결하지 못했습니다: ${err.message}`);
      return;
    }
  }

  if (!savedItem) {
    // 서버가 없는 오프라인(file://) 화면. 이 데이터는 이 브라우저에만 남는다.
    if (!confirm('서버 없이 연 화면(01번)이라 정식 등록이 되지 않습니다.\n\n'
      + '지금 등록하면 이 브라우저에만 임시로 저장되고, 관리대장과 대장 엑셀에는 들어가지 않습니다.\n'
      + '나중에 [파일로 저장]한 뒤 02번 화면에서 그 파일을 고르면 정식 등록됩니다.\n\n'
      + '임시로 저장할까요?')) {
      return;
    }
    // Offline fallback: calculate max sequence for the specific year
    const yearStr = String(year);
    const yearItems = REQUEST_LEDGER.filter(l => String(l.year) === yearStr);
    let maxSeq = 0;
    yearItems.forEach(it => {
      const m = (it.ledger_id || '').match(/REQ-\d+-(\d+)/);
      if (m) {
        const num = parseInt(m[1], 10);
        if (!isNaN(num) && num > maxSeq) maxSeq = num;
      }
    });
    savedItem = {
      ...payload,
      // A local-only ID must not impersonate the server's sequential namespace.
      // It is replaced by the server-issued REQ ID during cloud synchronization.
      ledger_id: `LOCAL-${year}-${(crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`)}`,
      linked_doc_id: '',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };
    try {
      const localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
      localLedger.unshift(savedItem);
      localStorage.setItem('kocsc_local_ledger', JSON.stringify(localLedger));
    } catch (e) {}
  }

  REQUEST_LEDGER = REQUEST_LEDGER.filter(item => item.ledger_id !== savedItem.ledger_id);
  REQUEST_LEDGER.unshift(savedItem);
  LEDGER_MAP[savedItem.ledger_id] = savedItem;

  if (document.getElementById('count-mode-ledger')) {
    document.getElementById('count-mode-ledger').innerText = REQUEST_LEDGER.length;
  }

  // 저장을 마쳤으므로 "저장하지 않고 닫을까요?"를 묻지 않고 닫는다(true).
  closeAddLedgerModal(true);
  if (typeof buildDynamicFilters === 'function') buildDynamicFilters();
  switchMode('ledger');
  checkAndNotifyOfflineSync();
  const isLocalOnly = String(savedItem.ledger_id || '').startsWith('LOCAL-');
  notify(isLocalOnly
    ? `⚠️ 이 브라우저에만 임시로 저장했습니다 (관리대장·엑셀에는 아직 없음)
${savedItem.requester} - ${savedItem.title}

[파일로 저장]한 뒤 02번 화면에서 그 파일을 고르면 정식 등록됩니다.`
    : `✅ 요구자료를 등록했습니다.
[${savedItem.ledger_id}] ${savedItem.requester} - ${savedItem.title}${excelSyncMsg}`);
}

// 오프라인(file://) 화면에서는 서버 DB·마스터 엑셀에 쓸 수 없다. 이 브라우저에만 있는 임시 등록분
// (LOCAL- ID)만 여기서 고치거나 지울 수 있다. 예전에는 서버 항목도 수정·삭제가 "성공"으로 표시됐지만
// 새로고침하면 그대로 돌아왔다. (감사 R4-06)
function isLocalOnlyLedgerId(ledgerId) {
  return String(ledgerId || '').startsWith('LOCAL-');
}

function ledgerWriteBlockReason(item) {
  if (window.location.protocol.startsWith('http')) return '';
  if (item && isLocalOnlyLedgerId(item.ledger_id)) return '';
  return '서버 없이 연 화면(01번)에서는 관리대장을 고치거나 지울 수 없습니다.\n'
    + '02_웹관리서버_실행.bat으로 연 화면에서 해 주세요.';
}

function applyLedgerWriteButtons(item) {
  const reason = ledgerWriteBlockReason(item);
  ['btn-edit-ledger', 'btn-delete-ledger', 'btn-mark-submitted'].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    if (reason) {
      btn.setAttribute('aria-disabled', 'true');
      btn.title = reason;
    } else {
      btn.removeAttribute('aria-disabled');
      btn.title = '';
    }
  });
}

function openLedgerModal(item) {
  currentSelectedLedgerItem = item;
  applyLedgerWriteButtons(item);
  document.getElementById('ledger-detail-id').innerText = item.ledger_id || '';
  
  // 빈 진행 상태를 '제출'로 보여 주던 것을 엑셀 규칙과 같은 '미제출'로 맞췄다(불변조건 24).
  const statusBadge = document.getElementById('ledger-detail-status-badge');
  statusBadge.innerText = ledgerStatusLabel(item.status);
  statusBadge.className = `status-pill ${ledgerStatusClass(item.status)}`;
  const dueState = ledgerDueInfo(item).state;
  const dueBox = document.getElementById('ledger-detail-due');
  if (dueBox) dueBox.innerHTML = dueBadgeHtml(item);
  // 이미 제출했거나 제출 대상이 아닌 항목에는 [제출 완료로 표시]를 보이지 않는다.
  const markBtn = document.getElementById('btn-mark-submitted');
  if (markBtn) markBtn.hidden = dueState === 'done' || ledgerStatusLabel(item.status) === '해당없음';

  document.getElementById('ledger-detail-title').innerText = item.title || '(제목 없음)';
  document.getElementById('ledger-detail-req').innerText = `${item.party || ''} ${item.requester || ''}`.trim() || '요구자 모름';
  document.getElementById('ledger-detail-seq').innerText = item.seq_no ? `연번 ${item.seq_no}` : '';
  document.getElementById('ledger-detail-dept').innerText = item.department || '';
  document.getElementById('ledger-detail-date').innerText = item.request_date || item.year || '';

  document.getElementById('ledger-detail-body').innerText = item.details || '(적어 둔 세부 요구내역이 없습니다)';
  document.getElementById('ledger-detail-reqdate').innerText = item.request_date || '-';
  const deadlineEl = document.getElementById('ledger-detail-deadline');
  deadlineEl.innerText = item.deadline || '-';
  deadlineEl.className = dueState === 'overdue' ? 'is-overdue' : '';
  document.getElementById('ledger-detail-submitdate').innerText = item.submit_date || '-';
  document.getElementById('ledger-detail-aide').innerText = item.aide || '-';
  document.getElementById('ledger-detail-department').innerText = item.department || '-';
  document.getElementById('ledger-detail-reqtype').innerText = item.request_type || '-';
  document.getElementById('ledger-detail-notes').innerText = item.note || '-';

  const linkedBox = document.getElementById('ledger-detail-linked-box');
  const btnGoto = document.getElementById('btn-goto-linked-doc');
  if (item.linked_doc_id && DOCS_MAP[item.linked_doc_id]) {
    linkedBox.style.display = 'flex';
    document.getElementById('ledger-detail-linked-text').innerText = `${item.linked_doc_id}: ${DOCS_MAP[item.linked_doc_id].title}`;
    btnGoto.onclick = () => {
      closeLedgerModal();
      openDocModal(DOCS_MAP[item.linked_doc_id]);
    };
  } else {
    linkedBox.style.display = 'none';
  }

  document.getElementById('ledger-modal-backdrop').classList.add('open');
  if (item.ledger_id) {
    history.replaceState(null, null, `#ledger=${item.ledger_id}`);
  }
  const bodyEl = document.querySelector('#ledger-modal-backdrop .modal-body');
  if (bodyEl) bodyEl.scrollTop = 0;
}

function closeLedgerModal() {
  document.getElementById('ledger-modal-backdrop').classList.remove('open');
  history.replaceState(null, null, ' ');
}

function copyLedgerDetails() {
  if (!currentSelectedLedgerItem) return;
  const it = currentSelectedLedgerItem;
  const text = `[국회 요구자료 관리대장]
■ 요구번호(연번): ${it.seq_no || it.ledger_id}
■ 소속/의원: ${it.party || ''} ${it.requester || ''} (보좌관: ${it.aide || '-'})
■ 요구자료명: ${it.title}
■ 요구일자: ${it.request_date || '-'} (마감: ${it.deadline || '-'})
■ 담당부서: ${it.department || '-'}
■ 진행상태: ${it.status || '-'}

[세부 요구내역]
${it.details || '(없음)'}

[비고]
${it.note || '-'}`;
  copyText(text, '요구자료 내용을 복사했습니다.');
}

// Server live ledger fetch
async function fetchLatestLedgerFromServer() {
  if (!window.location.protocol.startsWith('http')) return;
  try {
    const res = await fetch('/api/ledger');
    if (res.ok) {
      const json = await res.json();
      if (json && json.success && Array.isArray(json.data)) {
        applyServerLedger(json.data);
      }
    }
  } catch (err) {
    console.warn('서버 최신 대장 불러오기 알림:', err);
  }
}

// Offline sync checks
function checkAndNotifyOfflineSync() {
  // file:// 화면의 localStorage는 서버 화면(http://127.0.0.1)에서 읽을 수 없다.
  // 그래서 오프라인 화면에서는 '내보내기', 서버 화면에서는 '가져오기/전송'을 안내한다.
  const isHttp = window.location.protocol.startsWith('http');
  try {
    const localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
    const banner = document.getElementById('offline-sync-banner');
    const textSpan = document.getElementById('offline-sync-text');
    const sendBtn = document.getElementById('offline-sync-send');
    const importBar = document.getElementById('local-import-bar');
    if (importBar) importBar.style.display = isHttp ? 'flex' : 'none';
    if (sendBtn) sendBtn.style.display = isHttp ? '' : 'none';
    if (banner) {
      const count = (localLedger && localLedger.length) || 0;
      if (count > 0) {
        if (textSpan) {
          textSpan.innerHTML = isHttp
            ? `이 브라우저에만 임시로 저장된 요구자료가 <strong id="offline-sync-count">${count}</strong>건 있습니다. [관리대장에 정식 등록]을 눌러 주세요.`
            : `이 브라우저에만 임시로 저장된 요구자료가 <strong id="offline-sync-count">${count}</strong>건 있습니다. <u>관리대장과 대장 엑셀에는 아직 없습니다.</u> [파일로 저장]한 뒤 02번 화면에서 그 파일을 고르면 정식 등록됩니다.`;
        }
        banner.style.display = 'flex';
      } else {
        banner.style.display = 'none';
      }
    }
  } catch (e) {}
}

function syncOfflineLedgerToServer() {
  return runExclusive('offline-sync', document.getElementById('offline-sync-send'), syncOfflineLedgerToServerNow);
}

async function syncOfflineLedgerToServerNow() {
  let localLedger = [];
  try {
    localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
  } catch (e) {
    return;
  }
  if (!localLedger.length) return;

  const successfulIds = new Set();
  for (const item of localLedger) {
    try {
      const { ledger_id, created_at, updated_at, ...serverPayload } = item;
      serverPayload.idempotency_key = ledger_id;
      const res = await fetch('/api/ledger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Always let SQLite allocate the authoritative ID.  This makes offline
        // entries collision-free even if another user registered in the same year.
        body: JSON.stringify(serverPayload)
      });
      if (res.ok) {
        const d = await res.json();
        if (d.success) {
          successfulIds.add(item.ledger_id);
          removeLocalLedgerIds(new Set([item.ledger_id]));
        }
      }
    } catch (e) {
      console.error('오프라인 동기화 실패:', item.ledger_id, e);
    }
  }

  // Only remove successfully synced items, preserving failed items to avoid permanent data loss!
  // Only remove successfully synced items; failed items stay for the next attempt.
  const stored = removeLocalLedgerIds(successfulIds);
  const remaining = stored || localLedger.filter(it => !successfulIds.has(it.ledger_id));

  const banner = document.getElementById('offline-sync-banner');
  const countSpan = document.getElementById('offline-sync-count');
  if (banner && countSpan) {
    if (remaining.length > 0) {
      countSpan.innerText = remaining.length;
      banner.style.display = 'flex';
    } else {
      banner.style.display = 'none';
    }
  }

  if (remaining.length > 0) {
    notify(`⚠️ 임시 등록 ${successfulIds.size}건을 정식 등록했지만 ${remaining.length}건은 실패했습니다. 실패한 건은 지우지 않고 남겨 두었으니 다시 눌러 보세요.`);
  } else {
    notify(`✅ 임시 등록 ${successfulIds.size}건을 관리대장에 정식 등록했습니다.`);
  }
  await fetchLatestLedgerFromServer();
  if (typeof buildDynamicFilters === 'function') buildDynamicFilters();
  if (typeof refreshConnStatus === 'function') refreshConnStatus();
  render();
}

function exportLocalLedger() {
  let localLedger = [];
  try {
    localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
  } catch (e) {}
  if (!localLedger.length) {
    notify('파일로 저장할 임시 등록이 없습니다.');
    return;
  }
  downloadBlob(new Blob([JSON.stringify(localLedger, null, 2)], { type: 'application/json' }),
    `임시등록_요구자료_${new Date().toISOString().slice(0, 10)}.json`);
  notify(`📤 임시 등록 ${localLedger.length}건을 파일로 저장했습니다.\n02_웹관리서버_실행.bat으로 연 화면에서 이 파일을 고르면 정식 등록됩니다.`);
}

async function importLocalLedgerFile(input) {
  const file = input && input.files && input.files[0];
  if (!file) return;
  input.value = '';
  let items = null;
  try {
    items = JSON.parse(await file.text());
  } catch (e) {
    notify('파일을 읽을 수 없습니다. 01번 화면의 [파일로 저장]으로 만든 파일인지 확인하세요.');
    return;
  }
  if (!Array.isArray(items) || !items.length) {
    notify('이 파일에는 임시 등록이 없습니다.');
    return;
  }
  try {
    const current = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
    const seen = new Set(current.map(it => it && it.ledger_id));
    items.forEach(it => {
      if (it && it.ledger_id && !seen.has(it.ledger_id)) {
        current.push(it);
        seen.add(it.ledger_id);
      }
    });
    localStorage.setItem('kocsc_local_ledger', JSON.stringify(current));
  } catch (e) {
    notify('브라우저에 저장할 수 없습니다. 시크릿(사생활 보호) 창이면 일반 창에서 다시 해 주세요.');
    return;
  }
  checkAndNotifyOfflineSync();
  await syncOfflineLedgerToServer();
}

const SYNC_BTN_LABEL = '새 문서 반영';
const SYNC_BTN_BUSY_LABEL = '반영하는 중…';

async function triggerServerSync() {
  if (!window.location.protocol.startsWith('http')) {
    notify('서버 없이 연 화면(01번)에서는 쓸 수 없습니다.\n00_새자료_추가_및_DB동기화.bat을 직접 실행해 주세요.');
    return;
  }
  if (!confirm('새자료_투입폴더에 넣은 문서를 읽어 검색 자료에 추가할까요?\n\n문서가 많으면 몇 분 걸립니다. 그동안 화면은 계속 쓸 수 있고, 끝나면 알려 드립니다.')) {
    return;
  }
  const syncBtn = document.getElementById('btn-trigger-sync');
  if (syncBtn) {
    syncBtn.disabled = true;
    syncBtn.innerText = SYNC_BTN_BUSY_LABEL;
  }
  try {
    const res = await fetch('/api/sync', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      pollSyncStatus();
    } else {
      notify(`❌ 새 문서 반영을 시작하지 못했습니다: ${data.error || data.message}`);
      if (syncBtn) {
        syncBtn.disabled = false;
        syncBtn.innerText = SYNC_BTN_LABEL;
      }
    }
  } catch (err) {
    notify(`❌ 서버에 연결하지 못했습니다: ${err.message}`);
    if (syncBtn) {
      syncBtn.disabled = false;
      syncBtn.innerText = SYNC_BTN_LABEL;
    }
  }
}

function pollSyncStatus() {
  const syncBtn = document.getElementById('btn-trigger-sync');
  const timer = setInterval(async () => {
    try {
      const res = await fetch('/api/sync');
      if (res.ok) {
        const status = await res.json();
        if (!status.is_syncing) {
          clearInterval(timer);
          if (syncBtn) {
            syncBtn.disabled = false;
            syncBtn.innerText = SYNC_BTN_LABEL;
          }
          if (status.last_success) {
            notify(`🎉 새 문서 반영이 끝났습니다 (${status.last_elapsed || 0}초 걸림). 새로고침하면 새 자료가 보입니다.`);
            const _stack = document.getElementById('toast-stack');
            if (_stack && _stack.lastChild) {
              const _btn = document.createElement('button');
              _btn.type = 'button';
              _btn.className = 'btn btn-primary btn-sm';
              _btn.textContent = '지금 새로고침';
              _btn.addEventListener('click', () => location.reload());
              _stack.lastChild.appendChild(_btn);
            } else {
              setTimeout(() => location.reload(), 2500);
            }
          } else if (status.last_error) {
            notify(`❌ 새 문서 반영에 실패했습니다: ${status.last_error}`);
          }
        }
      }
    } catch (e) {
      clearInterval(timer);
      if (syncBtn) {
        syncBtn.disabled = false;
        syncBtn.innerText = SYNC_BTN_LABEL;
      }
    }
  }, 2000);
}

// Edit Modal Functions
// 편집 폼 필드 ↔ 입력 요소. 제출은 모달을 연 시점의 값과 달라진 필드만 보낸다.
// 예전에는 13개 필드를 모두 보내, 서버가 전부 "바뀐 필드"로 보고 엑셀 행 전체를 다시 썼다.
// 보류 큐 재생 때 사용자가 엑셀에서 고친 다른 칸이 옛 값으로 되돌아갔다. (감사 7회차 ISSUE-001)
const EDIT_LEDGER_FIELDS = [
  ['seq_no', 'edit-seq-no'], ['party', 'edit-party'], ['requester', 'edit-requester'],
  ['aide', 'edit-aide'], ['department', 'edit-dept'], ['title', 'edit-title'],
  ['details', 'edit-details'], ['request_date', 'edit-req-date'], ['deadline', 'edit-deadline'],
  ['submit_date', 'edit-submit-date'], ['status', 'edit-status'], ['request_type', 'edit-req-type'],
  ['note', 'edit-note']
];
let EDIT_LEDGER_SNAPSHOT = null;
let EDIT_LEDGER_VERSION = "";

// 드롭다운에 없는 값을 대입하면 선택이 사라지고 value가 ''가 된다(HTML 표준 동작).
// `완료`·`업무설명` 같은 운영 값이 비고만 고친 저장에서 빈 값으로 넘어가 엑셀 칸이 지워졌다.
// 목록에 없는 현재 값은 임시 선택지로 붙여 그대로 보존한다. (감사 7회차 ISSUE-002)
function setSelectPreservingValue(select, value) {
  if (!select) return;
  Array.from(select.querySelectorAll('option[data-preserved="1"]')).forEach(o => o.remove());
  const v = String(value == null ? '' : value);
  const exists = Array.from(select.options).some(o => o.value === v);
  if (v && !exists) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v;
    opt.setAttribute('data-preserved', '1');
    select.appendChild(opt);
  }
  select.value = v;
}

function readEditLedgerForm() {
  const out = {};
  EDIT_LEDGER_FIELDS.forEach(([field, id]) => {
    const el = document.getElementById(id);
    out[field] = el ? String(el.value || '').trim() : '';
  });
  return out;
}

// 모달을 연 시점(before)과 지금(after)을 비교해 달라진 필드만 돌려준다.
function ledgerDirtyFields(before, after) {
  const changes = {};
  Object.keys(after || {}).forEach(k => {
    const a = String(after[k] == null ? '' : after[k]).trim();
    const b = String((before || {})[k] == null ? '' : before[k]).trim();
    if (a !== b) changes[k] = a;
  });
  return changes;
}

function openEditLedgerModal() {
  if (!currentSelectedLedgerItem) return;
  const it = currentSelectedLedgerItem;
  const blocked = ledgerWriteBlockReason(it);
  if (blocked) {
    notify(`⚠️ ${blocked}`);
    return;
  }
  document.getElementById('edit-id').value = it.ledger_id || '';
  document.getElementById('edit-display-id').value = it.ledger_id || '';
  document.getElementById('edit-seq-no').value = it.seq_no || '';
  document.getElementById('edit-party').value = it.party || '';
  document.getElementById('edit-requester').value = it.requester || '';
  document.getElementById('edit-aide').value = it.aide || '';
  document.getElementById('edit-dept').value = it.department || appConfig.department_name || '__DEFAULT_DEPT__';
  document.getElementById('edit-title').value = it.title || '';
  document.getElementById('edit-details').value = it.details || '';
  document.getElementById('edit-req-date').value = it.request_date || '';
  document.getElementById('edit-deadline').value = it.deadline || '';
  document.getElementById('edit-submit-date').value = it.submit_date || '';
  setSelectPreservingValue(document.getElementById('edit-status'), it.status || '작성중');
  setSelectPreservingValue(document.getElementById('edit-req-type'), it.request_type || '시스템');
  document.getElementById('edit-note').value = it.note || '';
  EDIT_LEDGER_SNAPSHOT = readEditLedgerForm();
  EDIT_LEDGER_VERSION = String(it.updated_at || "");

  document.getElementById('edit-ledger-backdrop').classList.add('open');
}

function closeEditLedgerModal() {
  document.getElementById('edit-ledger-backdrop').classList.remove('open');
}

function submitEditLedgerItem() {
  return runExclusive('ledger-edit', submitButtonOf('form-edit-ledger'), submitEditLedgerItemNow);
}

async function submitEditLedgerItemNow() {
  const ledger_id = document.getElementById('edit-id').value.trim();
  if (!ledger_id) return;

  const form = readEditLedgerForm();

  if (!form.title) {
    notify('요구자료 제목은 꼭 적어야 합니다.');
    return;
  }
  if (!form.requester) {
    notify('의원명(요구자)은 꼭 적어야 합니다.');
    return;
  }

  // 달라진 필드만 보낸다. 서버도 기존 값과 비교하지만(2중 방어), 보내지 않은 칸은
  // 엑셀 반영·보류 큐 보호 대상에서 확실히 빠진다.
  const payload = ledgerDirtyFields(EDIT_LEDGER_SNAPSHOT || {}, form);
  if (!Object.keys(payload).length) {
    notify('바뀐 내용이 없어 저장하지 않았습니다.', 'info');
    closeEditLedgerModal(true);
    return;
  }

  const existing = LEDGER_MAP[ledger_id];
  const blocked = ledgerWriteBlockReason(existing || { ledger_id });
  if (blocked) {
    notify(`⚠️ ${blocked}`);
    return;
  }

  const isHttp = window.location.protocol.startsWith('http');
  let serverUpdatedAt = null;
  let excelMessage = "";
  let applied = payload;
  if (isHttp) {
    // 화면이 읽어 온 시점의 수정 시각. 그사이 다른 사용자나 엑셀 동기화가 고쳤으면 서버가 409로
    // 거절해, 남의 수정을 조용히 덮어쓰지 않는다. (감사 R4 4.1-1)
    const body = Object.assign({}, payload);
    body.expected_updated_at = EDIT_LEDGER_VERSION;
    try {
      const res = await fetch(`/api/ledger/${encodeURIComponent(ledger_id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const d = await res.json().catch(() => ({}));
      if (res.status === 409 && d.conflict) {
        notify(`⚠️ ${d.error}\n적어 둔 내용은 수정 창에 그대로 있습니다. 필요한 부분을 복사해 둔 뒤 창을 닫고, 이 항목을 다시 열어 최신 내용과 비교해 주세요.`);
        await fetchLatestLedgerFromServer();
        render();
        return;
      }
      if (!res.ok || !d.success) {
        notify(`❌ 수정 실패: ${d.error || res.statusText || '서버 오류'}`);
        return;
      }
      serverUpdatedAt = d.updated_at || null;
      excelMessage = d.excel_message || "";
      if (d.changes && typeof d.changes === 'object') applied = d.changes;
    } catch (e) {
      notify(`❌ 서버에 연결하지 못했습니다: ${e.message}`);
      return;
    }
  }

  // Update in-memory item
  const updatedAt = serverUpdatedAt || new Date().toISOString().replace('T', ' ').slice(0, 19);
  if (existing) {
    Object.assign(existing, applied, { updated_at: updatedAt });
    currentSelectedLedgerItem = existing;
  }

  // Also update in localStorage if present
  try {
    const localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
    const idx = localLedger.findIndex(l => l.ledger_id === ledger_id);
    if (idx !== -1) {
      Object.assign(localLedger[idx], applied, { updated_at: updatedAt });
      localStorage.setItem('kocsc_local_ledger', JSON.stringify(localLedger));
    }
  } catch (e) {}

  // 저장을 마쳤으므로 "저장하지 않고 닫을까요?"를 묻지 않고 닫는다(true).
  closeEditLedgerModal(true);
  if (currentSelectedLedgerItem) {
    openLedgerModal(currentSelectedLedgerItem);
  }
  if (typeof buildDynamicFilters === 'function') buildDynamicFilters();
  render();
  notify(isHttp
    ? `✅ [${ledger_id}] 수정한 내용을 저장했습니다. ${excelMessage}`
    : `⚠️ 이 브라우저의 임시 등록을 고쳤습니다 (관리대장·엑셀에는 아직 없음).`);
}

// 상세창의 [제출 완료로 표시]: 진행 상태를 '제출'로, 비어 있는 실제 제출일을 오늘로 바꾼다.
// 가장 자주 하는 수정이라 수정 창을 거치지 않게 했다. 서버 경로·충돌 검사는 일반 수정과 같다.
function markLedgerSubmitted() {
  return runExclusive('ledger-submit', document.getElementById('btn-mark-submitted'), markLedgerSubmittedNow);
}

async function markLedgerSubmittedNow() {
  const it = currentSelectedLedgerItem;
  if (!it || !it.ledger_id) return;
  const blocked = ledgerWriteBlockReason(it);
  if (blocked) {
    notify(`⚠️ ${blocked}`);
    return;
  }
  const payload = { status: '제출' };
  if (!String(it.submit_date || '').trim()) payload.submit_date = localDateString();

  const isHttp = window.location.protocol.startsWith('http');
  let applied = payload;
  let updatedAt = new Date().toISOString().replace('T', ' ').slice(0, 19);
  let excelMessage = '';
  if (isHttp) {
    const body = Object.assign({}, payload);
    if (it.updated_at) body.expected_updated_at = it.updated_at;
    try {
      const res = await fetch(`/api/ledger/${encodeURIComponent(it.ledger_id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const d = await res.json().catch(() => ({}));
      if (res.status === 409 && d.conflict) {
        notify(`⚠️ ${d.error}\n그사이 다른 곳에서 이 항목이 바뀌었습니다. 최신 내용을 다시 불러왔으니 확인한 뒤 한 번 더 눌러 주세요.`);
        await fetchLatestLedgerFromServer();
        render();
        const latest = LEDGER_MAP[it.ledger_id];
        if (latest) openLedgerModal(latest);
        return;
      }
      if (!res.ok || !d.success) {
        notify(`❌ 제출 표시 실패: ${d.error || res.statusText || '서버 오류'}`);
        return;
      }
      if (d.updated_at) updatedAt = d.updated_at;
      excelMessage = d.excel_message || '';
      if (d.changes && typeof d.changes === 'object') applied = d.changes;
    } catch (e) {
      notify(`❌ 서버에 연결하지 못했습니다: ${e.message}`);
      return;
    }
  }

  Object.assign(it, applied, { updated_at: updatedAt });
  try {
    const localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
    const idx = localLedger.findIndex(l => l.ledger_id === it.ledger_id);
    if (idx !== -1) {
      Object.assign(localLedger[idx], applied, { updated_at: updatedAt });
      localStorage.setItem('kocsc_local_ledger', JSON.stringify(localLedger));
    }
  } catch (e) {}

  openLedgerModal(it);
  if (typeof buildDynamicFilters === 'function') buildDynamicFilters();
  render();
  notify(`✅ [${it.ledger_id}] 제출 완료로 표시했습니다${it.submit_date ? ` (제출일 ${it.submit_date})` : ''}. ${excelMessage}`.trim());
}

function deleteCurrentLedgerItem() {
  return runExclusive('ledger-delete', document.getElementById('btn-delete-ledger'), deleteCurrentLedgerItemNow);
}

async function deleteCurrentLedgerItemNow() {
  if (!currentSelectedLedgerItem) return;
  const it = currentSelectedLedgerItem;
  const ledger_id = it.ledger_id;
  const blocked = ledgerWriteBlockReason(it);
  if (blocked) {
    notify(`⚠️ ${blocked}`);
    return;
  }
  const isHttp = window.location.protocol.startsWith('http');

  if (!confirm(`${it.requester} - ${it.title}\n\n이 요구자료를 삭제할까요?\n`
    + (isHttp ? '관리대장과 대장 엑셀 파일에서 함께 지워집니다.' : '이 브라우저의 임시 등록에서 지워집니다.'))) {
    return;
  }

  let excelMessage = "";
  if (isHttp) {
    try {
      const res = await fetch(`/api/ledger/${encodeURIComponent(ledger_id)}`, {
        method: 'DELETE'
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || !d.success) {
        notify(`❌ 삭제 실패: ${d.error || res.statusText || '서버 오류'}`);
        return;
      }
      excelMessage = d.excel_message || "";
    } catch (e) {
      notify(`❌ 서버에 연결하지 못했습니다: ${e.message}`);
      return;
    }
  }

  // Remove from REQUEST_LEDGER and LEDGER_MAP
  const idx = REQUEST_LEDGER.findIndex(l => l.ledger_id === ledger_id);
  if (idx !== -1) {
    REQUEST_LEDGER.splice(idx, 1);
  }
  delete LEDGER_MAP[ledger_id];

  // Remove from localStorage if present
  try {
    const localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
    const filteredLocal = localLedger.filter(l => l.ledger_id !== ledger_id);
    localStorage.setItem('kocsc_local_ledger', JSON.stringify(filteredLocal));
  } catch (e) {}

  closeLedgerModal();
  currentSelectedLedgerItem = null;
  if (document.getElementById('count-mode-ledger')) {
    document.getElementById('count-mode-ledger').innerText = REQUEST_LEDGER.length;
  }
  if (typeof buildDynamicFilters === 'function') buildDynamicFilters();
  if (typeof checkAndNotifyOfflineSync === 'function') checkAndNotifyOfflineSync();
  render();
  notify(isHttp
    ? `🗑️ [${ledger_id}] 요구자료를 삭제했습니다. ${excelMessage}`
    : `🗑️ 이 브라우저의 임시 등록을 지웠습니다.`);
}


