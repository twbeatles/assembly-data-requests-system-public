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
  if (!scheme) return true; // 상대 경로·#앵커
  if (attrName === 'href') return ['http', 'https', 'mailto'].includes(scheme[1]);
  // img src의 원격 http(s)는 렌더링 시점에 외부 요청을 내므로 허용하지 않는다.
  // 오프라인 단일 HTML이라는 제품 보장을 지키기 위한 차단이다. (감사 Documentation Mismatch)
  return /^data:image\/(png|jpe?g|gif|webp|bmp);/.test(compact);
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

function safeMarkedParse(md) {
  try {
    if (typeof marked !== 'undefined' && marked.parse) {
      return sanitizeHtml(marked.parse(md || ''));
    }
    return escapeHtml(md || '');
  } catch (e) {
    return '<pre>' + escapeHtml(md || '') + '</pre>';
  }
}


function applyDomHighlight(element, tokens) {
  if (!tokens || !tokens.length) return;
  const validTokens = tokens.filter(t => t && !isChoseongOnly(t));
  if (!validTokens.length) return;
  const escapedTokens = validTokens.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).filter(t => t.length > 0);
  if (!escapedTokens.length) return;
  const pattern = `(${escapedTokens.join('|')})`;
  // split용(g)과 판정용(g 없음)을 나눈다. g 플래그 정규식에 test()를 반복 호출하면 lastIndex가
  // 누적돼 결과가 번갈아 바뀌고, 강조가 문단마다 빠지거나 엉뚱한 조각에 붙었다. (감사 R4-12)
  const splitRegex = new RegExp(pattern, 'gi');
  const matchRegex = new RegExp(pattern, 'i');
  const exactRegex = new RegExp(`^${pattern}$`, 'i');

  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
  const textNodes = [];
  let node;
  while (node = walker.nextNode()) {
    if (node.nodeValue && matchRegex.test(node.nodeValue)) {
      textNodes.push(node);
    }
  }

  textNodes.forEach(textNode => {
    const parent = textNode.parentNode;
    if (parent && parent.nodeName !== 'MARK' && parent.nodeName !== 'SCRIPT' && parent.nodeName !== 'STYLE') {
      const frag = document.createDocumentFragment();
      const parts = textNode.nodeValue.split(splitRegex);
      parts.forEach(part => {
        if (part && exactRegex.test(part)) {
          const mark = document.createElement('mark');
          mark.className = 'highlight';
          mark.textContent = part;
          frag.appendChild(mark);
        } else if (part) {
          frag.appendChild(document.createTextNode(part));
        }
      });
      parent.replaceChild(frag, textNode);
    }
  });
}

let __highlightRegexCache = { key: null, regex: null };
function highlightRegexFor(tokens) {
  const validTokens = (tokens || []).filter(t => t && !isChoseongOnly(t));
  if (!validTokens.length) return null;
  const escapedTokens = validTokens.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).filter(t => t.length > 0);
  if (!escapedTokens.length) return null;
  const key = escapedTokens.join('');
  if (__highlightRegexCache.key === key && __highlightRegexCache.regex) return __highlightRegexCache.regex;
  const regex = new RegExp(`(${escapedTokens.join('|')})`, 'gi');
  __highlightRegexCache = { key, regex };
  return regex;
}
function highlightTextString(text, tokens) {
  if (!text) return '';
  const safeText = escapeHtml(text);
  if (!tokens || !tokens.length) return safeText;
  const cachedHl = highlightRegexFor(tokens);
  if (cachedHl) return safeText.replace(cachedHl, '<mark class="highlight">$1</mark>');
  // 아래는 유효 토큰이 없어 캐시가 null인 경우의 폴백으로, 항상 safeText를 돌려준다.
  const validTokens = tokens.filter(t => t && !isChoseongOnly(t));
  if (!validTokens.length) return safeText;
  const escapedTokens = validTokens.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).filter(t => t.length > 0);
  if (!escapedTokens.length) return safeText;
  const regex = new RegExp(`(${escapedTokens.join('|')})`, 'gi');
  return safeText.replace(regex, '<mark class="highlight">$1</mark>');
}

function getSnippet(fullText, tokens, maxLen = 140) {
  if (!fullText) return '';
  const validTokens = tokens ? tokens.filter(t => t && !isChoseongOnly(t)) : [];
  if (!validTokens.length) {
    return escapeHtml(fullText.slice(0, maxLen) + (fullText.length > maxLen ? '...' : ''));
  }
  const lower = fullText.toLowerCase();
  let firstIdx = -1;
  for (const t of validTokens) {
    const idx = lower.indexOf(t);
    if (idx !== -1 && (firstIdx === -1 || idx < firstIdx)) {
      firstIdx = idx;
    }
  }
  if (firstIdx === -1) {
    return escapeHtml(fullText.slice(0, maxLen) + (fullText.length > maxLen ? '...' : ''));
  }
  const start = Math.max(0, firstIdx - 40);
  const end = Math.min(fullText.length, firstIdx + maxLen - 40);
  let snippet = (start > 0 ? '...' : '') + fullText.slice(start, end) + (end < fullText.length ? '...' : '');
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

  const htmlData = tbl.outerHTML;
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
      .catch(() => copyText(textData, '표 텍스트가 클립보드에 복사되었습니다.'));
  } else {
    copyText(textData, '표가 복사되었습니다.');
  }
}

