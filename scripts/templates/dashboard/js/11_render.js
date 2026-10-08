// render()가 겹쳐 불리면(연속 입력·필터 변경) 마지막 호출의 결과만 화면에 쓴다.
let RENDER_SEQ = 0;

async function render() {
  const seq = ++RENDER_SEQ;
  const { allHighlightTokens } = parseSearchTokens(activeFilters.query);
  const highlightTokens = allHighlightTokens || [];

  // 01(file://)과 02(웹 관리 서버) 모두 같은 클라이언트 검색을 쓴다. (감사 R3-03)
  // 예전에는 HTTP 모드에서 서버 검색 API 결과를 쓰면서 클라이언트 문법 처리(초성·조사·동의어·
  // OR·제외어)를 건너뛰어, 추천 모드인 02번에서 대부분의 검색 문법이 0건이거나 반대로 동작했다.
  // 문서·Q&A 전문은 HTML에 내장돼 있고, 관리대장은 서버 모드에서 /api/ledger로 최신화된다.
  // 검색은 시간 조각으로 나눠 돌린다(filterItemsAsync = filterItems()와 같은 결과). 문서가 많아도
  // 입력이 멈추지 않고, 그사이 새 검색이 시작되면 이 결과는 버린다.
  let spinnerTimer = null;
  if (typeof showSearchSpinner === 'function') {
    spinnerTimer = setTimeout(() => showSearchSpinner(), 200);
  }
  const filtered = await filterItemsAsync(() => seq !== RENDER_SEQ);
  if (spinnerTimer) clearTimeout(spinnerTimer);
  if (typeof hideSearchSpinner === 'function') hideSearchSpinner();
  if (filtered === null || seq !== RENDER_SEQ) return;
  if (typeof updateBodyLoadStatus === 'function') updateBodyLoadStatus();
  if (typeof refreshSearchExtras === 'function') refreshSearchExtras();

  document.getElementById('count-filtered').innerText = filtered.length;
  let totalCount = DOCUMENTS.length;
  if (currentMode === 'qa') totalCount = QA_ITEMS.length;
  if (currentMode === 'tables') totalCount = DOCUMENTS.filter(d => d.has_tables === 'Y').length;
  if (currentMode === 'ledger') totalCount = REQUEST_LEDGER.length;
  document.getElementById('count-total').innerText = totalCount;
  renderEmptyState(filtered.length, totalCount);

  // Pagination / Load more handling
  const displayItems = filtered.slice(0, currentRenderLimit);
  const loadMoreBox = document.getElementById('load-more-container');
  const remainingSpan = document.getElementById('load-more-remaining');
  if (loadMoreBox) {
    if (filtered.length > currentRenderLimit) {
      loadMoreBox.style.display = 'block';
      if (remainingSpan) remainingSpan.innerText = (filtered.length - currentRenderLimit);
    } else {
      loadMoreBox.style.display = 'none';
    }
  }

  const cardContainer = document.getElementById('card-container');
  cardContainer.innerHTML = '';
  const tableHead = document.getElementById('table-head');
  const tableBody = document.getElementById('table-body');
  tableBody.innerHTML = '';

  if (currentMode === 'docs' || currentMode === 'tables') {
    tableHead.innerHTML = `
      <tr>
        <th class="cell-center">선택</th>
        <th>문서 ID</th><th>요구일</th><th>요구자</th><th>제목</th>
        <th>답변 미리보기</th><th class="cell-center">표</th><th>주제</th><th>파일</th>
      </tr>
    `;
    displayItems.forEach(doc => {
      const isFav = favorites.includes(doc.doc_id);
      const isChecked = selectedDocIds.has(doc.doc_id);
      // 인라인 핸들러에 넣는 ID는 JSON 문자열로 만든 뒤 HTML 이스케이프한다(따옴표가 든 ID도 안전).
      const docIdJs = escapeHtml(JSON.stringify(doc.doc_id));

      const card = document.createElement('div');
      card.className = 'card';
      card.onclick = () => openDocModal(doc);

      const docQas = DOC_QAS_MAP[doc.doc_id] || [];
      let qaChipsHtml = '';
      if (docQas.length > 0) {
        qaChipsHtml = `<div class="card-qa-chips">` +
          // 인라인 onclick은 전역 스코프에서 실행되므로 지역 변수 doc을 볼 수 없다(ReferenceError).
          // 전역 DOCS_MAP에서 문서를 다시 찾는다. (감사 R3-19a)
          docQas.slice(0, 5).map(q => `<span class="badge-qa-chip" onclick="event.stopPropagation(); openDocModal(DOCS_MAP[${docIdJs}], ${Number(q.q_num) || 1});" title="${escapeHtml(q.question_title)} — 이 질문의 답변으로 바로 가기">질문 ${Number(q.q_num) || 1}. ${escapeHtml(q.question_title.length > 20 ? q.question_title.slice(0, 20) + '...' : q.question_title)}</span>`).join('') +
          (docQas.length > 5 ? `<span class="badge-qa-more" onclick="event.stopPropagation(); openDocModal(DOCS_MAP[${docIdJs}], 1);">+${docQas.length - 5}개 더</span>` : '') +
          `</div>`;
      }

      const bodyText = doc.full_markdown || doc.answer_full_text || doc.question_list;
      let snippet = getSnippet(highlightTokens.length ? bodyText : cardPreviewText(bodyText), highlightTokens);
      if (currentMode === 'tables' && doc.table_summary) {
        snippet = `<div class="table-summary-note"><strong>들어 있는 표:</strong> ${highlightTextString(doc.table_summary, highlightTokens)}</div>` + snippet;
      }
      const titleHighlighted = highlightTextString(doc.title, highlightTokens);
      const reqHighlighted = highlightTextString(doc.requester, highlightTokens);
      const dateText = escapeHtml(doc.request_date || doc.year);

      const tagsHtml = (doc.topic_tags ? doc.topic_tags.split(',') : [])
        .slice(0, 3)
        .map(t => `<span class="card-tag">${escapeHtml(t.trim())}</span>`).join('');

      card.innerHTML = `
        <div class="card-header-actions" onclick="event.stopPropagation();">
          <div class="card-meta">
            <input type="checkbox" class="card-checkbox" aria-label="이 답변서 선택" ${isChecked ? 'checked' : ''} onchange="toggleSelectDoc(${docIdJs}, this.checked)">
            <span class="badge-id">${escapeHtml(doc.doc_id)}</span>
            <span class="card-date">${dateText}</span>
          </div>
          <div class="card-top-icons">
            <button class="btn-star ${isFav ? 'active' : ''}" onclick="toggleFavorite(${docIdJs}, event)" title="${isFav ? '즐겨찾기에서 빼기' : '즐겨찾기에 담기'}" aria-label="즐겨찾기" aria-pressed="${isFav ? 'true' : 'false'}">★</button>
          </div>
        </div>
        <div class="card-requester">
          <span class="badge-inst">${escapeHtml(doc.institution)}</span>
          <span>${reqHighlighted}</span>
          ${doc.doc_number ? `<span class="sub">(${escapeHtml(doc.doc_number)})</span>` : ''}
        </div>
        <div class="card-title">${titleHighlighted}</div>
        <div class="card-snippet">${snippet}</div>
        ${qaChipsHtml}
        <div class="card-footer">
          <div class="card-tags">${tagsHtml}</div>
          <div class="card-foot-right">
            ${doc.has_tables === 'Y' ? `<span class="has-table-badge">표 ${escapeHtml(doc.table_count)}개</span>` : ''}
            ${docQas.length > 0 ? `<span class="qa-count-badge">질문 ${docQas.length}개</span>` : ''}
            <span class="card-link">열어 보기 &rarr;</span>
          </div>
        </div>
      `;
      cardContainer.appendChild(card);

      const tr = document.createElement('tr');
      tr.onclick = () => openDocModal(doc);
      tr.innerHTML = `
        <td class="cell-center" onclick="event.stopPropagation();">
          <input type="checkbox" class="card-checkbox" aria-label="이 답변서 선택" ${isChecked ? 'checked' : ''} onchange="toggleSelectDoc(${docIdJs}, this.checked)">
        </td>
        <td class="cell-id">${escapeHtml(doc.doc_id)}</td>
        <td class="cell-nowrap">${dateText}</td>
        <td class="cell-nowrap"><strong>${reqHighlighted}</strong></td>
        <td class="cell-title">${titleHighlighted}</td>
        <td class="cell-clip">${escapeHtml(doc.answer_summary || doc.question_list)}</td>
        <td class="cell-center cell-nowrap">${doc.has_tables === 'Y' ? `${escapeHtml(doc.table_count)}개` : '-'}</td>
        <td>${escapeHtml(doc.topic_tags || '-')}</td>
        <td class="cell-nowrap" onclick="event.stopPropagation();">
          <a href="${escapeHtml(getDocMdUrl(doc.parsed_md_path))}" target="_blank" title="변환된 전문을 새 탭에서 엽니다">전문</a> ·
          <a href="${escapeHtml(getDocOrigUrl(doc.original_path))}" onclick="return handleOrigClick(event, ${escapeHtml(JSON.stringify(doc.original_path || ''))});" target="_blank" title="원본 파일(서버 화면에서는 파일이 있는 폴더)을 엽니다">원본</a>
        </td>
      `;
      tableBody.appendChild(tr);
    });
  } else if (currentMode === 'ledger') {
    tableHead.innerHTML = `
      <tr>
        <th>대장 ID</th>
        <th class="cell-center">연번</th>
        <th>요구일</th>
        <th>소속 / 요구자</th>
        <th>요구자료 제목</th>
        <th>세부 요구내역</th>
        <th>제출 마감일</th>
        <th class="cell-center">진행 상태</th>
        <th class="cell-center">답변서</th>
      </tr>
    `;
    displayItems.forEach(item => {
      const dueClass = dueRowClass(item);
      const card = document.createElement('div');
      card.className = dueClass ? `card ${dueClass}` : 'card';
      card.onclick = () => openLedgerModal(item);

      const titleHighlighted = highlightTextString(item.title, highlightTokens);
      const reqHighlighted = highlightTextString(`${item.party || ''} ${item.requester || ''}`.trim(), highlightTokens);
      const nameHighlighted = highlightTextString(item.requester || '', highlightTokens);
      const detailsSnippet = getSnippet(item.details, highlightTokens);
      const dateText = escapeHtml(item.request_date || item.year);
      const linkedDoc = item.linked_doc_id ? DOCS_MAP[item.linked_doc_id] : null;
      const footMeta = [
        item.deadline ? `마감 ${escapeHtml(item.deadline)}` : '마감일 없음',
        item.submit_date ? `제출 ${escapeHtml(item.submit_date)}` : '',
        item.department ? escapeHtml(item.department) : ''
      ].filter(Boolean).join(' · ');

      card.innerHTML = `
        <div class="card-header-actions">
          <div class="card-meta">
            <span class="badge-id">${escapeHtml(item.ledger_id)}</span>
            ${item.seq_no ? `<span class="meta-pill">연번 ${escapeHtml(item.seq_no)}</span>` : ''}
            <span class="card-date">${dateText}</span>
          </div>
          <span class="card-badges">${dueBadgeHtml(item)}${statusPillHtml(item)}</span>
        </div>
        <div class="card-requester">
          ${item.party ? `<span class="badge-inst">${escapeHtml(item.party)}</span>` : ''}
          <span>${nameHighlighted || '요구자 모름'}</span>
          ${item.aide ? `<span class="sub">(보좌관 ${escapeHtml(item.aide)})</span>` : ''}
        </div>
        <div class="card-title">${titleHighlighted}</div>
        <div class="card-snippet">${detailsSnippet || '<span class="muted">적어 둔 세부 요구내역이 없습니다</span>'}</div>
        <div class="card-footer">
          <div class="card-foot-meta">${footMeta}</div>
          <div class="card-foot-right">
            ${linkedDoc ? `<span class="linked-badge">답변서 있음</span>` : ''}
            <span class="card-link">열어 보기 &rarr;</span>
          </div>
        </div>
      `;
      cardContainer.appendChild(card);

      const tr = document.createElement('tr');
      if (dueClass) tr.className = dueClass;
      tr.onclick = () => openLedgerModal(item);
      tr.innerHTML = `
        <td class="cell-id">${escapeHtml(item.ledger_id)}</td>
        <td class="cell-center">${escapeHtml(item.seq_no || '-')}</td>
        <td class="cell-nowrap">${dateText}</td>
        <td class="cell-nowrap"><strong>${reqHighlighted}</strong></td>
        <td class="cell-title">${titleHighlighted}</td>
        <td class="cell-clip">${escapeHtml(item.details || '-')}</td>
        <td class="cell-nowrap">${escapeHtml(item.deadline || '-')} ${dueBadgeHtml(item)}</td>
        <td class="cell-center">${statusPillHtml(item)}</td>
        <td class="cell-center" onclick="event.stopPropagation();">
          ${linkedDoc ? `<a href="#" onclick="openDocModal(DOCS_MAP[${escapeHtml(JSON.stringify(item.linked_doc_id))}]); return false;">열기</a>` : '<span class="muted">-</span>'}
        </td>
      `;
      tableBody.appendChild(tr);
    });
  } else {
    tableHead.innerHTML = `
      <tr>
        <th>질문 ID</th><th>문서 ID</th><th>요구일</th><th>요구자</th><th>질문</th>
        <th>답변 미리보기</th><th class="cell-center">표</th><th>파일</th>
      </tr>
    `;
    displayItems.forEach(qa => {
      const openParent = () => {
        const parentDoc = DOCS_MAP[qa.doc_id];
        if (parentDoc) openDocModal(parentDoc, qa.q_num);
        else notify('이 질문이 들어 있던 답변서를 찾을 수 없습니다.', 'warn');
      };
      const card = document.createElement('div');
      card.className = 'card';
      card.onclick = openParent;

      const snippet = getSnippet(highlightTokens.length ? qa.answer_full : cardPreviewText(qa.answer_full), highlightTokens);
      const qTitleHighlighted = highlightTextString(qa.question_title, highlightTokens);
      const reqHighlighted = highlightTextString(qa.requester, highlightTokens);
      const dateText = escapeHtml(qa.request_date || qa.year);

      card.innerHTML = `
        <div class="card-header-actions">
          <div class="card-meta">
            <span class="badge-id">${escapeHtml(qa.qa_id)}</span>
            <span class="card-date">${dateText}</span>
          </div>
        </div>
        <div class="card-requester">
          <span class="badge-inst">${escapeHtml(qa.institution)}</span>
          <span>${reqHighlighted}</span>
          <span class="sub">(질문 ${escapeHtml(qa.q_num)})</span>
        </div>
        <div class="card-title">${qTitleHighlighted}</div>
        <div class="card-snippet">${snippet}</div>
        <div class="card-footer">
          <div>${qa.has_tables === 'Y' ? '<span class="has-table-badge">표 있음</span>' : ''}</div>
          <span class="card-link">답변 보기 &rarr;</span>
        </div>
      `;
      cardContainer.appendChild(card);

      const tr = document.createElement('tr');
      tr.onclick = openParent;
      tr.innerHTML = `
        <td class="cell-id">${escapeHtml(qa.qa_id)}</td>
        <td class="cell-id">${escapeHtml(qa.doc_id)}</td>
        <td class="cell-nowrap">${dateText}</td>
        <td class="cell-nowrap"><strong>${reqHighlighted}</strong></td>
        <td class="cell-title">${qTitleHighlighted}</td>
        <td class="cell-clip">${escapeHtml(cardPreviewText(qa.answer_full).slice(0, 200))}</td>
        <td class="cell-center">${qa.has_tables === 'Y' ? '있음' : '-'}</td>
        <td class="cell-nowrap" onclick="event.stopPropagation();">
          <a href="${escapeHtml(getDocMdUrl(qa.parsed_md_path))}" target="_blank" title="변환된 전문을 새 탭에서 엽니다">전문</a>
        </td>
      `;
      tableBody.appendChild(tr);
    });
  }
}

// 카드 미리보기용: 마크다운 기호(#, |, ---, ** 등)와 HTML 태그를 걷어 낸 앞부분 글.
// 미리보기에만 쓴다. 문서를 열면 full_markdown 전문을 그대로 보여 준다(전문 보존 정책 5).
function cardPreviewText(text) {
  return String(text || '').slice(0, 1500)
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<[^>]{0,200}>/g, ' ')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|?\s*$/gm, ' ')
    .replace(/\|/g, ' ')
    .replace(/(\*\*|__|`)/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// Favorite Toggle
function toggleFavorite(id, event) {
  if (event) event.stopPropagation();
  const idx = favorites.indexOf(id);
  if (idx !== -1) {
    favorites.splice(idx, 1);
  } else {
    favorites.push(id);
  }
  localStorage.setItem('datareq_favorites', JSON.stringify(favorites));
  updateFavCount();
  render();
}

function toggleModalFavorite() {
  if (currentSelectedDoc) {
    toggleFavorite(currentSelectedDoc.doc_id);
    document.getElementById('modal-star').classList.toggle('active', favorites.includes(currentSelectedDoc.doc_id));
  }
}

// Selection handling
function toggleSelectDoc(id, isChecked) {
  if (isChecked) {
    selectedDocIds.add(id);
  } else {
    selectedDocIds.delete(id);
  }
  updateSelectionBar();
}

function toggleSelectAll(checked) {
  // 선택(묶음 인쇄·내려받기)은 답변서에만 있다. 다른 탭에서 부르면 doc_id가 없는 항목이
  // undefined로 들어가 "선택 1건"이 됐다.
  if (currentMode !== 'docs' && currentMode !== 'tables') return;
  const filtered = filterItems();
  filtered.forEach(d => {
    if (!d.doc_id) return;
    if (checked) selectedDocIds.add(d.doc_id);
    else selectedDocIds.delete(d.doc_id);
  });
  updateSelectionBar();
  render();
}

function clearSelection() {
  selectedDocIds.clear();
  document.getElementById('chk-select-all').checked = false;
  updateSelectionBar();
  render();
}

function updateSelectionBar() {
  const bar = document.getElementById('selection-bar');
  const countSpan = document.getElementById('selected-count');
  countSpan.innerText = selectedDocIds.size;
  if (selectedDocIds.size > 0) {
    bar.classList.add('active');
  } else {
    bar.classList.remove('active');
  }
}

// Print Report for Selected Documents
async function printSelectedReport() {
  if (selectedDocIds.size === 0) {
    notify('인쇄할 답변서를 먼저 선택해 주세요.');
    return;
  }
  // 창은 버튼을 누른 그 순간에 열어야 팝업 차단에 걸리지 않는다(본문을 기다린 뒤 열면 막힌다).
  const printWindow = window.open('', '_blank');
  if (!printWindow) {
    notify('인쇄 창을 열지 못했습니다. 브라우저 주소창 옆의 팝업 차단을 풀고 다시 눌러 주세요.', 'error');
    return;
  }
  const selectedDocs = [];
  for (const d of DOCUMENTS.filter(x => selectedDocIds.has(x.doc_id))) {
    selectedDocs.push(await ensureDocBody(d));
  }
  let html = `
    <!DOCTYPE html>
    <html>
    <head>
      <title>자료요구 답변서 모음</title>
      <style>
        body { font-family: "맑은 고딕", sans-serif; padding: 30px; color: #111; line-height: 1.6; }
        h1 { border-bottom: 2px solid #1F4E79; padding-bottom: 10px; color: #1F4E79; font-size: 22px; }
        .doc-entry { page-break-after: always; margin-bottom: 40px; border-bottom: 1px dashed #999; padding-bottom: 30px; }
        .doc-entry:last-child { page-break-after: avoid; }
        .doc-header { background: #f1f5f9; padding: 12px; border-radius: 6px; margin-bottom: 16px; }
        .doc-title { font-size: 18px; font-weight: bold; color: #1F4E79; }
        .doc-meta { font-size: 13px; color: #555; margin-top: 4px; }
        table { width: 100%; border-collapse: collapse; margin: 14px 0; font-size: 13px; }
        th, td { border: 1px solid #cbd5e1; padding: 8px 10px; text-align: center; }
        th { background: #f1f5f9; font-weight: bold; }
      </style>
    </head>
    <body>
      <h1>${escapeHtml(appConfig.department_name || '__DEFAULT_DEPT__')} 자료요구 답변서 모음 (${selectedDocs.length}건)</h1>
      <p style="font-size:12px; color:#666; margin-bottom: 20px;">출력 일시: ${new Date().toLocaleString()}</p>
  `;

  selectedDocs.forEach(d => {
    let bodyHtml = safeMarkedParse(docDisplayBody(d));
    html += `
      <div class="doc-entry">
        <div class="doc-header">
          <div class="doc-title">[${escapeHtml(d.requester)}] ${escapeHtml(d.title)}</div>
          <div class="doc-meta">문서ID: ${escapeHtml(d.doc_id)} | 요구일: ${escapeHtml(d.request_date || d.year)} | 관리번호: ${escapeHtml(d.doc_number || '-')} | 담당: ${escapeHtml(d.contact_person || d.department || '-')}</div>
        </div>
        <div>${bodyHtml}</div>
      </div>
    `;
  });

  html += '</body></html>';
  printWindow.document.write(html);
  printWindow.document.close();
  printWindow.focus();
  setTimeout(() => { printWindow.print(); }, 500);
}

function exportSelectedCsv() {
  if (selectedDocIds.size === 0) {
    notify('내려받을 답변서를 먼저 선택해 주세요.');
    return;
  }
  const selectedDocs = DOCUMENTS.filter(d => selectedDocIds.has(d.doc_id));
  let csv = '\uFEFF';
  csv += '문서ID,일자,연도,기관,요구자,관리번호,제목,답변내용(앞 1000자),표포함,원본경로,마크다운경로\n';
  selectedDocs.forEach(d => {
    csv += csvRow([
      csvCell(d.doc_id), csvCell(d.request_date), csvCell(d.year), csvCell(d.institution),
      csvCell(d.requester), csvCell(d.doc_number), csvCell(d.title),
      csvCell(d.full_markdown || d.answer_full_text || d.answer_summary, 1000),
      csvCell(d.has_tables), csvCell(d.original_path), csvCell(d.parsed_md_path)
    ]);
  });
  downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8;' }),
    `국회_대외기관_선택문서_${new Date().toISOString().slice(0,10)}.csv`);
}

