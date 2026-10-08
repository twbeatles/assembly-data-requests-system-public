// Domain-specific comprehensive synonym dictionary for National Assembly & Public Affairs Response
// 동의어 사전의 정본은 `scripts/search_synonyms.py`다. 조립할 때 아래 자리에 주입된다.
// 서버 API·GUI·이 화면이 같은 표를 써야 같은 검색어에 같은 결과가 나온다(제안서 S4).
// 주입이 없으면(소스 그대로 열었을 때) 빈 사전이라 동의어 확장만 꺼진다.
const SYNONYMS = /* __SYNONYMS_JSON__ */ {};

// Korean Particles to strip for stemming
const KOREAN_PARTICLES = [
  '에서', '으로', '에게', '까지', '부터', '보다', '처럼',
  '을', '를', '이', '가', '은', '는', '의', '에', '로',
  '과', '와', '도', '만', '나', '이나'
];

function stripKoreanParticle(word) {
  if (!word || word.length <= 2) return word;
  for (const p of KOREAN_PARTICLES) {
    if (word.length > p.length + 1 && word.endsWith(p)) {
      return word.slice(0, -p.length);
    }
  }
  return word;
}

// Korean Choseong Extraction Engine for Smart Search
const CHOSEONG_LIST = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];

function getChoseong(str) {
  if (!str) return '';
  let result = '';
  for (let i = 0; i < str.length; i++) {
    const code = str.charCodeAt(i);
    if (code >= 0xAC00 && code <= 0xD7A3) {
      const choIndex = Math.floor((code - 0xAC00) / (21 * 28));
      result += CHOSEONG_LIST[choIndex];
    } else {
      result += str[i].toLowerCase();
    }
  }
  return result;
}

function getChoseongNoSpace(str) {
  return getChoseong(str).replace(/\s+/g, '');
}

function isChoseongOnly(str) {
  return /^[ㄱ-ㅎ]+$/.test(str.trim());
}

// Convert a hybrid search string (e.g. "디지털ㅅㅂㅈ") into a regex matching both syllables and initials
function buildHybridKoreanRegex(pattern) {
  if (!pattern) return null;
  let reStr = '';
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    const choIdx = CHOSEONG_LIST.indexOf(ch);
    if (choIdx !== -1) {
      const startCode = 0xAC00 + (choIdx * 21 * 28);
      const endCode = startCode + (21 * 28) - 1;
      const startChar = String.fromCharCode(startCode);
      const endChar = String.fromCharCode(endCode);
      reStr += '[' + ch + startChar + '-' + endChar + ']';
    } else if (/[.*+?^${}()|[\]\\]/.test(ch)) {
      reStr += '\\' + ch;
    } else {
      reStr += ch;
    }
  }
  try {
    return new RegExp(reStr, 'i');
  } catch (e) {
    return null;
  }
}

// 검색용 파생 문자열(소문자·공백 제거) 캐시. 열거되지 않는 속성이라 CSV·JSON 내보내기에 섞이지 않는다.
// 무효화 기준은 길이 + 앞·가운데·뒤 표본이다. 예전에는 길이만 봐서, 같은 길이로 바뀐 텍스트(대장
// 제목 한 글자 수정 등)가 옛 캐시로 검색됐다. 전문 전체를 비교하면 검색마다 수백 KB를 다시 훑는다.
// (감사 R4-15b)
function searchCacheSignature(text) {
  const s = String(text == null ? '' : text);
  if (s.length <= 96) return `${s.length}:${s}`;
  const mid = Math.floor(s.length / 2);
  return `${s.length}:${s.slice(0, 32)}|${s.slice(mid - 16, mid + 16)}|${s.slice(-32)}`;
}

function searchCache(item, key, text, compute) {
  if (!item || typeof item !== 'object') return compute(text);
  if (!Object.prototype.hasOwnProperty.call(item, '__searchCache')) {
    Object.defineProperty(item, '__searchCache', { value: {}, enumerable: false, writable: true });
  }
  const sig = searchCacheSignature(text);
  const entry = item.__searchCache[key];
  if (entry && entry.sig === sig) return entry.value;
  const value = compute(text);
  item.__searchCache[key] = { sig, value };
  return value;
}

function noSpaceIncludes(target, query) {
  if (!target || !query) return false;
  const tNoSpace = target.replace(/\s+/g, '').toLowerCase();
  const qNoSpace = query.replace(/\s+/g, '').toLowerCase();
  return tNoSpace.includes(qNoSpace);
}

