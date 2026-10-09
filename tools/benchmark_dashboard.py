"""Synthetic dashboard benchmark; never reads production documents or databases.

Run: python tools/benchmark_dashboard.py --documents 1000 --body-chars 32768
"""
import argparse
import json
from pathlib import Path
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from template_renderer import DashboardRenderer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=200)
    parser.add_argument("--body-chars", type=int, default=16384)
    args = parser.parse_args()
    if not 1 <= args.documents <= 10000 or not 100 <= args.body_chars <= 1000000:
        parser.error("documents: 1..10000; body-chars: 100..1000000")
    tracemalloc.start()
    start = time.perf_counter()
    documents = []
    for i in range(args.documents):
        body = (f"문서 {i} 국회 자료 응답 본문 가나다라 {i * 7919}\n" * (args.body_chars // 20 + 1))[:args.body_chars]
        documents.append(dict(doc_id=f"SYNTH-{i}", title=f"synthetic {i}", full_markdown=body))
    renderer = DashboardRenderer()
    raw = dict(documents=documents, qa_items=[], request_ledger=[])
    slim = renderer.build_slim_payload(raw)
    meta, bodies = renderer.encode_payload_parts(slim, threshold=1000000, chunk_chars=1000000)
    html = renderer.render(renderer.load_template(), "", meta, {"department_name": "합성 벤치마크"}, bodies)
    encoded = time.perf_counter()
    decoded = renderer.decode_dashboard_payload(html)
    assert [d["full_markdown"] for d in decoded["documents"]] == [d["full_markdown"] for d in documents]
    end = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps(dict(synthetic=True, python=sys.version.split()[0], documents=args.documents,
                         body_chars=args.body_chars, body_blocks=len(bodies), html_bytes=len(html.encode("utf-8")),
                         encode_seconds=round(encoded-start, 3), decode_seconds=round(end-encoded, 3),
                         python_peak_mib=round(peak / 1024**2, 2), full_text_equal=True), ensure_ascii=False))


if __name__ == "__main__":
    main()
