#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0 — Đo chất lượng dịch ZH→VI trên golden set (chỉ stdlib, không mạng/LLM/GPU).

Golden: data/golden/<job>/{lines.gold.jsonl, pairs.json, characters.json, meta.json}

Ví dụ:
  # baseline = vi_system lưu trong golden (bản hiện tại lúc dựng golden)
  python scripts/eval_translation.py --golden data/golden --baseline
  # đo một script.json bất kỳ (bản chạy lại) cho một job
  python scripts/eval_translation.py --golden data/golden --job 32da3d8f225b --script D:/work/32da3d8f225b/script.json
  # đo cả thư mục work (mỗi job lấy <work>/<job>/script.json)
  python scripts/eval_translation.py --golden data/golden --work-dir D:/PinelineV2_work
  # tự kiểm bộ trích đại từ: chấm chính vi_gold (phải ~100%)
  python scripts/eval_translation.py --golden data/golden --self-check
  --out-json f.json --out-md f.md để ghi kết quả.

Chỉ số (mỗi job + tổng):
  pron_acc      pronoun pair accuracy = đúng / (đúng + sai) trên câu có pron_expected (self/call);
                câu không có đại từ nào = "omitted" (không tính sai, báo riêng)
  pron_wrong    số câu sai xưng hô
  consistency   số lần đổi bộ xưng hô (cùng cảnh, cùng cặp spk→to) VƯỢT so với bản gold
  ref3_acc      % câu có ref3 kỳ vọng (他/她/它) mà giới đại từ ngôi 3 trong VI đúng
  idiom_acc     % câu có thành ngữ/lóng (trường `idiom`) mà VI chứa dạng chấp nhận và không chứa dạng cấm
  halfcue_left  số câu gắn halfcue_dup chưa được sửa ; halfcue_auto: cặp câu kề lặp ý (tự dò)
  chrF          chrF (n=1..6, beta=2) trung bình câu so với vi_gold ; edit_rate = 1 - tỉ lệ giống (difflib)
  changed       % câu khác vi_gold (sau chuẩn hoá)
  residual_by_tag  số câu còn lỗi theo tag (pronoun/gender_ref3/idiom/halfcue_dup: dùng bộ kiểm riêng;
                   meaning/style/name/term: xấp xỉ bằng chrF < 0.8 so với gold — cần người xác nhận)
"""
import argparse
import difflib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

# --------------------------------------------------------------------------- chuẩn hoá

_PUNCT_RE = re.compile(r"[\s\.,!?;:…\"'“”‘’«»()\[\]{}\-–—~/\\|*]+")


def nfc(s):
    return unicodedata.normalize('NFC', s or '')


def norm_text(s):
    """Chữ thường + bỏ dấu câu, giữ dấu tiếng Việt."""
    s = nfc(s).lower()
    return _PUNCT_RE.sub(' ', s).strip()


def strip_accents(s):
    s = unicodedata.normalize('NFD', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
    return s.replace('đ', 'd').replace('Đ', 'D')


def norm_zh(s):
    return re.sub(r"[\s，。！？、,.!?…：:；;“”\"'《》]+", '', nfc(s or ''))


# --------------------------------------------------------------------------- bộ trích đại từ VI

# base form -> loại. FIRST/SECOND = chỉ một ngôi; KIN = danh xưng dùng được cho cả ngôi 1 và 2.
FIRST = {
    'tôi', 'tao', 'tớ', 'ta', 'lão phu', 'lão tử', 'bản tọa', 'bổn tọa', 'bản tôn', 'bổn tôn', 'vãn bối', 'tại hạ',
    'vi sư', 'bản cung', 'trẫm', 'bổn vương', 'bản vương', 'lão nương', 'anh mày', 'bố mày', 'ông đây', 'tiểu gia',
    'bổn cô nương', 'thuộc hạ', 'ông nội đằng', 'mình',
}
SECOND = {'cậu', 'mày', 'ngươi', 'bạn', 'ngài', 'các người', 'mi', 'lão nhân gia', 'chư vị', 'các vị', 'mọi người'}
KIN = {
    'anh', 'em', 'chị', 'con', 'bố', 'ba', 'mẹ', 'cô', 'chú', 'bác', 'ông', 'bà', 'cháu', 'huynh', 'đệ', 'muội', 'tỷ',
    'sư huynh', 'sư đệ', 'sư tỷ', 'sư muội', 'sư phụ', 'sư tôn', 'tiền bối', 'đại ca', 'lão đại', 'học trưởng',
    'học muội', 'học tỷ', 'học đệ', 'tiểu tử', 'nhóc', 'đồ nhi', 'đệ tử', 'chủ nhân', 'đại tỷ', 'điện chủ', 'đội trưởng',
    'cha', 'ông nội', 'bà nội', 'ông ngoại', 'bà ngoại',
}
# "mình" mơ hồ (phản thân «tự mình», «của mình») — không bao giờ tính là vi phạm, chỉ tính là trúng nếu được chấp nhận.
SOFT = {'mình'}
# "con" quá nhiều nghĩa danh từ (con đường, con sâu…) — chỉ xét khi được kỳ vọng.
ONLY_IF_EXPECTED = {'con', 'ba', 'bố', 'mẹ', 'cha', 'ông nội', 'bà nội', 'ông ngoại', 'bà ngoại', 'đệ tử', 'chủ nhân',
                    'điện chủ', 'đội trưởng', 'mọi người', 'thuộc hạ', 'bạn'}
# tiền tố số nhiều -> bỏ khi so base
_PLURAL = ('các ', 'chúng ', 'bọn ', 'mấy ', 'nhà ')
# số nhiều gộp/ngôi 1 bao hàm — bỏ qua
INCLUSIVE = {'chúng ta', 'chúng mình', 'bọn mình', 'tụi mình', 'chúng tôi', 'bọn tôi', 'chúng em', 'bọn em',
             'chúng con', 'chúng tao', 'bọn tao', 'tụi tao', 'bọn ta'}
# Từ ghép không phải đại từ ngôi 1/2 (danh từ, ngôi 3, thán từ…)
NOT_PRON = [
    # ngôi 3 / danh từ chứa từ xưng hô
    'anh ấy', 'anh ta', 'anh trai', 'anh em', 'anh hùng', 'anh minh', 'anh tài', 'anh linh', 'anh rể', 'anh họ',
    'em ấy', 'em gái', 'em trai', 'em bé', 'em út', 'em họ', 'em rể', 'em dâu',
    'chị ấy', 'chị ta', 'chị gái', 'chị dâu', 'chị em', 'chị họ',
    'cô ấy', 'cô ta', 'cô gái', 'cô nương', 'cô đơn', 'cô nhi', 'cô nàng', 'cô bé', 'cô dâu', 'cô giáo',
    'cậu ấy', 'cậu ta', 'cậu bé', 'cậu chủ', 'cậu ruột', 'cậu ấm',
    'ông ta', 'ông ấy', 'ông trời', 'ông lão', 'ông chủ', 'đàn ông', 'ông cụ', 'ông bà',
    'bà ta', 'bà ấy', 'đàn bà', 'bà con', 'bà xã', 'bà lão', 'bà cụ',
    'hắn ta', 'người ta', 'gã ta', 'lão ta', 'lão ấy', 'mụ ta',
    'con người', 'con đường', 'con số', 'con gái', 'con trai', 'con cái', 'con tin', 'con mắt', 'con ong', 'con sâu',
    'con khỉ', 'con dao', 'con đằng', 'con rắn', 'con thú', 'con cháu', 'con dâu', 'con rể', 'con nít', 'con ruột',
    'mẹ kiếp', 'mẹ nó', 'cha nó', 'bố láo', 'mẹ chồng', 'mẹ vợ', 'bố vợ', 'bố chồng', 'ba mẹ', 'bố mẹ', 'cha mẹ',
    'chú ý', 'bác sĩ', 'cháu gái', 'cháu trai', 'huynh đệ', 'đệ tử thân truyền', 'tỷ muội',
    'bạn bè', 'bạn trai', 'bạn gái', 'bạn học', 'bạn đời', 'bạn cùng', 'ngài ấy', 'thằng nhóc', 'tiểu tử kia',
    'ta đây',
]
# Ngôi 3 có giới (để chấm ref3)
REF3 = {
    'm': ['hắn ta', 'hắn', 'anh ta', 'anh ấy', 'ông ta', 'ông ấy', 'lão ta', 'lão ấy', 'cậu ta', 'cậu ấy', 'gã',
          'chàng', 'huynh ấy', 'y ta', 'thằng đó', 'thằng kia'],
    'f': ['cô ta', 'cô ấy', 'nàng', 'bà ta', 'bà ấy', 'chị ta', 'chị ấy', 'ả', 'mụ', 'cô nàng', 'muội ấy', 'tỷ ấy'],
    'n': ['nó', 'bọn họ', 'họ', 'người đó', 'người ấy', 'bọn chúng', 'chúng nó', 'em ấy'],
}


def base_form(f):
    f = norm_text(f)
    if f in INCLUSIVE:
        return f
    for p in _PLURAL:
        if f.startswith(p) and len(f) > len(p):
            return f[len(p):]
    return f


def _tokens(text):
    """Trả về list (word_lower, word_orig) theo khoảng trắng, đã bỏ dấu câu ở biên."""
    out = []
    for w in nfc(text).split():
        w2 = w.strip(".,!?;:…\"'“”‘’«»()[]{}-–—~")
        if not w2:
            continue
        out.append((w2.lower(), w2))
    return out


def _all_lex():
    lex = {}
    for f in FIRST:
        lex[f] = 'FIRST'
    for f in SECOND:
        lex[f] = 'SECOND'
    for f in KIN:
        lex.setdefault(f, 'KIN')
    for f in INCLUSIVE:
        lex[f] = 'INCL'
    for f in ('các cậu', 'các ngươi', 'các anh', 'các chị', 'các em', 'các con', 'các cháu', 'nhà ngươi', 'mấy người',
              'các người', 'bọn mày', 'chúng mày', 'tụi mày', 'các bạn', 'các ngài', 'các vị sư huynh'):
        lex[f] = lex.get(base_form(f), 'SECOND')
    for f in NOT_PRON:
        lex[f] = 'NOT'
    for g, forms in REF3.items():
        for f in forms:
            lex[f] = 'REF3_' + g
    return lex


LEX = _all_lex()
_MAXN = max(len(k.split()) for k in LEX)


def scan(text, protect=()):
    """Quét câu VI, khớp dài nhất trước. protect: các cụm (đã chuẩn hoá) cần coi là một khối (vd dạng chấp nhận
    'Trần tiểu tử', 'lão Đằng').
    Trả về list dict {form, base, cat, pos, orig}."""
    toks = _tokens(text)
    words = [t[0] for t in toks]
    prot = sorted({norm_text(p) for p in protect if p}, key=lambda x: -len(x.split()))
    res = []
    i = 0
    while i < len(words):
        hit = None
        # cụm được bảo vệ (dạng chấp nhận nhiều chữ) trước
        for p in prot:
            pw = p.split()
            if pw and words[i:i + len(pw)] == pw:
                hit = (p, 'ACCEPT', len(pw))
                break
        if not hit:
            for n in range(min(_MAXN, len(words) - i), 0, -1):
                cand = ' '.join(words[i:i + n])
                if cand in LEX:
                    hit = (cand, LEX[cand], n)
                    break
        if hit:
            form, cat, n = hit
            nxt = toks[i + n][1] if i + n < len(toks) else ''
            nxt_l = toks[i + n][0] if i + n < len(toks) else ''
            prev_l = words[i - 1] if i > 0 else ''
            res.append(dict(form=form, base=base_form(form), cat=cat, pos=i, next=nxt, next_l=nxt_l, prev=prev_l,
                            orig=' '.join(t[1] for t in toks[i:i + n])))
            i += n
        else:
            i += 1
    return res


def classify_line(vi, exp_self, exp_call):
    """Chấm xưng hô một câu.
    Trả về (verdict, detail) verdict ∈ {'correct','wrong','omitted','na'}."""
    if not exp_self and not exp_call:
        return 'na', {}
    acc_s = {base_form(x) for x in (exp_self or [])}
    acc_c = {base_form(x) for x in (exp_call or [])}
    acc_all = acc_s | acc_c
    protect = [x for x in (exp_self or []) + (exp_call or []) if len(x.split()) > 1]
    items = scan(vi, protect)
    hits, viol, kin_unknown = [], [], []
    pron_words = {it['form'] for it in items if it['cat'] in ('FIRST', 'SECOND', 'KIN', 'ACCEPT', 'INCL')}
    for it in items:
        cat, b = it['cat'], it['base']
        if cat in ('NOT', 'INCL') or cat.startswith('REF3'):
            continue
        if cat == 'ACCEPT' or b in acc_all:
            hits.append(it['orig'])
            continue
        if b in SOFT:
            continue
        if b in ONLY_IF_EXPECTED:
            continue
        if cat == 'KIN':
            # danh xưng + tên riêng (anh Trần, cô Tần) hoặc danh từ sở hữu (bố tôi, mẹ anh) → bỏ
            nxt = it['next']
            if nxt and nxt[:1].isupper():
                continue
            if it['next_l'] in pron_words or it['next_l'] in ('ấy', 'ta', 'kia', 'này', 'đó', 'nó', 'họ', 'hắn'):
                continue
            if it['prev'] in ('của', 'nhà', 'là', 'làm', 'một', 'những', 'người', 'vị', 'hai', 'ba'):
                # «của anh» vẫn là đại từ → chỉ bỏ các từ chỉ danh từ
                if it['prev'] != 'của':
                    continue
            if it['next_l'] == 'của':
                continue  # «học trưởng của em» — danh từ bị sở hữu
            if not (exp_self and exp_call):
                # chỉ biết một vế: danh xưng lạ có thể là vế còn lại → chỉ kết luận sai khi không có vế nào trúng
                kin_unknown.append(('kin', it['orig']))
                continue
            viol.append(('kin', it['orig']))
            continue
        if cat == 'FIRST':
            if exp_self:
                viol.append(('self', it['orig']))
            continue
        if cat == 'SECOND':
            if exp_call:
                viol.append(('call', it['orig']))
            continue
    if not hits and kin_unknown:
        viol.extend(kin_unknown)
    if viol:
        return 'wrong', {'hits': hits, 'viol': viol}
    if hits:
        return 'correct', {'hits': hits}
    return 'omitted', {}


def ref3_verdict(vi, exp):
    if not exp:
        return 'na', []
    exp = exp.strip().lower()[:1]
    items = [it for it in scan(vi) if it['cat'].startswith('REF3_')]
    if not items:
        return 'omitted', []
    gs = [it['cat'][-1] for it in items]
    if exp == 'n':
        bad = [g for g in gs if g == 'f']  # vật/quái: nữ là sai; «hắn» chấp nhận
    elif exp == 'm':
        bad = [g for g in gs if g == 'f']
    else:  # f
        bad = [g for g in gs if g == 'm']
    return ('wrong' if bad else 'correct'), [it['orig'] for it in items]


def signature(vi, exp_self, exp_call):
    """Bộ xưng hô dùng trong câu (để đo nhất quán): tập base FIRST/SECOND/KIN (không tính NOT/REF3)."""
    protect = [x for x in (exp_self or []) + (exp_call or []) if len(x.split()) > 1]
    sig = set()
    for it in scan(vi, protect):
        if it['cat'] in ('FIRST', 'SECOND', 'KIN', 'ACCEPT') and it['base'] not in SOFT \
                and it['base'] not in ONLY_IF_EXPECTED and not (it['next'][:1].isupper() if it['next'] else False):
            sig.add(it['base'])
    return frozenset(sig)


# --------------------------------------------------------------------------- thành ngữ

def _contains(text, phrase):
    """Khớp cụm theo ranh giới từ, sau chuẩn hoá (chữ thường, bỏ dấu câu, giữ dấu tiếng Việt)."""
    p = norm_text(phrase)
    return bool(p) and (' ' + p + ' ') in (' ' + norm_text(text) + ' ')


def idiom_verdict(vi, spec):
    if not spec:
        return 'na'
    if not (vi or '').strip():
        return 'wrong'
    if any(_contains(vi, f) for f in spec.get('forbid') or []):
        return 'wrong'
    acc = spec.get('accept') or []
    if not acc:
        return 'na'
    return 'correct' if any(_contains(vi, a) for a in acc) else 'wrong'


# --------------------------------------------------------------------------- chrF / edit

def _ngrams(s, n):
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def chrf(hyp, ref, n_max=6, beta=2.0):
    """chrF (Popović 2015), bỏ khoảng trắng, chữ thường. Trả về 0..1."""
    h = re.sub(r'\s+', '', nfc(hyp).lower())
    r = re.sub(r'\s+', '', nfc(ref).lower())
    if not h and not r:
        return 1.0
    if not h or not r:
        return 0.0
    precs, recs = [], []
    for n in range(1, n_max + 1):
        hn, rn = _ngrams(h, n), _ngrams(r, n)
        if not hn or not rn:
            continue
        m = sum((hn & rn).values())
        precs.append(m / sum(hn.values()))
        recs.append(m / sum(rn.values()))
    if not precs:
        return 1.0 if h == r else 0.0
    p, rr = sum(precs) / len(precs), sum(recs) / len(recs)
    if p == 0 and rr == 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * p * rr / (b2 * p + rr)


def edit_ratio(hyp, ref):
    a, b = norm_text(hyp), norm_text(ref)
    if not a and not b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _tri(s):
    s = norm_text(s).replace(' ', '')
    return {s[i:i + 3] for i in range(len(s) - 2)}


def dup_pair(a, b, thr=0.6, min_len=6):
    """Hai câu kề nhau lặp ý (câu cắt đôi)? So 3-gram ký tự của câu ngắn hơn."""
    a, b = (a or '').strip(), (b or '').strip()
    if not a or not b:
        return False
    sa, sb = _tri(a), _tri(b)
    short = sa if len(sa) <= len(sb) else sb
    long_ = sb if short is sa else sa
    if len(short) < min_len - 2:
        return False
    return len(short & long_) / max(1, len(short)) >= thr


# --------------------------------------------------------------------------- nạp dữ liệu

def load_golden(golden_dir, jobs=None):
    out = {}
    for name in sorted(os.listdir(golden_dir)):
        d = os.path.join(golden_dir, name)
        f = os.path.join(d, 'lines.gold.jsonl')
        if not os.path.isfile(f) or (jobs and name not in jobs):
            continue
        lines = [json.loads(l) for l in open(f, encoding='utf-8') if l.strip()]
        meta = {}
        mp = os.path.join(d, 'meta.json')
        if os.path.isfile(mp):
            meta = json.load(open(mp, encoding='utf-8'))
        pairs = {}
        pp = os.path.join(d, 'pairs.json')
        if os.path.isfile(pp):
            pairs = json.load(open(pp, encoding='utf-8'))
        out[name] = {'lines': lines, 'meta': meta, 'pairs': pairs}
    return out


def align(gold_lines, script_rows):
    """Ghép câu gold với script đang đo: theo i (kiểm zh), lệch thì tìm theo zh trong ±40 câu.
    Trả về (dict i_gold -> vi, số câu không ghép được)."""
    by_i = {}
    for r in script_rows:
        try:
            by_i[int(r.get('i'))] = r
        except Exception:
            continue
    by_zh = defaultdict(list)
    for r in script_rows:
        by_zh[norm_zh(r.get('zh'))].append(r)
    res, miss = {}, 0
    for g in gold_lines:
        r = by_i.get(g['i'])
        if r is not None and norm_zh(r.get('zh')) == norm_zh(g['zh']):
            res[g['i']] = r.get('vi') or ''
            continue
        cands = [c for c in by_zh.get(norm_zh(g['zh']), []) if abs(int(c.get('i', 0)) - g['i']) <= 40]
        if cands:
            res[g['i']] = cands[0].get('vi') or ''
        else:
            res[g['i']] = None
            miss += 1
    return res, miss


# --------------------------------------------------------------------------- chấm

TAGS = ['pronoun', 'gender_ref3', 'idiom', 'meaning', 'style', 'halfcue_dup', 'name', 'term']


def evaluate_job(gold, vi_map, keep_examples=4):
    lines = gold['lines']
    st = Counter()
    ex = defaultdict(list)
    chrfs, edits = [], []
    residual = Counter()
    tagged = Counter()
    sig_sys = defaultdict(list)
    sig_gold = defaultdict(list)
    for g in lines:
        vi = vi_map.get(g['i'])
        if vi is None:
            st['unaligned'] += 1
            continue
        gold_vi = g.get('vi_gold') or ''
        pe = g.get('pron_expected') or {}
        st['lines'] += 1
        # chrF / edit
        chrfs.append(chrf(vi, gold_vi))
        edits.append(edit_ratio(vi, gold_vi))
        if norm_text(vi) != norm_text(gold_vi):
            st['changed'] += 1
        # xưng hô
        v, det = classify_line(vi, pe.get('self'), pe.get('call'))
        if v != 'na':
            st['pron_' + v] += 1
            if v == 'wrong' and len(ex['pronoun']) < keep_examples:
                ex['pronoun'].append({'i': g['i'], 'zh': g['zh'], 'vi': vi, 'gold': gold_vi, 'viol': det.get('viol')})
        # nhất quán: theo cảnh + cặp
        if g.get('spk') not in (None, '?') and (pe.get('self') or pe.get('call')):
            key = (g.get('scene'), g['spk'], g.get('to'))
            s1 = signature(vi, pe.get('self'), pe.get('call'))
            s2 = signature(gold_vi, pe.get('self'), pe.get('call'))
            if s1:
                sig_sys[key].append(s1)
            if s2:
                sig_gold[key].append(s2)
        # ref3
        rv, forms = ref3_verdict(vi, pe.get('ref3'))
        if rv != 'na':
            st['ref3_' + rv] += 1
            if rv == 'wrong' and len(ex['gender_ref3']) < keep_examples:
                ex['gender_ref3'].append({'i': g['i'], 'zh': g['zh'], 'vi': vi, 'gold': gold_vi, 'found': forms})
        # thành ngữ
        iv = idiom_verdict(vi, g.get('idiom'))
        if iv != 'na' and gold_vi:
            st['idiom_' + iv] += 1
            if iv == 'wrong' and len(ex['idiom']) < keep_examples:
                ex['idiom'].append({'i': g['i'], 'zh': g['zh'], 'vi': vi, 'gold': gold_vi,
                                    'idiom': (g.get('idiom') or {}).get('zh')})
        # tag dư (residual)
        for t in g.get('error_tags') or []:
            tagged[t] += 1
            if t == 'pronoun':
                bad = v == 'wrong' or (v in ('omitted', 'na') and chrf(vi, gold_vi) < 0.8)
            elif t == 'gender_ref3':
                bad = rv == 'wrong' or (rv == 'na' and chrf(vi, gold_vi) < 0.8)
            elif t == 'idiom':
                bad = iv == 'wrong' or (iv == 'na' and chrf(vi, gold_vi) < 0.8)
            elif t == 'halfcue_dup':
                bad = (bool(vi.strip()) != bool(gold_vi.strip())) or (gold_vi.strip() and chrf(vi, gold_vi) < 0.8)
            else:
                bad = chrf(vi, gold_vi) < 0.8
            if bad:
                residual[t] += 1
                if t not in ('pronoun', 'gender_ref3', 'idiom') and len(ex[t]) < keep_examples:
                    ex[t].append({'i': g['i'], 'zh': g['zh'], 'vi': vi, 'gold': gold_vi, 'note': g.get('note', '')})
    # câu cắt đôi tự dò (trên các cặp liên tiếp trong mẫu)
    seq = [vi_map.get(g['i']) or '' for g in lines]
    gseq = [g.get('vi_gold') or '' for g in lines]
    st['halfcue_auto'] = sum(1 for a, b in zip(seq, seq[1:]) if dup_pair(a, b))
    st['halfcue_auto_gold'] = sum(1 for a, b in zip(gseq, gseq[1:]) if dup_pair(a, b))

    def switches(d):
        n = 0
        for key, sigs in d.items():
            for a, b in zip(sigs, sigs[1:]):
                if not (a & b):
                    n += 1
        return n
    sw_sys, sw_gold = switches(sig_sys), switches(sig_gold)
    st['switch_sys'] = sw_sys
    st['switch_gold'] = sw_gold
    n = max(1, st['lines'])
    pc, pw = st['pron_correct'], st['pron_wrong']
    rc, rw = st['ref3_correct'], st['ref3_wrong']
    ic, iw = st['idiom_correct'], st['idiom_wrong']
    metrics = {
        'lines': st['lines'], 'unaligned': st['unaligned'],
        'pron_acc': round(pc / (pc + pw), 4) if pc + pw else None,
        'pron_correct': pc, 'pron_wrong': pw, 'pron_omitted': st['pron_omitted'],
        'consistency': max(0, sw_sys - sw_gold), 'switch_sys': sw_sys, 'switch_gold': sw_gold,
        'ref3_acc': round(rc / (rc + rw), 4) if rc + rw else None,
        'ref3_correct': rc, 'ref3_wrong': rw, 'ref3_omitted': st['ref3_omitted'],
        'idiom_acc': round(ic / (ic + iw), 4) if ic + iw else None, 'idiom_correct': ic, 'idiom_wrong': iw,
        'halfcue_left': residual['halfcue_dup'], 'halfcue_tagged': tagged['halfcue_dup'],
        'halfcue_auto': st['halfcue_auto'], 'halfcue_auto_gold': st['halfcue_auto_gold'],
        'chrF': round(100 * sum(chrfs) / max(1, len(chrfs)), 2),
        'edit_rate': round(sum(edits) / max(1, len(edits)), 4),
        'changed': round(st['changed'] / n, 4),
        'tagged_by_tag': {t: tagged[t] for t in TAGS},
        'residual_by_tag': {t: residual[t] for t in TAGS},
    }
    return metrics, dict(ex)


def aggregate(per_job):
    tot = Counter()
    tag_t, tag_r = Counter(), Counter()
    chrf_sum = edit_sum = 0.0
    for m in per_job.values():
        for k in ('lines', 'unaligned', 'pron_correct', 'pron_wrong', 'pron_omitted', 'consistency', 'switch_sys',
                  'switch_gold', 'ref3_correct', 'ref3_wrong', 'ref3_omitted', 'idiom_correct', 'idiom_wrong',
                  'halfcue_left', 'halfcue_tagged', 'halfcue_auto', 'halfcue_auto_gold'):
            tot[k] += m[k] or 0
        tot['changed_n'] += round(m['changed'] * m['lines'])
        chrf_sum += m['chrF'] * m['lines']
        edit_sum += m['edit_rate'] * m['lines']
        tag_t.update(m['tagged_by_tag'])
        tag_r.update(m['residual_by_tag'])
    n = max(1, tot['lines'])
    pc, pw, rc, rw, ic, iw = (tot[k] for k in ('pron_correct', 'pron_wrong', 'ref3_correct', 'ref3_wrong',
                                               'idiom_correct', 'idiom_wrong'))
    return {
        'lines': tot['lines'], 'unaligned': tot['unaligned'],
        'pron_acc': round(pc / (pc + pw), 4) if pc + pw else None, 'pron_correct': pc, 'pron_wrong': pw,
        'pron_omitted': tot['pron_omitted'], 'consistency': tot['consistency'], 'switch_sys': tot['switch_sys'],
        'switch_gold': tot['switch_gold'],
        'ref3_acc': round(rc / (rc + rw), 4) if rc + rw else None, 'ref3_correct': rc, 'ref3_wrong': rw,
        'ref3_omitted': tot['ref3_omitted'],
        'idiom_acc': round(ic / (ic + iw), 4) if ic + iw else None, 'idiom_correct': ic, 'idiom_wrong': iw,
        'halfcue_left': tot['halfcue_left'], 'halfcue_tagged': tot['halfcue_tagged'],
        'halfcue_auto': tot['halfcue_auto'], 'halfcue_auto_gold': tot['halfcue_auto_gold'],
        'chrF': round(chrf_sum / n, 2), 'edit_rate': round(edit_sum / n, 4), 'changed': round(tot['changed_n'] / n, 4),
        'tagged_by_tag': {t: tag_t[t] for t in TAGS}, 'residual_by_tag': {t: tag_r[t] for t in TAGS},
    }


# --------------------------------------------------------------------------- xuất

def _pct(x):
    return '—' if x is None else '%.1f%%' % (100 * x)


def to_markdown(result):
    rows = result['jobs']
    names = list(rows)
    cols = names + ['TỔNG']
    get = lambda c: result['total'] if c == 'TỔNG' else rows[c]['metrics']
    out = ['| Chỉ số | ' + ' | '.join(cols) + ' |', '|---|' + '---|' * len(cols)]

    def line(label, fn):
        out.append('| %s | %s |' % (label, ' | '.join(fn(get(c)) for c in cols)))
    line('Số câu', lambda m: str(m['lines']))
    line('Pronoun pair acc.', lambda m: '%s (%d/%d)' % (_pct(m['pron_acc']), m['pron_correct'], m['pron_correct'] + m['pron_wrong']))
    line('Câu không đại từ (omitted)', lambda m: str(m['pron_omitted']))
    line('Consistency (đổi cặp vượt gold)', lambda m: '%d (sys %d / gold %d)' % (m['consistency'], m['switch_sys'], m['switch_gold']))
    line('Gender ref3 acc.', lambda m: '%s (%d/%d)' % (_pct(m['ref3_acc']), m['ref3_correct'], m['ref3_correct'] + m['ref3_wrong']))
    line('Idiom/lóng acc.', lambda m: '%s (%d/%d)' % (_pct(m['idiom_acc']), m['idiom_correct'], m['idiom_correct'] + m['idiom_wrong']))
    line('Câu cắt đôi còn lại (tag)', lambda m: '%d/%d' % (m['halfcue_left'], m['halfcue_tagged']))
    line('Cặp kề lặp ý (tự dò; gold)', lambda m: '%d (%d)' % (m['halfcue_auto'], m['halfcue_auto_gold']))
    line('chrF vs gold', lambda m: '%.1f' % m['chrF'])
    line('Edit rate (1−sim)', lambda m: '%.3f' % m['edit_rate'])
    line('% câu khác gold', lambda m: _pct(m['changed']))
    for t in TAGS:
        line('lỗi `%s` (còn/gắn)' % t, lambda m, t=t: '%d/%d' % (m['residual_by_tag'][t], m['tagged_by_tag'][t]))
    return '\n'.join(out)


def run(golden_dir, jobs=None, script=None, work_dir=None, baseline=False, self_check=False):
    gd = load_golden(golden_dir, jobs)
    if not gd:
        raise SystemExit('Không thấy golden trong %s' % golden_dir)
    res = {'mode': 'self_check' if self_check else ('baseline' if baseline else 'script'), 'jobs': {}}
    for job, gold in gd.items():
        if self_check:
            vi_map, miss = {g['i']: g.get('vi_gold') or '' for g in gold['lines']}, 0
            src = 'vi_gold'
        elif baseline or (not script and not work_dir):
            vi_map, miss = {g['i']: g.get('vi_system') or '' for g in gold['lines']}, 0
            src = 'vi_system (golden)'
        else:
            path = script if script else os.path.join(work_dir, job, 'script.json')
            if not os.path.isfile(path):
                res['jobs'][job] = {'error': 'không có %s' % path}
                continue
            rows = json.load(open(path, encoding='utf-8'))
            if isinstance(rows, dict):
                rows = rows.get('rows') or rows.get('lines') or []
            vi_map, miss = align(gold['lines'], rows)
            src = path
        m, ex = evaluate_job(gold, vi_map)
        res['jobs'][job] = {'source': src, 'title': gold['meta'].get('title'), 'genre': gold['meta'].get('genre'),
                            'metrics': m, 'examples': ex}
    ok = {k: v['metrics'] for k, v in res['jobs'].items() if 'metrics' in v}
    res['jobs'] = {k: v for k, v in res['jobs'].items() if 'metrics' in v}
    res['total'] = aggregate(ok)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description='Đo chất lượng dịch trên golden (M0)')
    ap.add_argument('--golden', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'golden'))
    ap.add_argument('--job', action='append', help='chỉ đo job này (lặp được)')
    ap.add_argument('--script', help='script.json cần đo (dùng với 1 --job)')
    ap.add_argument('--work-dir', help='thư mục work: lấy <work>/<job>/script.json')
    ap.add_argument('--baseline', action='store_true', help='đo vi_system lưu trong golden')
    ap.add_argument('--self-check', action='store_true', help='đo chính vi_gold (kiểm bộ trích)')
    ap.add_argument('--out-json')
    ap.add_argument('--out-md')
    a = ap.parse_args(argv)
    if a.script and (not a.job or len(a.job) != 1):
        ap.error('--script cần đúng một --job')
    res = run(a.golden, a.job, a.script, a.work_dir, a.baseline, a.self_check)
    md = to_markdown(res)
    if a.out_json:
        with open(a.out_json, 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    if a.out_md:
        with open(a.out_md, 'w', encoding='utf-8') as f:
            f.write(md + '\n')
    sys.stdout.write(md + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
