"""검색어를 URL 로 받지 않고 폼 제출(POST)로만 검색하는 도서관을 '폼 제출 템플릿'으로 바꾼다.

정적 사이트에서도 보이지 않는 <form method=post target=_blank> 를 만들어 제출하면 그 도서관의 검색 결과 화면을
새 탭에 바로 열 수 있다(일반 폼 제출이라 서버·키·CORS 불필요). 단 CSRF 토큰을 요구하는 사이트는 안 된다.

  1) data/lib_search.json 에서 ok=False 이고 검색 페이지(page)가 있는 홈페이지를 연다.
  2) 사이트 검색창에 그 도서관이 소장한 책 제목을 넣고 제출하면서 브라우저가 보내는 POST 요청(url, body)을 잡는다.
  3) body 에서 제목 자리를 {t} 로 바꾸고 토큰류 파라미터를 뺀 뒤, **우리 사이트(github.io) 페이지에서** 같은 폼을 만들어
     제출해 본다 → 결과에 제목+출판사/저자와 소장 정보 단어가 있고, 없는 검색어로 제출하면(새 세션) 책이 사라지면 성공.
  4) 성공하면 url = "POST <인코딩> <action> <k=v&k={t}…>" (값은 UTF-8 URL 인코딩, 인코딩은 폼 accept-charset).

실행: python scripts/lib_search_post.py [--homes URL,URL] [--regions 21,38] [--workers 4] [--dry]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.parse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import lib_search as L  # noqa: E402
import lib_search_audit as A  # noqa: E402
import lib_search_browse as B  # noqa: E402

ORIGIN = "https://boboseong.github.io/AgeShelf/"          # 실제 사용자가 제출하는 출처에서 시험
TOKEN_KEY = re.compile(r"csrf|token|signature|nonce|jsessionid|sessionid|_ts$|timestamp|captcha", re.I)
NEG = "쿼크쯔잉 없는책"

SUBMIT_JS = """
([action, enc, fields]) => {
  const f = document.createElement('form'); f.method = 'POST'; f.action = action; f.acceptCharset = enc;
  for (const [k, v] of fields) { const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i); }
  document.body.appendChild(f); f.submit(); return fields.length;
}
"""


def decode_body(body: str, title: str) -> tuple[list[tuple[str, str]], str] | None:
    """POST body → ([(k, v)], 인코딩). 제목이 들어 있는 값을 {t} 로. 제목을 못 찾으면 None."""
    for enc in ("utf-8", "euc-kr"):
        try:
            pairs = urllib.parse.parse_qsl(body, keep_blank_values=True, encoding=enc, errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        if not any(title in v for _k, v in pairs):
            continue
        out = []
        for k, v in pairs:
            if TOKEN_KEY.search(k):
                continue                                   # 세션마다 바뀌는 토큰은 뺀다(필수면 검증에서 실패)
            out.append((k, v.replace(title, "{t}")))
        return out, enc
    return None


def encode_tpl(action: str, enc: str, fields: list[tuple[str, str]]) -> str:
    qs = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='{}')}" for k, v in fields)
    return f"POST {enc} {action} {qs}"


async def post_from_site(browser, action: str, enc: str, fields: list[tuple[str, str]], query: str) -> str:
    """우리 사이트 출처에서 폼을 제출하고 결과 본문을 돌려준다(새 세션)."""
    ctx = await browser.new_context(user_agent=L.UA, locale="ko-KR", ignore_https_errors=True)
    try:
        pg = await ctx.new_page()
        await pg.goto(ORIGIN, wait_until="domcontentloaded", timeout=30000)
        filled = [(k, v.replace("{t}", query)) for k, v in fields]
        async with pg.expect_navigation(wait_until="domcontentloaded", timeout=40000):
            await pg.evaluate(SUBMIT_JS, [action, enc, filled])
        await B.settle(pg, 3500)
        return await B.body_text(pg)
    finally:
        await ctx.close()


async def probe(browser, home: str, rec: dict, books: list[dict]) -> dict | None:
    page_url = rec.get("page") or home
    for book in books[:3]:
        title = re.sub(r"\s*[:(\[].*$", "", book["title"]).strip()
        ctx = await browser.new_context(user_agent=L.UA, locale="ko-KR", ignore_https_errors=True,
                                        viewport={"width": 1280, "height": 900})
        ctx.set_default_timeout(15000)
        try:
            pg = await ctx.new_page()
            await pg.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            await B.settle(pg, 1500)
            url, text, why = await B.search_here(pg, ctx, book, title)
            posts = getattr(pg, "ageshelf_posts", None) or []
        except Exception as e:  # noqa: BLE001
            print(f"    {home[:50]} 검색 실패 {type(e).__name__}", flush=True)
            posts, text = [], ""
        finally:
            await ctx.close()
        if not posts:
            continue
        for action, body in posts:
            dec = decode_body(body, title)
            if not dec:
                continue
            fields, enc = dec
            action = L.strip_tokens(action)
            try:
                txt = await asyncio.wait_for(post_from_site(browser, action, enc, fields, title), 70)
            except Exception as e:  # noqa: BLE001
                print(f"    {home[:50]} 재제출 실패 {type(e).__name__}", flush=True)
                continue
            if not (B.hit(txt, book) and A.HOLD.search(txt)):
                continue
            try:
                ntxt = await asyncio.wait_for(post_from_site(browser, action, enc, fields, NEG), 70)
            except Exception:  # noqa: BLE001
                continue
            if B.hit(ntxt, book):
                continue                                   # 검색과 무관하게 책이 보이는 페이지
            return {"ok": True, "how": "post", "url": encode_tpl(action, enc, fields), "query": "title",
                    "checked": str(date.today()), "libs": rec.get("libs"), "sample": [book["isbn"], book["title"][:40]],
                    "page": rec.get("page"), "note": "폼 제출(POST) — github.io 출처에서 재제출·음성 검사 통과"}
    return None


async def main(args):
    from playwright.async_api import async_playwright
    libs, hold, books, popular = L.load_inputs()
    out = json.load(open(L.OUT, encoding="utf-8"))
    by_home = libs.groupby("home")["libCode"].apply(lambda s: list(s.astype(str))).to_dict()
    region_of = libs.groupby("home")["region"].first().to_dict()
    if args.homes:
        todo = [h for h in args.homes.split(",") if h in out]
    else:
        regs = set(args.regions.split(",")) if args.regions else None
        todo = [h for h, r in out.items() if not r.get("ok") and r.get("page") and h in by_home
                and (regs is None or str(region_of.get(h)) in regs)]
    todo.sort(key=lambda h: -len(by_home.get(h, [])))
    print(f"폼 제출 시험 대상 {len(todo)}개 홈페이지", flush=True)
    sem = asyncio.Semaphore(args.workers)
    n_ok = 0
    async with async_playwright() as pw:
        br = await pw.chromium.launch()

        async def one(h):
            nonlocal n_ok
            async with sem:
                bks = L.pick_books(by_home.get(h, []), hold, books, popular)
                try:
                    res = await asyncio.wait_for(probe(br, h, out[h], bks), 400)
                except Exception as e:  # noqa: BLE001
                    res = None
                    print(f"    {h[:50]} 오류 {type(e).__name__}", flush=True)
                if res:
                    n_ok += 1
                    if not args.dry:
                        out[h] = res
                print(f"  {'OK ' if res else '-- '}{len(by_home.get(h, [])):>2} {h[:60]} {(res or {}).get('url', '')[:90]}", flush=True)
        await asyncio.gather(*(one(h) for h in todo))
        await br.close()
    if not args.dry:
        L.OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"완료: 폼 제출 템플릿 {n_ok} / {len(todo)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--homes", default="")
    ap.add_argument("--regions", default="", help="지역 코드 쉼표 구분(예: 21,38). 비우면 전국")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    asyncio.run(main(ap.parse_args()))
