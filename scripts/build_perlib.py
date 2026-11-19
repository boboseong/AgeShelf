"""도서관당 대출 순위 → data/processed/perlib_ranks.parquet  (PLAN.md 11절)

실행: python scripts/build_perlib.py        # loans_long_years.parquet(연도별 대출) + holdings.parquet(전국 소장) 필요

대출순은 많이 비치된 책이 유리하다. 최근 WINDOW(3)개 연도의 그 나이 카드 대출을 전국 소장 도서관 수로 나눠
'소장한 도서관 100곳당 그 나이 아이가 1년에 빌린 횟수'로 줄을 세운다.

  분자     build_years.clean_years 의 값(잡음 제거 + 급등 제외, 수록 하한 적용 전)을 최근 WINDOW 개 연도 합산.
           같은 책 다른 판(groups 의 wk)은 한 권으로 합친다. 대표 판 = 그 나이 대출이 가장 많은 판.
  분모     판 묶음의 소장 도서관 합집합(holdings.parquet, 소장 없음 행 제외) × 연수.
           연수 = 창 안에서 '시작 해' 이후의 해 수(1~WINDOW). 시작 해 = min(판들의 가장 이른 출간 연도, 연도 자료에 처음 나온 해).
           시작한 해도 1년으로 세므로 신간은 낮게 잡힌다(소장도 지금 시점이라 대출 당시보다 많다 — 같은 방향).
  자격     사이트 수록(총 대출 30건 이상) · 대표 판의 그 나이 연령 적합도 20% 이상('많이 빌린 책'과 같은 문턱) · 소장 하한 이상.
  묶음     set = 전집, book = 그 밖의 책(낱권). 따로 줄을 세운다 — 전집은 소장이 적고 여러 권을 한꺼번에 빌려 가서, 섞으면
           2~4세 상위 100권의 90% 이상을 차지한다.
           형태 태그 '전집'(ISBN 부가기호의 발행형태 4)은 '시리즈·다권본' 전체라 사이트 도서의 58%이고 100층짜리 집·이지은
           그림책 같은 낱권 그림책도 들어간다. 시리즈 키(groups 의 sk)도 출간 연도가 빠진 전집 권은 한 권짜리로 떨어진다.
           그래서 전집 = 형태가 전집이고 · ISBN 앞 9자리가 같은 '형태 전집' 사이트 도서가 SET_MIN_VOLS(10)권 이상이고 ·
           그 묶음의 소장 중앙이 SET_GROUP_LIBS(400)곳 이하이고 · 이 책의 소장이 SET_BOOK_LIBS(450)곳 이하인 책.
           (방문판매형 전집은 권마다 소장이 90~420곳, 서점 그림책 라인은 묶음 중앙 400곳 이상. 경험적 기준이라 경계의 책은 틀릴 수 있다.)
           set 목록은 전집 한 질당 한 권만: 키 = 2권 이상 묶인 sk, 아니면 ISBN 앞 9자리 + 소장 수 구간(같은 질은 소장 수가 거의 같다).
  소장 하한 연도 자료는 나이당 상위 5,000행뿐이라, 안 보이는 책은 그 해 대출이 컷오프 미만 → 100곳당 값 < 100 × 컷오프 ÷ 소장.
           하한 F 이상인 책 가운데 값이 문턱(100 × 컷오프 ÷ F)을 넘는 책은 빠짐없이 관측된 것이다 ('완전 관측 구간').
           F 는 LIBS_MIN(150)부터 올려 가며 구간 안의 책이 RANK_MAX(100)권 이상이 되는 가장 작은 값으로 잡는다.
           어느 F 에서도 모자라면(전집) LIBS_MIN 그대로 두고 구간 안의 책만 싣는다 — F 를 올려 권수를 채우면 소장이 적은
           전집(로보카 폴리 247곳 등)이 빠져 순위의 뜻이 없어진다. 목록에는 구간 안의 책을 LIST_MAX 권까지 싣는다.
           (한계: 여러 행·여러 판으로 쪼개진 책은 조각마다 컷오프 미만이면 합이 문턱을 넘어도 안 보인다. 관측 안 된 해는 0건으로 센다.)
  대출순   loan_rank = 같은 창의 대출 합 순위(그 나이 자격 도서 전체, 묶음 구분 없이). 카드에 "대출순 N위"로 같이 보여 준다.

출력 열: age, grp(book/set), rank, isbn13(대표 판), work, sk, loan3(창의 대출 합), years(연수), start(시작 해), libs,
         per100(100곳당 연 대출), loan_rank, floor(그 목록의 소장 하한), cent, n_ed(합친 판 수), y{연도}(해마다 대출)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_years import POP_CENT_MIN, SITE_MIN_TOTAL, clean_years, prep_long, year_cutoffs  # noqa: E402
from groups import build_groups  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

WINDOW = 3                  # 합치는 최근 연도 수 (한 해만 쓰면 상위 100권이 해마다 절반쯤 바뀐다)
LIBS_MIN = 150              # 소장 도서관 수 하한 (사용자 결정 2026-11-19)
LIBS_MIN_MAX = 400          # 하한을 올려 볼 범위의 끝
RANK_MAX = 100              # 완전 관측 구간에 들어야 하는 최소 권수 (소장 하한을 여기에 맞춘다)
LIST_MAX = 300              # 싣는 최대 권수
SHOW_AGES = list(range(1, 8))
SET_FORM = "전집"
SET_MIN_VOLS = 10           # 전집: ISBN 앞 9자리가 같은 '형태 전집' 사이트 도서 수
SET_GROUP_LIBS = 400        # 전집: 그 묶음의 소장 중앙 상한
SET_BOOK_LIBS = 450         # 전집: 이 책의 소장 상한
SET_LIBS_BUCKET = 20        # 전집 한 질을 가르는 소장 수 구간 폭 (시리즈 키가 없는 권)
VALID_ISBN = r"97[89]\d{10}"   # 정보나루 원자료의 깨진 ISBN('0000000000000', 12자리 등) 제외


def work_libs(holdings: pd.DataFrame, wk: dict) -> pd.Series:
    """판 묶음 → 소장 도서관 수(합집합)."""
    h = holdings[holdings.libCode.astype(str) != ""][["isbn13", "libCode"]]
    work = h.isbn13.map(wk)
    work = work.where(work.notna(), h.isbn13)
    return h.assign(work=work).drop_duplicates(["work", "libCode"]).groupby("work").size()


def work_start(long: pd.DataFrame, bm: pd.DataFrame, wk: dict, last_year: int) -> pd.Series:
    """판 묶음 → 시작 해 = min(판들의 가장 이른 출간 연도, 연도 자료(모든 나이)에 처음 나온 해)."""
    seen = long[long.loan_count > 0][["isbn13", "year"]]
    sw = seen.isbn13.map(wk)
    first = seen.assign(work=sw.where(sw.notna(), seen.isbn13)).groupby("work").year.min()
    py = pd.to_numeric(bm.publication_year.astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
    py = py.where((py >= 1900) & (py <= last_year + 1))
    bw = pd.Series(bm.index.map(lambda i: wk.get(i, i)), index=bm.index)
    pub = py.groupby(bw).min()
    return pd.concat([first.rename("first"), pub.rename("pub")], axis=1).min(axis=1)


def set_keys(bm: pd.DataFrame, grp: pd.DataFrame, form: pd.Series, libs: pd.Series) -> pd.Series:
    """전집으로 보는 ISBN → 그 전집 한 질의 키 (전집이 아닌 책은 들어 있지 않다). 규칙은 파일 머리말의 '묶음'."""
    site = bm.index[(bm.total_loans >= SITE_MIN_TOTAL).values & (form.reindex(bm.index).fillna("") == SET_FORM).values
                    & bm.index.str.fullmatch(VALID_ISBN)]
    d = pd.DataFrame({"sk": grp.sk.reindex(site), "libs": grp.wk.reindex(site).map(libs), "p9": site.str[:9]}, index=site)
    g = d.groupby("p9").libs.agg(["size", "median"])
    d = d.join(g, on="p9")
    d = d[(d["size"] >= SET_MIN_VOLS) & (d["median"] <= SET_GROUP_LIBS) & (d.libs <= SET_BOOK_LIBS)]
    multi = d.sk.map(d.sk.value_counts()) >= 2
    return d.sk.where(multi, "p:" + d.p9 + "|" + (d.libs // SET_LIBS_BUCKET).astype(int).astype(str))


def pick(cand: pd.DataFrame, maxcut: float, dedupe: str | None) -> tuple[pd.DataFrame, int]:
    """소장 하한을 정하고 완전 관측 구간의 책을 LIST_MAX 권까지 고른다. 반환 (목록, 하한)."""
    cand = cand.sort_values(["per100", "loan3", "isbn13"], ascending=[False, False, True])
    first = None
    for floor in range(LIBS_MIN, LIBS_MIN_MAX + 1, 5):
        z = cand[cand.libs >= floor]
        if dedupe:
            z = z.drop_duplicates(dedupe)
        zone = z[z.per100 >= 100.0 * maxcut / floor]      # 안 보이는 책이 가질 수 있는 값의 상한을 넘는 책
        if first is None:
            first = zone
        if len(zone) >= RANK_MAX:
            return zone.head(LIST_MAX), floor
    return first.head(LIST_MAX), LIBS_MIN


def build(long: pd.DataFrame, bm: pd.DataFrame, cent: pd.Series, grp: pd.DataFrame, form: pd.Series,
          holdings: pd.DataFrame, cells: pd.DataFrame | None, verbose: bool = True) -> pd.DataFrame:
    long = prep_long(long)
    years = sorted(int(y) for y in long.year.unique())[-WINDOW:]
    wk = grp.wk.to_dict()
    in_site = bm.index[(bm.total_loans >= SITE_MIN_TOTAL).values & bm.index.str.fullmatch(VALID_ISBN)]
    libs = work_libs(holdings, wk)
    start = work_start(long, bm, wk, years[-1])
    skey = set_keys(bm, grp, form, libs)

    per_year = {}
    for year, ages, _, Lc in clean_years(long, bm, cells, verbose=False):
        if year in years:
            per_year[year] = Lc.reindex(columns=[a for a in SHOW_AGES if a in ages]).fillna(0.0)
    cuts = {y: year_cutoffs(cells, y) for y in years}
    if verbose:
        print(f"창 {years[0]}~{years[-1]}년 · 소장 {len(libs):,} 묶음 · 사이트 수록 {len(in_site):,}권 · 전집으로 보는 책 {len(skey):,}권({skey.nunique():,}질)")
        print("나이 묶음 | 후보(자격·소장 하한 전) | 하한 | 목록 | 100곳당 1위·100위·끝 | 문턱(100×컷오프÷하한) | 상위 100권: 소장 중앙 | 대출순 100위 안 | 연수<3 | 여러 판 합침")

    out = []
    for a in SHOW_AGES:
        cols = {y: per_year[y][a] for y in years if a in per_year[y].columns}
        d = pd.DataFrame(cols).fillna(0.0)
        d = d[d.index.isin(in_site)]
        d["loan3"] = d[list(cols)].sum(axis=1)
        d = d[d.loan3 > 0].reset_index().rename(columns={"index": "isbn13"})
        d["work"] = d.isbn13.map(lambda i: wk.get(i, i))
        # 판 묶음 합산. 대표 판 = 그 나이 대출이 가장 많은 판(같으면 ISBN 순)
        d = d.sort_values(["work", "loan3", "isbn13"], ascending=[True, False, True])
        w = d.groupby("work", sort=False).agg(isbn13=("isbn13", "first"), n_ed=("isbn13", "size"),
                                              loan3=("loan3", "sum"), **{f"y{y}": (y, "sum") for y in cols}).reset_index()
        # 사이트 색인이 적합도를 소수 둘째 자리로 싣고 그 값으로 20% 문턱을 보므로 같은 값으로 판정한다 (build_years 와 동일)
        w["cent"] = pd.Series(cent.reindex(pd.MultiIndex.from_arrays([[a] * len(w), w.isbn13])).values, index=w.index).round(2)
        w = w[w.cent >= POP_CENT_MIN].copy()
        w["loan_rank"] = w.loan3.rank(ascending=False, method="min").astype(int)
        w["libs"] = w.work.map(libs)
        w["start"] = w.work.map(start)
        w["years"] = sum(((w.start.isna()) | (w.start <= y)).astype(int) for y in years)
        w = w[w.libs.notna() & (w.years > 0)].copy()
        w["libs"] = w.libs.astype(int)
        w["per100"] = 100.0 * w.loan3 / (w.libs * w.years)
        w["sk"] = w.isbn13.map(skey)                       # 전집 한 질의 키 (낱권은 NaN)
        w["grp"] = np.where(w.sk.notna(), "set", "book")
        maxcut = max((cuts[y].get(a, 0.0) for y in years), default=0.0)
        top_loans = set(w.nsmallest(RANK_MAX, "loan_rank").work)
        for g in ("book", "set"):
            c = w[w.grp == g]
            top, floor = pick(c, maxcut, "sk" if g == "set" else None)
            top = top.copy()
            top.insert(0, "rank", range(1, len(top) + 1))
            top.insert(0, "age", a)
            top["floor"] = floor
            out.append(top)
            if verbose and len(top):
                h = top.head(RANK_MAX)
                print(f"{a}세 {g:4s} | {len(c):5,d} | {floor:3d}곳 | {len(top):3d}권 | "
                      f"{top.per100.iloc[0]:5.1f} · {h.per100.iloc[-1]:5.1f} · {top.per100.iloc[-1]:5.1f} | {100 * maxcut / floor:5.1f} | {h.libs.median():5.0f}곳 | "
                      f"{len(set(h.work) & top_loans):3d}권 | {int((h.years < WINDOW).sum()):2d}권 | {int((h.n_ed > 1).sum()):2d}권")
    res = pd.concat(out, ignore_index=True)
    ycols = sorted(c for c in res.columns if re.fullmatch(r"y\d{4}", c))
    return res[["age", "grp", "rank", "isbn13", "work", "sk", "loan3", "years", "start", "libs", "per100", "loan_rank", "floor", "cent", "n_ed", *ycols]]


def window_years(res: pd.DataFrame) -> list[int]:
    """perlib_ranks 의 창(연도 오름차순)."""
    return sorted(int(c[1:]) for c in res.columns if re.fullmatch(r"y\d{4}", c))


def main():
    long = pd.read_parquet(PROC / "loans_long_years.parquet")
    bm = pd.read_parquet(PROC / "books_metrics.parquet").set_index("isbn13", drop=False)
    ar = pd.read_parquet(PROC / "age_ranks.parquet", columns=["age", "isbn13", "cent"])
    cent = ar.set_index(["age", "isbn13"])["cent"]
    tags = pd.read_parquet(PROC / "book_tags.parquet").set_index("isbn13")[["form", "publisher_norm"]]
    grp = build_groups(bm.join(tags))
    holdings = pd.read_parquet(PROC / "holdings.parquet", columns=["isbn13", "libCode"])
    cells_p = PROC / "cells_years.parquet"
    res = build(long, bm, cent, grp, tags.form, holdings, pd.read_parquet(cells_p) if cells_p.exists() else None)
    res.to_parquet(PROC / "perlib_ranks.parquet", index=False)
    print(f"\n저장: perlib_ranks.parquet  {len(res):,}행")
    for a in (2, 4, 6):
        for g, name in (("book", "낱권"), ("set", "전집(한 질당 1권)")):
            top = res[(res.age == a) & (res.grp == g)].head(12)
            print(f"\n[{a}세 {name} TOP12]  순위 / 100곳당 연 / 소장 / 대출 합(대출순) / 연수 / 제목")
            for r in top.itertuples():
                print(f"  {r.rank:>3}  {r.per100:5.1f}  {r.libs:>5,}곳  {r.loan3:6.0f}건({r.loan_rank:>4}위)  {r.years}년  "
                      f"{str(bm.bookname.get(r.isbn13, '')).strip()[:36]}")


if __name__ == "__main__":
    main()
