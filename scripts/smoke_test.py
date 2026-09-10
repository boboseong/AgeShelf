"""키 활성화 후 첫 검증. 실행: python scripts/smoke_test.py

확인 항목
 1. 키가 활성 상태인가 (libSrch)
 2. from_age=to_age 단일 연령 조회가 age=0(0~5세 코드)과 실제로 다른 결과를 주는가
 3. 연령끼리(1세 vs 4세) 상위 목록이 얼마나 겹치는가 (겹침이 크면 해상도가 없는 것)
 4. pageSize 상한과 5,000건 상한, 전체기간 컷오프 심각도
 5. addCode=7(아동) 필터가 동작하는가
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

import naru  # noqa: E402

PERIOD = dict(start="2025-09-01", end="2026-08-31")


def show(label, rows, n=8):
    print(f"\n=== {label}  ({len(rows)}건)")
    for d in rows[:n]:
        print(f"  #{d.get('ranking'):>4} {d.get('loan_count', 0):>7}  [{d.get('addition_symbol') or '-----'}] "
              f"{(d.get('bookname') or '')[:40]} / {d.get('publisher')}")


def jaccard(a, b):
    a, b = {d["isbn13"] for d in a}, {d["isbn13"] for d in b}
    return len(a & b) / max(1, len(a | b))


def main():
    # 1. 활성화 확인
    try:
        naru.call("libSrch", pageSize=1, cache=False)
        print("[1] 키 활성 OK")
    except naru.NaruError as e:
        print(f"[1] 키 사용 불가 → {e}\n    마이페이지에서 승인 상태와 호출서버 IP 등록을 확인하세요.")
        return

    # 2. 단일 연령 vs 코드
    by_age = {}
    for a in (0, 1, 2, 4, 7):
        by_age[a] = naru.popular(from_age=a, to_age=a, page_size=100, cache=False, **PERIOD)
        show(f"from_age={a}&to_age={a}", by_age[a])
    code0 = naru.popular(age=0, page_size=100, cache=False, **PERIOD)
    show("age=0 (0~5세 코드)", code0)

    # 3. 연령 간 겹침
    print("\n[3] 상위 100권 Jaccard 유사도 (낮을수록 연령 해상도가 있음)")
    for x, y in [(0, 1), (1, 2), (2, 4), (4, 7), (0, 7)]:
        print(f"  {x}세 vs {y}세: {jaccard(by_age[x], by_age[y]):.2f}")
    print(f"  2세 vs age=0코드: {jaccard(by_age[2], code0):.2f}")

    # 4. pageSize 상한 / 총량
    print("\n[4] pageSize 상한 탐색 (2세, addCode=7)")
    for ps in (500, 1000, 2000, 5000):
        rows = naru.popular(from_age=2, to_age=2, add_code=7, page_size=ps, cache=False, **PERIOD)
        tail = rows[-1] if rows else {}
        print(f"  pageSize={ps}: {len(rows)}건, 마지막 rank={tail.get('ranking')} loan_count={tail.get('loan_count')}")
        if len(rows) < ps:
            break

    # 4b. 전체기간 컷오프 심각도: 2세 5,000위 대출수 vs 1위
    rows = naru.popular_all(from_age=2, to_age=2, add_code=7, page_size=500, cache=False,
                            start="2014-01-01", end=PERIOD["end"])
    if rows:
        print(f"\n[4b] 전체기간 2세 아동서: {len(rows)}건 수집, 1위 {rows[0]['loan_count']:,} / "
              f"마지막 {rows[-1]['loan_count']:,} → 컷오프가 높으면 --gender/--kdc 분할 수집 필요")

    # 5. addCode 필터
    plain = naru.popular(from_age=2, to_age=2, page_size=100, cache=False, **PERIOD)
    kids = naru.popular(from_age=2, to_age=2, add_code=7, page_size=100, cache=False, **PERIOD)
    non_kids = [d for d in plain if not (d.get("addition_symbol") or "").startswith("7")]
    print(f"\n[5] 2세 상위 100 중 부가기호가 아동(7xxxx)이 아닌 책: {len(non_kids)}권 (가족 카드 오염 추정치)")
    show("  예시", non_kids, 5)
    print(f"    addCode=7 적용 시 상위 100 전부 아동 여부: "
          f"{all((d.get('addition_symbol') or '').startswith('7') for d in kids)}")
    picture = sum(1 for d in kids if (d.get("addition_symbol") or "")[1:2] == "7")
    print(f"    아동 상위 100 중 그림책(77xxx) 비율: {picture}%")


if __name__ == "__main__":
    main()
