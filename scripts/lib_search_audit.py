"""검증된 검색 템플릿 감사(헤드리스 브라우저).

각 템플릿을 표본 책으로 열어 (1) 제목+출판사/저자가 보이고 (2) 청구기호·대출가능·소장위치 같은 소장 정보 단어가 있고
(3) 없는 검색어로 열면 그 책이 사라지는지 확인한다. 셋 중 하나라도 어긋나면 ok=False 로 내리고 사유를 note 에 남긴다.
  - (2) 실패: 구청 통합검색·블로그처럼 도서관 목록이 아닌 검색
  - (3) 실패: 인기도서 위젯·대출 베스트처럼 검색어와 무관하게 책이 보이는 페이지
실행: python scripts/lib_search_audit.py [--how browser,manual,form,...] [--workers 6] [--dry]
"""
from __future__ import annotations
import argparse, asyncio, json, re, sys, urllib.parse
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import lib_search as L  # noqa: E402
import lib_search_browse as B  # noqa: E402

HOLD = re.compile(r"청구기호|대출\s*가능|대출\s*중|대출불가|소장처|소장\s*위치|소장도서관|소장\s*정보|자료실|등록번호|비치중|예약")


def fill(url: str, book: dict, neg: bool = False) -> str:
    q = "9790000000000" if neg else book["isbn"]
    t = "쿼크쯔잉 없는책" if neg else re.sub(r"\s*[:(\[].*$", "", book["title"]).strip()
    return url.replace("{q}", urllib.parse.quote(q)).replace("{t}", urllib.parse.quote(t))


async def render(ctx, url: str) -> str:
    pg = await ctx.new_page()
    try:
        await pg.goto(url, wait_until="domcontentloaded", timeout=30000)
        await B.settle(pg, 3500)
        return await B.body_text(pg)
    finally:
        await pg.close()


async def main(args):
    from playwright.async_api import async_playwright
    libs, hold, books, popular = L.load_inputs()
    out = json.load(open(L.OUT, encoding="utf-8"))
    by_home = libs.groupby("home")["libCode"].apply(lambda s: list(s.astype(str))).to_dict()
    hows = set(args.how.split(","))
    todo = [h for h, r in out.items() if r.get("ok") and (r.get("how") in hows or ("static" in hows and r.get("how") not in ("browser", "manual")))
            and (not args.since or (r.get("checked") or "") >= args.since)]
    print(f"감사 대상 {len(todo)}개", flush=True)
    sem = asyncio.Semaphore(args.workers)
    res = {}
    async with async_playwright() as pw:
        br = await pw.chromium.launch()

        async def one(h):
            async with sem:
                r = out[h]
                bks = ([books[r["sample"][0]]] if r.get("sample") and r["sample"][0] in books else []) + \
                      L.pick_books(by_home.get(h, []), hold, books, popular)
                ctx = await br.new_context(user_agent=L.UA, locale="ko-KR", ignore_https_errors=True)
                verdict = "err"
                try:
                    for b in bks[:3]:                      # 표본 책이 그 사이 빠졌을 수 있어 소장 책 몇 권 시도
                        strict = "{t}" in r["url"]
                        txt = await asyncio.wait_for(render(ctx, fill(r["url"], b)), 60)
                        if not B.hit(txt, b) if strict else not (L.norm(re.sub(r"[:(\[].*$", "", b["title"]))[:14] in L.norm(txt)):
                            verdict = "no-hit"
                            continue
                        if not HOLD.search(txt):
                            verdict = "no-holding-words"
                            break
                        # 음성 검사는 새 세션에서(같은 세션이면 사이트가 기억한 직전 검색 결과가 남아 보이는 곳이 있음)
                        nctx = await br.new_context(user_agent=L.UA, locale="ko-KR", ignore_https_errors=True)
                        try:
                            ntxt = await asyncio.wait_for(render(nctx, fill(r["url"], b, neg=True)), 60)
                        finally:
                            await nctx.close()
                        verdict = "always-shows" if B.hit(ntxt, b) else "ok"
                        break
                except Exception as e:  # noqa: BLE001
                    verdict = f"err:{type(e).__name__}"
                finally:
                    await ctx.close()
                res[h] = verdict
                print(f"  {verdict:<17} {r.get('libs', 0):>2} {h[:60]}", flush=True)
        await asyncio.gather(*(one(h) for h in todo))
        await br.close()
    # 확실한 오탐(고정 위젯·소장 정보 없는 페이지)만 내린다. no-hit 은 결과가 늦게 그려져 못 본 것일 수 있어 목록만 남김.
    bad = {h: v for h, v in res.items() if v in ("no-holding-words", "always-shows")}
    for h, v in res.items():
        if v == "no-hit":
            print(f"  [확인 필요] no-hit {h}")
    print(f"결과: ok {sum(1 for v in res.values() if v == 'ok')}, 문제 {len(bad)}, 오류 {sum(1 for v in res.values() if v.startswith('err'))}")
    if not args.dry:
        for h, v in bad.items():
            r = out[h]
            out[h] = {"ok": False, "checked": str(date.today()), "libs": r.get("libs"), "how": r.get("how"), "note": f"audit:{v}",
                      "bad_url": r.get("url"), "sample": r.get("sample")}
        L.OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    json.dump(res, open(L.PROC / "lib_search_audit.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--how", default="browser,manual")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--since", default="", help="이 날짜(YYYY-MM-DD) 이후 검증된 것만")
    asyncio.run(main(ap.parse_args()))
