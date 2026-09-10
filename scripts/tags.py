"""도서 태그 부여 → data/processed/book_tags.parquet

태그 종류
  kdc2 / kdc2_name / kdc1_name : KDC 2자리 세부 라벨(부모용 이름)과 대분류
  form                          : 그림책 / 전집 / 단행본 / 도감 / 기타 (ISBN 부가기호 2번째 자리)
  publisher_norm                : 출판사 정규화 이름 (괄호 안 한글명 우선, 공백 제거)
  성격: 먼저 보기 좋은 / 커서도 보는 / 여러 나이 스테디 / 10년 스테디셀러 / 베스트셀러
  요즘 인기                     : 최근 12개월 대출(1~8세 합)이 상위 10% (loans_long_recent.parquet 필요)

실행: python scripts/tags.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"

KDC1_NAME = {"0": "총류·백과", "1": "마음·철학", "2": "종교·신화", "3": "사회·생활습관", "4": "자연·과학",
             "5": "기술·몸·환경", "6": "예술·놀이", "7": "한글·언어", "8": "이야기·그림책", "9": "역사·인물·지리"}
KDC2_NAME = {
    "00": "지식 일반", "01": "도서·서지", "02": "문헌정보", "03": "백과사전", "04": "논문집", "05": "연속간행물", "06": "학회·기관", "07": "신문·언론", "08": "일반 전집", "09": "향토자료",
    "10": "철학", "11": "형이상학", "12": "인식·인간학", "13": "철학 체계", "14": "경학", "15": "동양철학", "16": "서양철학", "17": "논리학", "18": "심리", "19": "도덕·예절",
    "20": "종교", "21": "신화", "22": "불교", "23": "기독교", "24": "도교", "25": "천도교", "26": "신도", "27": "힌두교", "28": "이슬람", "29": "기타 종교",
    "30": "사회 전집", "31": "통계", "32": "경제", "33": "사회", "34": "정치", "35": "행정", "36": "법", "37": "유아교육·생활습관", "38": "전래·민속", "39": "국방",
    "40": "과학 일반", "41": "수학", "42": "물리", "43": "화학", "44": "우주·천문", "45": "지구·공룡", "46": "광물", "47": "생물", "48": "식물", "49": "동물",
    "50": "기술 일반", "51": "몸·건강", "52": "농업·축산", "53": "환경·공학", "54": "건축", "55": "기계·탈것", "56": "전기·전자", "57": "화학공학", "58": "제조", "59": "요리·가정",
    "60": "예술", "61": "건축술", "62": "조각", "63": "공예", "64": "서예", "65": "회화", "66": "사진", "67": "음악", "68": "연극", "69": "오락·운동",
    "70": "언어", "71": "한글", "72": "중국어", "73": "일본어", "74": "영어", "75": "독일어", "76": "프랑스어", "77": "스페인어", "78": "이탈리아어", "79": "기타 언어",
    "80": "문학 전집", "81": "한국 창작", "82": "중국 번역", "83": "일본 번역", "84": "영미 번역", "85": "독일 번역", "86": "프랑스 번역", "87": "스페인 번역", "88": "이탈리아 번역", "89": "기타 번역",
    "90": "세계사", "91": "한국사", "92": "유럽사", "93": "아프리카사", "94": "북미사", "95": "남미사", "96": "오세아니아사", "97": "극지", "98": "지리", "99": "인물",
}
FORM = {"7": "그림책", "4": "전집", "3": "단행본", "6": "도감"}
RECENT_AGES = list(range(1, 9))
RECENT_TOP_Q = 0.90


def norm_publisher(s) -> str:
    t = str(s or "").strip()
    if not t:
        return ""
    m = re.search(r"\(([^)]*[가-힣][^)]*)\)", t)
    outside = re.sub(r"\([^)]*\)", "", t).strip()
    if m and (re.search(r"[A-Za-z]", outside) or not re.search(r"[가-힣]", outside)):
        t = m.group(1)                      # 예: '그레이트 Books(그레이트북스)' → 그레이트북스
    else:
        t = outside or t                    # 예: '키즈아이콘(아이코닉스)' → 키즈아이콘
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"(주식회사|㈜|\(주\)|출판사|출판|퍼블리싱)$", "", t)
    return t


def recent_loans(books: pd.DataFrame) -> pd.Series | None:
    p = PROC / "loans_long_recent.parquet"
    if not p.exists():
        return None
    r = pd.read_parquet(p)
    r["age"] = r["age"].astype(int)
    r = r[r.age.isin(RECENT_AGES)]
    per = r.groupby(["isbn13", "age"])["loan_count"].sum()      # 쿼리 내 ISBN 중복행 합산
    tot = per.groupby(level="isbn13").sum()
    return tot.reindex(books.index).fillna(0.0)


def main():
    books = pd.read_parquet(PROC / "books_metrics.parquet").set_index("isbn13")
    k2 = books.class_no.fillna("").astype(str).str.strip().str[:2]
    k2 = k2.where(k2.str.fullmatch(r"\d\d"), "")
    tags = pd.DataFrame(index=books.index)
    tags["kdc2"] = k2
    tags["kdc2_name"] = k2.map(lambda k: KDC2_NAME.get(k, "") if k else "미분류")
    tags["kdc1_name"] = k2.map(lambda k: KDC1_NAME.get(k[:1], "") if k else "미분류")
    tags["form"] = books.addition_symbol.fillna("").astype(str).str[1:2].map(FORM).fillna("기타")
    tags["publisher_norm"] = books.publisher.map(norm_publisher)

    q90 = books.total_loans.quantile(0.90)
    q99 = books.total_loans.quantile(0.99)
    yr = pd.to_numeric(books.publication_year, errors="coerce")
    tags["먼저 보기 좋은"] = books["shape"].eq("더 어린 아이부터 봄")
    tags["커서도 보는"] = books["shape"].eq("더 큰 아이 쪽으로 넓음")
    tags["여러 나이 스테디"] = (books.spread >= 2.5) & (books.total_loans >= q90)
    tags["10년 스테디셀러"] = (yr <= 2015) & (books.total_loans >= q90)
    tags["베스트셀러"] = books.total_loans >= q99

    rec = recent_loans(books)
    if rec is None:
        print("loans_long_recent.parquet 없음 → '요즘 인기' 태그 생략")
        tags["recent_loans"] = 0.0
        tags["요즘 인기"] = False
    else:
        tags["recent_loans"] = rec
        pos = rec[rec > 0]
        thr = pos.quantile(RECENT_TOP_Q) if len(pos) else np.inf
        tags["요즘 인기"] = rec >= thr
        print(f"요즘 인기: 최근 12개월 1~8세 대출 ≥ {thr:,.0f}건 → {int(tags['요즘 인기'].sum()):,}권 (관측 {len(pos):,}권 중)")

    tags.reset_index().to_parquet(PROC / "book_tags.parquet", index=False)
    print(f"tags: {len(tags):,}권 → book_tags.parquet")
    print("형태:", tags.form.value_counts().to_dict())
    print("출판사 상위:", tags.publisher_norm.value_counts().head(12).to_dict())
    for c in ("먼저 보기 좋은", "커서도 보는", "여러 나이 스테디", "10년 스테디셀러", "베스트셀러", "요즘 인기"):
        print(f"  {c}: {int(tags[c].sum()):,}권")


if __name__ == "__main__":
    main()
