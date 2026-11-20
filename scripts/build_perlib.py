"""도서관당 대출 순위 → data/processed/perlib_ranks.parquet  (PLAN.md 11절)

실행: python scripts/build_perlib.py        # loans_long_years.parquet(연도별 대출) + holdings.parquet(전국 소장) 필요
      분야별 보충 수집(loans_long_years_kdc.parquet·cells_years_kdc.parquet)이 있으면 같이 쓴다 — 없으면 무분할 자료만으로 계산.
        python scripts/collect.py --years 2023-2025 --ages 1-7 --tag years_kdc --cells "kdc=0,kdc=1,kdc=2,kdc=3,kdc=4,kdc=5,kdc=6,kdc=7,kdc=9,dtl_kdc=80,dtl_kdc=81,dtl_kdc=82,dtl_kdc=83,dtl_kdc=84,dtl_kdc=85,dtl_kdc=86,dtl_kdc=87,dtl_kdc=88,dtl_kdc=89"

대출순은 많이 비치된 책이 유리하다. 최근 WINDOW(3)개 연도의 그 나이 카드 대출을 전국 소장 도서관 수로 나눠
'소장한 도서관 100곳당 그 나이 아이가 1년에 빌린 횟수'로 줄을 세운다.

  분자     build_years.clean_years 의 값(잡음 제거 + 급등 제외, 수록 하한 적용 전)을 최근 WINDOW 개 연도 합산.
           분야별 보충 수집이 있으면 무분할 자료와 행 단위로 합친 값(merge_cells)을 쓴다. 무분할 상위 5,000행은 그 나이 대출의
           45%쯤만 담고, 같은 책이 vol 표기마다 다른 행으로 쪼개져 있어 컷오프 아래 행이 빠진 만큼 값이 낮게 잡힌다
           (무분할에 보이던 칸도 합치면 대출 합이 7~12% 는다).
           같은 책 다른 판(groups 의 wk)은 한 권으로 합친다. 대표 판 = 그 나이 대출이 가장 많은 판.
  분모     판 묶음의 소장 건수(holdings.parquet 의 (판, 도서관) 쌍 수 = 판마다 가진 도서관 수의 합, 소장 없음 행 제외) × 연수.
           판이 하나인 책(95%)은 소장 도서관 수 그대로다. 처음에는 합집합(도서관 수)으로 나눴는데, 한 도서관이 여러 판을
           같이 갖고 있으면 대출만 더해지고 분모는 그대로라 값이 부풀었다 — 특히 제목이 같은 1~4권이 '다른 판'으로 묶인
           '돌더지야 어디로 갈까?'(네 권 모두 같은 156곳 소장)가 4배로 잡혀 6세 1위가 됐다. 합으로 나누면 권·판마다의 평균이 된다.
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
           분야별 보충 수집을 한 나이는 컷오프 = 분야 셀 컷오프 가운데 가장 큰 값(한국문학 dtl_kdc=81 이 정한다:
           2025년 1~7세 2·2·5·10·17·26·43건, 무분할은 4·7·14·27·44·64·94건) → 문턱이 2~3분의 1로 내려간다.
           하한 F 이상인 책 가운데 값이 문턱(100 × 컷오프 ÷ F)을 넘는 책은 빠짐없이 관측된 것이다 ('완전 관측 구간').
           F 는 LIBS_MIN(150)부터 올려 가며 구간 안의 책이 RANK_MAX(100)권 이상이 되는 가장 작은 값으로 잡는다.
           어느 F 에서도 모자라면(전집) LIBS_MIN 그대로 두고 구간 안의 책만 싣는다 — F 를 올려 권수를 채우면 소장이 적은
           전집(로보카 폴리 247곳 등)이 빠져 순위의 뜻이 없어진다. 목록에는 구간 안의 책을 모두 싣는다(LIST_MAX 는 안전 상한).
           (한계: 여러 행·여러 판으로 쪼개진 책은 조각마다 컷오프 미만이면 합이 문턱을 넘어도 안 보인다. 관측 안 된 해는 0건으로 센다.)
  대출순   loan_rank = 같은 창의 대출 합 순위(그 나이 자격 도서 전체, 묶음 구분 없이). 카드에 "대출순 N위"로 같이 보여 준다.

출력 열: age, grp(book/set), rank, isbn13(대표 판), work, sk, loan3(창의 대출 합), years(연수), start(시작 해), libs(소장 건수),
         per100(100곳당 연 대출), loan_rank, floor(그 목록의 소장 하한), cent, n_ed(그 나이 대출이 있는 판 수),
         n_hold(소장 기록이 있는 판 수 — 2 이상이면 libs 는 판마다 가진 도서관 수의 합), y{연도}(해마다 대출)
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
LIST_MAX = 5000             # 싣는 최대 권수 (안전 상한 — 완전 관측 구간은 보충 수집 뒤 나이당 1,000~3,100권)
SHOW_AGES = list(range(1, 8))
SET_FORM = "전집"
SET_MIN_VOLS = 10           # 전집: ISBN 앞 9자리가 같은 '형태 전집' 사이트 도서 수
SET_GROUP_LIBS = 400        # 전집: 그 묶음의 소장 중앙 상한
SET_BOOK_LIBS = 450         # 전집: 이 책의 소장 상한
SET_LIBS_BUCKET = 20        # 전집 한 질을 가르는 소장 수 구간 폭 (시리즈 키가 없는 권)
VALID_ISBN = r"97[89]\d{10}"   # 정보나루 원자료의 깨진 ISBN('0000000000000', 12자리 등) 제외
# 분야별 보충 수집의 셀(collect.KCELLS): 모든 책이 이 가운데 한 셀에 든다 — 대주제 0~7·9 + 문학(8)은 세부주제 80~89
LEAF_CELLS = frozenset([f"kdc={k}" for k in (0, 1, 2, 3, 4, 5, 6, 7, 9)] + [f"dtl_kdc={k}" for k in range(80, 90)])


def work_libs(holdings: pd.DataFrame, wk: dict) -> pd.DataFrame:
    """판 묶음 → libs 소장 건수((판, 도서관) 쌍 수), n_hold 소장 기록이 있는 판 수, libs_u 소장 도서관 수(합집합)."""
    h = holdings[holdings.libCode.astype(str) != ""][["isbn13", "libCode"]].drop_duplicates()
    work = h.isbn13.map(wk)
    h = h.assign(work=work.where(work.notna(), h.isbn13))
    out = h.groupby("work").agg(libs=("libCode", "size"), n_hold=("isbn13", "nunique"))
    out["libs_u"] = h.drop_duplicates(["work", "libCode"]).groupby("work").size()
    return out


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


def merge_cells(extra: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """분야별 보충 수집 원자료(loans_long_years_kdc)와 무분할 자료(base, prep_long 을 거친 것)를 행 단위로 합친다
    → (year, age, isbn13)당 한 행. 보충 수집을 한 (연도, 나이)만 낸다.

    정보나루 응답의 한 행은 (ISBN, vol 표기)다 — 같은 책이 vol 표기('', '5', '10' …)마다 따로 나오고, 한 조회 안에서
    (ISBN, vol)은 겹치지 않는다(실측). 그래서 조회(무분할·셀)끼리는 같은 (ISBN, vol) 행의 **큰 값**을 잡고, 그 뒤 ISBN 으로 더한다.
    대출 수까지 같은 행만 같은 행으로 보면 안 된다: 정보나루 자료는 지난 연도도 뒤늦게 갱신돼서(2025년 2세 대출 합이 하루 사이
    +2.6%) 수집한 날이 다르면 같은 행의 값이 조금 다르다. 셀 컷오프가 무분할보다 낮아 쪼개진 행이 더 많이 잡히므로
    합친 값은 무분할 값 이상이다."""
    e = prep_long(extra)
    scope = e[["year", "age"]].drop_duplicates()
    b = base.merge(scope, on=["year", "age"])
    rows = pd.concat([b[["year", "age", "isbn13", "vol", "loan_count"]].assign(q="*"),
                      e[["year", "age", "isbn13", "vol", "loan_count"]].assign(q=e.kcell)], ignore_index=True)
    rows["vol"] = rows.vol.fillna("").astype(str).str.strip()
    rows = rows.groupby(["year", "age", "q", "isbn13", "vol"], as_index=False)["loan_count"].sum()   # 한 조회 안 (만일의 중복)
    rows = rows.groupby(["year", "age", "isbn13", "vol"], as_index=False)["loan_count"].max()        # 조회끼리
    return rows.groupby(["year", "age", "isbn13"], as_index=False)["loan_count"].sum()               # 책으로


def leaf_cutoffs(cells: pd.DataFrame | None, year: int) -> dict[int, float]:
    """보충 수집을 한 (연도, 나이)의 컷오프 = 셀별 컷오프 가운데 가장 큰 값(상한에 안 걸린 셀은 0).
    LEAF_CELLS 가 다 모인 나이만 낸다 — 한 셀이라도 빠지면 그 셀의 책은 무분할 컷오프까지 숨을 수 있다."""
    if cells is None:
        return {}
    cy = cells[cells["slice"] == f"y{year}"]
    out = {}
    for a, g in cy.groupby("age"):
        g = g[g.kcell.isin(LEAF_CELLS)]
        if set(g.kcell) == LEAF_CELLS:
            out[int(a)] = float(g.cutoff.where(g.capped.astype(bool), 0).max())
    return out


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
          holdings: pd.DataFrame, cells: pd.DataFrame | None, verbose: bool = True,
          extra: pd.DataFrame | None = None, cells_extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """extra·cells_extra = 분야별 보충 수집(loans_long_years_kdc·cells_years_kdc). 없으면 무분할 자료만으로 계산."""
    long = prep_long(long)
    ex = merge_cells(extra, long) if extra is not None else None
    years = sorted(int(y) for y in long.year.unique())[-WINDOW:]
    wk = grp.wk.to_dict()
    in_site = bm.index[(bm.total_loans >= SITE_MIN_TOTAL).values & bm.index.str.fullmatch(VALID_ISBN)]
    wl = work_libs(holdings, wk)
    libs, libs_u = wl.libs, wl.libs_u
    seen = long[["isbn13", "year", "loan_count"]] if ex is None else pd.concat([long[["isbn13", "year", "loan_count"]], ex[["isbn13", "year", "loan_count"]]])
    start = work_start(seen, bm, wk, years[-1])
    skey = set_keys(bm, grp, form, libs_u)      # 전집 판정은 '한 권을 가진 도서관 수' 기준 그대로

    per_year = {}
    for year, ages, _, Lc in clean_years(long, bm, cells, verbose=False, extra=ex):
        if year in years:
            per_year[year] = Lc.reindex(columns=[a for a in SHOW_AGES if a in ages]).fillna(0.0)
    cuts = {y: {**year_cutoffs(cells, y), **leaf_cutoffs(cells_extra, y)} for y in years}   # 보충한 나이는 셀 컷오프로
    if verbose:
        print(f"창 {years[0]}~{years[-1]}년 · 소장 {len(libs):,} 묶음 · 사이트 수록 {len(in_site):,}권 · 전집으로 보는 책 {len(skey):,}권({skey.nunique():,}질)")
        if ex is None:
            print("분야별 보충 수집 없음 — 무분할 자료(나이당 상위 5,000행)만 사용")
        else:
            n_cells = cells_extra.groupby(["slice", "age"]).ngroups if cells_extra is not None else 0
            print(f"분야별 보충 수집: {ex.year.min()}~{ex.year.max()}년 × {ex.age.min()}~{ex.age.max()}세 ({n_cells}개 (연도, 나이)) · "
                  f"합친 칸 {len(ex):,}개 · 컷오프(연도별 나이 순) "
                  + " / ".join(f"{y}: " + "·".join(f"{cuts[y].get(a, 0):.0f}" for a in SHOW_AGES) for y in years))
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
        w["n_hold"] = w.work.map(wl.n_hold)
        w["start"] = w.work.map(start)
        w["years"] = sum(((w.start.isna()) | (w.start <= y)).astype(int) for y in years)
        w = w[w.libs.notna() & (w.years > 0)].copy()
        w["libs"] = w.libs.astype(int)
        w["n_hold"] = w.n_hold.astype(int)
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
    return res[["age", "grp", "rank", "isbn13", "work", "sk", "loan3", "years", "start", "libs", "per100", "loan_rank", "floor", "cent", "n_ed", "n_hold", *ycols]]


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
    ex_p, cx_p = PROC / "loans_long_years_kdc.parquet", PROC / "cells_years_kdc.parquet"
    has_ex = ex_p.exists() and cx_p.exists()
    res = build(long, bm, cent, grp, tags.form, holdings, pd.read_parquet(cells_p) if cells_p.exists() else None,
                extra=pd.read_parquet(ex_p) if has_ex else None, cells_extra=pd.read_parquet(cx_p) if has_ex else None)
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
