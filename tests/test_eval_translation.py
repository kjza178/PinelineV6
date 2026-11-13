# -*- coding: utf-8 -*-
"""Test scripts/eval_translation.py (M0) — thuần stdlib, không mạng/LLM."""
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('eval_translation', os.path.join(ROOT, 'scripts', 'eval_translation.py'))
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)
GOLDEN = os.path.join(ROOT, 'data', 'golden')


# ---------------------------------------------------------------- chrF / edit

def test_chrf_identity_and_empty():
    assert ev.chrf('Xin chào anh', 'Xin chào anh') == pytest.approx(1.0)
    assert ev.chrf('', '') == 1.0
    assert ev.chrf('abc', '') == 0.0
    assert 0 < ev.chrf('Sao em lại đến đây?', 'Sao anh lại đến đây?') < 1


def test_edit_ratio_ignores_case_and_punct():
    assert ev.edit_ratio('Được rồi!', 'được rồi') == 0.0
    assert ev.edit_ratio('abc', 'xyz') > 0.9


# ---------------------------------------------------------------- đại từ

@pytest.mark.parametrize('vi,self_,call,want', [
    ('Anh đoán em mới học làm bánh.', ['anh'], ['em'], 'correct'),
    ('Tôi đoán cậu mới học làm bánh.', ['anh'], ['em'], 'wrong'),
    ('Học trưởng, anh hát dở quá.', None, ['anh', 'học trưởng'], 'correct'),
    ('Không sao.', ['anh'], ['em'], 'omitted'),
    ('Cậu ấy không sao.', ['tôi'], ['anh'], 'omitted'),          # «cậu ấy» là ngôi 3
    ('Đàn ông gì mà yếu thế.', ['tôi'], ['cô'], 'omitted'),       # «đàn ông» không phải «ông»
    ('Sau khi anh Trần vào trong,', None, ['cô'], 'omitted'),   # danh xưng + tên riêng
    ('Lấy được tiền tôi sẽ thả anh đi.', ['tôi', 'tao'], ['cô', 'mày'], 'wrong'),  # sai giới người nghe
    ('Ngươi chắc muốn đấu với ta?', ['ta'], ['ngươi'], 'correct'),
    ('Các ngươi là ai?', None, ['các ngươi'], 'correct'),
    ('Nhưng cậu chắc muốn đánh với tôi?', ['ta'], ['ngươi'], 'wrong'),
    ('Học trưởng của em hát hay chứ,', ['anh'], ['em'], 'correct'),  # «học trưởng của» là danh từ
    ('Nhóc không biết tìm chỗ trống mà triệu hồi lão tử à?', ['lão tử'], ['nhóc', 'Trần tiểu tử'], 'correct'),
    ('Trần tiểu tử, không phải ta không giúp.', ['lão tử', 'ta'], ['Trần tiểu tử'], 'correct'),
    ('Em tưởng không gặp lại bố với ông nội nữa.', ['em'], None, 'correct'),
    ('Chúng ta đi thôi.', ['anh'], ['em'], 'omitted'),           # ngôi 1 bao hàm: bỏ qua
])
def test_classify_line(vi, self_, call, want):
    v, _ = ev.classify_line(vi, self_, call)
    assert v == want, (vi, v, _)


def test_classify_na_when_no_expectation():
    assert ev.classify_line('Tôi đi đây.', None, None)[0] == 'na'


def test_one_sided_kin_only_wrong_without_hit():
    # chỉ kỳ vọng vế gọi (cô); câu dùng «anh» và không có vế nào trúng → sai
    assert ev.classify_line('Miễn là bố anh mắc bẫy,', None, ['cô', 'mày'])[0] == 'wrong'
    # có vế trúng → danh xưng còn lại có thể là vế kia → không kết luận sai
    assert ev.classify_line('Em có tin vui muốn kể anh nghe.', ['em'], None)[0] == 'correct'


@pytest.mark.parametrize('vi,exp,want', [
    ('Cô ấy bị hoảng sợ.', 'f', 'correct'),
    ('Hắn bị hoảng sợ.', 'f', 'wrong'),
    ('Lần trước, khi cậu gặp nàng,', 'm', 'wrong'),
    ('Rồi nó chui ra từ hố đen.', 'n', 'correct'),
    ('Rồi hắn chui ra từ hố đen.', 'n', 'correct'),   # quái vật: «hắn» chấp nhận
    ('Không sao.', 'm', 'omitted'),
    ('Không sao.', None, 'na'),
])
def test_ref3(vi, exp, want):
    assert ev.ref3_verdict(vi, exp)[0] == want


def test_signature_ignores_names_and_nouns():
    assert ev.signature('Anh đoán em mới học làm bánh.', ['anh'], ['em']) == frozenset({'anh', 'em'})
    assert ev.signature('Sau khi anh Trần vào,', None, None) == frozenset()


# ---------------------------------------------------------------- thành ngữ / câu cắt đôi

def test_idiom_verdict():
    spec = {'zh': '苟延残喘', 'accept': ['thoi thóp', 'lay lắt'], 'forbid': ['cẩu kéo dài']}
    assert ev.idiom_verdict('Lão phu đúng là đang thoi thóp,', spec) == 'correct'
    assert ev.idiom_verdict('Từ "cẩu kéo dài hơi tàn" dùng tốt, thoi thóp', spec) == 'wrong'
    assert ev.idiom_verdict('Lão phu còn sống,', spec) == 'wrong'
    assert ev.idiom_verdict('gì cũng được', None) == 'na'


def test_dup_pair():
    assert ev.dup_pair('Sau khi anh Trần vào.', 'Sau khi anh Trần vào,')
    assert not ev.dup_pair('Không sao.', 'Mau giảm tốc!')
    assert not ev.dup_pair('', 'abc')


# ---------------------------------------------------------------- đầu-cuối trên golden giả

def _mk_golden(tmp_path):
    job = tmp_path / 'golden' / 'job1'
    job.mkdir(parents=True)
    lines = [
        dict(i=10, scene='s1', zh='你怎么来了？', vi_system='Sao cậu lại tới?', spk='a', to='b',
             pron_expected={'self': None, 'call': ['em'], 'ref3': None}, vi_gold='Sao em lại tới?',
             error_tags=['pronoun'], note=''),
        dict(i=11, scene='s1', zh='我来接你。', vi_system='Tôi tới đón cậu.', spk='b', to='a',
             pron_expected={'self': ['anh'], 'call': ['em'], 'ref3': None}, vi_gold='Anh tới đón em.',
             error_tags=['pronoun'], note=''),
        dict(i=12, scene='s1', zh='苟延残喘。', vi_system='Cẩu kéo dài hơi tàn.', spk='a', to='b',
             pron_expected={'self': None, 'call': None, 'ref3': None}, vi_gold='Sống lay lắt.',
             error_tags=['idiom'], note='', idiom={'zh': '苟延残喘', 'accept': ['lay lắt'], 'forbid': ['cẩu kéo dài']}),
        dict(i=13, scene='s1', zh='她走了。', vi_system='Hắn đi rồi.', spk='a', to='b',
             pron_expected={'self': None, 'call': None, 'ref3': 'f'}, vi_gold='Cô ấy đi rồi.',
             error_tags=['gender_ref3'], note=''),
        dict(i=14, scene='s1', zh='走吧。', vi_system='Đi thôi.', spk='a', to='b',
             pron_expected={'self': None, 'call': None, 'ref3': None}, vi_gold='', error_tags=['halfcue_dup'], note=''),
    ]
    with open(job / 'lines.gold.jsonl', 'w', encoding='utf-8') as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    (job / 'meta.json').write_text(json.dumps({'title': 't', 'genre': 'do_thi'}), encoding='utf-8')
    return tmp_path / 'golden', lines


def test_end_to_end_baseline_and_self_check(tmp_path):
    gdir, _ = _mk_golden(tmp_path)
    base = ev.run(str(gdir), baseline=True)
    m = base['jobs']['job1']['metrics']
    assert m['pron_correct'] == 0 and m['pron_wrong'] == 2
    assert m['ref3_wrong'] == 1 and m['idiom_wrong'] == 1
    assert m['halfcue_left'] == 1
    assert m['residual_by_tag']['pronoun'] == 2
    sc = ev.run(str(gdir), self_check=True)['total']
    assert sc['pron_acc'] == 1.0 and sc['ref3_acc'] == 1.0 and sc['idiom_acc'] == 1.0
    assert sc['chrF'] == 100.0 and sc['halfcue_left'] == 0
    md = ev.to_markdown(base)
    assert 'Pronoun pair acc.' in md and 'TỔNG' in md


def test_script_alignment_by_zh_when_index_shifts(tmp_path):
    gdir, lines = _mk_golden(tmp_path)
    # script chạy lại: chỉ số lệch +3 (vd gộp câu phía trước) nhưng zh giữ nguyên
    rows = [dict(i=r['i'] + 3, zh=r['zh'], vi=r['vi_gold']) for r in lines]
    sp = tmp_path / 'script.json'
    sp.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    res = ev.run(str(gdir), jobs=['job1'], script=str(sp))
    m = res['jobs']['job1']['metrics']
    assert m['unaligned'] == 0 and m['pron_acc'] == 1.0 and m['chrF'] == 100.0


def test_cli_writes_outputs(tmp_path):
    gdir, _ = _mk_golden(tmp_path)
    oj, om = tmp_path / 'r.json', tmp_path / 'r.md'
    assert ev.main(['--golden', str(gdir), '--baseline', '--out-json', str(oj), '--out-md', str(om)]) == 0
    assert json.loads(oj.read_text(encoding='utf-8'))['total']['lines'] == 5
    assert 'chrF' in om.read_text(encoding='utf-8')


# ---------------------------------------------------------------- golden thật (nếu có trong cây)

@pytest.mark.skipif(not os.path.isdir(GOLDEN) or not any(
    os.path.isfile(os.path.join(GOLDEN, d, 'lines.gold.jsonl')) for d in os.listdir(GOLDEN)) if os.path.isdir(GOLDEN)
    else True, reason='chưa có data/golden')
def test_real_golden_schema_and_self_check():
    gd = ev.load_golden(GOLDEN)
    assert len(gd) >= 3
    for job, g in gd.items():
        assert len(g['lines']) >= 120, job
        idx = [r['i'] for r in g['lines']]
        assert idx == list(range(idx[0], idx[0] + len(idx))), 'mẫu phải liên tục: %s' % job
        for r in g['lines']:
            assert set(r['error_tags']) <= set(ev.TAGS), (job, r['i'])
            assert {'self', 'call', 'ref3'} <= set(r['pron_expected'])
        assert g['meta'].get('status') == 'silver'
    sc = ev.run(GOLDEN, self_check=True)['total']
    # bộ trích đại từ phải chấm bản gold ≈ 100% (nếu không: sửa bộ trích hoặc golden)
    assert sc['pron_acc'] >= 0.98 and sc['ref3_acc'] >= 0.98 and sc['idiom_acc'] >= 0.95
