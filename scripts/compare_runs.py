"""무분할(plain) vs 지역분할(region) 결과 비교: 커버리지, 관측률, 상위 목록 겹침, 알려진 책 밴드 변화.

실행: python scripts/compare_runs.py
  (build_metrics 를 두 입력으로 각각 돌려 books_metrics_plain / books_metrics 로 저장해 두었다고 가정)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
KNOWN = ["달님 안녕", "사과가 쿵", "두드려 보아요", "누가 내 머리에 똥 쌌어", "구름빵", "수박 수영장", "당근 유치원",
         "흔한남매", "긴긴밤", "변기에 쉬해요", "안돼, 데이빗", "곰 사냥을 떠나자", "덤프차가 꽈당"]


def load(tag: str):
    b = pd.read_parquet(PROC / f"books_metrics{tag}.parquet").set_index("isbn13")
    r = pd.read_parquet(PROC / f"age_ranks{tag}.parquet")
    return b, r


def main():
    bp, rp = load("_plain")
    br, rr = load("")
    ages = sorted(int(c[5:]) for c in br.columns if c.startswith("prof_"))
    print(f"도서 수: 무분할 {len(bp):,} → 지역분할 {len(br):,}")
    print("나이별 관측률(책 기준)  무분할 → 지역분할")
    for a in ages:
        print(f"  {a:>2}세: {bp[f'obs_{a}'].mean():.0%} → {br[f'obs_{a}'].mean():.0%}   "
              f"코호트 총량 {int(bp[f'loan_{a}'].sum()):>10,} → {int(br[f'loan_{a}'].sum()):>10,}")
    print(f"관측 연령 수 중앙값: {bp.observed_ages.median():.0f} → {br.observed_ages.median():.0f}")

    for a in (1, 2, 3, 4):
        tp = set(rp[(rp.age == a) & rp.fit_rank.notna()].nsmallest(30, "fit_rank").isbn13)
        tr = set(rr[(rr.age == a) & rr.fit_rank.notna()].nsmallest(30, "fit_rank").isbn13)
        print(f"{a}세 fit TOP30 겹침: {len(tp & tr)}/30")

    print("\n알려진 책: 밴드/peak/lift@2  무분할 → 지역분할")
    for name in KNOWN:
        hp = bp[bp.bookname.str.contains(name, regex=False, na=False)].sort_values("total_loans", ascending=False)
        hr = br[br.bookname.str.contains(name, regex=False, na=False)].sort_values("total_loans", ascending=False)
        if hp.empty or hr.empty:
            print(f"  {name:<12} (한쪽에 없음)"); continue
        p, r = hp.iloc[0], hr.iloc[0]
        print(f"  {name:<12} {p.band:>7} peak{int(p.peak_age):>2} lift {p['lift_2']:5.2f} obs{int(p.observed_ages):>2}  →  "
              f"{r.band:>7} peak{int(r.peak_age):>2} lift {r['lift_2']:5.2f} obs{int(r.observed_ages):>2}  총대출 {int(p.total_loans):,}→{int(r.total_loans):,}")

    # 지역분할에서 새로 fit TOP30 에 들어온 2세 책
    tp = rp[(rp.age == 2) & rp.fit_rank.notna()].nsmallest(30, "fit_rank")
    tr = rr[(rr.age == 2) & rr.fit_rank.notna()].nsmallest(30, "fit_rank")
    new = tr[~tr.isbn13.isin(tp.isbn13)].merge(br[["bookname", "band"]], left_on="isbn13", right_index=True)
    print(f"\n2세 TOP30 에 새로 들어온 책 ({len(new)}권):")
    for _, x in new.iterrows():
        print(f"  {int(x.loan_count):>6} lift {x.lift:5.2f} {x.band:>7} {x.bookname[:36]}")


if __name__ == "__main__":
    main()
