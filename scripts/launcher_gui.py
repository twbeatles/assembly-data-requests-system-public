import os
import sys
import re
import sqlite3
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading
import queue

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from gui.search_text import (CHOSEONG, get_choseong, is_choseong_only, clean_fts_query,
                             service_search, target_key, highlight_terms)
from gui.views import ViewsMixin
from gui.actions import ActionsMixin

def get_base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    else:
        return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
DB_PATH = BASE_DIR / "data_requests.db"

try:
    import system_config
    CFG = system_config.get_config(BASE_DIR)
except Exception:
    CFG = {}

HTML_PATH = BASE_DIR / CFG.get("dashboard_filename", "자료요구_통합검색_대시보드.html")
EXCEL_PATH = BASE_DIR / CFG.get("excel_filename", "국회_대외기관_자료요구_통합DB.xlsx")
MANUAL_PATH = BASE_DIR / "사용설명서_및_안내.html"

DEPT_CONFIG = {
    "dept_name": CFG.get("department_name", "기획예산팀"),
    "system_title": CFG.get("system_title", "기획예산팀 국회·대외기관 자료요구 통합 검색 시스템")
}

class LauncherApp(ViewsMixin, ActionsMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(DEPT_CONFIG.get("system_title", "국회·대외기관 자료요구 통합 검색 시스템"))
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
        self.center_window(1180, 760)
        self._db_conn = None
        self.detail_cache = {}
        self.current_rows_map = {}
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.setup_styles()
        self.create_header()
        self.create_quick_launch_bar()
        self.create_search_view()
        self.create_status_bar()
        self.root.after(100, self.init_db_and_load)

    def get_db_conn(self):
        if not hasattr(self, '_db_conn') or self._db_conn is None:
            self._db_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            self._db_conn.execute("PRAGMA journal_mode = WAL;")
            self._db_conn.execute("PRAGMA query_only = 1;")
            self._db_conn.execute("PRAGMA cache_size = -16000;")
            self._db_conn.execute("PRAGMA mmap_size = 67108864;")
        return self._db_conn

    def close_db_conn(self):
        if hasattr(self, '_db_conn') and self._db_conn:
            try:
                self._db_conn.close()
            except Exception:
                pass
            self._db_conn = None

    def on_close(self):
        self.close_db_conn()
        self.root.destroy()








    def do_search(self):
        if not DB_PATH.exists():
            return

        kw = self.kw_entry.get().strip()
        year = self.year_combo.get()
        target = target_key(self.target_var.get() if hasattr(self, "target_var") else "qa")
        self.current_keyword = kw
        self.current_target = target

        for row in self.tree.get_children():
            self.tree.delete(row)
        self.apply_target_columns(target)

        self.current_rows = []
        self.current_rows_map = {}
        try:
            # 02번 API·대시보드와 같은 검색 엔진(LedgerService.search_all)을 쓴다(제안서 P0-1).
            # 스키마 준비를 할 수 없는 읽기 전용 위치 등에서만 예전 SQL 경로로 되돌아간다.
            try:
                rows, total = service_search(DB_PATH, BASE_DIR, kw, year, target)
                engine = ""
            except Exception as svc_err:
                print(f"GUI 통합 검색 사용 불가, 기본 검색으로 전환: {svc_err}")
                rows = self._legacy_search_rows(kw, year, target)
                total = len(rows)
                engine = " (기본 검색 — 동의어·OR 미지원)"

            self.current_rows = rows
            self.current_rows_map = {r["id"]: r for r in rows}
            for r in rows:
                self.tree.insert("", tk.END, iid=r["id"], values=(
                    r["id"], r["date"] or "-", r["requester"] or "-", r["title"], self.extra_column_text(r, target),
                ))

            cnt = len(rows)
            limit_msg = f" — 상위 {cnt:,}건만 표시, 검색어를 좁혀 주세요" if total > cnt else ""
            self.count_label.config(text=f"검색 결과: {total:,}건{engine}{limit_msg}")

            if rows:
                first_id = rows[0]["id"]
                self.tree.selection_set(first_id)
                self.tree.see(first_id)
                self.load_item_detail(first_id)
            else:
                self.det_title_lbl.config(text="검색 결과가 없습니다.")
                self.det_meta_lbl.config(text="")
                self.det_text.delete("1.0", tk.END)

        except Exception as e:
            self.count_label.config(text=f"검색 오류: {e}")

    @staticmethod
    def extra_column_text(row, target):
        """목록 마지막 열. 대장은 마감 상태(대시보드와 같은 규칙), 그 밖은 문서번호."""
        if target != "ledger":
            return row.get("num") or "-"
        from services.ledger.due import ledger_due_state
        state, days = ledger_due_state({"status": row.get("status"), "deadline": row.get("deadline")})
        status = row.get("status") or "-"
        if state == "overdue" and days is not None:
            return f"🚨 기한 경과 {-days}일 · {status}"
        if state == "soon" and days is not None:
            label = "오늘 마감" if days == 0 else f"D-{days}"
            return f"⏰ {label} · {status}"
        return status

    def _legacy_search_rows(self, kw, year, target):
        """예전 GUI 전용 SQL 검색. 통합 검색 엔진을 쓸 수 없을 때만 쓴다(읽기 전용 연결)."""
        conn = self.get_db_conn()
        cur = conn.cursor()
        is_choseong = is_choseong_only(kw) if kw else False
        fts_used = False
        if target == "qa":
            rows = []
            # 1. High-speed FTS5 Accelerated Query (Fastest for keywords)
            if kw and not is_choseong:
                fts_kw = clean_fts_query(kw)
                if fts_kw:
                    try:
                        sql = """
                            SELECT q.qa_id, q.request_date, q.requester, q.question_title, q.doc_number 
                            FROM qa_items_fts f 
                            JOIN qa_items q ON f.rowid = q.rowid 
                            WHERE qa_items_fts MATCH ?
                        """
                        params = [fts_kw]
                        if year != "전체":
                            sql += " AND q.year = ?"
                            params.append(year)
                        sql += " ORDER BY q.request_date DESC, q.qa_id ASC LIMIT 500"
                        cur.execute(sql, params)
                        rows = cur.fetchall()
                        if rows:
                            fts_used = True
                    except Exception:
                        rows = []

            # 2. Fallback to LIKE query if FTS5 yielded 0 results or keyword is empty
            if not rows and not fts_used:
                sql = "SELECT qa_id, request_date, requester, question_title, doc_number FROM qa_items WHERE 1=1"
                params = []
                if year != "전체":
                    sql += " AND year = ?"
                    params.append(year)
                if kw and not is_choseong:
                    for t in kw.split():
                        # answer_full은 엑셀 3만 자 잘림본이다. 검색은 정본을 봐야
                        # 대시보드와 같은 결과가 나온다(제안서 S1).
                        sql += " AND (question_title LIKE ? OR answer_markdown LIKE ? OR requester LIKE ?)"
                        kw_wild = f"%{t}%"
                        params.extend([kw_wild, kw_wild, kw_wild])
                sql += " ORDER BY request_date DESC, qa_id ASC LIMIT 500"

                cur.execute(sql, params)
                rows = cur.fetchall()

            # 3. Choseong Filtering
            if kw and is_choseong:
                ch_tokens = kw.split()
                filtered = []
                for r in rows:
                    search_blob = f"{r[2] or ''} {r[3] or ''}"
                    blob_ch = get_choseong(search_blob)
                    if all(ct in blob_ch for ct in ch_tokens):
                        filtered.append(r)
                rows = filtered[:500]
        elif target == "ledger":
            rows = []
            # 1. High-speed FTS5 Accelerated Query for Request Ledger
            if kw and not is_choseong:
                fts_kw = clean_fts_query(kw)
                if fts_kw:
                    try:
                        sql = """
                            SELECT l.ledger_id, l.request_date, l.requester, l.title, l.seq_no 
                            FROM request_ledger_fts f 
                            JOIN request_ledger l ON f.rowid = l.rowid 
                            WHERE request_ledger_fts MATCH ?
                        """
                        params = [fts_kw]
                        if year != "전체":
                            sql += " AND l.year = ?"
                            params.append(year)
                        sql += " ORDER BY l.year DESC, l.request_date DESC, l.seq_no DESC LIMIT 500"
                        cur.execute(sql, params)
                        rows = cur.fetchall()
                        if rows:
                            fts_used = True
                    except Exception:
                        rows = []

            # 2. Fallback to LIKE query for Request Ledger
            if not rows and not fts_used:
                sql = "SELECT ledger_id, request_date, requester, title, seq_no FROM request_ledger WHERE 1=1"
                params = []
                if year != "전체":
                    sql += " AND year = ?"
                    params.append(year)
                if kw and not is_choseong:
                    for t in kw.split():
                        sql += " AND (title LIKE ? OR details LIKE ? OR requester LIKE ? OR party LIKE ? OR seq_no LIKE ? OR note LIKE ?)"
                        kw_wild = f"%{t}%"
                        params.extend([kw_wild, kw_wild, kw_wild, kw_wild, kw_wild, kw_wild])
                sql += " ORDER BY year DESC, request_date DESC, seq_no DESC LIMIT 500"

                cur.execute(sql, params)
                rows = cur.fetchall()

            # 3. Choseong Filtering for Ledger
            if kw and is_choseong:
                ch_tokens = kw.split()
                filtered = []
                for r in rows:
                    search_blob = f"{r[2] or ''} {r[3] or ''}"
                    blob_ch = get_choseong(search_blob)
                    if all(ct in blob_ch for ct in ch_tokens):
                        filtered.append(r)
                rows = filtered[:500]
        else:
            rows = []
            # 1. High-speed FTS5 Accelerated Query for Documents
            if kw and not is_choseong:
                fts_kw = clean_fts_query(kw)
                if fts_kw:
                    try:
                        sql = """
                            SELECT d.doc_id, d.request_date, d.requester, d.title, d.doc_number 
                            FROM documents_fts f 
                            JOIN documents d ON f.rowid = d.rowid 
                            WHERE documents_fts MATCH ?
                        """
                        params = [fts_kw]
                        if year != "전체":
                            sql += " AND d.year = ?"
                            params.append(year)
                        sql += " ORDER BY d.request_date DESC, d.doc_id ASC LIMIT 500"
                        cur.execute(sql, params)
                        rows = cur.fetchall()
                        if rows:
                            fts_used = True
                    except Exception:
                        rows = []

            # 2. Fallback to LIKE query for Documents
            if not rows and not fts_used:
                sql = "SELECT doc_id, request_date, requester, title, doc_number FROM documents WHERE 1=1"
                params = []
                if year != "전체":
                    sql += " AND year = ?"
                    params.append(year)
                if kw and not is_choseong:
                    for t in kw.split():
                        # answer_full_text는 엑셀 3만 자 잘림본이다. 이 열만 보면 본문
                        # 뒷부분에만 있는 낱말을 통째로 놓친다(제안서 S1).
                        sql += " AND (title LIKE ? OR full_markdown LIKE ? OR requester LIKE ? OR question_list LIKE ?)"
                        kw_wild = f"%{t}%"
                        params.extend([kw_wild, kw_wild, kw_wild, kw_wild])
                sql += " ORDER BY request_date DESC, doc_id ASC LIMIT 500"

                cur.execute(sql, params)
                rows = cur.fetchall()

            # 3. Choseong Filtering for Documents
            if kw and is_choseong:
                ch_tokens = kw.split()
                filtered = []
                for r in rows:
                    search_blob = f"{r[2] or ''} {r[3] or ''}"
                    blob_ch = get_choseong(search_blob)
                    if all(ct in blob_ch for ct in ch_tokens):
                        filtered.append(r)
                rows = filtered[:500]

        return [
            {"id": r[0], "date": r[1] or "", "requester": r[2] or "",
             "title": (r[3] or "").strip().replace("\n", " "),
             "num": r[4] or "", "deadline": "", "status": "", "snippet": ""}
            for r in rows
        ]

    def reset_search(self):
        self.kw_entry.delete(0, tk.END)
        self.year_combo.set("전체")
        self.do_search()

    def load_item_detail(self, selected_id):
        match = self.current_rows_map.get(selected_id)
        if not match:
            return

        row_id, date, req, title, doc_num = match["id"], match["date"], match["requester"], match["title"], match["num"]
        self.det_title_lbl.config(text=f"[{row_id}] {title}")
        num_label = "연번" if row_id.startswith("REQ-") else "문서번호"
        meta_str = f"요구일자: {date or '미상'} | 요구자: {req or '미분류'} | {num_label}: ({doc_num or '-'})"
        self.det_meta_lbl.config(text=meta_str)

        # On-demand lazy fetch with LRU cache
        if row_id in self.detail_cache:
            body = self.detail_cache[row_id]
        else:
            try:
                conn = self.get_db_conn()
                cur = conn.cursor()
                if row_id.startswith("REQ-"):
                    cur.execute("SELECT details, note, status, deadline, department, submit_date, linked_doc_id FROM request_ledger WHERE ledger_id = ?", (row_id,))
                    r_det = cur.fetchone()
                    if r_det:
                        body = f"[세부 요구내역]\n{r_det[0] or '(세부 요구내역 없음)'}\n\n" \
                               f"■ 진행상태: {r_det[2] or '-'}  |  담당부서: {r_det[4] or '-'}\n" \
                               f"■ 요구일: {date or '-'}  |  마감일: {r_det[3] or '-'}  |  제출일: {r_det[5] or '-'}\n" \
                               f"■ 연계 공문서: {r_det[6] or '없음'}\n" \
                               f"■ 비고: {r_det[1] or '-'}"
                    else:
                        body = ""
                elif "-Q" in row_id:
                    # 문서 상세와 같은 이유로 Q&A 상세도 정본을 쓴다. answer_full은
                    # 엑셀 전용 잘림본이라 긴 답변이 중간에서 끊겨 보였다.
                    cur.execute("SELECT answer_markdown FROM qa_items WHERE qa_id = ?", (row_id,))
                    r_det = cur.fetchone()
                    body = r_det[0] if r_det and r_det[0] else ""
                else:
                    # answer_full_text is Excel's 30,000-character export field;
                    # the GUI detail view must use the canonical full text.
                    cur.execute("SELECT full_markdown FROM documents WHERE doc_id = ?", (row_id,))
                    r_det = cur.fetchone()
                    body = r_det[0] if r_det and r_det[0] else ""
                if len(self.detail_cache) > 250:
                    self.detail_cache.pop(next(iter(self.detail_cache)))
                self.detail_cache[row_id] = body
            except Exception as e:
                body = f"[상세 본문 로드 오류: {e}]"

        self.det_text.delete("1.0", tk.END)
        self.det_text.insert("1.0", body or "본문 내용이 없습니다.")
        self.det_text.see("1.0")
        # 검색어를 본문에서 강조하고 첫 일치 위치로 이동한다(제안서 G3).
        self.highlight_detail(highlight_terms(getattr(self, "current_keyword", "")))

    def on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        self.load_item_detail(sel[0])

    def run_smart_update(self, selected_files=None):
        progress_win = tk.Toplevel(self.root)
        progress_win.title("DB 및 대시보드 자동 갱신 중")
        progress_win.geometry("450x170")
        progress_win.resizable(False, False)
        progress_win.transient(self.root)
        progress_win.grab_set()

        self.root.update_idletasks()
        rx = self.root.winfo_x()
        ry = self.root.winfo_y()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        progress_win.geometry(f"+{rx + (rw - 450)//2}+{ry + (rh - 170)//2}")

        status_lbl = tk.Label(
            progress_win,
            text="⏳ 신규 문서 감지 및 고속 증분 파싱 준비 중...",
            font=("Malgun Gothic", 10, "bold"),
            fg="#1e293b",
            pady=16
        )
        status_lbl.pack()

        pbar = ttk.Progressbar(progress_win, mode="determinate", length=360, maximum=100)
        pbar.pack(pady=4)
        pbar["value"] = 10

        def update_ui_status(msg, pct):
            status_lbl.config(text=f"⏳ {msg}")
            pbar["value"] = pct

        # 워커 스레드는 Tk 위젯을 직접 건드리지 않고 큐에 사건만 넣는다. 메인 스레드가 큐를 읽어
        # 화면에 반영한다. 예전에는 워커가 progress_win.after()를 직접 불러, 사용자가 진행 창을
        # 닫으면 TclError가 파이프라인 안으로 올라가 DB 교체 뒤·대시보드 재생성 전 같은 중간
        # 단계에서 갱신이 멈췄다. (감사 R3-19b)
        ui_events = queue.Queue()

        def _refuse_close():
            messagebox.showinfo(
                "갱신 진행 중",
                "DB·대시보드 갱신이 진행 중입니다. 끝나면 창이 자동으로 닫힙니다.",
                parent=progress_win,
            )

        progress_win.protocol("WM_DELETE_WINDOW", _refuse_close)

        def worker():
            err_msg = None
            result_data = None
            try:
                def cb(msg, pct):
                    ui_events.put(("status", msg, pct))

                scripts_path = BASE_DIR / "scripts"
                if str(scripts_path) not in sys.path:
                    sys.path.insert(0, str(scripts_path))

                import add_documents_smart
                result_data = add_documents_smart.run_smart_add(selected_files, status_callback=cb)
            except Exception as e:
                err_msg = str(e)

            def on_finish():
                # err_msg에 대입하므로 nonlocal이 없으면 첫 참조에서 UnboundLocalError가 난다.
                # 그 경우 완료/오류 안내도, DB 재적재(init_db_and_load)도 실행되지 않는다.
                nonlocal err_msg
                try:
                    progress_win.grab_release()
                    progress_win.destroy()
                except Exception:
                    pass

                if not err_msg and isinstance(result_data, dict) and not result_data.get("success", True):
                    # 파이프라인이 예외 없이 실패로 끝난 경우에도 실패를 알린다.
                    err_msg = result_data.get("error") or (
                        "파이프라인 단계가 실패하여 기존 산출물을 유지했습니다.\n"
                        "scripts 폴더의 parsing_report.json에서 실패한 문서를 확인하세요."
                    )

                if err_msg:
                    guide_msg = (
                        f"DB 갱신 중 오류가 발생했습니다:\n\n{err_msg}\n\n"
                        "※ 신규 한글(HWP/HWPX) 문서 자동 변환 및 동기화는\n"
                        "Python 및 kordoc 변환기가 구성된 관리자 환경에서 지원됩니다.\n"
                        "일반 열람 환경에서는 기구축된 SQLite DB, 웹 대시보드(HTML), 엑셀(XLSX) 검색 기능을 즉시 이용하실 수 있습니다."
                    )
                    messagebox.showerror("자료 갱신 안내", guide_msg)
                else:
                    assert isinstance(result_data, dict)
                    new_cnt = result_data.get("newly_added_count", 0)
                    total_docs = result_data.get("total_docs", 0)
                    total_qa = result_data.get("total_qa", 0)
                    elapsed = result_data.get("elapsed_seconds", 0)

                    warn_list = result_data.get("warnings") or []
                    warn_block = ""
                    if warn_list:
                        warn_block = (
                            "\n\n⚠️ 일부 문서는 처리하지 못했습니다:\n"
                            + "\n".join(f"• {w}" for w in warn_list[:5])
                            + "\n(상세 원인: parsing_report.json)"
                        )

                    info_msg = (
                        f"🎉 DB 및 웹 대시보드 자동 갱신 완료!\n\n"
                        f"• 새로 등록된 파일: {new_cnt}개\n"
                        f"• 총 관리 문서: {total_docs}건\n"
                        f"• 세부 질의(Q&A): {total_qa:,}건\n"
                        f"• 소요 시간: {elapsed:.1f}초\n\n"
                        f"웹 대시보드(HTML) 및 엑셀 통합 DB(XLSX)에도 최신 내용이 즉시 반영되었습니다."
                        f"{warn_block}"
                    )
                    if warn_list:
                        messagebox.showwarning("갱신 완료 (일부 문서 제외)", info_msg)
                    else:
                        messagebox.showinfo("갱신 완료", info_msg)
                    self.close_db_conn()
                    self.init_db_and_load()

            def safe_on_finish():
                # Tk after 콜백에서 난 예외는 조용히 삼켜져 사용자가 결과를 전혀 못 본다.
                try:
                    on_finish()
                except Exception as ui_err:
                    try:
                        messagebox.showerror(
                            "자료 갱신 안내",
                            f"갱신 결과를 표시하는 중 오류가 발생했습니다:\n\n{ui_err}\n\n"
                            "데이터 갱신 자체는 완료되었을 수 있습니다. 프로그램을 다시 실행해 확인하세요."
                        )
                    except Exception:
                        pass

            ui_events.put(("finish", safe_on_finish))

        def pump_ui_events():
            try:
                while True:
                    event = ui_events.get_nowait()
                    if event[0] == "status":
                        try:
                            if progress_win.winfo_exists():
                                update_ui_status(event[1], event[2])
                        except tk.TclError:
                            pass
                    elif event[0] == "finish":
                        event[1]()
                        return
            except queue.Empty:
                pass
            self.root.after(100, pump_ui_events)

        # Release active SQLite file handles so Windows os.replace won't raise PermissionError
        self.close_db_conn()
        th = threading.Thread(target=worker, daemon=True)
        th.start()
        self.root.after(100, pump_ui_events)

def main():
    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
