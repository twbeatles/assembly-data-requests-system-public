function escapeHtml(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ---------------------------------------------------------------------------
// HTML 살균: 허용 목록으로 태그를 "다시 조립"한다. (감사 R3-15)
// 예전의 정규식 치환은 엔티티로 인코딩한 스킴(javascript&colon;) 같은 변형을 막지 못했다.
// 출력에는 여기서 만든 태그와 허용 속성만 남는다. 허용하지 않은 태그는 지우지 않고 글자 그대로
// 보여 준다. 원문의 "<JB>", "<KBS>" 같은 표기가 태그로 해석돼 화면에서 사라지지 않게 하기 위해서다.
// ---------------------------------------------------------------------------
const SANITIZE_TAGS = new Set([
  'a', 'abbr', 'b', 'blockquote', 'br', 'caption', 'code', 'col', 'colgroup', 'dd', 'del', 'div', 'dl', 'dt',
  'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'ins', 'kbd', 'li', 'mark', 'ol', 'p', 'pre',
  'q', 's', 'small', 'span', 'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'u', 'ul'
]);
const SANITIZE_DROP_WITH_CONTENT = /<(script|style|iframe|object|embed|noscript|template|svg|math|textarea|title|xmp)\b[\s\S]*?<\/\1\s*>/gi;
const SANITIZE_ATTRS = new Set(['href', 'src', 'alt', 'title', 'colspan', 'rowspan', 'align', 'valign', 'width', 'height', 'start', 'scope']);

function decodeHtmlEntities(text) {
  return String(text || '')
    .replace(/&#x([0-9a-f]+);?/gi, (_, h) => String.fromCodePoint(parseInt(h, 16) || 0))
    .replace(/&#(\d+);?/g, (_, d) => String.fromCodePoint(parseInt(d, 10) || 0))
    .replace(/&colon;/gi, ':').replace(/&tab;/gi, '\t').replace(/&newline;/gi, '\n')
    .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&quot;/gi, '"').replace(/&apos;/gi, "'").replace(/&amp;/gi, '&');
}

function isSafeUrl(value, attrName) {
  const compact = decodeHtmlEntities(value).replace(/[\u0000-\u0020\u007f-\u009f]+/g, '').toLowerCase();
  const scheme = /^([a-z][a-z0-9+.-]*):/.exec(compact);
  if (attrName === 'src') {
    // Browser URL parsing treats //host and backslash variants as an external
    // authority. These are not local relative paths, even without a scheme.
    if (/\\/.test(compact) || compact.startsWith('//')) return false;
    if (!scheme) return true;
    return /^data:image\/(png|jpe?g|gif|webp|bmp);/.test(compact);
  }
  if (!scheme) return true; // 상대 경로·#앵커
  if (attrName === 'href') return ['http', 'https', 'mailto'].includes(scheme[1]);
  return false;
}

function rebuildTag(tag) {
  const m = /^<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>$/.exec(tag);
  if (!m) return escapeHtml(tag);
  const name = m[2].toLowerCase();
  if (!SANITIZE_TAGS.has(name)) return escapeHtml(tag);
  if (m[1] === '/') return `</${name}>`;
  const attrs = [];
  const attrRe = /([^\s"'<>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let a;
  while ((a = attrRe.exec(m[3])) !== null) {
    const attrName = a[1].toLowerCase();
    if (!SANITIZE_ATTRS.has(attrName)) continue;
    const raw = a[2] !== undefined ? a[2] : (a[3] !== undefined ? a[3] : (a[4] || ''));
    const value = decodeHtmlEntities(raw);
    if ((attrName === 'href' || attrName === 'src') && !isSafeUrl(value, attrName)) continue;
    attrs.push(` ${attrName}="${escapeHtml(value)}"`);
  }
  if (name === 'a') attrs.push(' rel="noopener noreferrer"');
  return `<${name}${attrs.join('')}>`;
}

function sanitizeHtml(html) {
  if (!html) return '';
  return String(html)
    .replace(SANITIZE_DROP_WITH_CONTENT, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<\/?[a-zA-Z][^>]*>?|<[!?][^>]*>?/g, rebuildTag);
}

// KorDoc이 한글 개요 수준을 그대로 `#`로 옮겨, 본문 항목(`## - 일반인 신고…`, `## ○ …`)과
// 긴 문장이 큰 제목으로 보이는 문서가 있다(운영 48건·358줄, 80자 넘는 제목 17건). 화면 표시에서만
// 제목 표시를 떼어 본문으로 보여 준다. 글자는 그대로이고 저장된 전문·복사본은 바뀌지 않는다.
function demoteBodyHeadings(md) {
  return String(md || '')
    .replace(/^#{1,6}[ \t]+(?=[-–○ㅇ※·▶•][ \t])/gm, '')
    .replace(/^#{1,6}[ \t]+(?=\S[^\r\n]{79,}\r?$)/gm, '');
}

function safeMarkedParse(md) {
  try {
    if (typeof marked !== 'undefined' && marked.parse) {
      const clean = String(md || '')
        .replace(/\u25cc/g, '○')
        .replace(/^\[포맷:\s*[A-Z0-9]+\s*\|\s*페이지:\s*\d+\]\s*\n?/i, '')
        .replace(/^<!--\s*source:.*?-->\s*\n?/i, '');
      return sanitizeHtml(marked.parse(demoteBodyHeadings(clean)));
    }
    return escapeHtml(md || '');
  } catch (e) {
    return '<pre>' + escapeHtml(md || '') + '</pre>';
  }
}


// 검색은 띄어쓰기를 무시하므로(‘시정요구’로 ‘시정 요구’도 찾는다) 강조·미리보기도 같은 규칙을 따른다.
// 공백 없는 낱말은 글자 사이에 공백이 끼어도 맞게 만든다. 너무 긴 낱말은 그대로 둔다(정규식 비용).
function looseTokenPattern(token) {
  const t = String(token || '');
  const esc = (c) => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  if (t.length < 2 || t.length > 30 || /\s/.test(t)) return esc(t);
  return Array.from(t).map(esc).join('\\s*');
}

function applyDomHighlight(element, tokens) {
  if (!element || !tokens || !tokens.length) return;
  const validTokens = Array.from(new Set(tokens.filter(t => t && !isChoseongOnly(t))))
    .sort((a, b) => b.length - a.length);
  if (!validTokens.length) return;
  const escapedTokens = validTokens.map(looseTokenPattern).filter(t => t.length > 0);
  if (!escapedTokens.length) return;
  const pattern = `(${escapedTokens.join('|')})`;
  // split용(g)과 판정용(g 없음)을 나눈다. g 플래그 정규식에 test()를 반복 호출하면 lastIndex가
  // 누적돼 결과가 번갈아 바뀌고, 강조가 문단마다 빠지거나 엉뚱한 조각에 붙었다. (감사 R4-12)
  let splitRegex, matchRegex, exactRegex;
  try {
    splitRegex = new RegExp(pattern, 'gi');
    matchRegex = new RegExp(pattern, 'i');
    exactRegex = new RegExp(`^${pattern}$`, 'i');
  } catch (e) {
    return;
  }

  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
  const textNodes = [];
  let node;
  while (node = walker.nextNode()) {
    if (node.nodeValue && matchRegex.test(node.nodeValue)) {
      const p = node.parentNode;
      if (p && p.closest && p.closest('.table-wrapper-header')) continue;
      textNodes.push(node);
    }
  }

  // 대용량 문서에서 1글자 검색어(예: '1', 'x')로 수만 개 <mark> 노드가 생성돼 브라우저가 멈추는 것을 막는다.
  const MAX_DOM_HIGHLIGHTS = 1500;
  let totalHighlights = 0;
  for (const textNode of textNodes) {
    if (totalHighlights >= MAX_DOM_HIGHLIGHTS) break;
    const parent = textNode.parentNode;
    if (parent && parent.nodeName !== 'MARK' && parent.nodeName !== 'SCRIPT' && parent.nodeName !== 'STYLE' && !(parent.closest && parent.closest('.table-wrapper-header'))) {
      const frag = document.createDocumentFragment();
      const parts = textNode.nodeValue.split(splitRegex);
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (!part) continue;
        if (totalHighlights < MAX_DOM_HIGHLIGHTS && exactRegex.test(part)) {
          const mark = document.createElement('mark');
          mark.className = 'highlight';
          mark.textContent = part;
          frag.appendChild(mark);
          totalHighlights += 1;
        } else {
          frag.appendChild(document.createTextNode(part));
        }
      }
      parent.replaceChild(frag, textNode);
    }
  }
}

let __highlightRegexCache = { key: null, regex: null };
function highlightRegexFor(tokens) {
  const validTokens = Array.from(new Set((tokens || []).filter(t => t && !isChoseongOnly(t))))
    .sort((a, b) => b.length - a.length);
  if (!validTokens.length) return null;
  // 단독 추출 테스트에서도 동작하도록 느슨 이스케이프를 내장한다(looseTokenPattern과 동일 로직).
  const loose = (token) => {
    const t = String(token || '');
    const esc = (c) => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (t.length < 2 || t.length > 30 || /\s/.test(t)) return esc(t);
    return Array.from(t).map(esc).join('\\s*');
  };
  const escapedTokens = validTokens.map(loose).filter(t => t.length > 0);
  if (!escapedTokens.length) return null;
  const key = escapedTokens.join('');
  if (__highlightRegexCache.key === key && __highlightRegexCache.regex) return __highlightRegexCache.regex;
  try {
    const regex = new RegExp(`(${escapedTokens.join('|')})`, 'gi');
    __highlightRegexCache = { key, regex };
    return regex;
  } catch (e) {
    return null;
  }
}
function highlightTextString(text, tokens) {
  if (!text) return '';
  const raw = String(text);
  if (!tokens || !tokens.length) return escapeHtml(raw);
  const regex = highlightRegexFor(tokens);
  if (!regex) return escapeHtml(raw);
  // escapeHtml을 먼저 하면 &quot;, &lt; 같은 엔티티 내부 글자(t, x, quot 등)가 <mark>로 찢어진다.
  // 원문에서 먼저 일치 구간을 나누고 각 조각을 escapeHtml 처리한다.
  regex.lastIndex = 0;
  let out = '';
  let lastIdx = 0;
  let match;
  let hits = 0;
  const MAX_TEXT_HIGHLIGHTS = 200;
  while ((match = regex.exec(raw)) !== null) {
    if (!match[0]) {
      regex.lastIndex += 1;
      continue;
    }
    if (match.index > lastIdx) {
      out += escapeHtml(raw.slice(lastIdx, match.index));
    }
    out += '<mark class="highlight">' + escapeHtml(match[0]) + '</mark>';
    lastIdx = match.index + match[0].length;
    hits += 1;
    if (hits >= MAX_TEXT_HIGHLIGHTS) break;
  }
  if (lastIdx < raw.length) {
    out += escapeHtml(raw.slice(lastIdx));
  }
  return out;
}

// clean: 잘라 낸 미리보기 조각을 다듬는 함수(카드에서는 마크다운 기호를 걷어 내는 cardPreviewText).
// 검색어 자리는 원문에서 찾고, 보여 줄 조각만 다듬는다 — 다듬은 뒤에 찾으면 앞 1500자 밖의 검색어를 놓친다.
function getSnippet(fullText, tokens, maxLen = 140, clean = null) {
  if (!fullText) return '';
  const tidy = (x) => (clean ? clean(x) : x);
  const validTokens = tokens ? tokens.filter(t => t && !isChoseongOnly(t)) : [];
  if (!validTokens.length) {
    return escapeHtml(tidy(fullText.slice(0, maxLen)) + (fullText.length > maxLen ? '...' : ''));
  }
  const lower = fullText.toLowerCase();
  let firstIdx = -1;
  for (const t of validTokens) {
    const idx = lower.indexOf(String(t).toLowerCase());
    if (idx !== -1 && (firstIdx === -1 || idx < firstIdx)) {
      firstIdx = idx;
    }
  }
  if (firstIdx === -1) {
    // 본문에 띄어쓰기가 다르게 들어 있는 경우(‘시정요구’ ↔ ‘시정 요구’)도 그 자리를 보여 준다.
    const loose = highlightRegexFor(validTokens);
    if (loose) {
      loose.lastIndex = 0;
      const m = loose.exec(fullText);
      loose.lastIndex = 0;
      if (m) firstIdx = m.index;
    }
  }
  if (firstIdx === -1) {
    return escapeHtml(tidy(fullText.slice(0, maxLen)) + (fullText.length > maxLen ? '...' : ''));
  }
  const start = Math.max(0, firstIdx - 40);
  const end = Math.min(fullText.length, firstIdx + maxLen - 40);
  let snippet = (start > 0 ? '...' : '') + tidy(fullText.slice(start, end)) + (end < fullText.length ? '...' : '');
  return highlightTextString(snippet, validTokens);
}

function enhanceTablesInElement(container) {
  if (!container) return;
  const tables = container.querySelectorAll('table');
  tables.forEach((tbl, idx) => {
    if (tbl.closest('.table-wrapper')) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'table-wrapper';
    
    const header = document.createElement('div');
    header.className = 'table-wrapper-header';
    header.innerHTML = `
      <span>📊 통계표 [표 ${idx + 1}]</span>
      <button class="btn-copy-table" onclick="copyTableHtml(this)" title="엑셀에 그대로 붙여넣을 수 있는 표 복사">📋 이 표 엑셀 복사</button>
    `;
    
    const responsive = document.createElement('div');
    responsive.className = 'table-responsive';
    
    tbl.parentNode.insertBefore(wrapper, tbl);
    wrapper.appendChild(header);
    responsive.appendChild(tbl);
    wrapper.appendChild(responsive);
  });
}

function copyTableHtml(btn) {
  const wrapper = btn.closest('.table-wrapper');
  if (!wrapper) return;
  const tbl = wrapper.querySelector('table');
  if (!tbl) return;

  const htmlData = tbl.outerHTML.replace(/<mark class="highlight">([\s\S]*?)<\/mark>/gi, '$1');
  const textData = tbl.innerText;

  if (navigator.clipboard && window.ClipboardItem) {
    const blobHtml = new Blob([htmlData], { type: 'text/html' });
    const blobText = new Blob([textData], { type: 'text/plain' });
    navigator.clipboard.write([new ClipboardItem({ 'text/html': blobHtml, 'text/plain': blobText })])
      .then(() => {
        const orig = btn.innerText;
        btn.innerText = '✅ 표 복사 완료!';
        setTimeout(() => btn.innerText = orig, 2000);
      })
      .catch(() => copyText(textData, '표의 텍스트를 복사했습니다. 이 환경에서는 표 서식 복사가 지원되지 않습니다.'));
  } else {
    copyText(textData, '표의 텍스트를 복사했습니다. 이 환경에서는 표 서식 복사가 지원되지 않습니다.');
  }
}

