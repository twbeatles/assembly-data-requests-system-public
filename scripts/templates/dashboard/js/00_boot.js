// Apply successful server snapshots, including zero rows, preserving LOCAL drafts.
function applyServerLedger(rows) {
  REQUEST_LEDGER = mergeLocalLedger(rows);
  LEDGER_MAP = {};
  REQUEST_LEDGER.forEach(item => { LEDGER_MAP[item.ledger_id] = item; });
  const count = document.getElementById('count-mode-ledger');
  if (count) count.innerText = REQUEST_LEDGER.length;
}

let DB = { documents: [], qa_items: [] };
let DOCUMENTS = [];
let QA_ITEMS = [];
let DOCS_MAP = {};
let QA_MAP = {};
let DOC_QAS_MAP = {};

// 대용량 모드: 문서 전문이 COMPRESSED_BODY_n 블록으로 나뉘어 있으면 메타로 화면을 먼저 띄우고
// 본문 블록을 뒤에서 하나씩 푼다. 한 덩어리로 풀면 브라우저 문자열 한도(약 5억 자)를 넘는 순간
// 대시보드가 아예 열리지 않는다. 전문은 여전히 이 HTML 안에 전부 있다(전문 보존 정책).
let BODY_CHUNKS_TOTAL = 0;
let BODY_CHUNKS_LOADED = 0;
let BODY_LOAD_ERROR = '';
let BODIES_READY = Promise.resolve();
let resolveBodiesReady = null;

function bodiesLoading() {
  return BODY_CHUNKS_TOTAL > 0 && BODY_CHUNKS_LOADED < BODY_CHUNKS_TOTAL;
}

// `am_ref`(부모 본문 안의 [시작, 길이], 파이썬 코드포인트 단위)를 문자열로 되살리는 함수를 만든다.
// 문서 하나에 Q&A가 수십 개라도 본문 분석은 한 번만 한다. 예전에는 Q&A마다 본문 전체를
// `Array.from`으로 풀어 문서가 클수록 부팅이 느렸고 임시 배열이 메모리를 크게 썼다.
// - 서로게이트(이모지 등 BMP 밖 문자)가 없으면 코드포인트 = 코드유닛이라 `substr`로 자른다.
//   V8은 이 부분 문자열을 부모 본문과 메모리를 공유하는 조각으로 만든다.
// - 있으면 코드포인트 → 코드유닛 위치표를 한 번 만들어 쓴다. (감사 R6-02 규칙 유지)
// 파이썬 쪽 같은 규칙: template_renderer.DashboardRenderer.rehydrate_qa_bodies
function makeAnswerRehydrator(body) {
  const s = String(body || '');
  if (!/[\uD800-\uDFFF]/.test(s)) {
    return (ref) => s.substr(ref[0], ref[1]);
  }
  const offsets = [0];
  for (let i = 0; i < s.length; ) {
    const code = s.charCodeAt(i);
    i += (code >= 0xD800 && code <= 0xDBFF && i + 1 < s.length) ? 2 : 1;
    offsets.push(i);
  }
  const at = (cp) => offsets[Math.max(0, Math.min(cp, offsets.length - 1))];
  return (ref) => s.slice(at(ref[0]), at(ref[0] + ref[1]));
}

function rehydrateAnswerMarkdown(body, ref) {
  return makeAnswerRehydrator(body)(ref);
}

async function gunzipBase64Text(b64) {
  const binStr = atob(b64);
  const bytes = new Uint8Array(binStr.length);
  for (let i = 0; i < binStr.length; i++) {
    bytes[i] = binStr.charCodeAt(i);
  }
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('현재 웹 브라우저가 최신 압축 해제 기능(DecompressionStream)을 지원하지 않습니다. 최신 Chrome, Edge, Whale 브라우저를 사용해주세요.');
  }
  const ds = new DecompressionStream('gzip');
  const writer = ds.writable.getWriter();
  writer.write(bytes);
  writer.close();
  return await new Response(ds.readable).text();
}

// 내장 블록 하나를 풀어 JSON으로 돌려준다. 푼 뒤에는 블록 문자열을 비워 메모리를 돌려준다
// (base64 원문은 압축본의 1.33배라 대용량에서는 수백 MB가 된다). 다시 읽는 곳은 없다.
async function readCompressedBlock(el) {
  const text = await gunzipBase64Text(el.textContent.trim());
  el.textContent = '';
  return JSON.parse(text);
}

function bodyChunkElements() {
  return Array.from(document.querySelectorAll('script[id^="COMPRESSED_BODY_"]'))
    .map(el => ({ el, idx: Number(el.id.slice('COMPRESSED_BODY_'.length)) }))
    .sort((a, b) => a.idx - b.idx)
    .map(x => x.el);
}

// 본문 블록 하나를 문서·Q&A에 되돌린다. 파이썬 쪽 같은 규칙: PayloadMixin.decode_dashboard_payload
function applyBodyChunk(chunk) {
  const bodies = (chunk && chunk.bodies) || {};
  for (const id of Object.keys(bodies)) {
    const doc = DOCS_MAP[id];
    if (!doc) continue;
    doc.full_markdown = bodies[id];
    doc.answer_full_text = doc.full_markdown;
    doc.body_pending = false;
    let rehydrate = null;
    for (const q of (DOC_QAS_MAP[id] || [])) {
      if (q.am_ref && !q.answer_markdown) {
        rehydrate = rehydrate || makeAnswerRehydrator(doc.full_markdown);
        q.answer_markdown = rehydrate(q.am_ref);
        q.answer_full = q.answer_markdown;
        delete q.am_ref;
      }
    }
  }
  const qaBodies = (chunk && chunk.qa_bodies) || {};
  for (const id of Object.keys(qaBodies)) {
    const q = QA_MAP[id];
    if (!q) continue;
    q.answer_markdown = qaBodies[id];
    q.answer_full = q.answer_markdown;
  }
}

function updateBodyLoadStatus() {
  const hint = document.getElementById('search-scope-hint');
  if (!hint) return;
  if (BODY_LOAD_ERROR) {
    hint.textContent = `⚠️ 일부 문서 전문을 불러오지 못했습니다: ${BODY_LOAD_ERROR}`;
    hint.style.display = 'inline';
  } else if (bodiesLoading()) {
    hint.textContent = `📚 문서 전문 불러오는 중 (${BODY_CHUNKS_LOADED}/${BODY_CHUNKS_TOTAL}) — 본문 검색 결과가 늘어날 수 있습니다`;
    hint.style.display = 'inline';
  } else {
    hint.style.display = 'none';
  }
}

// 화면이 뜬 뒤 본문 블록을 하나씩 푼다. 블록 사이마다 이벤트 루프에 양보해 입력이 막히지 않는다.
async function loadDeferredBodies() {
  if (!BODY_CHUNKS_TOTAL) return;
  updateBodyLoadStatus();
  for (const el of bodyChunkElements()) {
    try {
      applyBodyChunk(await readCompressedBlock(el));
    } catch (err) {
      console.error('문서 전문 블록을 풀지 못했습니다:', el.id, err);
      BODY_LOAD_ERROR = `${el.id} ${(err && err.message) || err}`;
    }
    BODY_CHUNKS_LOADED += 1;
    updateBodyLoadStatus();
    await new Promise(r => setTimeout(r, 0));
  }
  BODY_CHUNKS_LOADED = BODY_CHUNKS_TOTAL;
  DOCUMENTS.forEach(d => {
    if (d.body_pending) {
      d.body_pending = false;
      d.body_incomplete = true;
    }
  });
  if (resolveBodiesReady) resolveBodiesReady();
  updateBodyLoadStatus();
  warmSearchCache();
  // 본문이 오기 전에 한 검색은 제목·요약만 봤다. 전문으로 다시 평가한다.
  if (activeFilters.query) render();
}

async function loadAndDecompressDatabase() {
  const el = document.getElementById('COMPRESSED_DATA');
  if (!el) return;
  DB = await readCompressedBlock(el);
  DOCUMENTS = DB.documents || [];
  QA_ITEMS = DB.qa_items || [];
  REQUEST_LEDGER = DB.request_ledger || [];
  LEDGER_HISTORY = DB.ledger_history || {};
  BODY_CHUNKS_TOTAL = Number(DB.body_chunks || 0);
  BODY_CHUNKS_LOADED = 0;
  if (BODY_CHUNKS_TOTAL > 0) {
    BODIES_READY = new Promise(resolve => { resolveBodiesReady = resolve; });
  }

  // If running in web server mode (HTTP/HTTPS), fetch latest live records from server SQLite DB
  if (window.location.protocol.startsWith('http')) {
    try {
      const serverResp = await fetch('/api/ledger');
      if (serverResp.ok) {
        const sdata = await serverResp.json();
        if (sdata && sdata.success && Array.isArray(sdata.data)) {
          REQUEST_LEDGER = sdata.data;
        }
      }
    } catch (err) {
      console.warn('서버 최신 대장 불러오기 알림 (오프라인 모드 유지):', err);
    }
  }

  // Merge any local additions from localStorage
  applyServerLedger(REQUEST_LEDGER);

  // Check and notify user if there are offline records to sync to server
  checkAndNotifyOfflineSync();

  // Hydrate DOCUMENTS & build O(1) DOCS_MAP
  DOCS_MAP = {};
  DOCUMENTS.forEach(d => {
    if (d.full_markdown === undefined && d.body_chunk !== undefined) {
      d.body_pending = true;
    }
    if (!d.answer_full_text) d.answer_full_text = d.full_markdown || '';
    DOCS_MAP[d.doc_id] = d;
  });

  // Hydrate QA_ITEMS from parent docs & build O(1) QA_MAP
  QA_MAP = {};
  const rehydrators = {};
  QA_ITEMS.forEach(q => {
    const doc = DOCS_MAP[q.doc_id];
    // Q&A 답변 전문은 부모 문서 본문 안에 그대로 들어 있으므로, 페이로드에는 위치(시작·길이)만
    // 싣고 여기서 원문 그대로 되살린다. 같은 글자를 두 번 싣지 않아 HTML이 절반 가까이 작아진다.
    // 잘라내는 것이 아니다 — 복원한 문자열은 원본과 한 글자도 다르지 않다(전문 보존 정책 2항).
    // 대용량 모드에서 부모 본문이 아직 없으면 본문 블록이 풀릴 때(applyBodyChunk) 되살린다.
    if (q.am_ref && !q.answer_markdown && doc && typeof doc.full_markdown === 'string') {
      const r = rehydrators[q.doc_id] || (rehydrators[q.doc_id] = makeAnswerRehydrator(doc.full_markdown));
      q.answer_markdown = r(q.am_ref);
      delete q.am_ref;
    }
    if (doc) {
      if (!q.year) q.year = doc.year;
      if (!q.request_date) q.request_date = doc.request_date;
      if (!q.institution) q.institution = doc.institution;
      if (!q.requester) q.requester = doc.requester;
      if (!q.doc_number) q.doc_number = doc.doc_number;
      if (!q.parsed_md_path) q.parsed_md_path = doc.parsed_md_path;
      if (!q.original_path) q.original_path = doc.original_path;
    }
    if (!q.answer_full) q.answer_full = q.answer_markdown || '';
    QA_MAP[q.qa_id] = q;
  });

  // Pre-index Q&A items by doc_id for O(1) instant lookup
  DOC_QAS_MAP = {};
  QA_ITEMS.forEach(q => {
    const pId = q.doc_id || q.parent_id;
    if (pId) {
      if (!DOC_QAS_MAP[pId]) DOC_QAS_MAP[pId] = [];
      DOC_QAS_MAP[pId].push(q);
    }
  });
}
