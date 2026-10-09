# -*- coding: utf-8 -*-
"""GUI 외부 액션 믹스인(SRP: 복사·대시보드/엑셀/폴더 열기·자료 추가)."""
import os
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from launcher_gui import LauncherApp

    _HostBase_ActionsMixin = LauncherApp
else:
    _HostBase_ActionsMixin = object


class ActionsMixin(_HostBase_ActionsMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def copy_current_text(self):
        text = self.det_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        # 복사할 때마다 확인 창을 띄우면 작업 흐름이 끊긴다. 상태 표시줄로 알린다(제안서 G4).
        if hasattr(self, "flash_status"):
            self.flash_status(f"✓ 본문 {len(text):,}자를 복사했습니다. 한글(HWP)·메신저에 Ctrl+V로 붙여넣으세요.")
        else:
            messagebox.showinfo("복사 완료", "내용이 클립보드에 복사되었습니다.")
    def open_current_in_web(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("안내", "대시보드에서 열 항목을 목록에서 먼저 선택해주세요.")
            return
        selected_id = sel[0]
        if selected_id.startswith("REQ-"):
            target_hash = f"#ledger={selected_id}"
        elif "-Q" in selected_id:
            target_hash = f"#qa={selected_id}"
        else:
            target_hash = f"#doc={selected_id}"

        if lg.HTML_PATH.exists():
            from ui_guard import should_open_browser
            if should_open_browser():
                webbrowser.open(f"file:///{lg.HTML_PATH.resolve().as_posix()}{target_hash}")
        else:
            messagebox.showerror("파일 없음", f"웹 대시보드 파일을 찾을 수 없습니다:\n{lg.HTML_PATH}")
    def on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.open_current_in_web()
    def open_html_dashboard(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        if lg.HTML_PATH.exists():
            from ui_guard import should_open_browser
            if should_open_browser():
                webbrowser.open(f"file:///{lg.HTML_PATH.resolve().as_posix()}")
        else:
            messagebox.showerror("파일 없음", f"웹 대시보드 파일을 찾을 수 없습니다:\n{lg.HTML_PATH}")
    def open_excel_db(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        if lg.EXCEL_PATH.exists():
            try:
                os.startfile(str(lg.EXCEL_PATH))
            except Exception as e:
                messagebox.showerror("실행 실패", f"엑셀 파일을 열 수 없습니다: {e}")
        else:
            messagebox.showerror("파일 없음", f"엑셀 파일을 찾을 수 없습니다:\n{lg.EXCEL_PATH}")
    def open_manual(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        if lg.MANUAL_PATH.exists():
            from ui_guard import should_open_browser
            if should_open_browser():
                webbrowser.open(f"file:///{lg.MANUAL_PATH.resolve().as_posix()}")
        elif (lg.BASE_DIR / "README_배포안내.txt").exists():
            os.startfile(str(lg.BASE_DIR / "README_배포안내.txt"))
        else:
            messagebox.showinfo("안내", "사용설명서 파일을 찾을 수 없습니다.")
    def open_folder(self):
        import launcher_gui as lg  # 지연 import: 진입점 전역 패치를 그대로 본다.
        try:
            os.startfile(str(lg.BASE_DIR))
        except Exception as e:
            messagebox.showerror("오류", f"폴더를 열 수 없습니다: {e}")
    def on_click_add_documents(self):
        files = filedialog.askopenfilenames(
            title="새로 추가할 국회·대외기관 요구자료 선택 (HWP, HWPX, PDF, XLSX)",
            filetypes=[
                ("지원 문서 (HWP, HWPX, XLSX, PDF)", "*.hwp *.hwpx *.xlsx *.pdf"),
                ("한글 문서 (*.hwp, *.hwpx)", "*.hwp *.hwpx"),
                ("엑셀 문서 (*.xlsx)", "*.xlsx"),
                ("PDF 문서 (*.pdf)", "*.pdf"),
                ("모든 파일 (*.*)", "*.*")
            ]
        )
        if files:
            preview_list = "\n".join([f"• {Path(f).name}" for f in files[:4]])
            if len(files) > 4:
                preview_list += f"\n... 외 {len(files) - 4}개"

            confirm = messagebox.askyesno(
                "신규 자료 등록 확인",
                f"선택하신 {len(files)}개의 신규 문서를 시스템에 등록하고\n"
                "고속 파싱 및 DB/웹 대시보드 갱신을 진행하시겠습니까?\n\n"
                f"[선택된 파일 목록]:\n{preview_list}"
            )
            if not confirm:
                return
            self.run_smart_update(selected_files=files)
        else:
            confirm = messagebox.askyesno(
                "신규 자료 자동 감지 및 DB 갱신",
                "선택된 파일이 없습니다.\n\n"
                "'새자료_투입폴더' 또는 연도별 폴더에 새로 넣어둔\n"
                "문서를 자동 감지하여 DB와 웹 대시보드를 갱신하시겠습니까?"
            )
            if confirm:
                self.run_smart_update(selected_files=None)
