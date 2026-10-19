let currentMode = 'docs'; // 'docs', 'qa', 'tables'
let currentView = 'card';
let currentRenderLimit = 40;
let currentModalFontSize = 14;
// 저장소가 막혔거나 값이 깨져도 화면은 떠야 한다(예전에는 여기서 예외가 나면 대시보드 전체가 멈췄다).
let favorites = (() => {
  try {
    const v = JSON.parse(localStorage.getItem('datareq_favorites') || '[]');
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
})();
let selectedDocIds = new Set();

let activeFilters = {
  year: 'ALL',
  inst: 'ALL',
  topic: 'ALL',
  status: 'ALL',
  due: 'ALL',
  sort: 'date-desc',
  tablesOnly: false,
  onlyFavorites: false,
  query: ''
};
let currentSelectedDoc = null;
let currentSelectedLedgerItem = null;
let REQUEST_LEDGER = [];
let LEDGER_MAP = {};
let LEDGER_HISTORY = {};

