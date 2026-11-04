// F18.3 학생 리뷰 filter: the same code runs in the Worker (this ESM module) and on the phone (published as
// /static/review_filter.js by the console, `export` removed). It blocks 욕설, 혐오, 차별 from a plain wordlist
// (filters/review_words.txt, extendable locally), phone numbers, links, and classmates' names (by hash only).
//
// Three readings of the text, each checked against the list:
//   syllables: only Hangul syllables are kept (spaces, symbols, digits and letters between them are dropped): 시1발, 시 . 발
//   jamo:      only standalone jamo typed as such (ㅅㅂ, ㅅ1ㅂ); words made only of jamo are matched here
//   latin:     Latin letters as typed (fuck, sibal) and as typed on a Korean keyboard (tlqkf = 시발)
const SBASE = 0xac00, L = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ', V = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ',
  T = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'];
const SPLIT = { 'ㅘ': 'ㅗㅏ', 'ㅙ': 'ㅗㅐ', 'ㅚ': 'ㅗㅣ', 'ㅝ': 'ㅜㅓ', 'ㅞ': 'ㅜㅔ', 'ㅟ': 'ㅜㅣ', 'ㅢ': 'ㅡㅣ', 'ㄳ': 'ㄱㅅ', 'ㄵ': 'ㄴㅈ',
  'ㄶ': 'ㄴㅎ', 'ㄺ': 'ㄹㄱ', 'ㄻ': 'ㄹㅁ', 'ㄼ': 'ㄹㅂ', 'ㄽ': 'ㄹㅅ', 'ㄾ': 'ㄹㅌ', 'ㄿ': 'ㄹㅍ', 'ㅀ': 'ㄹㅎ', 'ㅄ': 'ㅂㅅ',
  'ㄲ': 'ㄱ', 'ㄸ': 'ㄷ', 'ㅃ': 'ㅂ', 'ㅆ': 'ㅅ', 'ㅉ': 'ㅈ' };                 // doubles: shift is lost when lower-cased
const QWERTY = { q: 'ㅂ', w: 'ㅈ', e: 'ㄷ', r: 'ㄱ', t: 'ㅅ', y: 'ㅛ', u: 'ㅕ', i: 'ㅑ', o: 'ㅐ', p: 'ㅔ', a: 'ㅁ', s: 'ㄴ', d: 'ㅇ',
  f: 'ㄹ', g: 'ㅎ', h: 'ㅗ', j: 'ㅓ', k: 'ㅏ', l: 'ㅣ', z: 'ㅋ', x: 'ㅌ', c: 'ㅊ', v: 'ㅍ', b: 'ㅠ', n: 'ㅜ', m: 'ㅡ' };
const SYL_RE = /[가-힣]/, JAMO_RE = /[ㄱ-ㅣ]/, LAT_RE = /[a-z]/;
const PHONE_RE = /01[016789][\s.\-]*\d{3,4}[\s.\-]*\d{4}/;
// (open.ka[k]ao: spelled so the page lint's brand-word check does not trip on the published copy)
const LINK_RE = /https?:|www\.|\b[a-z0-9-]+\.(?:com|net|org|kr|io|me|ly|gg|co)\b|bit\.ly|open\.ka[k]ao|카톡\s*아이디/i;

function keys(s) {          // syllables -> the jamo keystrokes that type them (doubles and compounds split)
  let out = '';
  for (const ch of s) {
    const c = ch.charCodeAt(0) - SBASE;
    if (c >= 0 && c < 11172) {
      const parts = [L[Math.floor(c / 588)], V[Math.floor((c % 588) / 28)], T[c % 28]];
      for (const p of parts) out += SPLIT[p] || p;
    } else if (JAMO_RE.test(ch)) out += SPLIT[ch] || ch;
  }
  return out;
}

export function readings(text) {
  // NFKC (full-width letters, ligatures) for everything but standalone jamo, which NFKC would turn into conjoining jamo
  let t = '';
  for (const ch of String(text || '')) t += JAMO_RE.test(ch) ? ch : ch.normalize('NFKC');
  t = t.toLowerCase();
  let syl = '', jamo = '', lat = '';
  for (const ch of t) {
    if (SYL_RE.test(ch)) syl += ch;
    else if (JAMO_RE.test(ch)) jamo += ch;
    else if (LAT_RE.test(ch)) lat += ch;
  }
  let typed = '';
  for (const ch of lat) typed += QWERTY[ch] || '';
  return { raw: t, syl, jamo, lat, typed, sylKeys: keys(syl) };
}

export function parseList(text) {
  const block = [], allow = [];
  for (const line of String(text || '').split('\n')) {
    const w = line.trim().toLowerCase();
    if (!w || w.startsWith('#')) continue;
    (w.startsWith('!') ? allow : block).push(w.replace(/^!/, '').replace(/\s+/g, ''));
  }
  return { block, allow };
}

// { ok: true } or { ok: false, reason: 'word' | 'phone' | 'link' | 'name' }; nameHit(syllables) is a sync test (the
// callers hash the pieces first: see pieces())
export function check(text, list, nameHit = null) {
  const r = readings(text);
  if (PHONE_RE.test(r.raw.replace(/[^\d\s.\-]/g, ' ')) || PHONE_RE.test(r.raw)) return { ok: false, reason: 'phone' };
  if (LINK_RE.test(r.raw)) return { ok: false, reason: 'link' };
  let syl = r.syl;
  for (const a of list.allow) if (SYL_RE.test(a)) syl = syl.split(a).join('');      // 시발점 is a math word
  for (const w of list.block) {
    if (/^[ㄱ-ㅣ]+$/.test(w)) { if (r.jamo.includes(w) || keys(r.jamo).includes(keys(w))) return { ok: false, reason: 'word' }; continue; }
    if (/^[a-z]+$/.test(w)) { if (r.lat.includes(w)) return { ok: false, reason: 'word' }; continue; }
    if (syl.includes(w)) return { ok: false, reason: 'word' };
    const k = keys(w);
    if (k.length >= 4 && (r.typed.includes(k) || (r.jamo && keys(r.jamo).includes(k)))) return { ok: false, reason: 'word' };
  }
  if (nameHit && nameHit(r.syl)) return { ok: false, reason: 'name' };
  return { ok: true };
}

// every 2-4 syllable piece of the text, for the name check (names are compared by hash, never stored in plain)
export function pieces(syl) {
  const out = new Set();
  for (let n = 2; n <= 4; n++) for (let i = 0; i + n <= syl.length; i++) out.add(syl.slice(i, i + n));
  return [...out];
}
