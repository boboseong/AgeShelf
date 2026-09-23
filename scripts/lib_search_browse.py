"""lib_search.py 로 못 찾은 홈페이지를 헤드리스 브라우저(Playwright)로 실제 검색해 검색 URL 형식을 알아낸다.

  홈페이지를 열고 → 검색창(placeholder/name 에 검색·search·keyword 등)을 찾아 → 책 제목을 입력하고 Enter →
  결과 화면에 제목+출판사(또는 저자)가 보이면 성공.
    - 결과 URL 에 검색어가 들어 있으면 그 자리를 {t} 로 바꿔 템플릿으로 저장(ISBN 도 되는지 확인해 되면 {q}).
    - 검색은 되는데 URL 에 검색어가 없으면(SPA, POST) 검색 페이지 URL 만 "page" 로 저장 → 사이트는 제목을 복사해 주고 검색 페이지를 연다.
  검색창이 홈에 없으면 '자료검색/통합검색/도서검색' 링크를 눌러 검색 페이지로 간 뒤 다시 시도한다.

실행: python scripts/lib_search_browse.py [--only-fail] [--homes URL,URL] [--workers 4] [--headed]
  결과는 data/lib_search.json 에 how="browser" 로 기록(기존 ok/manual 항목은 건드리지 않음).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import lib_search as L  # noqa: E402

SEARCH_HINT = re.compile(r"검색|search|srch|keyword|query|kwd|찾기", re.I)
EXCLUDE_HINT = re.compile(r"(?:^|[^a-z])(?:id|pw|pass|login|mail|zip|addr|phone|tel|captcha)(?:[^a-z]|$)|userid|user_id|memberid|loginid|login_id|passwd|password|아이디|비밀번호", re.I)
SEARCH_LINK_TEXT = re.compile(r"^(통합자료검색|통합검색|자료검색|도서검색|소장자료검색|소장자료|자료찾기|책검색|도서 검색|자료 검색|통합 검색|검색|간략검색|자료검색\s*[>›]?\s*통합검색|소장자료 검색|도서 검색하기|장서검색|목록검색)$")
OPEN_SEARCH_JS = """
() => {
  const c = Array.from(document.querySelectorAll('button,a,i,span,div')).find(e => {
    const r = e.getBoundingClientRect(); if (r.width < 8 || r.height < 8 || r.top > 300) return false;
    const d = (e.className || '') + ' ' + (e.id || '') + ' ' + (e.title || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.textContent || '').trim().slice(0, 12);
    return /search|검색|srch/i.test(d) && !/result|list/i.test(d) && (e.tagName !== 'DIV' || e.onclick);
  });
  if (!c) return null; c.click(); return (c.tagName + ' ' + (c.className || '')).slice(0, 60);
}
"""

FIND_INPUT_JS = """
([hintRe, exclRe]) => {
  const hint = new RegExp(hintRe, 'i'), excl = new RegExp(exclRe, 'i');
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 30 && r.height > 10 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const els = Array.from(document.querySelectorAll('input[type=text],input[type=search],input:not([type])'));
  const desc = (e) => [e.placeholder, e.name, e.id, e.title, e.getAttribute('aria-label'), e.className,
                       e.form && (e.form.id + ' ' + e.form.action + ' ' + e.form.className)].join(' ');
  const scored = els.filter(vis).map((e, i) => ({e, i, d: desc(e)})).filter(x => !excl.test(x.d))
    .map(x => ({...x, s: (hint.test(x.d) ? 2 : 0) + (x.e.getBoundingClientRect().top < 400 ? 1 : 0)}))
    .sort((a, b) => b.s - a.s);
  if (!scored.length) return null;
  const pick = scored[0].e; pick.setAttribute('data-ageshelf', '1');
  return scored[0].d.slice(0, 120);
}
"""

CLICK_SEARCH_LINK_JS = """
(re) => {
  const rx = new RegExp(re);
  const as = Array.from(document.querySelectorAll('a,button')).filter(a => rx.test(a.textContent.trim()));
  if (!as.length) return null;
  const a = as[0]; const href = a.getAttribute('href') || '';
  a.click(); return a.textContent.trim() + ' ' + href;
}
"""


def variants(s: str) -> list[str]:
    out = [urllib.parse.quote(s), urllib.parse.quote(s, safe=""), urllib.parse.quote_plus(s), s, s.replace(" ", "+")]
    for enc in ("euc-kr", "cp949"):
        try:
            out.append(urllib.parse.quote(s, encoding=enc))
            out.append(urllib.parse.quote_plus(s, encoding=enc))
        except UnicodeEncodeError:
            pass
    return sorted(set(out), key=len, reverse=True)


def templatize(url: str, query: str, key: str) -> str | None:
    for v in variants(query):
        if v and v in url:
            return url.replace(v, key)
    for dec in (urllib.parse.unquote_plus, urllib.parse.unquote):
        try:
            d = dec(url)
        except Exception:  # noqa: BLE001
            continue
        if query in d:                      # 인코딩 방식이 달라도 디코딩하면 같다 → 디코딩한 URL 을 템플릿으로
            return d.replace(query, key)
    return None


def hit(text: str, book: dict) -> bool:
    n = L.norm(text)
    t = L.norm(re.sub(r"[:(\[].*$", "", book["title"]))
    t = t[:14] if len(t) > 14 else t
    if len(t) < 3 or t not in n:
        return False
    pub = L.norm(book.get("publisher", ""))
    au = L.norm(re.split(r"[,;/(]| 글| 그림", book.get("authors", "") or "")[0])
    return (len(pub) >= 2 and pub in n) or (len(au) >= 2 and au in n)


async def body_text(page) -> str:
    try:
        return await page.evaluate("document.body ? document.body.innerText : ''")
    except Exception:  # noqa: BLE001
        return ""


async def settle(page, ms: int = 4000):
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(ms)


async def find_box(page):
    """메인 문서 → iframe 순으로 검색창을 찾아 (frame, locator) 반환."""
    for fr in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        try:
            d = await fr.evaluate(FIND_INPUT_JS, [SEARCH_HINT.pattern, EXCLUDE_HINT.pattern])
        except Exception:  # noqa: BLE001
            continue
        if d is not None:
            return fr, fr.locator("[data-ageshelf='1']").first
    return None, None


async def search_here(page, context, book: dict, query: str) -> tuple[str | None, str | None, str]:
    """현재 페이지에서 검색창을 찾아 query 를 입력·제출. (결과 URL, 결과 본문, 비고)
    POST 로 제출되는 폼은 그 요청을 잡아 두었다가 같은 파라미터의 GET 이 되는지 나중에 확인한다(page.ageshelf_posts)."""
    fr, box = await find_box(page)
    if box is None:                                       # 늦게 그려지는 검색창
        await page.wait_for_timeout(2500)
        fr, box = await find_box(page)
    if box is None:
        # 검색 아이콘/버튼을 눌러야 검색창이 나오는 사이트
        try:
            opened = await page.evaluate(OPEN_SEARCH_JS)
        except Exception:  # noqa: BLE001
            opened = None
        if opened:
            await page.wait_for_timeout(1200)
            fr, box = await find_box(page)
    if box is None:
        return None, None, "no-input"
    before = page.url
    n_pages = len(context.pages)
    posts = []

    def on_req(req):
        if req.method == "POST" and req.post_data and "text/html" in (req.headers.get("accept") or "text/html"):
            posts.append((req.url, req.post_data))
    page.on("request", on_req)
    try:
        try:
            await box.click(timeout=3000)
            await box.fill("")
            await box.type(query, delay=20)
        except Exception:  # noqa: BLE001
            try:                                          # 가려진 입력창: 값을 직접 넣고 이벤트 발생
                await box.evaluate("(e, v) => { e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); }", query)
                await box.focus()
            except Exception as e:  # noqa: BLE001
                return None, None, f"type-fail:{type(e).__name__}"
        try:
            await box.press("Enter")
        except Exception:  # noqa: BLE001
            await page.keyboard.press("Enter")
    finally:
        pass
    await settle(page)
    page.ageshelf_posts = posts
    page.remove_listener("request", on_req)
    target = page
    if len(context.pages) > n_pages:                      # 새 창으로 결과가 열린 경우
        target = context.pages[-1]
        await settle(target)
    text = await body_text(target)
    if hit(text, book):
        return target.url, text, "enter"
    # Enter 로 안 되면 검색 버튼 클릭
    try:
        clicked = await page.evaluate("""() => {
            const box = document.querySelector("[data-ageshelf='1']"); if (!box) return false;
            const form = box.form; const cand = [];
            if (form) cand.push(...form.querySelectorAll("button,input[type=submit],input[type=image],a"));
            let p = box.parentElement; for (let i = 0; i < 3 && p; i++, p = p.parentElement) cand.push(...p.querySelectorAll("button,a,input[type=submit],input[type=image]"));
            const b = cand.find(x => /검색|search|돋보기|찾기/i.test((x.textContent || '') + (x.title || '') + (x.className || '') + (x.value || '') + (x.alt || '') + (x.id || '')));
            if (b) { b.click(); return true; } return false; }""")
    except Exception:  # noqa: BLE001
        clicked = False
    if clicked:
        await settle(page)
        target = context.pages[-1] if len(context.pages) > n_pages else page
        text = await body_text(target)
        if hit(text, book):
            return target.url, text, "button"
    return (page.url if page.url != before else None), text, "no-hit"


async def probe_home(browser, home: str, books: list[dict], headed: bool) -> dict:
    rec = {"ok": False, "how": "browser", "checked": str(date.today())}
    context = await browser.new_context(user_agent=L.UA, locale="ko-KR", ignore_https_errors=True,
                                        viewport={"width": 1280, "height": 900})
    context.set_default_timeout(15000)
    page = await context.new_page()
    try:
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            rec["note"] = f"goto:{type(e).__name__}"
            return rec
        await settle(page, 1500)
        start_url = page.url
        url = text = None
        why = "no-book"
        search_page = start_url
        book = books[0]
        for bi, book in enumerate(books[:3]):              # 그 도서관이 소장한 책을 차례로(첫 책이 검색에 안 걸려도 다음 책으로)
            if bi:
                try:
                    await page.goto(search_page or start_url, wait_until="domcontentloaded", timeout=30000)
                    await settle(page, 1500)
                except Exception:  # noqa: BLE001
                    break
            # 1) 현재 페이지에서 검색
            url, text, why = await search_here(page, context, book, book["title"].strip())
            if not bi:
                search_page = page.url
            if not (url and text and hit(text, book)) and not bi:
                # 2) 검색 페이지 링크로 이동 후 재시도(첫 책에서만 — 찾은 검색 페이지는 다음 책에 재사용)
                try:
                    info = await page.evaluate(CLICK_SEARCH_LINK_JS, SEARCH_LINK_TEXT.pattern)
                except Exception:  # noqa: BLE001
                    info = None
                if info:
                    await settle(page, 2000)
                    if len(context.pages) > 1:
                        page = context.pages[-1]
                        await settle(page, 1000)
                    search_page = page.url
                    url, text, why = await search_here(page, context, book, book["title"].strip())
            if url and text and hit(text, book):
                break
            if why == "no-input":                          # 검색창이 없으면 책을 바꿔도 소용없음
                break
        rec["books_tried"] = [b["isbn"] for b in books[:bi + 1]]
        if not (url and text and hit(text, book)):
            rec["note"] = f"browse:{why}"
            if why != "no-input":                      # 검색창은 있었던 페이지 → 제목 복사 폴백용
                rec["page"] = search_page
            return rec
        rec["sample"] = [book["isbn"], book["title"][:40]]
        tpl = templatize(url, book["title"].strip(), "{t}")
        if tpl:
            tpl = L.strip_tokens(tpl)
            # ISBN 으로도 되는지 확인 → 되면 {q}
            try:
                p2 = await context.new_page()
                await p2.goto(tpl.replace("{t}", urllib.parse.quote(book["isbn"])), wait_until="domcontentloaded", timeout=25000)
                await settle(p2, 1500)
                if hit(await body_text(p2), book):
                    rec.update({"ok": True, "url": tpl.replace("{t}", "{q}"), "query": "isbn"})
                else:
                    # 토큰을 뺀 제목 템플릿이 그대로 동작하는지 확인
                    await p2.goto(tpl.replace("{t}", urllib.parse.quote(book["title"].strip())), wait_until="domcontentloaded", timeout=25000)
                    await settle(p2, 1500)
                    if hit(await body_text(p2), book):
                        rec.update({"ok": True, "url": tpl, "query": "title"})
                    else:
                        rec.update({"page": search_page, "note": "browse:url-not-reusable"})
                await p2.close()
            except Exception as e:  # noqa: BLE001
                rec.update({"ok": True, "url": tpl, "query": "title", "note": f"isbn-check:{type(e).__name__}"})
        else:
            rec.update({"page": search_page, "note": "browse:query-not-in-url", "result_url": url[:200]})
            # POST 로 검색한 사이트: 같은 파라미터를 GET 으로 붙여 열리는지 확인
            for purl, pdata in (getattr(page, "ageshelf_posts", None) or [])[:3]:
                cand = templatize(f"{purl.split('?')[0]}?{pdata}", book["title"].strip(), "{t}")
                if not cand:
                    continue
                cand = L.strip_tokens(cand)
                try:
                    p2 = await context.new_page()
                    await p2.goto(cand.replace("{t}", urllib.parse.quote(book["title"].strip())), wait_until="domcontentloaded", timeout=25000)
                    await settle(p2, 1500)
                    if hit(await body_text(p2), book):
                        await p2.goto(cand.replace("{t}", urllib.parse.quote(book["isbn"])), wait_until="domcontentloaded", timeout=25000)
                        await settle(p2, 1500)
                        isbn_ok = hit(await body_text(p2), book)
                        rec.update({"ok": True, "url": cand.replace("{t}", "{q}") if isbn_ok else cand, "query": "isbn" if isbn_ok else "title", "note": "post-as-get"})
                        rec.pop("result_url", None)
                    await p2.close()
                    if rec["ok"]:
                        break
                except Exception:  # noqa: BLE001
                    continue
        return rec
    finally:
        await context.close()


async def run(args):
    from playwright.async_api import async_playwright

    libs, hold, books, popular = L.load_inputs()
    out = json.load(open(L.OUT, encoding="utf-8")) if L.OUT.exists() else {}
    by_home = libs.groupby("home")["libCode"].apply(lambda s: list(s.astype(str))).to_dict()
    if args.homes:
        homes = [h for h in args.homes.split(",") if h]
    else:
        homes = [h for h in by_home if not out.get(h, {}).get("ok") and out.get(h, {}).get("how") != "manual"
                 and (not args.only_fail or h in out)]
        if not args.redo:
            homes = [h for h in homes if out.get(h, {}).get("how") != "browser"]
    # 도서관 수가 많은 홈페이지부터
    homes.sort(key=lambda h: -len(by_home.get(h, [])))
    print(f"브라우저 검증 대상 {len(homes)}개 홈페이지", flush=True)
    sem = asyncio.Semaphore(args.workers)
    t0 = time.time()
    done = [0]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)

        async def one(home):
            async with sem:
                prev = out.get(home) or {}
                excl = set(prev.get("books_tried") or []) if args.other_books else set()
                bks = L.pick_books(by_home.get(home, []), hold, books, popular, exclude=excl)
                try:
                    rec = await asyncio.wait_for(probe_home(browser, home, bks, args.headed), timeout=150)
                except asyncio.TimeoutError:
                    rec = {"ok": False, "how": "browser", "checked": str(date.today()), "note": "browse:timeout"}
                except Exception as e:  # noqa: BLE001
                    rec = {"ok": False, "how": "browser", "checked": str(date.today()), "note": f"browse:{type(e).__name__}"}
                rec["libs"] = len(by_home.get(home, []))
                if bks:
                    rec["held"] = bool(hold[hold.libCode.isin(by_home.get(home, []))].shape[0])
                old = out.get(home, {})
                if not rec["ok"] and old.get("note"):
                    rec["note_http"] = old["note"][:60]
                out[home] = rec
                done[0] += 1
                mark = "OK " if rec["ok"] else ("PG " if rec.get("page") else "-- ")
                print(f"  {mark}{done[0]}/{len(homes)} {rec['libs']:>2} {home[:60]} → {(rec.get('url') or rec.get('page') or rec.get('note',''))[:90]}", flush=True)
                if done[0] % 10 == 0 and not args.dry:
                    L.OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

        await asyncio.gather(*(one(h) for h in homes))
        await browser.close()
    if not args.dry:
        L.OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    n_ok = sum(1 for h in homes if out[h].get("ok"))
    n_pg = sum(1 for h in homes if not out[h].get("ok") and out[h].get("page"))
    print(f"완료 {time.time()-t0:.0f}s: 템플릿 {n_ok}, 검색 페이지만 {n_pg}, 실패 {len(homes)-n_ok-n_pg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only-fail", action="store_true")
    ap.add_argument("--redo", action="store_true", help="이전 브라우저 시도도 다시")
    ap.add_argument("--homes", default="", help="쉼표로 구분한 홈페이지 URL 만")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--dry", action="store_true", help="결과를 파일에 쓰지 않음(시험용)")
    ap.add_argument("--other-books", action="store_true", help="이전 시도와 다른 소장 책으로")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
