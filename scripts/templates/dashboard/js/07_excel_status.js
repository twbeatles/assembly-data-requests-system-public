// 서버 상태(/api/sync)를 배너 문구로 바꾼다. 보여 줄 것이 없으면 null.
// 대기·격리 건수뿐 아니라, 예전에는 아무 흔적 없이 멈추던 신호도 알린다: 엑셀에서 한 건도 읽지
// 못한 동기화(R4-07), 손상된 보류 큐(R4-11), 서버 시작 동기화 오류(R4-15e).
function excelStatusMessage(status) {
  const s = status || {};
  const pending = Number(s.excel_pending || 0);
  const failed = Number(s.excel_failed || 0);
  const lastSync = s.excel_last_sync || null;
  const syncProblem = lastSync && lastSync.success === false && lastSync.reason === 'parsed_zero';
  const corrupt = s.excel_queue_corrupt || null;
  const startupError = s.excel_startup_error || '';
  const candidates = Array.isArray(s.excel_master_candidates) ? s.excel_master_candidates : [];
  const ambiguous = candidates.length > 1;
  if (!pending && !failed && !syncProblem && !corrupt && !startupError && !ambiguous) return null;
  const parts = [];
  if (ambiguous) {
    parts.push(`대장 엑셀로 보이는 파일이 ${candidates.length}개 있어 가장 최근에 고친 파일(${candidates[0]})을 쓰고 있습니다. `
      + '다른 파일을 써야 하면 config.json의 master_excel에 파일 이름을 적어 주세요');
  }
  if (corrupt) parts.push(`엑셀에 적으려고 모아 둔 변경 목록 파일이 깨져 있어 비운 채로 시작했습니다 (원본 보관: ${corrupt.backup || '없음'})`);
  if (startupError) parts.push(startupError);
  if (syncProblem) parts.push(`대장 엑셀 내용을 화면에 가져오지 못했습니다: ${lastSync.message || '대장 항목을 읽지 못했습니다'}`);
  if (pending) parts.push(`대장 엑셀에 아직 적지 못한 변경 ${pending}건 (엑셀 파일이 열려 있으면 닫을 때 자동으로 적습니다)`);
  if (failed) parts.push(`대장 엑셀에 반영하지 못한 변경 ${failed}건`);
  const isError = failed || syncProblem || corrupt || startupError;
  return {
    level: isError ? 'error' : 'warn',
    text: `${isError ? '⚠️' : '⏳'} ${parts.join(' · ')}`,
    canManageQuarantine: failed > 0,
  };
}

async function refreshServerExcelStatus() {
  const banner = document.getElementById('server-excel-banner');
  if (!banner || !window.location.protocol.startsWith('http')) return;
  try {
    const res = await fetch('/api/sync');
    if (!res.ok) return;
    const msg = excelStatusMessage(await res.json());
    if (!msg) {
      banner.style.display = 'none';
      return;
    }
    banner.className = msg.level;
    banner.textContent = '';
    const text = document.createElement('span');
    text.textContent = msg.text;
    banner.appendChild(text);
    if (msg.canManageQuarantine && typeof openQuarantineModal === 'function') {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-secondary banner-action';
      btn.textContent = '내용 보고 처리하기';
      btn.addEventListener('click', openQuarantineModal);
      banner.appendChild(btn);
    }
    banner.style.display = 'block';
  } catch (e) {}
}

// ---------------------------------------------------------------------------
// 알림(토스트)·중복 실행 방지·클립보드·CSV 안전 처리·화면 설정 기억·키보드 조작
// ---------------------------------------------------------------------------
// notify()는 창을 멈추고 확인을 눌러야 다음 작업을 할 수 있다. 복사·저장 완료 같은 안내는
// 화면을 막지 않는 알림으로 띄운다. 새로고침 직전 안내와 되돌릴 수 없는 작업 확인(confirm)은 그대로 둔다.
function inferNotifyType(message) {
  const s = String(message == null ? '' : message).trim();
  if (s.startsWith('⚠️')) return 'warn';
  if (/^(✅|🎉|🗑️|📤)/.test(s)) return 'success';
  if (s.startsWith('❌') || /오류|실패/.test(s)) return 'error';
  if (/필수|선택해주세요|없습니다|찾을 수 없|확인하세요/.test(s)) return 'warn';
  if (/복사|저장|반영|완료|등록/.test(s)) return 'success';
  return 'info';
}

function notifyDuration(kind, text) {
  const base = kind === 'error' ? 8000 : kind === 'warn' ? 6000 : 3500;
  return base + Math.min(String(text || '').length * 30, 4000);
}

function notify(message, type) {
  const text = String(message == null ? '' : message);
  const kind = type || inferNotifyType(text);
  const stack = document.getElementById('toast-stack');
  if (!stack) {
    console.log(text);
    return null;
  }
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  const body = document.createElement('div');
  body.textContent = text;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', '알림 닫기');
  close.textContent = '×';
  el.appendChild(body);
  el.appendChild(close);
  const remove = () => { if (el.parentNode) el.parentNode.removeChild(el); };
  close.addEventListener('click', remove);
  stack.appendChild(el);
  while (stack.children.length > 4) stack.removeChild(stack.firstChild);
  let timer = setTimeout(remove, notifyDuration(kind, text));
  el.addEventListener('mouseenter', () => clearTimeout(timer));
  el.addEventListener('mouseleave', () => { clearTimeout(timer); timer = setTimeout(remove, 2500); });
  return el;
}

// 등록 버튼을 두 번 누르거나 Enter를 연달아 치면 같은 요구자료가 대장에 두 줄 들어갔다.
// 같은 작업이 끝나기 전에는 다시 실행하지 않고, 그동안 버튼을 잠근다.
const BUSY_ACTIONS = new Set();

async function runExclusive(key, button, task) {
  if (BUSY_ACTIONS.has(key)) return undefined;
  BUSY_ACTIONS.add(key);
  const wasDisabled = button ? button.disabled : false;
  if (button) {
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  }
  try {
    return await task();
  } finally {
    BUSY_ACTIONS.delete(key);
    if (button) {
      button.disabled = wasDisabled;
      button.removeAttribute('aria-busy');
    }
  }
}

function submitButtonOf(formId) {
  const form = document.getElementById(formId);
  return form ? form.querySelector('button[type="submit"]') : null;
}

// writeText 결과를 기다리지 않고 "복사되었습니다"를 띄우면, 권한이 막힌 환경에서도 성공으로 보였다.
async function copyText(text, successMessage) {
  const value = String(text == null ? '' : text);
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (!ok) throw new Error('copy command rejected');
    }
    if (successMessage) notify(successMessage, 'success');
    return true;
  } catch (err) {
    notify('클립보드에 복사하지 못했습니다. 브라우저 권한을 확인하거나 내용을 직접 선택해 복사해 주세요.', 'error');
    return false;
  }
}

// CSV 셀. 엑셀은 =, +, -, @로 시작하는 셀을 수식으로 해석한다. 웹에서 누구나 입력할 수 있는
// 대장 제목·비고가 `=HYPERLINK(...)` 같은 수식으로 실행되지 않도록 앞에 '를 붙인다.
function csvCell(value, maxLen) {
  let s = value === null || value === undefined ? '' : String(value);
  if (maxLen && s.length > maxLen) s = s.slice(0, maxLen);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replace(/"/g, '""') + '"';
}

function csvRow(cells) {
  return cells.join(',') + '\n';
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // 바로 해제하면 일부 브라우저에서 내려받기가 취소된다.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 오프라인 등록 전송은 건별로 확정한다. 전송 도중 창을 닫아도 이미 등록된 건이 다음에 또
// 전송되지 않는다. 다른 탭이 그 사이 추가한 임시 등록도 덮어쓰지 않도록 매번 다시 읽는다.
// 서버(또는 내장) 대장 목록 앞에 이 브라우저의 임시 등록분을 붙인다. 부팅·수동 새로고침·실시간
// 자동 갱신이 모두 이 함수를 쓴다. 예전에는 자동 갱신이 목록을 서버 응답으로 통째로 바꿔, 아직
// 전송하지 않은 임시 등록이 화면에서 사라졌다(배너 건수만 남음). (감사 R4-15a)
function mergeLocalLedger(list) {
  const merged = Array.isArray(list) ? list.slice() : [];
  try {
    const localLedger = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
    if (Array.isArray(localLedger) && localLedger.length) {
      const existingIds = new Set(merged.map(l => l && l.ledger_id));
      for (let i = localLedger.length - 1; i >= 0; i--) {
        const item = localLedger[i];
        if (item && !existingIds.has(item.ledger_id)) merged.unshift(item);
      }
    }
  } catch (e) {}
  return merged;
}

function removeLocalLedgerIds(ids) {
  try {
    const current = JSON.parse(localStorage.getItem('kocsc_local_ledger') || '[]');
    const next = (Array.isArray(current) ? current : []).filter(it => !(it && ids.has(it.ledger_id)));
    if (next.length) {
      localStorage.setItem('kocsc_local_ledger', JSON.stringify(next));
    } else {
      localStorage.removeItem('kocsc_local_ledger');
    }
    return next;
  } catch (e) {
    return null;
  }
}

