let currentMode = 'docs'; // 'docs', 'qa', 'tables'
let currentView = 'card';
let currentRenderLimit = 40;
let currentModalFontSize = 14;
let favorites = JSON.parse(localStorage.getItem('kocsc_db_favorites') || '[]');
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

