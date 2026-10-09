function docDisplayBody(doc) {
  // 전문 보존: 모달·인쇄·비교는 full_markdown 전체를 쓴다. 요약/엑셀 잘림본으로 대체하지 않는다.
  return (doc && (doc.full_markdown || doc.answer_full_text)) || '';
}

function applyDocBodyPayload(doc, data) {
  if (!doc || !data) return;
  if (data.full_markdown) doc.full_markdown = data.full_markdown;
  else if (data.answer_full_text && !doc.full_markdown) doc.full_markdown = data.answer_full_text;
  if (data.answer_full_text) doc.answer_full_text = data.answer_full_text;
  if (Array.isArray(data.qa_items)) {
    DOC_QAS_MAP[doc.doc_id] = data.qa_items;
  }
  if (doc.full_markdown) {
    doc.body_deferred = false;
    doc.body_incomplete = false;
    if (!doc.answer_full_text) doc.answer_full_text = doc.full_markdown;
  }
}

async function fetchMarkdownFile(doc) {
  const rel = String(doc.parsed_md_path || '').replace(/\\/g, '/').replace(/^\/+/, '');
  if (!rel) return '';
  const urls = ['/api/file?path=' + encodeURIComponent(rel), '/' + rel];
  for (const url of urls) {
    try {
      const res = await fetch(url);
      if (res.ok) {
        const md = await res.text();
        if (md && md.length > 0) return md;
      }
    } catch (e) {}
  }
  return '';
}

async function ensureDocBody(doc) {
  if (!doc) return doc;
  // 대용량 모드에서 이 문서의 본문 블록이 아직 풀리지 않았으면 끝날 때까지 기다린다.
  // 서버 조회나 요약으로 대신하지 않는다 — 전문은 이 HTML 안에 있다.
  if (doc.body_pending) await BODIES_READY;
  if (doc.full_markdown && !doc.body_deferred) return doc;
  if (window.location.protocol.startsWith('http')) {
    try {
      const res = await fetch('/api/documents/' + encodeURIComponent(doc.doc_id));
      if (res.ok) {
        const data = await res.json();
        if (data && data.success && data.data) {
          Object.assign(doc, data.data);
          applyDocBodyPayload(doc, data.data);
        }
      }
    } catch (e) {}
    if (!doc.full_markdown) {
      const md = await fetchMarkdownFile(doc);
      if (md) {
        doc.full_markdown = md;
        doc.answer_full_text = md;
        doc.body_deferred = false;
        doc.body_incomplete = false;
      }
    }
  }
  if (!doc.full_markdown) {
    doc.full_markdown = doc.answer_summary || '';
    doc.body_incomplete = true;
  }
  return doc;
}

// Modal open
async function openDocModal(doc, targetQNum = null) {
  doc = await ensureDocBody(doc);
  currentSelectedDoc = doc;
  const { allHighlightTokens } = parseSearchTokens(activeFilters.query);
  const highlightTokens = allHighlightTokens || [];

  // 주소창에 바로가기(#doc=…)를 남긴다. location.hash에 대입하면 hashchange가 applyDeepLink를 다시 불러
  // 같은 문서를 한 번 더 열었고, 질문 칩으로 연 '질문별로 보기' 탭이 곧바로 '전문 보기'로 되돌아갔다.
  try {
    history.replaceState(null, '', `#doc=${encodeURIComponent(doc.doc_id)}`);
  } catch (e) { /* 주소를 못 바꿔도 문서는 연다 */ }

  document.getElementById('modal-id').innerText = doc.doc_id;
  document.getElementById('modal-title').innerHTML = highlightTextString(doc.title, highlightTokens);
  document.getElementById('modal-date').innerText = doc.request_date || doc.year;
  document.getElementById('modal-requester').innerText = `${doc.institution} / ${doc.requester}`;
  document.getElementById('modal-dept').innerText = doc.contact_person || doc.department || '';
  document.getElementById('modal-star').classList.toggle('active', favorites.includes(doc.doc_id));

  // Show or hide bilateral link to request ledger
  const btnGotoLedger = document.getElementById('btn-goto-ledger');
  if (btnGotoLedger) {
    if (doc.linked_ledger_id && LEDGER_MAP[doc.linked_ledger_id]) {
      btnGotoLedger.style.display = '';
      btnGotoLedger.title = `이 답변서와 연결된 요구자료 [${doc.linked_ledger_id}] 보기`;
    } else {
      btnGotoLedger.style.display = 'none';
    }
  }

  const displayBody = docDisplayBody(doc);
  let incompleteBanner = '';
  if (doc.body_incomplete || (doc.body_deferred && !displayBody)) {
    const mdHref = escapeHtml(doc.parsed_md_path || '#');
    incompleteBanner = `<div class="incomplete-banner">문서가 커서 이 화면에 전문을 다 싣지 못했습니다. <a href="${mdHref}" target="_blank">변환된 전문 파일</a>을 열거나, 02_웹관리서버_실행.bat으로 연 화면에서 다시 열어 주세요.</div>`;


  }

  // 1. Markdown & Tables Rendering with marked.js
  const formattedContainer = document.getElementById('modal-view-formatted');
  try {
    let parsedHtml = safeMarkedParse(displayBody);
    formattedContainer.innerHTML = incompleteBanner + parsedHtml;

    enhanceTablesInElement(formattedContainer);

    if (highlightTokens.length) {
      applyDomHighlight(formattedContainer, highlightTokens);
    }
  } catch (err) {
    formattedContainer.innerHTML = incompleteBanner + '<pre>' + escapeHtml(displayBody) + '</pre>';
  }

  // 2. Raw Text View — 엑셀용 30,000자 잘림(answer_full_text)보다 마크다운 전문을 우선한다
  document.getElementById('modal-text-content').innerText = doc.full_markdown || doc.answer_full_text || '';

  // 3. QA Items View (Render full markdown tables)
  const docQas = DOC_QAS_MAP[doc.doc_id] || [];
  const qaContainer = document.getElementById('modal-view-qa');
  qaContainer.innerHTML = '';
  if (docQas.length) {
    docQas.forEach((q, qIdx) => {
      const qBox = document.createElement('div');
      qBox.id = `qa-box-${q.q_num}`;
      qBox.className = 'qa-detail-box';
      
      const rawContent = q.answer_markdown || q.answer_full || '';
      let qHtml = '';
      try {
        qHtml = safeMarkedParse(rawContent);
      } catch (e) {
        qHtml = '<pre>' + escapeHtml(rawContent) + '</pre>';
      }

      const hasTbl = q.has_tables === 'Y' || rawContent.includes('<table') || rawContent.includes('| --- |');

      qBox.innerHTML = `
        <div class="qa-head">
          <div>
            <span class="badge-id">질문 ${Number(q.q_num) || qIdx + 1}</span>
            <h4>${highlightTextString(q.question_title, highlightTokens)}</h4>
          </div>
          <div class="qa-head-actions">
            ${hasTbl ? `<button class="btn btn-secondary btn-sm" onclick="copyQaTable(${qIdx})" title="이 답변에 들어 있는 표를 복사합니다 (엑셀·한글에 붙여도 표 모양 유지)">표 복사</button>` : ''}
            <button class="btn btn-secondary btn-sm" onclick="copySingleQa(${qIdx})" title="이 질문과 답변을 복사합니다">질문·답변 복사</button>
          </div>
        </div>
        <div class="modal-rendered-view">${qHtml}</div>
      `;

      enhanceTablesInElement(qBox);

      if (highlightTokens.length) {
        applyDomHighlight(qBox, highlightTokens);
      }
      qaContainer.appendChild(qBox);
    });
  } else {
    qaContainer.innerHTML = '<p class="muted">이 답변서는 질문별로 나뉘어 있지 않습니다. [전문 보기]에서 전체 내용을 확인하세요.</p>';
  }

  if (targetQNum) {
    switchModalTab('qa');
    setTimeout(() => {
      const targetBox = document.getElementById(`qa-box-${targetQNum}`);
      if (targetBox) {
        targetBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
        targetBox.classList.add('target-highlight');
        setTimeout(() => {
          targetBox.classList.remove('target-highlight');
        }, 2200);
      }
    }, 150);
  } else {
    switchModalTab('formatted');
  }

  const isWebMode = window.location.protocol.startsWith('http');

  const mdBtn = document.getElementById('modal-link-md');
  const origBtn = document.getElementById('modal-link-orig');

  if (mdBtn) {
    mdBtn.href = getDocMdUrl(doc.parsed_md_path);
    mdBtn.title = '변환된 전문을 새 탭에서 엽니다';
  }

  if (origBtn) {
    origBtn.href = getDocOrigUrl(doc.original_path);
    if (isWebMode) {
      origBtn.textContent = '원본 파일 위치 열기';
      origBtn.title = '원본 파일이 있는 폴더를 엽니다 (마우스 오른쪽 버튼: 파일 내려받기)';
      origBtn.onclick = (e) => {
        e.preventDefault();
        openFileLocation(doc.original_path);
      };
    } else {
      origBtn.textContent = '원본 파일 열기';
      origBtn.title = '원본 파일을 엽니다';
      origBtn.onclick = null;
    }
  }

  // Setup rich copy buttons
  setupModalCopyButtons(doc);


  document.getElementById('modal-backdrop').classList.add('open');
  // 다른 문서를 보다가 열어도 맨 위부터 보이게 한다(질문 위치로 가는 경우는 아래 scrollIntoView가 맡는다).
  if (!targetQNum) {
    const bodyEl = document.querySelector('#main-modal-dialog .modal-body');
    if (bodyEl) bodyEl.scrollTop = 0;
  }
}

function setupModalCopyButtons(doc) {
  // 1. Full text
  document.getElementById('btn-copy-full').onclick = () => {
    copyText(`[${doc.requester}] ${doc.title}\n\n${doc.full_markdown || doc.answer_full_text || ''}`,
      '답변서 전문을 복사했습니다.');
  };

  // 2. Official Korean Doc Format (□ ○ -)
  document.getElementById('btn-copy-gov').onclick = () => {
    const govText = `□ [${doc.requester}] ${doc.title}\n  ○ 질의내용:\n${(doc.question_list || '').split('\n').map(l => '    - ' + l).join('\n')}\n\n  ○ 답변내용:\n${(doc.answer_summary || doc.full_markdown || doc.answer_full_text || '').slice(0, 800)}\n\n  * 담당: ${doc.contact_person || doc.department || appConfig.department_name || '__DEFAULT_DEPT__'}`;
    copyText(govText, '보고용 개조식(□ ○ -)으로 복사했습니다.');
  };

  // 3. Rich HTML Table Copy (preserves table grid in Excel & HWP!)
  document.getElementById('btn-copy-html-table').onclick = () => {
    const formattedContainer = document.getElementById('modal-view-formatted');
    const tables = formattedContainer.querySelectorAll('table');
    if (!tables.length) {
      notify('이 답변서에는 표가 없습니다.');
      return;
    }
    const htmlToCopy = Array.from(tables).map(t => t.outerHTML).join('<br><br>');
    const plainToCopy = Array.from(tables).map(t => t.innerText).join('\n\n');

    if (navigator.clipboard && window.ClipboardItem) {
      const blobHtml = new Blob([htmlToCopy], { type: 'text/html' });
      const blobText = new Blob([plainToCopy], { type: 'text/plain' });
      const data = [new ClipboardItem({ 'text/html': blobHtml, 'text/plain': blobText })];
      navigator.clipboard.write(data).then(() => {
        notify('표를 복사했습니다. 엑셀이나 한글에 붙여 넣으면 표 모양이 그대로 유지됩니다.', 'success');
      }).catch(() => copyText(plainToCopy, '표를 글자로 복사했습니다.'));
    } else {
      copyText(plainToCopy, '표를 글자로 복사했습니다.');
    }
  };

  // 4. Messenger / Email Summary
  document.getElementById('btn-copy-msg').onclick = () => {
    const msgText = `[자료요구 답변 요약]\n- 건명: ${doc.title}\n- 요구자: ${doc.requester}${doc.doc_number ? ` (${doc.doc_number})` : ''}\n- 요약: ${(doc.answer_summary || '').slice(0, 200)}${docShareLink(doc) ? `\n- 바로가기: ${docShareLink(doc)}` : ''}`;
    copyText(msgText, '메신저·메일용 요약을 복사했습니다.');
  };

  // 5. Deep link copy
  document.getElementById('btn-copy-link').onclick = () => {
    const link = docShareLink(doc);
    copyText(link, `이 답변서를 바로 여는 주소를 복사했습니다.\n${link}`);
  };
}

// 파일로 연 화면(file://)의 origin은 "null"이라, origin을 이어 붙이면 열리지 않는 주소("null/C:/…")가 됐다.
// 지금 주소에서 # 뒤만 바꾸면 서버 화면과 파일 화면 모두 그대로 열리는 주소가 된다.
function docShareLink(doc) {
  return `${String(window.location.href).split('#')[0]}#doc=${encodeURIComponent(doc.doc_id)}`;
}

function copyQaTable(qIdx) {
  if (!currentSelectedDoc) return;
  const docQas = DOC_QAS_MAP[currentSelectedDoc.doc_id] || [];
  const q = docQas[qIdx];
  if (!q) return;
  const qBox = document.getElementById(`qa-box-${q.q_num}`);
  if (!qBox) return;
  const tables = qBox.querySelectorAll('table');
  if (!tables.length) {
    notify('이 질문에는 복사할 표가 없습니다.');
    return;
  }
  const htmlToCopy = Array.from(tables).map(t => t.outerHTML).join('<br><br>');
  const plainToCopy = Array.from(tables).map(t => t.innerText).join('\n\n');

  if (navigator.clipboard && window.ClipboardItem) {
    const blobHtml = new Blob([htmlToCopy], { type: 'text/html' });
    const blobText = new Blob([plainToCopy], { type: 'text/plain' });
    const data = [new ClipboardItem({ 'text/html': blobHtml, 'text/plain': blobText })];
    navigator.clipboard.write(data).then(() => {
      notify(`질문 ${q.q_num}의 표를 복사했습니다. 엑셀이나 한글에 붙여 넣으면 표 모양이 유지됩니다.`, 'success');
    }).catch(() => copyText(plainToCopy, `질문 ${q.q_num}의 표를 글자로 복사했습니다.`));
  } else {
    copyText(plainToCopy, `질문 ${q.q_num}의 표를 글자로 복사했습니다.`);
  }
}

function copySingleQa(qIdx) {
  if (!currentSelectedDoc) return;
  const docQas = DOC_QAS_MAP[currentSelectedDoc.doc_id] || [];
  const q = docQas[qIdx];
  if (!q) return;
  const text = `[질의] ${q.question_title}\n\n[답변]\n${q.answer_full || q.answer_markdown || ''}`;
  copyText(text, `질문 ${q.q_num}의 질문과 답변을 복사했습니다.`);
}

function switchModalTab(tab) {
  // 버튼 순서가 아니라 data-tab으로 고른다(순서를 바꿔도 다른 탭이 켜지지 않는다).
  document.querySelectorAll('.modal-tab-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tab);
  });
  document.getElementById('modal-view-formatted').style.display = tab === 'formatted' ? 'block' : 'none';
  document.getElementById('modal-view-text').style.display = tab === 'text' ? 'block' : 'none';
  document.getElementById('modal-view-qa').style.display = tab === 'qa' ? 'block' : 'none';
}

function closeModal() {
  document.getElementById('modal-backdrop').classList.remove('open');
  // clear hash without jump
  history.replaceState(null, null, ' ');
}

// Side-by-Side Compare Feature
function openComparePicker() {
  if (!currentSelectedDoc) return;
  const selectB = document.getElementById('compare-select-b');
  selectB.innerHTML = '';

  // 같은 요구자의 답변서를 먼저, 그다음 최신순으로 보여 준다(비교는 대개 같은 의원의 지난 답변과 한다).
  const sameReq = (d) => (d.requester && d.requester === currentSelectedDoc.requester) ? 0 : 1;
  const others = DOCUMENTS.filter(d => d.doc_id !== currentSelectedDoc.doc_id).sort((a, b) =>
    (sameReq(a) - sameReq(b))
    || String(b.request_date || b.year || '').localeCompare(String(a.request_date || a.year || '')));
  others.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d.doc_id;
    opt.text = `${sameReq(d) === 0 ? '★ ' : ''}[${d.requester}] ${d.title} (${d.request_date || d.year})`;
    selectB.appendChild(opt);
  });

  // Render left pane
  const leftPane = document.getElementById('compare-pane-left');
  leftPane.innerHTML = `
    <div class="compare-pane-header">
      <div class="side">지금 보던 답변서</div>
      <h3>${escapeHtml(currentSelectedDoc.title)}</h3>
      <div class="sub">${escapeHtml(currentSelectedDoc.requester)} · ${escapeHtml(currentSelectedDoc.request_date || currentSelectedDoc.year)}</div>
    </div>
    <div class="modal-rendered-view">${safeMarkedParse(docDisplayBody(currentSelectedDoc))}</div>
  `;

  updateCompareRightPane();
  document.getElementById('compare-backdrop').classList.add('open');
}

async function updateCompareRightPane() {
  const selectB = document.getElementById('compare-select-b');
  const docBId = selectB.value;
  const found = DOCUMENTS.find(d => d.doc_id === docBId);
  const rightPane = document.getElementById('compare-pane-right');
  if (!found) {
    rightPane.innerHTML = '<p class="muted" style="padding-top:16px;">위에서 비교할 답변서를 고르세요.</p>';
    return;
  }
  const docB = await ensureDocBody(found);
  rightPane.innerHTML = `
    <div class="compare-pane-header">
      <div class="side">고른 답변서</div>
      <h3>${escapeHtml(docB.title)}</h3>
      <div class="sub">${escapeHtml(docB.requester)} · ${escapeHtml(docB.request_date || docB.year)}</div>
    </div>
    <div class="modal-rendered-view">${safeMarkedParse(docDisplayBody(docB))}</div>
  `;
}

function gotoLinkedLedgerFromDoc() {
  if (!currentSelectedDoc || !currentSelectedDoc.linked_ledger_id) return;
  const item = LEDGER_MAP[currentSelectedDoc.linked_ledger_id];
  if (item) {
    closeModal();
    switchMode('ledger');
    openLedgerModal(item);
  } else {
    notify('연결된 요구자료를 관리대장에서 찾을 수 없습니다.');
  }
}

function closeCompareModal() {
  document.getElementById('compare-backdrop').classList.remove('open');
}

