#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Soi A/B o muc CAU: so script.json cua 2 bien the (mac dinh simple vs base) voi gold.

Chi doc (khong LLM/GPU/HTTP, khong ghi vao job tam). Tai dung ham cua scripts/eval_translation.py
(align, chrf, classify_line, idiom_verdict, norm_zh) nen so lieu khop voi bao cao golden_ab.

Doc:   <work-root>/<job>_<variant>/script.json   (mac dinh D:\\PinelineV6_work\\golden_ab)
Ghi:   docs/reviews/2026-11-12-ab-inspect.md (+ --out-json neu can)
Noi dung:
  1. Tong theo job (chrF trung binh 2 bien the, so cau tut/tang, so cau sai xung ho, khong ghep)
  2. N cau chrF(B) tut manh nhat so voi A (zh, gold, A, B)
  3. Cau KHONG GHEP cua moi bien the + chan doan: thieu i / lap i / lech zh (lap ASR chua go,
     vd "sha ba sha ba" vs "sha ba") / lech chi so (> +-40)
  4. MOI cau B sai xung ho (vi pham + ket qua A cung cau)
  5. Cau B sai thanh ngu/long (neu golden co truong idiom)

Vi du (cmd):
  python scripts\\ab_inspect.py
  python scripts\\ab_inspect.py --golden data\\golden_heldout --out docs\\reviews\\2026-11-12-ab-inspect-heldout.md
  python scripts\\ab_inspect.py --a nmt_hint_regex_mask_off --b simple --top 50
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_translation as EV  # noqa: E402


def load_rows(path):
    if not os.path.isfile(path):
        return None
    rows = json.load(open(path, encoding='utf-8'))
    if isinstance(rows, dict):
        rows = rows.get('rows') or rows.get('lines') or []
    return [r for r in rows if isinstance(r, dict)]


def _int(v):
    try:
        return int(v)
    except Exception:  # noqa: BLE001
        return None


def heal_stutter(zh):
    """Go lap doi kieu ASR ("AB AB" -> "AB") giong translate.heal_obvious_internal_stutter (ban rut gon)."""
    core = EV.norm_zh(zh)
    n = len(core)
    if n % 2 == 0 and n >= 2 and core[:n // 2] == core[n // 2:]:
        return core[:n // 2]
    return core


def diagnose_unaligned(gold_lines, rows, vi_map):
    """Chan doan tung cau gold khong ghep duoc (vi_map[i] is None)."""
    by_i = {}
    for r in rows:
        k = _int(r.get('i'))
        if k is not None:
            by_i.setdefault(k, []).append(r)
    zh_pos = {}
    for r in rows:
        zh_pos.setdefault(EV.norm_zh(r.get('zh')), []).append(_int(r.get('i')))
    out = []
    for g in gold_lines:
        if vi_map.get(g['i']) is not None:
            continue
        gz = EV.norm_zh(g['zh'])
        same = by_i.get(g['i']) or []
        d = {'i': g['i'], 'zh_gold': g['zh'], 'rows_at_i': [{'zh': r.get('zh'), 'vi': r.get('vi')} for r in same]}
        if not same:
            far = [p for p in zh_pos.get(gz, []) if p is not None]
            if far:
                d['kind'] = 'lech chi so (zh o i=%s, ngoai +-40)' % far[:3]
            else:
                d['kind'] = 'THIEU i (khong co dong i nay, khong tim thay zh)'
        elif len(same) > 1:
            d['kind'] = 'LAP i (%d dong cung i, zh khac gold)' % len(same)
        else:
            sz = same[0].get('zh') or ''
            if heal_stutter(sz) == gz or heal_stutter(sz) == heal_stutter(g['zh']):
                d['kind'] = 'lech zh: lap ASR chua go (stutter) -> script giu nguyen ASR, gold da go'
            elif EV.norm_zh(sz) and (EV.norm_zh(sz) in gz or gz in EV.norm_zh(sz)):
                d['kind'] = 'lech zh: cat/gop cau (zh script chua/la mot phan zh gold)'
            else:
                d['kind'] = 'lech zh: noi dung khac'
        # dong ke ben (de nhin lech)
        d['neighbors'] = [{'i': k, 'zh': (by_i.get(k) or [{}])[0].get('zh')} for k in (g['i'] - 1, g['i'] + 1) if by_i.get(k)]
        dup_i = [k for k, v in by_i.items() if len(v) > 1]
        if dup_i:
            d['dup_i_in_script'] = sorted(dup_i)[:10]
        out.append(d)
    return out


def inspect(golden_dir, work_root, a, b, jobs=None, top=30):
    gd = EV.load_golden(golden_dir, jobs)
    res = {'generated': time.strftime('%Y-%m-%d %H:%M'), 'golden': golden_dir, 'work_root': work_root, 'a': a, 'b': b,
           'jobs': {}, 'drops': [], 'pron_wrong_b': [], 'idiom_wrong_b': []}
    all_pairs = []
    for job, gold in gd.items():
        pa = os.path.join(work_root, '%s_%s' % (job, a), 'script.json')
        pb = os.path.join(work_root, '%s_%s' % (job, b), 'script.json')
        ra, rb = load_rows(pa), load_rows(pb)
        info = {'path_a': pa, 'path_b': pb, 'has_a': ra is not None, 'has_b': rb is not None}
        res['jobs'][job] = info
        if ra is None and rb is None:
            continue
        va, ma = EV.align(gold['lines'], ra or []) if ra is not None else ({}, None)
        vb, mb = EV.align(gold['lines'], rb or []) if rb is not None else ({}, None)
        info['unaligned_a'] = diagnose_unaligned(gold['lines'], ra, va) if ra is not None else None
        info['unaligned_b'] = diagnose_unaligned(gold['lines'], rb, vb) if rb is not None else None
        ca, cb, n, down, up = 0.0, 0.0, 0, 0, 0
        pw = Counter()
        for g in gold['lines']:
            gold_vi = g.get('vi_gold') or ''
            pe = g.get('pron_expected') or {}
            xa, xb = va.get(g['i']), vb.get(g['i'])
            fa = EV.chrf(xa, gold_vi) if xa is not None else None
            fb = EV.chrf(xb, gold_vi) if xb is not None else None
            if fa is not None and fb is not None:
                n += 1
                ca += fa
                cb += fb
                if fb < fa - 1e-9:
                    down += 1
                elif fb > fa + 1e-9:
                    up += 1
                all_pairs.append({'job': job, 'i': g['i'], 'zh': g['zh'], 'gold': gold_vi, 'a': xa, 'b': xb,
                                  'chrf_a': round(100 * fa, 1), 'chrf_b': round(100 * fb, 1),
                                  'delta': round(100 * (fb - fa), 1), 'tags': g.get('error_tags') or []})
            if xb is not None:
                vb_v, det_b = EV.classify_line(xb, pe.get('self'), pe.get('call'))
                if vb_v == 'wrong':
                    pw['b'] += 1
                    va_v, det_a = (EV.classify_line(xa, pe.get('self'), pe.get('call')) if xa is not None else ('-', {}))
                    res['pron_wrong_b'].append({'job': job, 'i': g['i'], 'spk': g.get('spk'), 'to': g.get('to'),
                                                'zh': g['zh'], 'gold': gold_vi, 'a': xa, 'b': xb,
                                                'expect_self': pe.get('self'), 'expect_call': pe.get('call'),
                                                'viol_b': det_b.get('viol'), 'verdict_a': va_v,
                                                'viol_a': det_a.get('viol') if isinstance(det_a, dict) else None})
                iv = EV.idiom_verdict(xb, g.get('idiom'))
                if iv == 'wrong' and gold_vi:
                    res['idiom_wrong_b'].append({'job': job, 'i': g['i'], 'zh': g['zh'], 'idiom': g['idiom'],
                                                 'gold': gold_vi, 'a': xa, 'b': xb,
                                                 'verdict_a': EV.idiom_verdict(xa, g.get('idiom')) if xa is not None else '-'})
            if xa is not None and EV.classify_line(xa, pe.get('self'), pe.get('call'))[0] == 'wrong':
                pw['a'] += 1
        info.update({'n_both': n, 'chrF_a': round(100 * ca / n, 2) if n else None,
                     'chrF_b': round(100 * cb / n, 2) if n else None, 'n_down': down, 'n_up': up,
                     'pron_wrong_a': pw['a'], 'pron_wrong_b': pw['b'],
                     'n_unaligned_a': len(info['unaligned_a'] or []), 'n_unaligned_b': len(info['unaligned_b'] or [])})
    all_pairs.sort(key=lambda x: (x['delta'], x['job'], x['i']))
    res['drops'] = [p for p in all_pairs if p['delta'] < 0][:top]
    # phan bo tut theo tag
    tag_drop = Counter()
    for p in all_pairs:
        if p['delta'] <= -10:
            for t in (p['tags'] or ['(khong tag)']):
                tag_drop[t] += 1
    res['drop_by_tag_le_minus10'] = dict(tag_drop.most_common())
    res['n_drop_le_minus10'] = sum(1 for p in all_pairs if p['delta'] <= -10)
    res['n_pairs'] = len(all_pairs)
    return res


def _c(s, n=400):
    s = '' if s is None else str(s)
    s = s.replace('\r', ' ').replace('\n', ' ').replace('|', '\\|')
    return s if len(s) <= n else s[:n] + '...'


def to_md(r):
    a, b = r['a'], r['b']
    o = ['# Soi A/B muc cau: `%s` (B) vs `%s` (A) tren golden' % (b, a), '',
         '_%s - `scripts/ab_inspect.py` - golden `%s` - job tam `%s`. Golden la SILVER; chrF tung cau rat nhieu '
         'khi cau ngan - doc kem gold._' % (r['generated'], r['golden'], r['work_root']), '',
         '## Tong theo job', '',
         '| Job | chrF %s | chrF %s | cau ca 2 ghep | B tut / tang | sai xung ho A / B | khong ghep A / B |' % (a, b),
         '|---|---|---|---|---|---|---|']
    for job, j in r['jobs'].items():
        if not (j.get('has_a') or j.get('has_b')):
            o.append('| %s | - | - | - | - | - | khong co script.json (%s, %s) |' % (job, j['path_a'], j['path_b']))
            continue
        o.append('| %s | %s | %s | %s | %s / %s | %s / %s | %s / %s |' % (
            job, j.get('chrF_a'), j.get('chrF_b'), j.get('n_both'), j.get('n_down'), j.get('n_up'),
            j.get('pron_wrong_a'), j.get('pron_wrong_b'), j.get('n_unaligned_a'), j.get('n_unaligned_b')))
    o += ['', 'Cau tut >= 10 diem chrF: %d / %d cau ghep. Theo error_tags cua gold (cau co the nhieu tag): %s' % (
        r['n_drop_le_minus10'], r['n_pairs'],
        ', '.join('%s=%d' % kv for kv in r['drop_by_tag_le_minus10'].items()) or '-'), '']
    o += ['## %d cau chrF(%s) tut manh nhat so voi %s' % (len(r['drops']), b, a), '',
          '| # | job | i | chrF A->B | zh | gold | %s | %s |' % (a, b), '|---|---|---|---|---|---|---|---|']
    for k, p in enumerate(r['drops'], 1):
        o.append('| %d | %s | %d | %.1f -> %.1f (%+.1f) | %s | %s | %s | %s |' % (
            k, p['job'], p['i'], p['chrf_a'], p['chrf_b'], p['delta'], _c(p['zh']), _c(p['gold']), _c(p['a']), _c(p['b'])))
    o += ['', '## Cau khong ghep', '']
    any_un = False
    for job, j in r['jobs'].items():
        for var, key in ((a, 'unaligned_a'), (b, 'unaligned_b')):
            for d in j.get(key) or []:
                any_un = True
                o.append('- **%s / %s / i=%d** - %s. Gold zh `%s`; script tai i: %s; ke ben: %s%s' % (
                    job, var, d['i'], d['kind'], _c(d['zh_gold']),
                    '; '.join('`%s` -> %s' % (_c(x['zh']), _c(x['vi'], 120)) for x in d['rows_at_i']) or '(khong co)',
                    ', '.join('i=%s `%s`' % (x['i'], _c(x['zh'], 40)) for x in d['neighbors']) or '-',
                    ('; i lap trong script: %s' % d['dup_i_in_script']) if d.get('dup_i_in_script') else ''))
    if not any_un:
        o.append('(khong co)')
    o += ['', '## Moi cau %s sai xung ho (%d)' % (b, len(r['pron_wrong_b'])), '',
          '| job | i | spk->to | ky vong self / call | vi pham B | A | zh | gold | %s |' % b,
          '|---|---|---|---|---|---|---|---|---|']
    for p in r['pron_wrong_b']:
        o.append('| %s | %d | %s->%s | %s / %s | %s | %s | %s | %s | %s |' % (
            p['job'], p['i'], p['spk'], p['to'], '/'.join(p['expect_self'] or ['-']), '/'.join(p['expect_call'] or ['-']),
            _c(', '.join('%s:%s' % tuple(v) for v in (p['viol_b'] or []))),
            p['verdict_a'] + ((' (' + ', '.join('%s:%s' % tuple(v) for v in p['viol_a']) + ')') if p.get('viol_a') else ''),
            _c(p['zh']), _c(p['gold']), _c(p['b'])))
    if r['idiom_wrong_b']:
        o += ['', '## Cau %s sai thanh ngu/long (%d)' % (b, len(r['idiom_wrong_b'])), '',
              '| job | i | thanh ngu | chap nhan | A | zh | %s |' % b, '|---|---|---|---|---|---|---|']
        for p in r['idiom_wrong_b']:
            o.append('| %s | %d | %s | %s | %s | %s | %s |' % (
                p['job'], p['i'], _c(p['idiom'].get('zh')), _c(' / '.join(p['idiom'].get('accept') or [])),
                p['verdict_a'], _c(p['zh']), _c(p['b'])))
    o.append('')
    return '\n'.join(o)


def main(argv=None):
    v6 = os.path.dirname(HERE)
    ap = argparse.ArgumentParser(description='Soi A/B muc cau (chi doc, khong LLM)')
    ap.add_argument('--golden', default=os.path.join(v6, 'data', 'golden'))
    ap.add_argument('--work-root', default=os.getenv('GOLDEN_AB_OUT') or r'D:\PinelineV6_work\golden_ab')
    ap.add_argument('--a', default='base', help='bien the doi chung (A)')
    ap.add_argument('--b', default='simple', help='bien the can soi (B)')
    ap.add_argument('--jobs', default='', help='danh sach job, cach dau phay (mac dinh: moi job trong golden)')
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--out', default=os.path.join(v6, 'docs', 'reviews', '2026-11-12-ab-inspect.md'))
    ap.add_argument('--out-json')
    x = ap.parse_args(argv)
    jobs = [j.strip() for j in x.jobs.split(',') if j.strip()] or None
    r = inspect(x.golden, x.work_root, x.a, x.b, jobs, x.top)
    if not any(j.get('has_a') or j.get('has_b') for j in r['jobs'].values()):
        print('[ab_inspect] khong thay script.json nao duoi %s (<job>_%s / <job>_%s)' % (x.work_root, x.a, x.b))
        return 2
    md = to_md(r)
    os.makedirs(os.path.dirname(os.path.abspath(x.out)), exist_ok=True)
    with open(x.out, 'w', encoding='utf-8') as f:
        f.write(md)
    if x.out_json:
        with open(x.out_json, 'w', encoding='utf-8') as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    for job, j in r['jobs'].items():
        print('[ab_inspect] %s: chrF %s=%s %s=%s, tut %s, sai xung ho %s=%s %s=%s, khong ghep %s=%s %s=%s' % (
            job, x.a, j.get('chrF_a'), x.b, j.get('chrF_b'), j.get('n_down'), x.a, j.get('pron_wrong_a'), x.b,
            j.get('pron_wrong_b'), x.a, j.get('n_unaligned_a'), x.b, j.get('n_unaligned_b')))
    print('[ab_inspect] ghi %s' % x.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
