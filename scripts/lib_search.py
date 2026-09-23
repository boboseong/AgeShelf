"""도서관 홈페이지의 '이 책 검색' URL 템플릿 탐색·검증

상세 페이지의 소장 도서관 배지를 누르면 그 도서관 홈페이지(OPAC)에서 그 책을 검색한 결과 화면
(자료실·청구기호·대출 가능 여부)으로 바로 가게 하려면 홈페이지마다 다른 검색 URL 형식을 알아야 한다.

  discover  홈페이지(libs.parquet 의 homepage) 마다 리다이렉트(HTTP·meta refresh·JS location·frame)를 따라간 뒤
            검색 폼(텍스트 입력 1개 + hidden 값)과 검색 링크에서 GET URL 후보를 만든다.
            + 알려진 솔루션 패턴(이젠 plusSearchResultList.do 등)을 후보로 추가.
  verify    홈페이지마다 그 도서관이 소장한 책(holdings.parquet) 하나를 골라 후보 URL 을 열고, 응답 HTML 에
            책 제목이 나오면 성공. ISBN 검색이 안 되는 곳은 제목 검색({t})으로 재시도.
  결과      data/lib_search.json  { homepage: {"url": "...{q}...", "ok": true, "how": "form|vendor|manual", ...} }
            {q}=ISBN13, {t}=제목(URL 인코딩). export_site 가 libs.json 의 search 열로 내보낸다.
            "manual" 항목은 브라우저로 직접 확인해 손으로 넣은 것이라 discover/verify 가 덮어쓰지 않는다.

실행:  python scripts/lib_search.py discover            # 후보 수집 → data/processed/lib_search_candidates.json
       python scripts/lib_search.py verify [--only-fail] # 검증 → data/lib_search.json
       python scripts/lib_search.py report              # 지역별·홈페이지별 성공률
"""
from __future__ import annotations

import argparse
import html as htmlmod
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
OUT = ROOT / "data" / "lib_search.json"
CAND = PROC / "lib_search_candidates.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
try:                       # 오래된 도서관 서버(TLS 1.0/1.1, 약한 암호) 대응
    CTX.set_ciphers("DEFAULT:@SECLEVEL=0")
    CTX.minimum_version = ssl.TLSVersion.TLSv1
except (ssl.SSLError, ValueError, AttributeError):
    pass
TIMEOUT = 25
MAX_BYTES = 1_500_000


# ---------------------------------------------------------------- HTTP
def fetch(url: str, *, data: bytes | None = None) -> tuple[str, str, int]:
    """(html, final_url, status). 인코딩은 utf-8 → euc-kr 순으로 시도."""
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.5",
                                                          "Accept": "text/html,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
        raw = r.read(MAX_BYTES)
        final, status = r.geturl(), r.status
    m = re.search(rb'charset=["\']?\s*([\w-]+)', raw[:4000], re.I)
    encs = [m.group(1).decode("ascii", "ignore").lower()] if m else []
    for enc in encs + ["utf-8", "euc-kr", "cp949"]:
        try:
            return raw.decode(enc), final, status
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore"), final, status


def follow(url: str, hops: int = 4) -> tuple[str, str]:
    """HTTP 리다이렉트 외에 meta refresh / JS location / frameset / 자동 submit 폼까지 따라간 (html, final_url)."""
    html, final = "", url
    for _ in range(hops):
        html, final, _s = fetch(url)
        nxt = next_hop(html, final)
        if not nxt or nxt == final:
            break
        url = nxt
    return html, final


def next_hop(html: str, base: str) -> str | None:
    head = html[:6000]
    stripped = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", html, flags=re.S | re.I)
    text_len = len(re.sub(r"<[^>]+>|\s+", "", stripped))
    pats = [r'<meta[^>]*http-equiv=["\']?refresh["\']?[^>]*content=["\'][^"\']*url=([^"\'>\s]+)',
            r'(?:window\.|top\.|parent\.|document\.)?location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
            r'location\.replace\(\s*["\']([^"\']+)["\']',
            r'(?:top|parent|window)\.location\.href\s*=\s*["\']([^"\']+)["\']']
    for p in pats:
        m = re.search(p, head if "refresh" in p else html[:20000], re.I)
        if m and text_len < 400:                       # 실질 내용 없는 리다이렉트 페이지일 때만
            return urllib.parse.urljoin(base, htmlmod.unescape(m.group(1).strip()))
    m = re.search(r'<frame[^>]*src=["\']([^"\']+)["\'][^>]*name=["\']?(?:main|body|content)', html, re.I) or \
        re.search(r'<frame[^>]*src=["\']([^"\']+)["\']', html, re.I)
    if m and text_len < 400:
        return urllib.parse.urljoin(base, m.group(1))
    m = re.search(r'<iframe[^>]*src=["\']([^"\']+)["\']', html, re.I)
    if m and text_len < 200:
        return urllib.parse.urljoin(base, m.group(1))
    m = re.search(r'(?:sendUrl|actionUrl|url)\s*=\s*["\'](https?://[^"\']+)["\'][^;]*;[\s\S]{0,300}?\.submit\(\)', html)
    if m and text_len < 200:
        return m.group(1)
    return None


# ---------------------------------------------------------------- HTML 파싱
def attr(tag: str, name: str) -> str | None:
    m = re.search(rf'{name}\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>"\']+))', tag, re.I)
    return htmlmod.unescape(next((g for g in m.groups() if g is not None), "")) if m else None


def parse_forms(html: str, page_url: str) -> list[dict]:
    """텍스트 입력이 있는 폼 → {action, method, params(hidden/select), text(입력 이름), text_n}"""
    out = []
    for m in re.finditer(r"<form\b[^>]*>(.*?)</form>", html, re.S | re.I):
        tag = m.group(0)[: m.group(0).find(">") + 1]
        action = attr(tag, "action") or ""
        if action.lower().startswith("javascript"):
            action = ""
        method = (attr(tag, "method") or "get").lower()
        params, texts = {}, []
        for t in re.findall(r"<input\b[^>]*>", m.group(1), re.I):
            n, ty, v = attr(t, "name"), (attr(t, "type") or "text").lower(), attr(t, "value")
            if not n:
                continue
            if ty in ("text", "search"):
                texts.append(n)
            elif ty == "hidden":
                params.setdefault(n, v or "")
            elif ty in ("checkbox", "radio") and re.search(r"\bchecked\b", t, re.I):
                params.setdefault(n, v or "on")
        for s in re.finditer(r"<select\b[^>]*>(.*?)</select>", m.group(1), re.S | re.I):
            n = attr(s.group(0)[: s.group(0).find(">") + 1], "name")
            if not n or n in params:
                continue
            opts = re.findall(r"<option\b[^>]*>", s.group(1), re.I)
            sel = next((o for o in opts if re.search(r"\bselected\b", o, re.I)), opts[0] if opts else None)
            v = attr(sel, "value") if sel else None
            if v is not None:
                params[n] = v
        if not texts:
            continue
        out.append({"action": urllib.parse.urljoin(page_url, action) if action else re.sub(r"\?.*$", "", page_url),
                    "method": method, "params": params, "text": texts[0], "text_n": len(texts),
                    "id": attr(tag, "id") or attr(tag, "name") or ""})
    return out


SEARCH_LINK = re.compile(r'(?:href|action|src)\s*=\s*["\']([^"\'#]*(?:[sS]earch|srch|SRCH|Srch|booklist|bookList|BookList)[^"\']*)["\']')
SEARCH_JS = re.compile(r'["\'](/[^"\'\s]*(?:[sS]earch|srch|Srch)[^"\'\s]*\.(?:do|jsp|php|asp|aspx|html?|json))["\']')


def search_links(html: str, base: str) -> list[str]:
    seen, out = set(), []
    for m in list(SEARCH_LINK.finditer(html)) + list(SEARCH_JS.finditer(html)):
        u = urllib.parse.urljoin(base, htmlmod.unescape(m.group(1).strip()))
        if u in seen or not u.startswith("http"):
            continue
        low = u.lower()
        if re.search(r"\.(css|js|png|jpg|gif|svg|ico)(\?|$)|login|member|mypage|research|academic|autocomplete|research", low):
            continue
        seen.add(u)
        out.append(u)
    prio = lambda u: (0 if re.search(r"simple|total|tot\b|integr|plusSearchSimple|book/search$|searchSimple|bookSearch|mainSearch", u, re.I) else 1, len(u))
    return sorted(out, key=prio)


# ---------------------------------------------------------------- 후보 URL
TOKEN_PARAM = re.compile(r"(?:^|(?<=[?&]))[^=&]*(?:csrf|token|signature|jsessionid|sessionid|_ts|timestamp)[^=&]*=[^&]*&?", re.I)


def strip_tokens(url: str) -> str:
    """세션마다 바뀌는 CSRF 토큰·서명 파라미터는 사용자에게 줄 수 없으니 뺀다."""
    base, _, qs = url.partition("?")
    base = re.sub(r";jsessionid=[^?/]*", "", base, flags=re.I)
    qs = TOKEN_PARAM.sub("", qs).rstrip("&")
    return f"{base}?{qs}" if qs else base


def form_candidates(forms: list[dict]) -> list[dict]:
    out = []
    for f in forms:
        if f["text_n"] > 3:
            continue
        p = dict(f["params"])
        p[f["text"]] = "{q}"
        # 검색 종류(select) 값이 비어 있으면 흔한 '전체' 값 시도
        qs = urllib.parse.urlencode(p, safe="{}")
        out.append({"url": f"{f['action']}?{qs}", "how": "form", "method": f["method"], "src": f["id"]})
    return out


VENDOR = [
    # (홈페이지·검색페이지 HTML 에서 찾는 시그니처, 후보 URL 생성 함수)
    ("plusSearchResultList.do", lambda html, base: [
        {"url": urllib.parse.urljoin(base, m.group(1)) + "?searchType=SIMPLE&searchCategory=ALL&searchKey=ALL&searchKeyword={q}", "how": "vendor:ezn"}
        for m in [re.search(r'["\']([^"\']*plusSearchResultList\.do)["\']', html)] if m]),
    ("/book/search", lambda html, base: [
        {"url": urllib.parse.urljoin(base, m.group(1)) + "?searchType=SIMPLE&searchInput={q}&display=10", "how": "vendor:portal"}
        for m in [re.search(r'["\']([^"\'?]*/menu/\d+/book/search)["\']', html)] if m]),
    ("search/tot", lambda html, base: [
        {"url": urllib.parse.urljoin(base, "/search/tot/result?st=KWRD&si=TOTAL&q={q}"), "how": "vendor:las"}]),
    ("wdDataSearch", lambda html, base: [
        {"url": urllib.parse.urljoin(base, m.group(1)) + "?mod=wdDataSearch&act=searchResultList&searchType=simple&searchWord={q}", "how": "vendor:dls"}
        for m in [re.search(r'["\']([^"\'?]*index\.php)\?mod=wdDataSearch', html)] if m]),
    ("bookSearchList.do", lambda html, base: [
        {"url": urllib.parse.urljoin(base, m.group(1)) + "?search_txt={q}&search_type=all", "how": "vendor:kolas"}
        for m in [re.search(r'["\']([^"\'?]*bookSearchList\.do)["\']', html)] if m]),
]


def discover_one(home: str) -> dict:
    rec = {"home": home, "pages": [], "cands": [], "err": None}
    try:
        html, final = follow(home)
    except Exception as e:  # noqa: BLE001
        rec["err"] = f"{type(e).__name__}: {str(e)[:120]}"
        return rec
    rec["final"] = final
    seen_pages, cands = {final: html}, []
    cands += form_candidates(parse_forms(html, final))
    links = search_links(html, final)
    rec["links"] = links[:20]
    for u in links[:5]:
        if u in seen_pages:
            continue
        try:
            h2, f2 = follow(u, hops=2)
        except Exception as e:  # noqa: BLE001
            rec["pages"].append({"url": u, "err": str(e)[:80]})
            continue
        seen_pages[f2] = h2
        fc = form_candidates(parse_forms(h2, f2))
        rec["pages"].append({"url": u, "final": f2, "forms": len(fc)})
        cands += fc
    for sig, gen in VENDOR:
        for page_url, h in seen_pages.items():
            if sig in h:
                try:
                    cands += gen(h, page_url)
                except Exception:  # noqa: BLE001
                    pass
                break
    # 중복 제거(순서 유지), 텍스트 필드가 검색어 같지 않은 폼(id/pw 등) 제외
    uniq, seen = [], set()
    for c in cands:
        if re.search(r"(?:^|[?&])(?:userId|user_id|id|pw|passwd|password|email|zipcode)=", c["url"], re.I):
            continue
        c["url"] = strip_tokens(c["url"])
        if re.search(r"blog\.naver|cafe\.(?:naver|daum)|tistory\.com", c["url"], re.I):
            continue
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        uniq.append(c)
    rec["cands"] = uniq[:12]
    return rec


# ---------------------------------------------------------------- 검증
def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", htmlmod.unescape(s or "")).lower()


def page_text(html: str) -> str:
    """스크립트·스타일·input 태그·value 속성(검색어 되돌려 보여주는 부분)을 뺀 본문."""
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<title>.*?</title>", " ", html, flags=re.S | re.I)
    body = re.sub(r"<input[^>]*>|<textarea.*?</textarea>", " ", body, flags=re.S | re.I)
    body = re.sub(r"""(?:value|placeholder|title|alt|data-[\w-]+)\s*=\s*(?:"[^"]*"|'[^']*')""", " ", body, flags=re.I)
    return norm(body)


HOLD_WORDS = re.compile(r"청구기호|대출\s*가능|대출\s*중|대출불가|소장처|소장\s*위치|소장도서관|소장\s*정보|자료실|등록번호|비치중|예약")


def title_hit(html: str, book: dict, strict: bool) -> bool:
    """제목이 본문에 있으면 성공. strict(제목으로 검색한 경우)는 검색어를 되돌려 보여준 것일 수 있으니
    출판사나 저자도 함께 있어야 한다."""
    text = page_text(html)
    t = norm(re.sub(r"[:(\[].*$", "", book["title"]))
    t = t[:14] if len(t) > 14 else t
    if len(t) < 3 or t not in text:
        return False
    if not strict:
        return True
    pub = norm(book.get("publisher", ""))
    au = norm(re.split(r"[,;/(]| 글| 그림", book.get("authors", "") or "")[0])
    return (len(pub) >= 2 and pub in text) or (len(au) >= 2 and au in text)


def try_url(url: str, method: str, book: dict) -> tuple[str | None, str]:
    """성공하면 (템플릿, 비고). {q} 를 ISBN 으로, 실패하면 제목으로 재시도."""
    for key, val in (("{q}", book["isbn"]), ("{t}", book["title"])):
        u = url.replace("{q}", urllib.parse.quote(val))
        strict = key == "{t}"
        try:
            if method == "post":
                base, _, qs = u.partition("?")
                html, _f, _s = fetch(base, data=qs.encode())
                if not title_hit(html, book, strict):
                    html, _f, _s = fetch(u)
            else:
                html, _f, _s = fetch(u)
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}"
        if title_hit(html, book, strict):
            if not HOLD_WORDS.search(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I))):
                return None, "no-holding-words"           # 구청 통합검색·블로그 등 도서관 목록이 아닌 검색
            # 음성 검사: 없는 검색어로도 이 책이 보이면 검색 결과가 아니라 인기도서 위젯·베스트 목록 같은 고정 영역 → 오탐
            nq = "9790000000000" if key == "{q}" else "쿼크쯔잉 없는책"
            try:
                nh, _f, _s = fetch(url.replace("{q}", urllib.parse.quote(nq)))
                if title_hit(nh, book, strict):
                    return None, "always-shows"
            except Exception:  # noqa: BLE001
                pass
            return url.replace("{q}", key), "isbn" if key == "{q}" else "title"
    return None, "no-title"


def pick_books(codes: list[str], hold: pd.DataFrame, books: dict, popular: list[str],
               n: int = 3, exclude: set[str] | frozenset = frozenset()) -> list[dict]:
    """검증에 쓸 책: 그 홈페이지의 도서관들이 실제 소장한 책 중 소장 도서관 수·대출 수가 많은 순으로 n권.
    exclude(이전에 쓴 책)는 건너뛰고, 제목 앞부분이 겹치는 책(같은 시리즈)도 한 권만. 소장 정보가 없으면 전국 인기 그림책."""
    sub = hold[hold.libCode.isin(codes)]
    vc = sub.isbn13.value_counts()
    order = sorted(vc.index, key=lambda i: (-vc[i], -(books.get(i) or {}).get("loans", 0)))
    out, seen = [], set()
    for isbn in order:
        b = books.get(isbn)
        if not b or isbn in exclude:
            continue
        key = norm(re.sub(r"[:(\[].*$", "", b["title"]))[:6]
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        out.append(b)
        if len(out) >= n:
            break
    return out or [books[i] for i in popular if i in books]


def load_inputs():
    libs = pd.read_parquet(PROC / "libs.parquet")
    libs["home"] = libs.homepage.fillna("").astype(str).str.strip()
    libs = libs[libs.home.str.match(r"^https?://")]
    hold = pd.read_parquet(PROC / "holdings.parquet")
    hold = hold[hold.libCode.astype(str) != ""]
    bm = pd.read_parquet(PROC / "books_metrics.parquet", columns=["isbn13", "bookname", "authors", "publisher", "total_loans", "is_picture"])
    books = {r.isbn13: {"isbn": r.isbn13, "title": r.bookname, "authors": r.authors or "", "publisher": r.publisher or "",
                        "loans": int(r.total_loans or 0)} for r in bm.itertuples()}
    want = ["장수탕 선녀님", "수박 수영장", "알사탕", "달 샤베트", "이상한 엄마"]
    pop = bm.sort_values("total_loans", ascending=False)
    popular = [next((r.isbn13 for r in pop.itertuples() if str(r.bookname).strip().startswith(t)), None) for t in want]
    popular = [i for i in popular if i][:3]
    return libs, hold, books, popular


def cmd_discover(args):
    libs, _h, _b, _p = load_inputs()
    homes = sorted(libs.home.unique())
    old = json.load(open(CAND, encoding="utf-8")) if CAND.exists() and not args.fresh else {}
    todo = [h for h in homes if h not in old]
    print(f"홈페이지 {len(homes):,}개 (도서관 {len(libs):,}곳) · 남은 {len(todo):,}개", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        for i, rec in enumerate(ex.map(discover_one, todo), 1):
            old[rec["home"]] = rec
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} · {time.time()-t0:.0f}s", flush=True)
                CAND.write_text(json.dumps(old, ensure_ascii=False, indent=0), encoding="utf-8")
    CAND.write_text(json.dumps(old, ensure_ascii=False, indent=0), encoding="utf-8")
    n_err = sum(1 for r in old.values() if r.get("err"))
    n_c = sum(1 for r in old.values() if r.get("cands"))
    print(f"완료: 오류 {n_err}, 후보 있음 {n_c}, 후보 없음 {len(old)-n_err-n_c} → {CAND}")


def cmd_verify(args):
    libs, hold, books, popular = load_inputs()
    cand = json.load(open(CAND, encoding="utf-8"))
    out = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
    by_home = libs.groupby("home")["libCode"].apply(lambda s: list(s.astype(str))).to_dict()
    todo = []
    for home, codes in by_home.items():
        cur = out.get(home)
        if cur and cur.get("how") == "manual":
            continue
        if cur and cur.get("ok") and not args.recheck:
            continue
        if args.only_fail and cur is None:
            continue
        todo.append((home, codes))
    print(f"검증 대상 {len(todo):,}개 홈페이지", flush=True)

    def work(item):
        home, codes = item
        rec = {"ok": False, "checked": str(date.today()), "libs": len(codes)}
        prev = out.get(home) or {}
        excl = {prev["sample"][0]} if args.other_books and prev.get("sample") else set()
        cands_books = pick_books(codes, hold, books, popular, exclude=excl)
        if not cands_books:
            rec["note"] = "no-book"
            return home, rec
        rec["sample"] = [cands_books[0]["isbn"], cands_books[0]["title"][:40]]
        rec["held"] = bool(hold[hold.libCode.isin(codes)].shape[0])
        c = cand.get(home) or {}
        if c.get("err"):
            rec["note"] = "fetch-error: " + c["err"]
            return home, rec
        if not c.get("cands"):
            rec["note"] = "no-candidates"
            return home, rec
        notes = []
        for cd in c["cands"]:
            if re.search(r"blog\.naver|cafe\.(?:naver|daum)|tistory\.com", cd["url"], re.I):
                continue
            for book in cands_books:
                tpl, why = try_url(cd["url"], cd.get("method", "get"), book)
                if tpl:
                    rec.update({"ok": True, "url": tpl, "how": cd["how"], "query": why, "sample": [book["isbn"], book["title"][:40]]})
                    return home, rec
                notes.append(why)
                if why not in ("no-title", "always-shows", "no-holding-words"):
                    break
        rec["note"] = "tried " + ",".join(notes)
        return home, rec

    def keep_page(home, rec):
        """정적 검증이 실패해도 브라우저가 찾아 둔 검색 페이지(제목 복사 폴백)는 잃지 않게."""
        prev = out.get(home) or {}
        if not rec.get("ok") and prev.get("page"):
            rec.update({"page": prev["page"], "how": prev.get("how", "browser")})
        rec["books_tried"] = [b for b in [rec.get("sample", [None])[0]] if b]
        return rec

    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        for i, (home, rec) in enumerate(ex.map(work, todo), 1):
            out[home] = keep_page(home, rec)
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} · {time.time()-t0:.0f}s · 성공 {sum(1 for r in out.values() if r.get('ok'))}", flush=True)
                OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    cmd_report(args)


def cmd_report(_args):
    libs, hold, _b, _p = load_inputs()
    out = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
    libs["ok"] = libs.home.map(lambda h: bool(out.get(h, {}).get("ok")))
    libs["has_hold"] = libs.libCode.astype(str).isin(set(hold.libCode.astype(str)))
    print(f"홈페이지 {libs.home.nunique():,}개 중 성공 {sum(1 for r in out.values() if r.get('ok')):,}개 · "
          f"도서관 {len(libs):,}곳 중 검색 링크 {int(libs.ok.sum()):,}곳")
    g = libs.groupby("region_name").agg(libs=("libCode", "size"), ok=("ok", "sum"), hold=("has_hold", "sum"))
    g["ok%"] = (100 * g.ok / g.libs).round(0).astype(int)
    print(g.sort_values("libs", ascending=False).to_string())
    notes = pd.Series([re.sub(r":.*", "", r.get("note", "")) for r in out.values() if not r.get("ok")]).value_counts()
    print("\n실패 사유:\n" + notes.to_string())
    hows = pd.Series([r.get("how") for r in out.values() if r.get("ok")]).value_counts()
    print("\n성공 방식:\n" + hows.to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["discover", "verify", "report"])
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--fresh", action="store_true", help="discover: 기존 후보를 버리고 전부 다시")
    ap.add_argument("--recheck", action="store_true", help="verify: 성공한 것도 다시")
    ap.add_argument("--only-fail", action="store_true", help="verify: 이전에 실패한 것만")
    ap.add_argument("--other-books", action="store_true", help="verify: 이전 검증에 쓴 책은 빼고 다른 소장 책으로")
    args = ap.parse_args()
    {"discover": cmd_discover, "verify": cmd_verify, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
