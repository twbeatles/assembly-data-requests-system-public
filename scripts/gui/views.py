# -*- coding: utf-8 -*-
"""GUI 뷰 빌더 믹스인(SRP: 창·스타일·헤더·검색뷰·상태바 구성)."""
import tkinter as tk
from tkinter import ttk, scrolledtext


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from launcher_gui import LauncherApp

    _HostBase_ViewsMixin = LauncherApp
else:
    _HostBase_ViewsMixin = object

FONT = "Malgun Gothic"
# 색 체계는 웹 대시보드와 같다. 주 동작 하나만 강한 색, 나머지는 중립색(제안서 G5).
PRIMARY = "#1F4E79"
PRIMARY_ACTIVE = "#163a5c"
NEUTRAL_BG = "#e2e8f0"
NEUTRAL_ACTIVE = "#cbd5e1"
TEXT = "#1e293b"
MUTED = "#64748b"

# 검색 대상(라디오 버튼). 값은 gui.search_text.GUI_TARGETS의 키다.
TARGET_CHOICES = (("qa", "Q&A 질문별"), ("docs", "공문서"), ("ledger", "관리대장"))
# 대상별 마지막 열 제목. 대장은 마감 상태를 보여 준다(제안서 G8).
EXTRA_COLUMN_TITLES = {"qa": "문서번호", "docs": "문서번호", "ledger": "마감 · 상태"}


class ViewsMixin(_HostBase_ViewsMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def center_window(self, width, height):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2 - 30)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
    def setup_styles(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use('vista' if 'vista' in self.style.theme_names() else 'clam')
        except Exception:
            pass
        self.style.configure(".", font=(FONT, 10))
        self.style.configure("Treeview.Heading", font=(FONT, 10, "bold"), padding=5)
        self.style.configure("Treeview", font=(FONT, 9), rowheight=26)
        self.style.configure("Target.TRadiobutton", font=(FONT, 9), background="#ffffff")
    def create_header(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        header = tk.Frame(self.root, bg="#133352", height=78)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)

        inner = tk.Frame(header, bg="#133352")
        inner.pack(fill=tk.BOTH, expand=True, padx=24, pady=12)

        title_lbl = tk.Label(
            inner,
            text=f"🛡️ {lg.DEPT_CONFIG.get('system_title', '국회·대외기관 자료요구 통합 검색 시스템')}",
            font=(FONT, 16, "bold"),
            fg="#ffffff",
            bg="#133352"
        )
        title_lbl.pack(anchor="w")

        self.sub_lbl = tk.Label(
            inner,
            text=f"{lg.DEPT_CONFIG.get('dept_name', '기획예산팀')} 공문서 및 Q&A 표준 데이터베이스 | 무설치 독립형 통합 패키지",
            font=(FONT, 9),
            fg="#93c5fd",
            bg="#133352"
        )
        self.sub_lbl.pack(anchor="w", pady=(2, 0))

    def _flat_button(self, parent, text, command, primary=False, bold=False, **pack):
        btn = tk.Button(
            parent,
            text=text,
            font=(FONT, 10 if primary else 9, "bold" if (primary or bold) else "normal"),
            bg=PRIMARY if primary else NEUTRAL_BG,
            fg="white" if primary else TEXT,
            activebackground=PRIMARY_ACTIVE if primary else NEUTRAL_ACTIVE,
            activeforeground="white" if primary else TEXT,
            padx=14 if primary else 10,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
            command=command,
        )
        btn.pack(**pack)
        return btn

    def create_quick_launch_bar(self):
        bar = tk.Frame(self.root, bg="#f8fafc", padx=16, pady=10, bd=1, relief=tk.SOLID)
        bar.pack(fill=tk.X, padx=16, pady=(12, 6))

        tk.Label(
            bar,
            text="바로 실행:",
            font=(FONT, 10, "bold"),
            fg="#334155",
            bg="#f8fafc"
        ).pack(side=tk.LEFT, padx=(4, 12))

        self._flat_button(bar, "🌐 웹 대시보드 열기", self.open_html_dashboard, primary=True, side=tk.LEFT, padx=6)
        self._flat_button(bar, "➕ 새 자료 추가 / DB 갱신", self.on_click_add_documents, bold=True, side=tk.LEFT, padx=6)
        self._flat_button(bar, "📊 엑셀 통합 DB", self.open_excel_db, side=tk.LEFT, padx=6)
        self._flat_button(bar, "📖 사용설명서", self.open_manual, side=tk.LEFT, padx=6)
        self._flat_button(bar, "📁 폴더 열기", self.open_folder, side=tk.RIGHT, padx=6)

    def create_search_view(self):
        container = tk.Frame(self.root, bg="#ffffff", padx=16, pady=6)
        container.pack(fill=tk.BOTH, expand=True)

        ctrl_frame = tk.Frame(container, bg="#ffffff")
        ctrl_frame.pack(fill=tk.X, pady=(4, 4))

        tk.Label(
            ctrl_frame,
            text="🔍 검색:",
            font=(FONT, 11, "bold"),
            fg=TEXT,
            bg="#ffffff"
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.kw_entry = tk.Entry(ctrl_frame, font=(FONT, 11), width=35, bd=2, relief=tk.GROOVE)
        self.kw_entry.pack(side=tk.LEFT, padx=(0, 8), ipady=3)
        self.kw_entry.bind("<Return>", lambda e: self.do_search())
        self.kw_entry.focus_set()

        tk.Label(ctrl_frame, text="연도:", font=(FONT, 9), fg="#475569", bg="#ffffff").pack(side=tk.LEFT, padx=(8, 4))
        # 연도 목록은 DB에 있는 연도로 채운다(init_db_and_load). 여기에 연도를 적어 두지 않는다.
        self.year_combo = ttk.Combobox(ctrl_frame, values=["전체"], width=7, state="readonly")
        self.year_combo.set("전체")
        self.year_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.year_combo.bind("<<ComboboxSelected>>", lambda e: self.do_search())

        self._flat_button(ctrl_frame, "검색", self.do_search, primary=True, side=tk.LEFT, padx=4)
        self._flat_button(ctrl_frame, "초기화", self.reset_search, side=tk.LEFT, padx=4)

        self.count_label = tk.Label(
            ctrl_frame,
            text="조회 준비 중...",
            font=(FONT, 9),
            fg=MUTED,
            bg="#ffffff"
        )
        self.count_label.pack(side=tk.RIGHT, padx=4)

        # 검색 대상: 콤보 대신 라디오 버튼(제안서 G8). 한눈에 보이고 한 번에 바꾼다.
        target_frame = tk.Frame(container, bg="#ffffff")
        target_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(target_frame, text="대상:", font=(FONT, 9), fg="#475569", bg="#ffffff").pack(side=tk.LEFT, padx=(0, 6))
        self.target_var = tk.StringVar(value="qa")
        for value, label in TARGET_CHOICES:
            ttk.Radiobutton(
                target_frame, text=label, value=value, variable=self.target_var,
                style="Target.TRadiobutton", command=self.do_search,
            ).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(
            target_frame,
            text="문법: 두 단어(모두 포함) · A OR B · -제외어 · \"구문\" · 초성(ㄷㅍㅇㅋ)",
            font=(FONT, 8), fg=MUTED, bg="#ffffff",
        ).pack(side=tk.RIGHT)

        paned = tk.PanedWindow(container, orient=tk.HORIZONTAL, bg="#cbd5e1", sashwidth=5, sashrelief=tk.RAISED)
        paned.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(paned, bg="#ffffff")
        paned.add(left_frame, minsize=450, width=560)

        cols = ("id", "date", "requester", "title", "extra")
        self.tree = ttk.Treeview(left_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("id", text="ID")
        self.tree.heading("date", text="일자")
        self.tree.heading("requester", text="의원/기관")
        self.tree.heading("title", text="제목 / 질문")
        self.tree.heading("extra", text=EXTRA_COLUMN_TITLES["qa"])

        self.tree.column("id", width=95, anchor="center")
        self.tree.column("date", width=85, anchor="center")
        self.tree.column("requester", width=95, anchor="w")
        self.tree.column("title", width=230, anchor="w")
        self.tree.column("extra", width=110, anchor="w")

        tree_scroll_y = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll_x = ttk.Scrollbar(left_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)

        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        right_frame = tk.Frame(paned, bg="#ffffff", padx=8, pady=4)
        paned.add(right_frame, minsize=420)

        det_top = tk.Frame(right_frame, bg="#ffffff")
        det_top.pack(fill=tk.X, pady=(0, 6))

        self.det_title_lbl = tk.Label(
            det_top,
            text="항목을 선택하면 상세 답변 내용이 표시됩니다. (더블클릭 시 웹 대시보드로 열기)",
            font=(FONT, 10, "bold"),
            fg=TEXT,
            bg="#ffffff",
            wraplength=380,
            justify=tk.LEFT
        )
        self.det_title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._flat_button(det_top, "🌐 대시보드로 열기", self.open_current_in_web, side=tk.RIGHT, padx=4)
        self._flat_button(det_top, "📋 전문 복사", self.copy_current_text, side=tk.RIGHT, padx=4)

        self.det_meta_lbl = tk.Label(
            right_frame,
            text="",
            font=(FONT, 9),
            fg=MUTED,
            bg="#ffffff",
            anchor="w"
        )
        self.det_meta_lbl.pack(fill=tk.X, pady=(0, 4))

        # 본문 안 찾기(Ctrl+F). 검색어 강조와 같은 태그를 쓴다(제안서 G3).
        find_bar = tk.Frame(right_frame, bg="#ffffff")
        find_bar.pack(fill=tk.X, pady=(0, 4))
        tk.Label(find_bar, text="본문에서 찾기:", font=(FONT, 9), fg="#475569", bg="#ffffff").pack(side=tk.LEFT)
        self.find_entry = tk.Entry(find_bar, font=(FONT, 9), width=24, bd=1, relief=tk.SOLID)
        self.find_entry.pack(side=tk.LEFT, padx=6, ipady=2)
        self.find_entry.bind("<Return>", lambda e: self.find_next_in_detail())
        self._flat_button(find_bar, "다음 ▼", self.find_next_in_detail, side=tk.LEFT)
        self.find_count_lbl = tk.Label(find_bar, text="", font=(FONT, 8), fg=MUTED, bg="#ffffff")
        self.find_count_lbl.pack(side=tk.LEFT, padx=6)

        self.det_text = scrolledtext.ScrolledText(
            right_frame,
            wrap=tk.WORD,
            font=(FONT, 10),
            bg="#f8fafc",
            fg=TEXT,
            bd=1,
            relief=tk.SOLID
        )
        self.det_text.pack(fill=tk.BOTH, expand=True)
        self.det_text.tag_config("hit", background="#fef08a", foreground="#854d0e")
        self.det_text.tag_config("hit_current", background="#f59e0b", foreground="#ffffff")
        self.root.bind_all("<Control-f>", lambda e: self.focus_find())
        self.root.bind_all("<Control-F>", lambda e: self.focus_find())

    def apply_target_columns(self, target):
        self.tree.heading("extra", text=EXTRA_COLUMN_TITLES.get(target, "비고"))

    def focus_find(self):
        self.find_entry.focus_set()
        self.find_entry.select_range(0, tk.END)
        return "break"

    def highlight_detail(self, terms):
        """본문에서 검색어를 강조하고 첫 일치 위치로 스크롤한다."""
        from gui.search_text import find_all
        self.det_text.tag_remove("hit", "1.0", tk.END)
        self.det_text.tag_remove("hit_current", "1.0", tk.END)
        body = self.det_text.get("1.0", tk.END)
        first = None
        for term in terms or []:
            for start, end in find_all(body, term):
                s_idx, e_idx = f"1.0+{start}c", f"1.0+{end}c"
                self.det_text.tag_add("hit", s_idx, e_idx)
                if first is None or start < first:
                    first = start
        if first is not None:
            self.det_text.see(f"1.0+{first}c")
        self._find_pos = 0

    def find_next_in_detail(self):
        from gui.search_text import find_all
        needle = self.find_entry.get().strip()
        body = self.det_text.get("1.0", tk.END)
        hits = find_all(body, needle)
        self.det_text.tag_remove("hit_current", "1.0", tk.END)
        if not hits:
            self.find_count_lbl.config(text="없음" if needle else "")
            return
        for start, end in hits:
            self.det_text.tag_add("hit", f"1.0+{start}c", f"1.0+{end}c")
        pos = getattr(self, "_find_pos", 0) % len(hits)
        start, end = hits[pos]
        self.det_text.tag_add("hit_current", f"1.0+{start}c", f"1.0+{end}c")
        self.det_text.see(f"1.0+{start}c")
        self.find_count_lbl.config(text=f"{pos + 1} / {len(hits)}")
        self._find_pos = pos + 1

    def create_status_bar(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        bar = tk.Frame(self.root, bg=NEUTRAL_BG, height=26)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)

        self.status_lbl = tk.Label(
            bar,
            text=f"데이터 위치: {lg.BASE_DIR}",
            font=(FONT, 8),
            fg="#475569",
            bg=NEUTRAL_BG
        )
        self.status_lbl.pack(side=tk.LEFT, padx=12)

        # 마감 요약(제안서 G7). 기한 경과가 있으면 빨간 글씨.
        self.due_lbl = tk.Label(bar, text="", font=(FONT, 8, "bold"), fg="#b91c1c", bg=NEUTRAL_BG)
        self.due_lbl.pack(side=tk.LEFT, padx=12)

        self.ver_lbl = tk.Label(
            bar,
            text="",
            font=(FONT, 8),
            fg=MUTED,
            bg=NEUTRAL_BG
        )
        self.ver_lbl.pack(side=tk.RIGHT, padx=12)

    def flash_status(self, message, millis=2500):
        """확인 창 대신 상태 표시줄에 잠깐 알린다(제안서 G4). 작업 흐름을 끊지 않는다."""
        previous = self.status_lbl.cget("text")
        self.status_lbl.config(text=message, fg="#15803d")

        def restore():
            try:
                self.status_lbl.config(text=previous, fg="#475569")
            except tk.TclError:
                pass
        self.root.after(millis, restore)

    def refresh_status_details(self, cur, doc_cnt, qa_cnt):
        """상태 표시줄 오른쪽: 스키마 버전·최종 갱신 시각. 가운데: 마감 요약."""
        import datetime
        import launcher_gui as lg
        try:
            schema = cur.execute("PRAGMA user_version").fetchone()[0]
        except Exception:
            schema = "?"
        try:
            mtime = datetime.datetime.fromtimestamp(lg.DB_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            mtime = "-"
        self.ver_lbl.config(text=f"DB 스키마 v{schema} · 최종 갱신 {mtime} · 문서 {doc_cnt:,} · Q&A {qa_cnt:,}")
        try:
            from gui.search_text import ledger_due_counts
            due = ledger_due_counts(lg.DB_PATH)
        except Exception:
            due = None
        if not due:
            self.due_lbl.config(text="")
        elif due["overdue"]:
            self.due_lbl.config(text=f"🚨 기한 경과 미제출 {due['overdue']}건 · ⏰ 7일 이내 {due['soon']}건", fg="#b91c1c")
        elif due["soon"]:
            self.due_lbl.config(text=f"⏰ 7일 이내 마감 {due['soon']}건 · 미제출 {due['open']}건", fg="#c2410c")
        else:
            self.due_lbl.config(text=f"마감 임박 없음 · 미제출 {due['open']}건", fg="#15803d")

    def init_db_and_load(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        if not lg.DB_PATH.exists():
            self.count_label.config(text="⚠️ DB 파일 없음 (data_requests.db)")
            return

        try:
            conn = self.get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM documents")
            doc_cnt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM qa_items")
            qa_cnt = cur.fetchone()[0]

            if hasattr(self, 'sub_lbl') and self.sub_lbl:
                self.sub_lbl.config(text=f"공문서 {doc_cnt}건 · Q&A {qa_cnt:,}건 표준 데이터베이스 | 무설치 독립형 통합 패키지")
            self.status_lbl.config(text=f"준비 완료 | 데이터베이스: data_requests.db (문서 {doc_cnt}건, Q&A {qa_cnt:,}건)")
            self.refresh_status_details(cur, doc_cnt, qa_cnt)

            # Dynamically load distinct years from documents and request_ledger
            try:
                cur.execute("""
                    SELECT DISTINCT year FROM documents WHERE year IS NOT NULL AND year != ''
                    UNION
                    SELECT DISTINCT year FROM request_ledger WHERE year IS NOT NULL AND year != ''
                    ORDER BY year DESC
                """)
                years = [r[0] for r in cur.fetchall() if r[0]]
                if years:
                    curr_val = self.year_combo.get()
                    self.year_combo['values'] = ["전체"] + years
                    if curr_val in (["전체"] + years):
                        self.year_combo.set(curr_val)
                    else:
                        self.year_combo.set("전체")
            except Exception:
                pass

            self.do_search()
        except Exception as e:
            self.count_label.config(text=f"DB 연결 오류: {e}")
