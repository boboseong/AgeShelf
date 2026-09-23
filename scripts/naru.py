"""도서관정보나루 Open API 최소 클라이언트.

- 키는 프로젝트 루트 .env 의 DATA4LIBRARY_KEY 에서 읽는다.
- 모든 호출은 format=json.
- data/raw/ 에 URL 해시 기반 캐시를 두어 같은 호출을 반복하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
BASE = "https://www.data4library.kr/api/"


class NaruError(RuntimeError):
    pass


def load_key() -> str:
    key = os.environ.get("DATA4LIBRARY_KEY")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATA4LIBRARY_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise NaruError("DATA4LIBRARY_KEY 가 .env 또는 환경변수에 없습니다.")
    return key


def call(endpoint: str, *, cache: bool = True, retries: int = 8, **params) -> dict:
    """endpoint 예: 'loanItemSrch', 'srchDtlList', 'extends/loanItemSrchByLib'."""
    params = {k: v for k, v in params.items() if v is not None}
    query = urllib.parse.urlencode({**params, "format": "json"})
    url = f"{BASE}{endpoint}?authKey={load_key()}&{query}"

    cache_file = None
    if cache:
        digest = hashlib.sha1(f"{endpoint}?{query}".encode()).hexdigest()[:16]
        cache_file = RAW / endpoint.replace("/", "_") / f"{digest}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except (OSError, json.JSONDecodeError) as e:   # URLError·TimeoutError·ConnectionResetError 모두 OSError
            last_err = e
            time.sleep(min(120, 5 * 2 ** attempt))   # 5,10,20,40,80,120,120s — 504 게이트웨이 타임아웃 대비
    else:
        raise NaruError(f"{endpoint} 호출 실패: {last_err}")

    resp = data.get("response", data)
    if "errCode" in resp or "error" in resp:
        raise NaruError(f"{endpoint}: {resp.get('errCode')} {resp.get('error')}")

    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def docs(data: dict) -> list[dict]:
    """loanItemSrch 류 응답의 docs 배열을 평탄한 dict 리스트로."""
    resp = data.get("response", data)
    out = []
    for d in resp.get("docs", []) or []:
        d = d.get("doc", d)
        if "loan_count" in d:
            try:
                d["loan_count"] = int(d["loan_count"])
            except (TypeError, ValueError):
                d["loan_count"] = 0
        out.append(d)
    return out


def popular(*, from_age=None, to_age=None, age=None, start=None, end=None,
            add_code=None, region=None, page=1, page_size=500, cache=True, **extra) -> list[dict]:
    """인기대출도서(loanItemSrch) 한 페이지."""
    return docs(call("loanItemSrch", cache=cache, from_age=from_age, to_age=to_age, age=age,
                     startDt=start, endDt=end, addCode=add_code, region=region,
                     pageNo=page, pageSize=page_size, **extra))


def popular_all(max_items: int = 5000, page_size: int = 500, **kw) -> list[dict]:
    """5,000건 상한까지 페이지네이션."""
    out: list[dict] = []
    page = 1
    while len(out) < max_items:
        chunk = popular(page=page, page_size=page_size, **kw)
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
    return out[:max_items]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(call("libSrch", pageSize=1, cache=False), ensure_ascii=False)[:300])
