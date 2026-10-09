# -*- coding: utf-8 -*-
from typing import Any, Optional, cast
import sys
import json
from collections import Counter
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent


def main(json_path: Optional[Path] = None):
    target = Path(json_path) if json_path else (ROOT / 'data_requests.json')
    if not target.exists():
        print('data_requests.json 이 없어 진단을 건너뜁니다.')
        return
    data = json.loads(target.read_text(encoding='utf-8'))
    docs = data.get('documents', [])
    qa = data.get('qa_items', [])

    print('=== 데이터 통계 진단 ===')
    print(f'문서 총계: {len(docs)}개')
    print(f'Q&A 총계: {len(qa)}개')

    req_counter = Counter(d.get('requester', '미분류') for d in docs)
    print('\n[요구자 TOP 12]:')
    for k, v in req_counter.most_common(12):
        print(f'  - {k}: {v}건')

    no_date_docs = [d for d in docs if not d.get('request_date')]
    print(f'\n날짜 미추출 문서: {len(no_date_docs)}건')
    if no_date_docs:
        print('  예시 3건:')
        for d in no_date_docs[:3]:
            print(f"   * [{d['doc_id']}] {d['title']} ({d['original_path']})")

    unknown_req_docs = [d for d in docs if d.get('requester') == '미분류']
    print(f'\n요구자 미분류 문서: {len(unknown_req_docs)}건')
    if unknown_req_docs:
        print('  예시 5건:')
        for d in unknown_req_docs[:5]:
            print(f"   * [{d['doc_id']}] {d['title']} ({d['original_path']})")

    all_tags = []
    for d in docs:
        tags = [t.strip() for t in d.get('topic_tags', '').split(',') if t.strip()]
        all_tags.extend(tags)
    print('\n[주제 태그 빈도 TOP 15]:')
    for k, v in Counter(all_tags).most_common(15):
        print(f'  - {k}: {v}건')

    qa_per_doc = Counter(q.get('doc_id') for q in qa)
    single_qa = [k for k, v in qa_per_doc.items() if v == 1]
    print(f'\n단일 Q&A 문서: {len(single_qa)}개')
    print('\n단일 Q&A 문서 중 실제로는 여러 질문이 있을 가능성 있는 문서 샘플:')
    sample_checked = 0
    for d in docs:
        if d['doc_id'] in single_qa:
            q_list = d.get('question_list', '')
            q_cnt = len([l for l in q_list.split('\n') if l.strip()])
            if q_cnt > 1:
                print(f"   * [{d['doc_id']}] {d['title']} -> question_list에는 {q_cnt}개 질문 감지됨!")
                sample_checked += 1
                if sample_checked >= 5:
                    break


if __name__ == '__main__':
    main()
