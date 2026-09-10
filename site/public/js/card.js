// 공용 카드 렌더러 (나이별 필터 결과용). 풀 카드 형식: {i,t,au,pu,yr,cv,band,med,sh,pic,k2,fm,pn,fl,sk,ln,ce,sp,fit,pr,prof}
(function () {
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function centLabel(pct) { return pct >= 80 ? '중심' : pct >= 50 ? '중심 근처' : pct >= 30 ? '가장자리' : '다른 나이 책'; }
  window.AgeShelfCard = function (b, opts) {
    const { rank, age, ageLabel, profAges, base = '', kdcNames = {}, flagTags = [] } = opts;
    const max = Math.max(...b.prof, 1);
    const bars = profAges.map((a, k) => `<i class="${a === age ? 'on' : ''}" style="height:${Math.max(6, Math.round(100 * b.prof[k] / max))}%" title="${a}세 ${b.prof[k]}%"></i>`).join('');
    const axis = profAges.map((a) => `<span>${a}</span>`).join('');
    const pct = Math.floor((b.ce ?? 0) * 100);
    const chips = [];
    if (b.k2 && kdcNames[b.k2]) chips.push(`<span class="badge tag" data-f="kdc2" data-v="${esc(b.k2)}">${esc(kdcNames[b.k2])}</span>`);
    if (b.fm) chips.push(`<span class="badge tag" data-f="form" data-v="${esc(b.fm)}">${esc(b.fm)}</span>`);
    (b.fl || []).slice(0, 2).forEach((c) => { if (flagTags[c]) chips.push(`<span class="badge tag flag" data-f="flag" data-v="${c}">${esc(flagTags[c])}</span>`); });
    return `<a class="card" href="${base}/book/${esc(b.i)}" data-fallback="${base}/b/?isbn=${esc(b.i)}">
      <div>${b.cv ? `<img class="cover" src="${esc(b.cv)}" alt="" loading="lazy">` : '<div class="cover"></div>'}</div>
      <div><div class="rank">#${rank} · ${esc(ageLabel)} 대출 ${Number(b.ln).toLocaleString()}건 · 이 나이 위치 ${pct}% (${centLabel(pct)})</div>
      <div class="title">${esc(b.t)}</div><div class="meta">${esc(b.au)} · ${esc(b.pu)} · ${esc(b.yr)}</div>
      <div class="badges"><span class="badge band">추천 ${esc(b.band)}</span><span class="badge">중앙 ${Number(b.med).toFixed(1)}세</span>${b.sp >= 2 ? '<span class="badge">이 나이에 집중</span>' : ''}${b.sh && b.sh !== '대칭' ? `<span class="badge">${esc(b.sh)}</span>` : ''}${b.pic ? '<span class="badge pic">그림책</span>' : ''}${chips.join('')}</div>
      <div class="bars">${bars}</div><div class="axis">${axis}</div></div></a>`;
  };
  // 선택한 도서관 (localStorage). {code, name, region}
  const LIB_KEY = 'ageshelf.lib';
  window.AgeShelfLib = {
    get() { try { return JSON.parse(localStorage.getItem(LIB_KEY) || 'null'); } catch (e) { return null; } },
    set(v) { try { v ? localStorage.setItem(LIB_KEY, JSON.stringify(v)) : localStorage.removeItem(LIB_KEY); } catch (e) {} },
    async isbns(base, code) {   // 도서관 소장 ISBN Set
      const r = await fetch(`${base}/data/libs/${code}.json?v=${window.__dataVersion || ''}`); if (!r.ok) return new Set();
      const j = await r.json(); return new Set(j.isbns || []);
    },
    async directory(base) {
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
  };
  // 시리즈당 cap 권 제한 (순서 유지)
  window.AgeShelfDiversify = function (rows, cap, n) {
    const seen = {}; const out = [];
    for (const b of rows) { const k = b.sk || b.i; if ((seen[k] || 0) >= cap) continue; seen[k] = (seen[k] || 0) + 1; out.push(b); if (out.length >= n) break; }
    return out;
  };
})();
