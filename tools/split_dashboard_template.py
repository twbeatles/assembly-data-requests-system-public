# -*- coding: utf-8 -*-
"""레거시 dashboard_template.html을 templates/dashboard 파트로 나눈다.

줄 단위로 잘라 옮기므로 함수 본문을 재작성하지 않는다.
한 번 나눈 뒤에는 파트가 정본이고, dashboard_template.html은 조립 캐시다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SRC = SCRIPTS / "templates" / "dashboard_template.html"
OUT = SCRIPTS / "templates" / "dashboard"

# 원본 파일 1-based 줄 번호 (포함). JS는 <script> 안쪽만.
JS_PARTS = [
    ("js/00_boot.js", 1622, 1725),
    ("js/01_search.js", 1726, 1855),
    ("js/02_state.js", 1856, 1878),
    ("js/03_ui.js", 1879, 2057),
    ("js/04_search_tokens.js", 2058, 2155),
    ("js/05_sanitize.js", 2156, 2356),
    ("js/06_ledger_due.js", 2357, 2532),
    ("js/07_excel_status.js", 2533, 2705),
    ("js/08_prefs.js", 2706, 2858),
    ("js/09_filter.js", 2859, 3081),
    ("js/10_ledger_crud.js", 3082, 3781),
    ("js/11_render.js", 3782, 4183),
    ("js/12_modal.js", 4184, 4551),
    ("js/13_events.js", 4552, 4745),
]


def main() -> int:
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines:
        print("빈 템플릿", file=sys.stderr)
        return 1

    pre, sep, rest = text.partition("<style>")
    if not sep:
        print("<style> 없음", file=sys.stderr)
        return 1
    css, sep2, rest = rest.partition("</style>")
    if not sep2:
        print("</style> 없음", file=sys.stderr)
        return 1

    marker = '<script id="COMPRESSED_DATA" type="text/plain">__B64_GZIP_DATA__</script>'
    idx = rest.find(marker)
    if idx < 0:
        print("COMPRESSED_DATA 블록 없음", file=sys.stderr)
        return 1
    after_data = rest[idx + len(marker):]
    app_open = after_data.find("<script>")
    app_close = after_data.rfind("</script>")
    if app_open < 0 or app_close < 0:
        print("앱 스크립트 블록 없음", file=sys.stderr)
        return 1
    js = after_data[app_open + len("<script>"):app_close]
    head_body = rest[: idx + len(marker)]
    prefix_ws = after_data[:app_open]
    tail = after_data[app_close + len("</script>"):]
    shell = pre + "<!-- __DASHBOARD_CSS__ -->" + head_body + prefix_ws + "<!-- __DASHBOARD_JS__ -->" + tail

    (OUT / "js").mkdir(parents=True, exist_ok=True)
    (OUT / "shell.html").write_text(shell, encoding="utf-8")
    (OUT / "styles.css").write_text(css, encoding="utf-8")

    js_manifest = []
    for rel, start, end in JS_PARTS:
        chunk = "".join(lines[start - 1:end])
        path = OUT / rel
        path.write_text(chunk, encoding="utf-8")
        js_manifest.append(rel.replace("\\", "/"))

    joined_js = "".join("".join(lines[s - 1:e]) for _, s, e in JS_PARTS)
    if joined_js != "".join(lines[1621:4745]):
        print("JS 파트 연결이 원본 1622-4745줄과 다릅니다", file=sys.stderr)
        return 1
    if js != joined_js and js.strip("\n") != joined_js.strip("\n"):
        # 허용: 원본 <script> 안쪽과 줄 슬라이스가 개행만 다른 경우
        if js.replace("\r\n", "\n") != joined_js.replace("\r\n", "\n"):
            print("JS 추출 내용이 원본 스크립트와 다릅니다", file=sys.stderr)
            return 1

    manifest = {"css": ["styles.css"], "js": js_manifest}
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT}")
    print(f"  css {len(css)} chars, js parts {len(js_manifest)}, shell {len(shell)} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
