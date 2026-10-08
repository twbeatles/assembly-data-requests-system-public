function currentModeItems() {
  if (currentMode === 'docs') return DOCUMENTS;
  if (currentMode === 'qa') return QA_ITEMS;
  if (currentMode === 'tables') return DOCUMENTS.filter(d => d.has_tables === 'Y');
  if (currentMode === 'ledger') return REQUEST_LEDGER;
  return [];
}

function filterItems() {
  return filterItemsFromList(currentModeItems(), false);
}

// 화면 검색 경로. 결과는 filterItems()와 같고, 항목을 시간 조각(약 12ms)으로 나눠 평가하며
// 사이사이 이벤트 루프에 양보한다. 문서가 많아져도 입력창·스크롤이 멈추지 않는다.
// `isStale()`이 참이 되면(새 검색어 입력 등) 중간에 멈추고 null을 돌려준다.
function filterItemsAsync(isStale) {
  return filterItemsFromListAsync(currentModeItems(), isStale);
}

function escapeRegExpLiteral(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 토큰마다 동의어 변형을 정규식 하나로 묶어 둔다. 전문(수만 자)은 변형마다 따로 훑지 않고
// 토큰당 한 번만 훑는다. 동의어가 10개로 퍼지는 낱말이면 전문 비교가 10배 줄어든다.
function prepareSearchQuery(query) {
  const { posGroups, neg } = parseSearchTokens(query);
  for (const group of posGroups) {
    for (const tok of group.andTokens) {
      if (tok.isCho || tok.hybridRe || !tok.expanded) continue;
      const variants = Array.from(new Set(tok.expanded
        .map(t => String(t).toLowerCase().replace(/\s+/g, ''))
        .filter(Boolean)));
      tok.bodyRe = variants.length ? new RegExp(variants.map(escapeRegExpLiteral).join('|')) : null;
    }
  }
  return { posGroups, neg, active: !!(posGroups.length || neg.length) };
}

// 항목 하나를 평가한다. 걸러지면 -1, 통과하면 점수(0 이상).
function matchSearchItem(item, prepared, skipQuery) {
  const itemId = item.doc_id || item.qa_id || item.ledger_id;
  if (activeFilters.onlyFavorites && !favorites.includes(itemId)) return -1;
  if (activeFilters.year !== 'ALL' && String(item.year || '') !== String(activeFilters.year)) return -1;
  // 관리대장 항목에는 기관·주제·통계표 값이 없다. 문서용 필터를 대장에 적용하면 결과가 0건이 되고,
  // 반대로 진행상태·마감 필터는 대장에만 의미가 있다.
  const isLedgerItem = !!item.ledger_id && !item.doc_id && !item.qa_id;
  if (isLedgerItem) {
    if (activeFilters.status && activeFilters.status !== 'ALL' && String(item.status || '').trim() !== activeFilters.status) return -1;
    if (activeFilters.due && activeFilters.due !== 'ALL' && !matchesDueFilter(item, activeFilters.due)) return -1;
  } else {
    if (activeFilters.inst !== 'ALL' && (!item.institution || !item.institution.includes(activeFilters.inst))) return -1;
    if (activeFilters.topic !== 'ALL') {
      const tags = (item.topic_tags || '') + (item.title || '') + (item.question_title || '');
      if (!tags.includes(activeFilters.topic)) return -1;
    }
    if (activeFilters.tablesOnly && item.has_tables !== 'Y') return -1;
  }

  if (skipQuery || !prepared.active) return 0;
  const { posGroups, neg } = prepared;

  const title = item.title || item.question_title || '';
  const titleLower = title.toLowerCase();
  const details = item.details || '';
  const detailsLower = details.toLowerCase();
  const party = item.party || '';
  const partyLower = party.toLowerCase();
  const seq = item.seq_no || '';
  const seqLower = seq.toLowerCase();
  const note = item.note || '';
  const noteLower = note.toLowerCase();
  const req = item.requester || '';
  const reqLower = req.toLowerCase();
  const tags = item.topic_tags || '';
  const tagsLower = tags.toLowerCase();
  const qList = item.question_list || '';
  const qListLower = qList.toLowerCase();
  // 본문 검색은 전문(full_markdown / answer_markdown)까지 본다. 대시보드 페이로드에는
  // answer_full_text(엑셀용)가 없어서, 예전에는 300자 요약만 검색됐다. 전문은 커서
  // 소문자·공백 제거 결과를 항목별로 캐시한다. (감사 R3-03)
  const answerSummary = item.answer_summary || '';
  const answerBody = item.full_markdown || item.answer_full_text || item.answer_full || item.answer_markdown || '';
  const answer = answerBody ? `${answerSummary} ${answerBody}` : answerSummary;
  // 전문은 커서 공백 제거·소문자 사본 하나만 캐시한다(사본 두 개는 수백 MB가 된다).
  // 공백을 뺀 비교가 공백 포함 비교를 포함하므로 결과는 같다.
  const answerNoSpace = searchCache(item, 'answerNoSpace', answer, (x) => x.replace(/\s+/g, '').toLowerCase());
  const table = item.table_summary || '';
  const tableLower = table.toLowerCase();
  // 짧은 필드의 공백 제거·소문자 사본은 항목당 한 번만 계산해 캐시한다. 아래 토큰×변형
  // 루프에서 noSpaceIncludes()를 부르면 변형마다 같은 replace를 반복한다. 캐시본의
  // includes()는 같은 문자열 비교라 결과는 같다.
  const titleNS = searchCache(item, 'titleNS', title, (x) => x.replace(/\s+/g, '').toLowerCase());
  const reqNS = searchCache(item, 'reqNS', req, (x) => x.replace(/\s+/g, '').toLowerCase());
  const detailsNS = searchCache(item, 'detailsNS', details, (x) => x.replace(/\s+/g, '').toLowerCase());
  const tagsNS = searchCache(item, 'tagsNS', tags, (x) => x.replace(/\s+/g, '').toLowerCase());
  const qListNS = searchCache(item, 'qListNS', qList, (x) => x.replace(/\s+/g, '').toLowerCase());
  const tableNS = searchCache(item, 'tableNS', table, (x) => x.replace(/\s+/g, '').toLowerCase());

  // 전문(answer)은 캐시된 공백 제거본으로 따로 비교한다. 짧은 필드만 합쳐 매번 문자열을 만든다.
  const searchTarget = `${titleLower} ${reqLower} ${partyLower} ${seqLower} ${detailsLower} ${noteLower} ${tagsLower} ${qListLower} ${tableLower}`;
  const searchTargetNS = searchCache(item, 'searchTargetNS', searchTarget, (x) => x.replace(/\s+/g, ''));

  // Check negative keywords
  for (const n of neg) {
    const nNoSpace = n.replace(/\s+/g, '');
    if (searchTarget.includes(n) || (nNoSpace && searchTargetNS.includes(nNoSpace.toLowerCase())) || (nNoSpace && answerNoSpace.includes(nNoSpace))) {
      return -1;
    }
  }

  if (!posGroups.length) return 0;

  // 초성 변환은 초성 토큰이 있을 때만 한다. 예전에는 검색어와 무관하게 항목마다 필드 11개를
  // 초성으로 바꿔, 문서가 많을수록 평범한 검색까지 느려졌다.
  let cho = null;
  const choFields = () => {
    if (!cho) {
      cho = {
        title: getChoseong(title), req: getChoseong(req), party: getChoseong(party),
        details: getChoseong(details), tags: getChoseong(tags), qList: getChoseong(qList),
        answer: getChoseong(answerSummary),
        titleNS: getChoseongNoSpace(title), reqNS: getChoseongNoSpace(req),
        detailsNS: getChoseongNoSpace(details), tagsNS: getChoseongNoSpace(tags)
      };
    }
    return cho;
  };

  // At least ONE group (OR block) must match!
  let anyGroupMatched = false;
  let bestGroupScore = 0;

  for (const group of posGroups) {
    let groupPassed = true;
    let groupScore = 0;

    // 1. Check exact quoted phrases in group
    for (const ph of group.phrases) {
      let phMatched = false;
      if (titleLower.includes(ph.text) || titleNS.includes(ph.noSpace.toLowerCase())) {
        groupScore += 50;
        phMatched = true;
      }
      if (detailsLower.includes(ph.text) || detailsNS.includes(ph.noSpace.toLowerCase())) {
        groupScore += 35;
        phMatched = true;
      }
      if (answerNoSpace.includes(ph.noSpace)) {
        groupScore += 25;
        phMatched = true;
      }
      if (qListLower.includes(ph.text) || qListNS.includes(ph.noSpace.toLowerCase())) {
        groupScore += 20;
        phMatched = true;
      }
      if (!phMatched) {
        groupPassed = false;
        break;
      }
    }
    if (!groupPassed) continue;

    // 2. Check all AND tokens in group
    for (const tok of group.andTokens) {
      let tokMatched = false;

      if (tok.isCho) {
        // Choseong match (with & without spaces)
        const ch = tok.raw;
        const c = choFields();
        if (c.title.includes(ch) || c.titleNS.includes(ch)) { groupScore += 35; tokMatched = true; }
        if (c.req.includes(ch) || c.reqNS.includes(ch)) { groupScore += 30; tokMatched = true; }
        if (c.party.includes(ch)) { groupScore += 15; tokMatched = true; }
        if (c.details.includes(ch) || c.detailsNS.includes(ch)) { groupScore += 25; tokMatched = true; }
        if (c.tags.includes(ch) || c.tagsNS.includes(ch)) { groupScore += 18; tokMatched = true; }
        if (c.qList.includes(ch)) { groupScore += 15; tokMatched = true; }
        if (c.answer.includes(ch)) { groupScore += 8; tokMatched = true; }
      } else if (tok.hybridRe) {
        // Hybrid choseong + syllable match (e.g. "디지털ㅅㅂㅈ")
        if (tok.hybridRe.test(title)) { groupScore += 40; tokMatched = true; }
        if (tok.hybridRe.test(req)) { groupScore += 35; tokMatched = true; }
        if (tok.hybridRe.test(details)) { groupScore += 25; tokMatched = true; }
        if (tok.hybridRe.test(answer)) { groupScore += 10; tokMatched = true; }
      } else {
        // Standard text, stemming & synonyms match (including noSpace matching!)
        for (const term of tok.expanded) {
          const t = term.toLowerCase();
          const tNoSpace = t.replace(/\s+/g, '');

          // Exact full match
          if (titleLower === t) groupScore += 60;

          // Substring & space-insensitive match
          if (titleLower.includes(t) || titleNS.includes(tNoSpace)) { groupScore += 35; tokMatched = true; }
          if (reqLower.includes(t) || reqNS.includes(tNoSpace)) { groupScore += 30; tokMatched = true; }
          if (seqLower.includes(t)) { groupScore += 30; tokMatched = true; }
          if (detailsLower.includes(t) || detailsNS.includes(tNoSpace)) { groupScore += 25; tokMatched = true; }
          if (partyLower.includes(t)) { groupScore += 15; tokMatched = true; }
          if (tagsLower.includes(t) || tagsNS.includes(tNoSpace)) { groupScore += 18; tokMatched = true; }
          if (qListLower.includes(t) || qListNS.includes(tNoSpace)) { groupScore += 15; tokMatched = true; }
          if (noteLower.includes(t)) { groupScore += 12; tokMatched = true; }
          if (tableLower.includes(t) || tableNS.includes(tNoSpace)) { groupScore += 10; tokMatched = true; }
        }
        // 전문은 토큰당 한 번만 본다(변형을 묶은 정규식). 가산점도 토큰당 한 번이다 —
        // 예전에는 변형마다 +8이 붙어, 동의어가 많은 낱말일수록 전문 일치가 순위를 흔들었다.
        // 서버 검색도 전문은 순위에 쓰지 않는다(score_row).
        if (tok.bodyRe ? tok.bodyRe.test(answerNoSpace) : false) { groupScore += 8; tokMatched = true; }
      }

      if (!tokMatched) {
        groupPassed = false;
        break;
      }
    }

    if (groupPassed) {
      anyGroupMatched = true;
      if (groupScore > bestGroupScore) bestGroupScore = groupScore;
    }
  }

  return anyGroupMatched ? bestGroupScore : -1;
}

function sortSearchResults(results) {
  const sortKey = activeFilters.sort || (activeFilters.query ? 'relevance' : 'date-desc');
  results.sort((a, b) => {
    if (sortKey === 'relevance') {
      const diff = (b._score || 0) - (a._score || 0);
      if (diff !== 0) return diff;
      return (b.request_date || b.year || '').localeCompare(a.request_date || a.year || '');
    } else if (sortKey === 'date-asc') {
      return (a.request_date || a.year || '').localeCompare(b.request_date || b.year || '');
    } else if (sortKey === 'qa-count') {
      // 예전에는 표 개수(table_count)로 정렬해 이름과 결과가 달랐다. 문서에 딸린 질문 수로 센다.
      const qaCount = (x) => (typeof DOC_QAS_MAP !== 'undefined' && x.doc_id && !x.qa_id && DOC_QAS_MAP[x.doc_id])
        ? DOC_QAS_MAP[x.doc_id].length : 0;
      const diff = qaCount(b) - qaCount(a);
      if (diff !== 0) return diff;
      return (b.request_date || b.year || '').localeCompare(a.request_date || a.year || '');
    } else if (sortKey === 'table-first') {
      const tA = a.has_tables === 'Y' ? 1 : 0;
      const tB = b.has_tables === 'Y' ? 1 : 0;
      return tB - tA;
    } else if (sortKey === 'deadline-asc') {
      return compareByDeadline(a, b);
    } else {
      return (b.request_date || b.year || '').localeCompare(a.request_date || a.year || '');
    }
  });
  return results;
}

function filterItemsFromList(items, skipQuery) {
  const prepared = prepareSearchQuery(activeFilters.query);
  const results = [];
  for (const item of items) {
    const score = matchSearchItem(item, prepared, skipQuery);
    if (score < 0) continue;
    item._score = score;
    results.push(item);
  }
  return sortSearchResults(results);
}

// 시간 조각 크기(ms). 이보다 오래 걸리면 한 번 양보한다. 60fps 한 프레임(16ms) 안쪽이다.
const SEARCH_SLICE_MS = 12;

async function filterItemsFromListAsync(items, isStale) {
  const prepared = prepareSearchQuery(activeFilters.query);
  const results = [];
  const list = items || [];
  let i = 0;
  while (i < list.length) {
    const started = Date.now();
    // 시간 확인 비용을 줄이려고 8개마다 본다.
    while (i < list.length) {
      const item = list[i++];
      const score = matchSearchItem(item, prepared, false);
      if (score >= 0) {
        item._score = score;
        results.push(item);
      }
      if ((i & 7) === 0 && Date.now() - started >= SEARCH_SLICE_MS) break;
    }
    if (i < list.length) {
      await new Promise(r => setTimeout(r, 0));
      if (isStale && isStale()) return null;
    }
  }
  if (isStale && isStale()) return null;
  return sortSearchResults(results);
}
