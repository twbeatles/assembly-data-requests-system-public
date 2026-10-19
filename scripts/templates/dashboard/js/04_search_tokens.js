// `필드:값` 한정자. 서버 정본 `search/lang.py`의 FIELD_ALIASES와 같은 이름·규칙이다(불변조건 19 취지).
// 여기 있는 이름만 한정자로 본다. 모르는 접두사(`테스트:질의`)는 평범한 검색어로 둔다.
const SEARCH_FIELD_ALIASES = {
  '요구자': 'requester', '의원': 'requester', 'requester': 'requester',
  '상태': 'status', '진행상태': 'status', 'status': 'status',
  '연도': 'year', '년도': 'year', 'year': 'year',
  '문서번호': 'doc_number', 'docno': 'doc_number',
  '연번': 'seq_no', 'seq': 'seq_no',
  '정당': 'party', 'party': 'party',
  '부서': 'department', '담당부서': 'department', 'department': 'department',
  '기관': 'institution', 'institution': 'institution',
  '기간': 'date', '일자': 'date', 'date': 'date'
};
// 값 전체가 같아야 하는 필드. 부분일치로 두면 `상태:제출`이 `미제출`에도 걸린다.
const SEARCH_EXACT_FIELDS = ['status', 'year', 'seq_no', 'doc_number'];

// `2026`, `2026-03`, `2026.03.01`을 비교 가능한 `YYYY-MM-DD`로 편다. 파이썬 normalize_date와 같은 규칙.
function normalizeSearchDate(value, endOfRange) {
  const v = String(value || '').trim().replace(/\./g, '-');
  const parts = v.split('-').filter(Boolean);
  if (!parts.length || !/^\d+$/.test(parts[0])) return '';
  const year = parts[0].padStart(4, '0');
  if (parts.length === 1) return endOfRange ? `${year}-12-31` : `${year}-01-01`;
  const month = parts[1].padStart(2, '0');
  if (parts.length === 2) {
    if (!endOfRange) return `${year}-${month}-01`;
    const last = new Date(Number(year), Number(month), 0).getDate();
    return `${year}-${month}-${String(Number.isFinite(last) && last > 0 ? last : 28).padStart(2, '0')}`;
  }
  return `${year}-${month}-${parts[2].padStart(2, '0')}`;
}

// 한정자 토큰이면 fields에 담고 true. 서버 search/parse.py `_take_field`와 같은 규칙.
function takeSearchField(raw, fields) {
  const m = /^([0-9A-Za-z가-힣]+):(.*)$/.exec(raw);
  if (!m) return false;
  const key = SEARCH_FIELD_ALIASES[m[1].toLowerCase()];
  if (!key) return false;
  const value = m[2].trim().replace(/^"+|"+$/g, '');
  if (!value) return true;
  if (key === 'date') {
    const rng = /^([0-9][0-9\-.]*)?\.\.([0-9][0-9\-.]*)?$/.exec(value);
    const start = normalizeSearchDate(rng ? (rng[1] || '') : value, false);
    const end = normalizeSearchDate(rng ? (rng[2] || '') : value, true);
    if (start) fields.date_from = start;
    if (end) fields.date_to = end;
    return true;
  }
  (fields[key] = fields[key] || []).push(value.toLowerCase());
  return true;
}

// 한정자를 항목 하나에 적용한다. 서버 search/match.py `fields_match`와 같은 규칙.
function searchFieldsMatch(fields, item) {
  if (!fields) return true;
  const ns = (x) => String(x == null ? '' : x).replace(/\s+/g, '').toLowerCase();
  for (const key of Object.keys(fields)) {
    if (key === 'date_from' || key === 'date_to') continue;
    let raw = item[key];
    if (key === 'department' && !raw) raw = item.contact_person;
    const actual = ns(raw);
    const wanted = fields[key];
    if (SEARCH_EXACT_FIELDS.includes(key)) {
      if (!wanted.some(w => ns(w) === actual)) return false;
    } else if (!wanted.some(w => actual.includes(ns(w)))) {
      return false;
    }
  }
  if (fields.date_from || fields.date_to) {
    const actual = String(item.request_date || '').trim();
    if (!actual) return false;
    if (fields.date_from && actual < fields.date_from) return false;
    if (fields.date_to && actual > fields.date_to) return false;
  }
  return true;
}

function parseSearchTokens(query) {
  if (!query) return { posGroups: [], neg: [], fields: null, allHighlightTokens: [] };

  const useSynonyms = document.getElementById('chk-synonyms') ? document.getElementById('chk-synonyms').checked : true;
  const neg = [];
  const fields = {};
  const allHighlightTokens = new Set();

  let cleanQuery = query.trim();

  // 1. Extract quoted exact phrases e.g. "24시간 신속심의"
  const phrases = [];
  cleanQuery = cleanQuery.replace(/"([^"]+)"/g, (m, p1) => {
    const trimmed = p1.trim();
    if (trimmed) {
      phrases.push(trimmed.toLowerCase());
      allHighlightTokens.add(trimmed);
    }
    return ' ';
  });

  // Helper to expand a single word token
  function expandWordToken(rawWord) {
    const lower = rawWord.toLowerCase();
    const stem = stripKoreanParticle(lower);
    const variants = new Set([lower]);
    if (stem && stem !== lower && stem.length >= 2) {
      variants.add(stem);
    }
    if (useSynonyms) {
      if (SYNONYMS[lower]) {
        SYNONYMS[lower].forEach(s => variants.add(s.toLowerCase()));
      }
      if (stem && SYNONYMS[stem]) {
        SYNONYMS[stem].forEach(s => variants.add(s.toLowerCase()));
      }
    }
    return Array.from(variants);
  }

  // 2. Handle OR / | clauses e.g. "딥페이크 OR 텔레그램"
  const orBlocks = cleanQuery.split(/\s+(?:OR|or|\|)\s+/).map(b => b.trim()).filter(Boolean);

  const phraseTokens = phrases.map(ph => ({
    isPhrase: true,
    text: ph,
    noSpace: ph.replace(/\s+/g, '')
  }));

  const posGroups = [];

  orBlocks.forEach(block => {
    const rawTokens = block.split(/\s+/);
    const andTokens = [];

    rawTokens.forEach(t => {
      if (t.startsWith('-') && t.length > 1) {
        neg.push(t.slice(1).toLowerCase());
      } else if (t && takeSearchField(t, fields)) {
        // 이름·기관처럼 부분일치하는 한정자 값은 화면에서도 강조한다.
        const key = SEARCH_FIELD_ALIASES[t.split(':')[0].toLowerCase()];
        const value = t.slice(t.indexOf(':') + 1).replace(/^"+|"+$/g, '').toLowerCase();
        if (value && key !== 'date' && !SEARCH_EXACT_FIELDS.includes(key)) allHighlightTokens.add(value);
      } else if (t) {
        const lower = t.toLowerCase();
        const isCho = isChoseongOnly(lower);
        const hybridRe = (!isCho && /[ㄱ-ㅎ]/.test(lower)) ? buildHybridKoreanRegex(lower) : null;
        const expandedTerms = isCho ? [lower] : expandWordToken(lower);

        expandedTerms.forEach(term => {
          if (!isChoseongOnly(term)) allHighlightTokens.add(term);
        });

        andTokens.push({
          raw: lower,
          isCho: isCho,
          hybridRe: hybridRe,
          expanded: expandedTerms,
          noSpace: lower.replace(/\s+/g, '')
        });
      }
    });

    if (andTokens.length || phraseTokens.length) {
      posGroups.push({
        andTokens: andTokens,
        phrases: phraseTokens
      });
    }
  });

  // 따옴표 구문만 입력하면 OR 블록이 비어 그룹이 하나도 생기지 않았고, 그 결과 모든 항목이
  // 일치했다. 구문만 있는 경우에도 그룹을 만든다.
  if (!posGroups.length && phraseTokens.length) {
    posGroups.push({ andTokens: [], phrases: phraseTokens });
  }

  return {
    posGroups: posGroups,
    neg: Array.from(new Set(neg)),
    fields: Object.keys(fields).length ? fields : null,
    allHighlightTokens: Array.from(allHighlightTokens)
  };
}

