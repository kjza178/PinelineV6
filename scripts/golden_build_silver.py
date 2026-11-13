#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dựng silver golden (M0) từ bản chú thích tay data/golden/_src/<job>.txt + meta.

Chỉ dùng stdlib. Không gọi mạng/LLM.
  python scripts/golden_build_silver.py --src data/golden/_src --scripts <thư mục chứa <job>/script.json> --out data/golden
Định dạng dòng _src: i|spk|to|self|call|ref3|tags|vi_gold|note
  - self/call: dạng chấp nhận, nhiều dạng cách '/', '-' = không kỳ vọng
  - ref3: m | f | n | '-'  (giới của người được nhắc bằng 他/她/它 trong câu)
  - vi_gold rỗng = giữ vi_system ; '∅' = câu nên gộp/bỏ (câu cắt đôi lặp ý)
  - dòng '@scene <id> <a> <b> <mô tả>' khai báo cảnh
"""
import argparse, json, os, sys, importlib.util
from datetime import date

TAGS = {'pronoun', 'gender_ref3', 'idiom', 'meaning', 'style', 'halfcue_dup', 'name', 'term'}


def _split(v):
    v = (v or '').strip()
    if not v or v == '-':
        return None
    return [x.strip() for x in v.split('/') if x.strip()]


def parse_src(path):
    scenes, rows = [], []
    for raw in open(path, encoding='utf-8'):
        line = raw.rstrip('\n')
        if not line.strip() or line.startswith('#'):
            continue
        if line.startswith('@scene'):
            p = line.split(None, 4)
            scenes.append({'id': p[1], 'from': int(p[2]), 'to': int(p[3]), 'desc': p[4] if len(p) > 4 else ''})
            continue
        f = line.split('|')
        if len(f) < 9:
            raise ValueError('%s: thiếu cột: %r' % (path, line))
        f = f[:8] + ['|'.join(f[8:])]
        i, spk, to, s, c, r3, tags, vg, note = f
        tags = [t.strip() for t in tags.split(',') if t.strip()]
        bad = [t for t in tags if t not in TAGS]
        if bad:
            raise ValueError('%s: tag lạ %s ở câu %s' % (path, bad, i))
        rows.append(dict(i=int(i), spk=spk.strip() or '?', to=to.strip() or '-', self=_split(s), call=_split(c),
                         ref3=None if r3.strip() in ('', '-') else r3.strip(), tags=tags, vi_gold=vg, note=note.strip()))
    return scenes, rows


def build(job, src_txt, meta, script_path, out_dir):
    scenes, rows = parse_src(src_txt)
    script = json.load(open(script_path, encoding='utf-8'))
    by_i = {r['i']: r for r in script}
    os.makedirs(out_dir, exist_ok=True)
    def scene_of(i):
        for sc in scenes:
            if sc['from'] <= i <= sc['to']:
                return sc['id']
        return None
    out_lines = []
    for r in rows:
        s = by_i.get(r['i'])
        if s is None:
            raise ValueError('%s: câu %d không có trong script' % (job, r['i']))
        vi_sys = s.get('vi') or ''
        vg = r['vi_gold']
        if vg == '':
            vg = vi_sys
        elif vg == '∅':
            vg = ''
        tags = list(r['tags'])
        if vg != vi_sys and not tags:
            tags = ['style']  # sửa mà không gắn tag → coi là văn phong
        pe = {'self': r['self'], 'call': r['call'], 'ref3': r['ref3']}
        rec = dict(i=r['i'], scene=scene_of(r['i']), zh=s.get('zh', ''), vi_system=vi_sys, spk=r['spk'], to=r['to'],
                   pron_expected=pe, vi_gold=vg, error_tags=tags, note=r['note'])
        idm = (meta.get('idioms') or {}).get(r['i'])
        if idm:
            rec['idiom'] = idm
        out_lines.append(rec)
    # cảnh phải liên tục
    idx = [r['i'] for r in out_lines]
    gaps = [b for a, b in zip(idx, idx[1:]) if b != a + 1]
    if gaps:
        raise ValueError('%s: mẫu không liên tục tại %s' % (job, gaps))
    with open(os.path.join(out_dir, 'lines.gold.jsonl'), 'w', encoding='utf-8') as f:
        for rec in out_lines:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    pairs = []
    for p in meta['pairs']:
        p = dict(p)
        p['evidence'] = [{'i': i, 'zh': by_i[i].get('zh', '')} for i in p.get('evidence', [])]
        p.setdefault('range', None)
        p['source'] = 'silver'
        pairs.append(p)
    json.dump(dict({'job': job, 'status': 'silver', 'pairs': pairs}, **({'heldout': True} if meta.get('heldout') else {})), open(os.path.join(out_dir, 'pairs.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    json.dump({'job': job, 'status': 'silver', 'characters': meta['characters']},
              open(os.path.join(out_dir, 'characters.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    info = {k: meta[k] for k in ('title', 'title_vi', 'genre', 'genre_note', 'range')}
    if meta.get('heldout'):
        # golden HELD-OUT: chỉ để đo, KHÔNG dùng seed lexicon/prompt/quy tắc (data/golden_heldout/README.md)
        info['heldout'] = True
    info.update(job=job, status='silver', reviewer='agent (Claude) — chờ chủ dự án xác nhận', built=str(date.today()),
                n_lines=len(out_lines), scenes=scenes, script_lines_total=len(script),
                script_has_mt=any(r.get('mt') for r in script))
    json.dump(info, open(os.path.join(out_dir, 'meta.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return len(out_lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--meta', default=None, help='file meta.py (mặc định <src>/meta.py)')
    ap.add_argument('--scripts', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    mp = a.meta or os.path.join(a.src, 'meta.py')
    spec = importlib.util.spec_from_file_location('golden_meta', mp)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    for job, meta in mod.META.items():
        n = build(job, os.path.join(a.src, job + '.txt'), meta, os.path.join(a.scripts, job, 'script.json'), os.path.join(a.out, job))
        print(job, n)


if __name__ == '__main__':
    main()
