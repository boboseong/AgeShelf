"""부산 미연결 20곳 수동 템플릿(2026-09-23 조사) → data/lib_search.json 의 "lib:<도서관코드>" 항목으로 검증 후 저장.

홈페이지 단위 템플릿으로는 한 홈페이지를 여러 도서관이 공유하거나(부산진구 당감·기적의) 홈페이지가 죽은 곳(busanlib.net)을
구분할 수 없어, 도서관 코드 단위로 지정한다. export_site 는 "lib:<코드>" 를 홈페이지 항목보다 먼저 본다.
각 템플릿은 그 도서관이 실제 소장한 책으로 github.io 출처에서 열어/제출해 ① 제목+출판사/저자 ② 소장 정보 단어 ③ 새 세션 음성 검사를 확인.
"""
from __future__ import annotations
import asyncio, json, re, sys, urllib.parse
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.stdout.reconfigure(encoding="utf-8")
import lib_search as L, lib_search_audit as A, lib_search_browse as B, lib_search_post as P  # noqa: E402

GIJANG = "POST utf-8 https://library.gijang.go.kr/portal/kolas/book/search.do?mId=1000000000 _csrf=temp%20_csrf&libraryPath={lp}&search_txt={{t}}&display=10"
BUKGU = "POST utf-8 https://www.bsbukgu.go.kr/hmlib/index.bsbukgu?menuCd=DOM_000001203001000000&mode=result search_category=search_title&libraryPath={lp}&search_txt={{t}}"
JIN = "https://www.busanjin.go.kr/library/index.busanjin?menuCd=DOM_000002103001000000&booktype=BOOK&pageno=1&manage_code={mc}&search_title={{t}}"
SAHA = "https://www.saha.go.kr/hadanlib/booksearch/list.do?mId=0301000000&page=1&searchType=search_title&book_type=BOOK&display=10&manage_code={mc}&searchTxt={{t}}"
HUB = "POST utf-8 https://library.busan.go.kr/{hub}/book/search/collectionOfMaterials searchMode=normal&procMode=search&search_type=normal&pageno=1&display=10&manage_code={mc}&option=0&search_txt={{t}}"

SPEC = {  # libCode: (템플릿, 설명)
    "126144": (GIJANG.format(lp="jglib"), "기장군 통합검색 정관"),
    "121026": (GIJANG.format(lp="gijang"), "기장군 통합검색 기장"),
    "126134": (GIJANG.format(lp="jgchildlib"), "기장군 통합검색 정관에듀파크어린이"),
    "126151": (GIJANG.format(lp="daera"), "기장군 통합검색 대라다목적(옛 주소 busanlib.net 폐쇄)"),
    "126159": (GIJANG.format(lp="gochon"), "기장군 통합검색 고촌어울림"),
    "126160": (GIJANG.format(lp="naeri"), "기장군 통합검색 내리새라"),
    "126165": (GIJANG.format(lp="gyori"), "기장군 통합검색 교리"),
    "126025": (BUKGU.format(lp="hmlib"), "북구 소장자료검색 화명(GET 은 한글 깨짐)"),
    "121023": (BUKGU.format(lp="mdlib"), "북구 소장자료검색 만덕"),
    "126152": (BUKGU.format(lp="bglib"), "북구 소장자료검색 금곡"),
    "126147": (JIN.format(mc="BL"), "부산진구통합도서관 어린이청소년"),
    "126175": (JIN.format(mc="CH"), "부산진구통합도서관 당감"),
    "126169": (JIN.format(mc="CD"), "부산진구통합도서관 기적의"),
    "126023": (SAHA.format(mc="BC"), "사하구 도서검색 다대"),
    "126168": (SAHA.format(mc="CE"), "사하구 도서검색 하단"),
    "121014": (HUB.format(hub="ydbooks", mc="AM"), "부산 통합검색 허브 영도"),
    "126015": (HUB.format(hub="ydbooks", mc="BB"), "부산 통합검색 허브 영도 남항분관"),
    "121025": (HUB.format(hub="ssbooks", mc="AW"), "부산 통합검색 허브 사상(홈페이지 링크의 구청 통합검색은 오탐)"),
    "121018": (HUB.format(hub="gjbooks", mc="AP"), "부산 통합검색 허브 금정(옛 홈페이지는 구청 첫 화면으로 넘어감)"),
}
PAGE_ONLY = {  # 온라인 소장 조회가 안 되는 곳: 현재 홈페이지의 검색 페이지로만 안내
    "126032": ("https://dlib.gijang.go.kr/dlib/book/search.do?mId=0201010000",
               "기장디지털도서관: 옛 주소 busanlib.net 폐쇄, dlib.gijang.go.kr 자체 검색은 결과가 비고 부산 통합검색·기장군 통합검색에도 없음"),
}


async def fetch(br, tpl: str, q: str) -> str:
    if tpl.startswith("POST "):
        _p, enc, action, qs = tpl.split(" ", 3)
        return await P.post_from_site(br, action, enc, urllib.parse.parse_qsl(qs, keep_blank_values=True), q)
    ctx = await br.new_context(user_agent=L.UA, locale="ko-KR", ignore_https_errors=True)
    try:
        pg = await ctx.new_page(); await pg.goto(P.ORIGIN)
        await pg.goto(tpl.replace("{t}", urllib.parse.quote(q)), wait_until="domcontentloaded", timeout=40000)
        await B.settle(pg, 3500); return await B.body_text(pg)
    finally:
        await ctx.close()


async def main():
    from playwright.async_api import async_playwright
    libs, hold, books, popular = L.load_inputs()
    out = json.load(open(L.OUT, encoding="utf-8"))
    names = dict(zip(libs.libCode.astype(str), libs.libName))
    async with async_playwright() as pw:
        br = await pw.chromium.launch(); sem = asyncio.Semaphore(4)

        async def one(code):
            async with sem:
                tpl, why = SPEC[code]
                verdict = "no-hit"
                for b in L.pick_books([code], hold, books, popular):
                    title = re.sub(r"\s*[:(\[].*$", "", b["title"]).strip()
                    t = await fetch(br, tpl, title)
                    if not (B.hit(t, b) and A.HOLD.search(t)):
                        continue
                    n = await fetch(br, tpl, P.NEG)
                    verdict = "always-shows" if B.hit(n, b) else "ok"
                    if verdict == "ok":
                        out[f"lib:{code}"] = {"ok": True, "how": "manual", "url": tpl, "query": "title", "checked": str(date.today()),
                                              "libs": 1, "sample": [b["isbn"], b["title"][:40]], "note": why}
                    break
                print(f"  {verdict:<12} {code} {names.get(code, '')}", flush=True)
        await asyncio.gather(*(one(c) for c in SPEC))
        await br.close()
    for code, (page, why) in PAGE_ONLY.items():
        out[f"lib:{code}"] = {"ok": False, "how": "manual", "page": page, "checked": str(date.today()), "libs": 1, "note": why}
    L.OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
