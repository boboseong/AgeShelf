// 공용 카드 렌더러 + 색인 행 디코더 + 도서관 선택 저장
(function () {
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const Q36 = '0123456789abcdefghijklmnopqrstuvwxyz';
  const SHAPES = ['대칭', '더 큰 아이 쪽으로 넓음', '더 어린 아이부터 봄'];
  function centLabel(pct) { return pct >= 80 ? '딱 맞음' : pct >= 50 ? '잘 맞음' : pct >= 30 ? '가장자리' : '다른 나이 책'; }
  // 비중 13자(0~35) → 숫자 배열 (최고점 대비 %)
  window.AgeShelfProf = function (q) {
    if (Array.isArray(q)) return q;
    return [...String(q || '')].map((ch) => Math.round((Q36.indexOf(ch) / 35) * 100));
  };
  // 나이별 색인 행 → 카드 객체. sk 시리즈·ai 작가·w 판 번호는 색인 안에 2권 이상인 묶음만 0 이상(나머지 -1)
  window.AgeShelfRow = function (r, idx) {
    const pub = idx.pubs[r[3]] || ['', ''];
    const cv = r[5] && r[5][1] ? ((r[5][0] >= 0 ? idx.cover_prefix[r[5][0]] : '') + r[5][1]) : '';
    return { i: r[0], t: r[1], au: r[2], pn: pub[0], pu: pub[1], yr: r[4], cv, band: r[6], med: r[7], sh: SHAPES[r[8]] || '',
      pic: r[9], k2: r[10], fm: idx.forms[r[11]] || '', flb: r[12], fit: r[13], pr: r[14], ce: r[15], ln: r[16], sk: r[17] ?? -1, prof: r[18], elig: r[19], tpb: r[20] || 0,
      ai: r[21] ?? -1, w: r[22] ?? -1, an: r[21] >= 0 ? (idx.authors?.[r[21]] ?? '') : '', sn: r[17] >= 0 ? (idx.series?.[r[17]] ?? '') : '' };
  };
  window.AgeShelfCard = function (b, opts) {
    const { rank, age, ageLabel, profAges, base = '', kdcNames = {}, flagTags = [], topics = [], sortBy = 'fit', more = null } = opts;
    const prof = window.AgeShelfProf(b.prof);
    const max = Math.max(...prof, 1);
    const bars = profAges.map((a, k) => `<i class="${a === age ? 'on' : ''}" style="height:${Math.max(6, Math.round(100 * (prof[k] || 0) / max))}%" title="${a}세 ${prof[k] || 0}%"></i>`).join('');
    const axis = profAges.map((a) => `<span>${a}</span>`).join('');
    const pct = Math.floor((b.ce ?? 0) * 100);
    const chips = [];
    if (b.k2 && kdcNames[b.k2]) chips.push(`<span class="badge tag" data-f="kdc2" data-v="${esc(b.k2)}">${esc(kdcNames[b.k2])}</span>`);
    if (b.fm) chips.push(`<span class="badge tag" data-f="form" data-v="${esc(b.fm)}">${esc(b.fm)}</span>`);
    const fl = Array.isArray(b.fl) ? b.fl : flagTags.map((_, i) => i).filter((i) => ((b.flb || 0) >> i) & 1);
    const tp = Array.isArray(b.tp) ? b.tp : topics.map((_, i) => i).filter((i) => ((b.tpb || 0) >> i) & 1);
    tp.slice(0, 2).forEach((c) => { if (topics[c]) chips.push(`<span class="badge tag topic" data-f="topic" data-v="${c}">${esc(topics[c])}</span>`); });
    fl.slice(0, 2).forEach((c) => { if (flagTags[c]) chips.push(`<span class="badge tag flag" data-f="flag" data-v="${c}">${esc(flagTags[c])}</span>`); });
    const href = `${base}/b/?isbn=${esc(b.i)}`;
    return `<div class="card">
      <div><a href="${href}">${b.cv ? `<img class="cover" src="${esc(b.cv)}" alt="" loading="lazy">` : '<div class="cover"></div>'}</a>${window.AgeShelfActs(b.i, b.t)}</div>
      <a href="${href}"><div class="rank">${sortBy === 'cent'   // 연령 적합도순: 정렬 기준인 적합도를 앞에 굵게
        ? `#${rank} · 연령 적합도 <b>${pct}%</b> (${centLabel(pct)}) · 추천도 ${Number(b.fit).toFixed(1)} · ${esc(ageLabel)} 대출 ${Number(b.ln).toLocaleString()}건`
        : `#${rank} · 추천도 <b>${Number(b.fit).toFixed(1)}</b> · ${esc(ageLabel)} 대출 ${Number(b.ln).toLocaleString()}건 · 연령 적합도 ${pct}% (${centLabel(pct)})`}</div>
      <div class="title">${esc(b.t)}</div><div class="meta">${esc(b.au)} · ${esc(b.pu)} · ${esc(b.yr)}</div>
      <div class="badges"><span class="badge band">추천 ${esc(b.band)}</span><span class="badge">중앙 ${Number(b.med).toFixed(1)}세</span>${b.sp >= 2 ? '<span class="badge">이 나이에 집중</span>' : ''}${b.sh && b.sh !== '대칭' ? `<span class="badge">${esc(b.sh)}</span>` : ''}${b.pic ? '<span class="badge pic">그림책</span>' : ''}${chips.join('')}</div>
      <div class="bars">${bars}</div><div class="axis">${axis}</div></a>${groupLinks(b, more)}</div>`;
  };
  // '작가당 1권'·'시리즈당 1권'으로 숨긴 책이 있으면 대표 카드에 "같은 작가 책 +N권" (누르면 그 작가·시리즈만 보기)
  function groupLinks(b, more) {
    if (!more) return '';
    const out = [];
    if (more.a > 0 && b.an) out.push(`<button type="button" class="grp-more" data-g="au" data-v="${esc(b.an)}" title="${esc(b.an)}의 책만 보기">같은 작가 책 +${more.a}권</button>`);
    if (more.s > 0 && b.sn) out.push(`<button type="button" class="grp-more" data-g="ser" data-v="${esc(b.sn)}" title="“${esc(b.sn)}” 시리즈만 보기">같은 시리즈 +${more.s}권</button>`);
    return out.length ? `<div class="grp">${out.join('')}</div>` : '';
  }
  // 선택한 도서관 (localStorage). {code, name, region}
  const LIB_KEY = 'ageshelf.lib';
  window.AgeShelfLib = {
    get() { try { return JSON.parse(localStorage.getItem(LIB_KEY) || 'null'); } catch (e) { return null; } },
    set(v) {
      try { v ? localStorage.setItem(LIB_KEY, JSON.stringify(v)) : localStorage.removeItem(LIB_KEY); } catch (e) {}
      window.AgeShelfSyncActs?.();
      if (v) this.directory().then(() => window.AgeShelfSyncActs?.()).catch(() => {});
    },
    async isbns(base, code) {   // 도서관 소장 ISBN Set
      const r = await fetch(`${base}/data/libs/${code}.json?v=${window.__dataVersion || ''}`); if (!r.ok) return new Set();
      const j = await r.json(); return new Set(j.isbns || []);
    },
    async directory(base = window.__base || '') {
      if (window.__libsDir) return window.__libsDir;
      const r = await fetch(`${base}/data/libs.json?v=${window.__dataVersion || ''}`); const j = await r.json();
      const f = j.fields; window.__libsDir = { regions: j.regions, libs: j.libs.map((row) => Object.fromEntries(f.map((k, i) => [k, row[i]]))) };
      return window.__libsDir;
    },
    dist(lat1, lon1, lat2, lon2) {   // km
      const R = 6371, dLat = (lat2 - lat1) * Math.PI / 180, dLon = (lon2 - lon1) * Math.PI / 180;
      const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
      return 2 * R * Math.asin(Math.sqrt(a));
    },
    // 도서관 검색창에 넣을 제목: "(시리즈) 제목 :부제 =Parallel" → "제목"
    shortTitle(title) {
      const full = String(title || '').trim();
      const m = full.match(/^[(\[][^)\]]*[)\]]\s*(.+)$/);
      return (m ? m[1] : full).replace(/\s*[:=;(\[].*$/, '').trim() || full;
    },
    // 도서관별 링크: search 템플릿({q}=ISBN, {t}=제목) → 검색 결과, 자리표시자 없는 검색 페이지 → 제목 복사 후 열기, 없으면 홈페이지
    link(l, isbn, title) {
      const s = l.search || '';
      if (s.startsWith('POST ')) {
        // "POST <인코딩> <action> <k=v&k={t}…>" — 검색어를 URL 로 받지 않는 도서관: 누르면 폼을 만들어 새 탭으로 제출
        return { href: s.split(' ')[2], direct: true, post: s };
      }
      if (s.includes('{q}') || s.includes('{t}')) {
        return { href: s.replace('{q}', encodeURIComponent(isbn)).replace('{t}', encodeURIComponent(this.shortTitle(title))), direct: true };
      }
      return { href: s || l.home || '', direct: false };
    },
    submitPost(tpl, isbn, title) {
      const [, enc, action, qs] = tpl.split(' ');
      const t = this.shortTitle(title);
      const f = document.createElement('form');
      f.method = 'POST'; f.action = action; f.target = '_blank'; f.acceptCharset = enc || 'utf-8'; f.style.display = 'none';
      for (const [k, v] of new URLSearchParams(qs || '')) {
        const i = document.createElement('input'); i.type = 'hidden'; i.name = k;
        i.value = v.replace('{t}', t).replace('{q}', isbn); f.appendChild(i);
      }
      document.body.appendChild(f); f.submit(); f.remove();
    },
    copyTitle(title) {
      const t = this.shortTitle(title);
      try { navigator.clipboard?.writeText(t)?.catch(() => {}); } catch (e) { /* 무시 */ }
      window.AgeShelfToast(`“${t}” 를 복사했습니다. 도서관 검색창에 붙여 넣으세요.`);
    },
    // 선택한 도서관에서 이 책 검색 (클릭 핸들러 안에서 바로 불러야 팝업 차단에 안 걸린다 → 도서관 목록은 미리 받아 둔다)
    search(isbn, title) {
      const lib = this.get(); if (!lib) return;
      const l = window.__libsDir?.libs.find((x) => x.code === lib.code);
      if (!l) { this.directory().catch(() => {}); window.AgeShelfToast('도서관 정보를 불러오는 중입니다. 잠시 후 다시 눌러 주세요.'); return; }
      const k = this.link(l, isbn, title);
      if (k.post) { this.submitPost(k.post, isbn, title); return; }
      if (!k.href) { window.AgeShelfToast(`${lib.name}은(는) 온라인 검색 주소가 없습니다.`); return; }
      if (!k.direct) this.copyTitle(title);
      const w = window.open(k.href, '_blank'); if (w) w.opener = null;
    },
  };
  window.AgeShelfToast = function (msg) {
    let el = document.getElementById('copyToast');
    if (!el) { el = document.createElement('div'); el.id = 'copyToast'; el.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#333;color:#fff;padding:8px 14px;border-radius:8px;font-size:13px;z-index:99;opacity:.95;max-width:90vw'; document.body.appendChild(el); }
    el.textContent = msg; el.hidden = false;
    clearTimeout(el._t); el._t = setTimeout(() => { el.hidden = true; }, 3500);
  };

  // 표지 아래 버튼: [도서관] 필터에서 고른 도서관의 검색 결과, [알라딘] 상품 페이지(ISBN).
  // held === false: 선택한 도서관 소장 목록에 없는 책(상세 페이지) → 비활성. 목록 화면은 도서관을 고르면 소장 도서만 보이므로 넘기지 않는다
  const LIB_OFF = '나이별 목록의 필터에서 도서관을 고르면 켜집니다';
  function libBtnState(held) {
    const lib = window.AgeShelfLib.get();
    if (!lib) return { on: false, tip: LIB_OFF };
    if (held === false) return { on: false, tip: `${lib.name} 소장 정보 없음 (도서관정보나루 기준)` };
    const l = window.__libsDir?.libs.find((x) => x.code === lib.code);
    const direct = !l || window.AgeShelfLib.link(l, '', '').direct;
    return { on: true, tip: direct ? `${lib.name}에서 이 책 검색` : `${lib.name} 검색 페이지를 열고 제목을 복사합니다` };
  }
  function paintLibBtn(el, st) {
    el.setAttribute('aria-disabled', st.on ? 'false' : 'true'); el.title = st.tip; el.textContent = st.on ? '도서관 ↗' : '도서관';
  }
  window.AgeShelfActs = function (isbn, title, held) {
    const st = libBtnState(held);
    return `<div class="acts"><button type="button" class="act" data-act="lib" data-isbn="${esc(isbn)}" data-title="${esc(title)}"${held === false ? ' data-held="0"' : ''} aria-disabled="${st.on ? 'false' : 'true'}" title="${esc(st.tip)}">${st.on ? '도서관 ↗' : '도서관'}</button>`
      + `<a class="act" href="https://www.aladin.co.kr/shop/wproduct.aspx?ISBN=${encodeURIComponent(isbn)}" target="_blank" rel="noopener" title="알라딘에서 책 정보 보기">알라딘 ↗</a></div>`;
  };
  // 도서관 선택이 바뀌었거나 서버에서 그린 카드: 버튼 상태를 다시 칠한다
  window.AgeShelfSyncActs = function (root = document) {
    root.querySelectorAll('.act[data-act="lib"]').forEach((el) => paintLibBtn(el, libBtnState(el.dataset.held === '0' ? false : undefined)));
  };
  document.addEventListener('click', (e) => {
    const el = e.target.closest?.('.act[data-act="lib"]'); if (!el) return;
    e.preventDefault();
    if (el.getAttribute('aria-disabled') !== 'true') window.AgeShelfLib.search(el.dataset.isbn, el.dataset.title);
  });
  document.addEventListener('DOMContentLoaded', () => {
    window.AgeShelfSyncActs();
    if (window.AgeShelfLib.get()) window.AgeShelfLib.directory().then(() => window.AgeShelfSyncActs()).catch(() => {});
  });
  // 순서를 유지하며 묶음당 한 권만: 같은 책 다른 판(w)은 항상, 작가(ai)·시리즈(sk)는 켰을 때만. 번호 -1 은 한 권짜리라 그대로 둔다.
  // 반환 { rows, more }: more 는 대표 카드 isbn → { a, s } (목록 안에서 같은 작가·시리즈인 다른 책 수)
  window.AgeShelfOnePer = function (rows, byAuthor, bySeries) {
    const seenW = new Set();
    rows = rows.filter((b) => b.w < 0 || (!seenW.has(b.w) && seenW.add(b.w)));
    const more = new Map();
    if (!byAuthor && !bySeries) return { rows, more };
    const cntA = new Map(), cntS = new Map();
    for (const b of rows) {
      if (byAuthor && b.ai >= 0) cntA.set(b.ai, (cntA.get(b.ai) || 0) + 1);
      if (bySeries && b.sk >= 0) cntS.set(b.sk, (cntS.get(b.sk) || 0) + 1);
    }
    const seenA = new Set(), seenS = new Set(), out = [];
    for (const b of rows) {
      const a = byAuthor && b.ai >= 0, s = bySeries && b.sk >= 0;
      if ((a && seenA.has(b.ai)) || (s && seenS.has(b.sk))) continue;
      if (a) seenA.add(b.ai);
      if (s) seenS.add(b.sk);
      out.push(b);
      more.set(b.i, { a: a ? cntA.get(b.ai) - 1 : 0, s: s ? cntS.get(b.sk) - 1 : 0 });
    }
    return { rows: out, more };
  };
})();
