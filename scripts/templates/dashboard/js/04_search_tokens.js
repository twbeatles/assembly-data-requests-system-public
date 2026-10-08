function parseSearchTokens(query) {
  if (!query) return { posGroups: [], neg: [], allHighlightTokens: [] };

  const useSynonyms = document.getElementById('chk-synonyms') ? document.getElementById('chk-synonyms').checked : true;
  const neg = [];
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
    allHighlightTokens: Array.from(allHighlightTokens)
  };
}

