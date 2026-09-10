"""나이별 추천 목록 CSV (도서관 갈 때 쓰는 용도). data/lists/age{a}_fit.csv, age{a}_pop.csv"""
import sys
from pathlib import Path
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]; PROC = ROOT / "data" / "processed"; OUT = ROOT / "data" / "lists"
books = pd.read_parquet(PROC / "books_metrics.parquet").set_index("isbn13", drop=False)
ranks = pd.read_parquet(PROC / "age_ranks.parquet")
OUT.mkdir(exist_ok=True)
from export_site import diversify  # noqa: E402  (시리즈당 최대 3권)
cols = ["bookname", "authors", "publisher", "publication_year", "band", "peak_age", "shape", "is_picture", "total_loans"]
for a in sorted(ranks.age.unique()):
    r = ranks[ranks.age == a]
    for kind, sel in (("fit", diversify(r[r.fit_rank.notna()].nsmallest(300, "fit_rank"), books, 100)),
                      ("pop", diversify(r[r.observed & (r.cent >= 0.2)].nsmallest(300, "pop_rank"), books, 100))):
        df = sel[["isbn13", "loan_count", "cent", "pop_score", "fit_score"]].merge(books[cols], left_on="isbn13", right_index=True)
        df.insert(0, "rank", range(1, len(df) + 1))
        df.rename(columns={"loan_count": f"{a}세_대출", "cent": "적합도", "pop_score": "인기점수(50)", "fit_score": "추천도(100)",
                           "bookname": "제목", "authors": "저자", "publisher": "출판사",
                           "publication_year": "출판년", "band": "추천연령", "peak_age": "최다선택나이", "shape": "분포", "is_picture": "그림책",
                           "total_loans": "총대출"}).to_csv(OUT / f"age{a}_{kind}.csv", index=False, encoding="utf-8-sig")
print("saved:", sorted(p.name for p in OUT.glob("*.csv"))[:6], "...")
