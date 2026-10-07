"""작가·시리즈·판 묶음 키 (사이트의 '작가당 1권'·'시리즈당 1권'·같은 책 다른 판 합치기용)

  author_key  저자 칸("글: 고대영 ;그림: 김영진", "트롤 글·그림 ;김정화 옮김")에서 대표 작가 한 명.
              옮긴이·감수는 버리고, 글 작가(또는 역할 표기 없는 첫 이름)를 우선, 없으면 그림 작가.
  work id     같은 책 다른 판: 정규화 제목 + 권차 + 대표 작가 (작가가 없으면 출판사). 사이트에서는 항상 한 권만 보인다.
  series id   책마다 하나, 위에서부터 먼저 해당하는 것:
              1) 제목 표지  "(시리즈명) …", "시리즈명 :부제", "시리즈명 3 - 부제" → 시리즈명 + 출판사(없으면 작가)
              2) 전집(부가기호 2번째 자리 4) → ISBN 앞 9자리 + 출판년 + 부가기호 (권마다 작가가 다른 세트)
              3) 끝 권 번호를 뗀 제목 + 대표 작가(없으면 출판사)  — 1권·2권…, 같은 책 다른 판
              그림책·단행본은 출판사·연도만 같다고 묶지 않는다 (예: 책읽는곰 그림책 '알사탕'·'왜냐면…' 은 별개)

실행(점검): python scripts/groups.py [--sample]
"""
from __future__ import annotations

import re

import pandas as pd

# 역할 표기 → 등급 (작은 값이 대표 작가 우선)
AUTHOR, NONE, ILLUS, DROP = 0, 0, 1, 9
ROLE_DROP = ("옮", "번역", "역자", "감수", "해설", "추천", "교정")
ROLE_ILLUS = ("그림", "그린", "일러스트", "사진", "채색", "꾸밈")
ROLE_AUTHOR = ("글", "지음", "지은", "저", "원작", "만화", "엮", "편", "구성", "기획", "각색", "스토리", "작")
ROLE_WORDS = sorted({"글", "그림", "글·그림", "그림·글", "글그림", "지음", "지은이", "저", "저자", "글쓴이", "원작", "원저", "만화", "엮음", "엮은이",
                     "편", "편저", "편집", "구성", "기획", "각색", "옮김", "옮긴이", "번역", "역", "역자", "감수", "해설", "그린이", "일러스트",
                     "사진", "채색", "꾸밈", "스토리", "작", "공저", "공편", "외"}, key=len, reverse=True)
_ROLE_TAIL = re.compile(r"\s+(?:" + "|".join(re.escape(w) for w in ROLE_WORDS) + r")(?:[·・ㆍ/](?:" + "|".join(re.escape(w) for w in ROLE_WORDS) + r"))*$")
_GENERIC = {"", "편집부", "편집팀", "편집실", "기획", "글", "그림", "외", "작자미상", "미상", "저자미상"}


def _role_rank(role: str) -> int:
    if not role:
        return NONE
    if any(w in role for w in ROLE_DROP) or role in ("역",):
        return DROP
    if any(w in role for w in ROLE_AUTHOR) and not role.startswith(("그림", "그린")):
        return AUTHOR
    if any(w in role for w in ROLE_ILLUS):
        return ILLUS
    return AUTHOR


def _clean_name(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>", " ", s)
    s = re.sub(r"\s+(외|등)\s*\d*\s*(명|인)?$", "", s.strip())
    s = re.sub(r"\s*편집부$|\s*편집팀$|\s*편집실$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip(" .·,")


def parse_author(authors) -> tuple[str, str]:
    """저자 칸 → (키, 표시 이름). 키는 공백·기호를 지운 소문자, 대표 작가를 못 찾으면 ('', '')."""
    best = None
    for gi, grp in enumerate(str(authors or "").split(";")):
        carry = ""
        for si, seg in enumerate(grp.split(",")):
            seg = seg.strip()
            if not seg:
                continue
            role = ""
            m = re.match(r"^([가-힣·・ㆍ/\s]{1,8})\s*:\s*(.*)$", seg)
            if m:
                role, seg = re.sub(r"\s+", "", m.group(1)), m.group(2)
            br = re.findall(r"[\[(]([가-힣·・ㆍ/]{1,6})[\])]", seg)
            if not role and br:
                role = br[-1]
            m = _ROLE_TAIL.search(seg)
            if m:
                role = role or re.sub(r"\s+", "", m.group(0))
                seg = seg[:m.start()]
            role = role or carry
            carry = role
            name = _clean_name(seg)
            key = re.sub(r"[^0-9a-z가-힣ぁ-んァ-ン一-龥]", "", name.lower())
            if key in _GENERIC:
                continue
            rank = _role_rank(role)
            if rank == DROP:
                continue
            cand = (rank, gi, si, key, name)
            if best is None or cand < best:
                best = cand
    return (best[3], best[4]) if best else ("", "")


def _norm(t: str) -> str:
    return re.sub(r"[^0-9a-z가-힣ぁ-んァ-ン一-龥]", "", str(t or "").lower())


def _strip_vol(t: str) -> str:
    return re.sub(r"[\s.\-]*(제?\s*\d+\s*(권|편|부|호)?|[ivx]+)$", "", t.strip(), flags=re.I).strip()


def title_heads(title) -> tuple[str, str]:
    """제목 → (시리즈 표지, 표지 없는 제목) 원문(권 번호만 뗌). 해당 없으면 ''.
    "(추리 천재) 엉덩이 탐정" → ("추리 천재", ""), "마법천자문 3 - 비춰라" → ("마법천자문", ""), "설민석의 한국사 대모험" → ("", 같은 제목)."""
    t = str(title or "").strip()
    m = re.match(r"^[(\[]([^)\]]{2,})[)\]]\s*(.*)$", t)
    if m:
        return _strip_vol(m.group(1)), ""
    parts = re.split(r"\s*[:=]\s*|\s+-\s+", t, maxsplit=1)
    if len(parts) == 2 and len(_norm(parts[0])) >= 2:
        return _strip_vol(parts[0]), ""
    return "", _strip_vol(t)


def build_groups(books: pd.DataFrame) -> pd.DataFrame:
    """books(isbn13 색인, bookname·authors·vol·publication_year·addition_symbol·form·publisher_norm) →
    DataFrame[ak, an, wk, sk, sn] (isbn13 색인). ak/an 대표 작가 키·이름, wk 판 묶음 키, sk/sn 시리즈 키·이름.
    시리즈 키는 책마다 하나를 우선순위로 정한다(키끼리 잇지 않음 — 이으면 같은 제목이 여러 전집에 실린 탓에 수천 권이 한 덩어리가 된다)."""
    pa = books.authors.map(parse_author)
    ak = [k for k, _ in pa]
    an = [v for _, v in pa]
    n = len(ak)
    isbns = [str(i) for i in books.index]
    pub = books.get("publisher_norm", pd.Series("", index=books.index)).fillna("").astype(str).map(_norm).tolist()
    form = books.get("form", pd.Series("", index=books.index)).fillna("").astype(str).tolist()
    vol = books.vol.fillna("").astype(str).str.strip().tolist() if "vol" in books.columns else [""] * n
    year = books.publication_year.fillna("").astype(str).tolist()
    add = books.addition_symbol.fillna("").astype(str).tolist()
    titles = books.bookname.fillna("").astype(str).tolist()
    pub_disp = books.publisher.fillna("").astype(str).str.strip().tolist() if "publisher" in books.columns else [""] * n
    wk, sk, sn = [], [], []
    for i in range(n):
        who = ak[i] or (("p:" + pub[i]) if pub[i] else "")
        t = _norm(titles[i])
        wk.append(f"{t}|{vol[i]}|{who}" if who and t else f"i:{isbns[i]}")
        marked, plain = title_heads(titles[i])
        mk, pk = _norm(marked), _norm(plain)
        if mk and (pub[i] or ak[i]):
            sk.append(f"m:{mk}|{pub[i] or ak[i]}")                  # (시리즈명) / 시리즈명 :부제 → 출판사(없으면 작가)별
            sn.append(marked)
        elif form[i] == "전집" and len(isbns[i]) == 13 and year[i] and add[i]:
            sk.append(f"s:{isbns[i][:9]}|{year[i]}|{add[i]}")       # 권마다 작가가 다른 전집 세트
            y = re.sub(r"\D", "", year[i])[:4]
            sn.append(f"{pub_disp[i] or '출판사 미상'} 전집" + (f" ({y})" if y else ""))
        elif pk and who:
            sk.append(f"t:{pk}|{who}")                                # 같은 제목의 1권·2권…, 같은 책 다른 판
            sn.append(plain)
        else:
            sk.append(f"i:{isbns[i]}")
            sn.append(titles[i])
    return pd.DataFrame({"ak": ak, "an": an, "wk": wk, "sk": sk, "sn": sn}, index=books.index)


def main():
    import argparse
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    src = root / "data" / ("sample" if args.sample else "processed")
    books = pd.read_parquet(src / "books_metrics.parquet").set_index("isbn13", drop=False)
    books = books[books.total_loans >= 30]
    tp = src / "book_tags.parquet"
    if tp.exists():
        tags = pd.read_parquet(tp).set_index("isbn13")
        books = books.join(tags[[c for c in ("form", "publisher_norm") if c in tags.columns]])
    g = build_groups(books)
    print(f"도서 {len(g):,}권 · 대표 작가 없음 {(g.ak == '').sum():,} · 작가 {g.ak[g.ak != ''].nunique():,}명 · "
          f"판 묶음 {g.wk.nunique():,} · 시리즈 {g.sk.nunique():,}")
    x = books.join(g)
    for col, label in (("wk", "판 묶음"), ("sk", "시리즈")):
        sz = x.groupby(col).size().sort_values(ascending=False)
        print(f"\n{label} 크기 상위:")
        for k, c in sz.head(15).items():
            ex = x[x[col] == k].sort_values("total_loans", ascending=False)
            print(f"  {c:4d}권  {ex.bookname.iloc[0][:30]} | {ex.authors.iloc[0][:25]} | 작가 {ex.ak.nunique()}명")


if __name__ == "__main__":
    main()
