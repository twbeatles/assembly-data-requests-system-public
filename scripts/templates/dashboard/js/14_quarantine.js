// ---------------------------------------------------------------------------
// 엑셀 반영 격리 항목 관리 (02번 웹관리서버 전용)
// 예전에는 격리 건수만 보이고, 내용 확인·재시도·삭제는 .excel_pending_failed.json을 직접 편집해야 했다.
// ---------------------------------------------------------------------------
const QUARANTINE_REASON_LABELS = {
  retry_limit: '여러 번 시도했지만 엑셀에 적지 못했습니다 (화면에서 고친 값은 그대로 지키고 있습니다)',
  conflict: '엑셀의 같은 대장ID 줄에 다른 요구자료가 적혀 있습니다',
};
const QUARANTINE_ACTION_LABELS = { insert: '등록', update: '수정', delete: '삭제' };

function quarantineRowHtml(op) {
  const reason = QUARANTINE_REASON_LABELS[op.quarantine_reason] || op.quarantine_reason || '-';
  const action = QUARANTINE_ACTION_LABELS[op.action] || op.action || '-';
  const fields = Array.isArray(op.changed_fields) && op.changed_fields.length ? op.changed_fields.join(', ') : '';
  const id = escapeHtml(op.operation_id || '');
  return `<div class="quarantine-row" data-op="${id}">
  <div class="quarantine-main">
    <div><span class="badge-id">${escapeHtml(op.ledger_id || '?')}</span> <strong>${escapeHtml(op.requester || '')}</strong> ${escapeHtml(op.title || '(제목 없음)')}</div>
    <div class="quarantine-meta">화면에서 한 일: ${escapeHtml(action)}${fields ? ` (바뀐 칸: ${escapeHtml(fields)})` : ''} · ${escapeHtml(op.quarantined_at || '')}</div>
    <div class="quarantine-meta">${escapeHtml(reason)}</div>
    ${op.quarantine_detail ? `<div class="quarantine-detail">${escapeHtml(op.quarantine_detail)}</div>` : ''}
  </div>
  <div class="quarantine-actions">
    <button type="button" class="btn btn-secondary btn-sm" data-quarantine-retry="${id}">다시 시도</button>
    <button type="button" class="btn btn-secondary btn-sm quarantine-dismiss" data-quarantine-dismiss="${id}">목록에서 지우기</button>
  </div>
</div>`;
}

async function loadQuarantineList() {
  const box = document.getElementById('quarantine-list');
  if (!box) return;
  box.textContent = '불러오는 중…';
  try {
    const res = await fetch('/api/sync/quarantine');
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) {
      box.textContent = `목록을 불러오지 못했습니다: ${data.error || res.statusText}`;
      return;
    }
    const items = Array.isArray(data.data) ? data.data : [];
    box.innerHTML = items.length
      ? items.map(quarantineRowHtml).join('')
      : '<div class="quarantine-empty">엑셀에 반영하지 못한 변경이 없습니다. 모두 정상입니다.</div>';
  } catch (e) {
    box.textContent = `서버 연결 오류: ${e.message}`;
  }
}

function openQuarantineModal() {
  if (!window.location.protocol.startsWith('http')) {
    notify('이 기능은 02_웹관리서버_실행.bat으로 연 화면에서만 쓸 수 있습니다.');
    return;
  }
  const bd = document.getElementById('quarantine-modal-backdrop');
  if (!bd) return;
  bd.classList.add('open');
  loadQuarantineList();
}

function closeQuarantineModal() {
  const bd = document.getElementById('quarantine-modal-backdrop');
  if (bd) bd.classList.remove('open');
}

async function quarantineAction(opId, kind, button) {
  const isDismiss = kind === 'dismiss';
  if (isDismiss && !confirm(
    '이 변경을 목록에서 지울까요?\n\n'
    + '지우면 화면에서 고친 값을 더 이상 지켜 주지 않습니다. 대장 엑셀을 화면과 같은 값으로 먼저 고쳐 두지 않으면, '
    + '다음에 엑셀을 읽어 올 때 화면 값이 엑셀의 옛 값으로 돌아갑니다.\n\n지운 뒤에는 되돌릴 수 없습니다.')) {
    return;
  }
  await runExclusive(`quarantine-${opId}`, button, async () => {
    try {
      const res = await fetch(`/api/sync/quarantine/${encodeURIComponent(opId)}${isDismiss ? '' : '/retry'}`, {
        method: isDismiss ? 'DELETE' : 'POST',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        notify(`❌ ${isDismiss ? '지우기' : '다시 시도'} 실패: ${data.error || res.statusText}`);
        return;
      }
      notify(isDismiss ? '🗑️ 목록에서 지웠습니다.' : `✅ ${data.message || '엑셀에 다시 적도록 대기 목록에 넣었습니다.'}`);
    } catch (e) {
      notify(`❌ 서버 통신 오류가 발생했습니다: ${e.message}`);
      return;
    }
    await loadQuarantineList();
    refreshServerExcelStatus();
  });
}

document.addEventListener('click', (e) => {
  const target = e.target && e.target.closest ? e.target.closest('[data-quarantine-retry],[data-quarantine-dismiss]') : null;
  if (!target) return;
  const retryId = target.getAttribute('data-quarantine-retry');
  if (retryId) quarantineAction(retryId, 'retry', target);
  const dismissId = target.getAttribute('data-quarantine-dismiss');
  if (dismissId) quarantineAction(dismissId, 'dismiss', target);
});
