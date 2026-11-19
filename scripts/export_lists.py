"""나이별 추천 목록 CSV (도서관 갈 때 쓰는 용도). data/lists/age{a}_fit.csv, age{a}_pop.csv"""
import sys
from pathlib import Path
import pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]; PROC = ROOT / "data" / "processed"; OUT = ROOT / "data" / "lists"
books = pd.read_parquet(PROC / "books_metrics.parquet").set_index("isbn13", drop=False)
ranks = pd.read_parquet(PROC / "age_ranks.parquet")
OUT.mkdir(exist_ok=True)
from export_site import dedupe_editions  # noqa: E402  (같은 책 다른 판은 한 권만)
from groups import build_groups  # noqa: E402
tags_p = PROC / "book_tags.parquet"
gb = books.join(pd.read_parquet(tags_p).set_index("isbn13")[["form", "publisher_norm"]]) if tags_p.exists() else books
wk = build_groups(gb).wk
cols = ["bookname", "authors", "publisher", "publication_year", "band", "peak_age", "shape", "is_picture", "total_loans"]
for a in sorted(ranks.age.unique()):
    r = ranks[ranks.age == a]
    for kind, sel in (("fit", dedupe_editions(r[r.fit_rank.notna()].nsmallest(300, "fit_rank"), wk, 100)),
                      ("pop", dedupe_editions(r[r.observed & (r.cent >= 0.2)].nsmallest(300, "pop_rank"), wk, 100))):
        df = sel[["isbn13", "loan_count", "cent", "pop_score", "fit_score"]].merge(books[cols], left_on="isbn13", right_index=True)
        df.insert(0, "rank", range(1, len(df) + 1))
        df.rename(columns={"loan_count": f"{a}세_대출", "cent": "적합도", "pop_score": "인기점수(50)", "fit_score": "추천도(100)",
                           "bookname": "제목", "authors": "저자", "publisher": "출판사",
                           "publication_year": "출판년", "band": "추천연령", "peak_age": "최다선택나이", "shape": "분포", "is_picture": "그림책",
                           "total_loans": "총대출"}).to_csv(OUT / f"age{a}_{kind}.csv", index=False, encoding="utf-8-sig")
# 연도별 대출순 (build_years.py, PLAN 10절): age{a}_pop_{연도}.csv — 그 해 그 나이 대출순 상위 100권,
# 전년 대비 변동(전년 자료가 있는 해만)과 다른 해 순위 전부(순위 밖이면 빈칸)
yp = PROC / "year_ranks.parquet"
if yp.exists():
    from build_years import hist_years  # noqa: E402
    yr = pd.read_parquet(yp)
    hy = hist_years(yr)
    yr = yr[yr["rank"].notna()]
    for (y, a), g in yr.groupby(["year", "age"]):
        others = [f"rank_{x}" for x in hy if x != y]
        df = g.nsmallest(100, "rank")[["rank", "isbn13", "loan", "prev_rank", *others, "cent"]].merge(books[cols], left_on="isbn13", right_index=True)
        df["rank"] = df["rank"].astype(int)
        if y - 1 in hy:
            df.insert(3, "변동", (df.prev_rank - df["rank"]).map(lambda v: "새로 진입" if pd.isna(v) else f"{int(v):+d}" if v else "0"))
        df = df.drop(columns=["prev_rank"])
        df[others] = df[others].astype("Int64")
        df.rename(columns={"loan": f"{y}년_{a}세_대출", **{f"rank_{x}": f"{x}년_순위" for x in hy}, "cent": "적합도",
                           "bookname": "제목", "authors": "저자", "publisher": "출판사",
                           "publication_year": "출판년", "band": "추천연령", "peak_age": "최다선택나이", "shape": "분포", "is_picture": "그림책",
                           "total_loans": "총대출"}).to_csv(OUT / f"age{a}_pop_{y}.csv", index=False, encoding="utf-8-sig")
print("saved:", sorted(p.name for p in OUT.glob("*.csv"))[:6], "...")
