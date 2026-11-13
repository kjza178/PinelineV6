# -*- coding: utf-8 -*-
# Meta silver golden HELD-OUT (2026-11-12, agent A). KHÔNG dùng để seed lexicon/prompt/quy tắc dịch.
# Chỉ để ĐO (thành ngữ/lóng + xưng hô) — xem ../README.md.
META = {}

META['2493d3c7cb29_p01'] = dict(
  title='游戏入侵：普攻加血一箭亿万伤屠戮神明三 - Phần 1/4',
  title_vi='Trò Chơi Xâm Lấn: Đánh Thường Hồi Máu, Một Tên Vạn Ức Sát Thương, Đồ Sát Thần Minh (mùa 3, phần 1/4)',
  genre='co_phong',
  genre_note='Job mixed (game/đô thị 2037 + huyền huyễn); CỬA SỔ chọn là cổ phong/huyền huyễn thuần: đế–cung chủ đàm phán, '
             'tuyên chiến (không có tuyến đô thị). V2 work không có job cổ trang thuần có asr.json — đây là cửa sổ thuần nhất. '
             'vi_system của job CHƯA qua REVIEW (rv=0).',
  heldout=True,
  range=[1815, 1955],
  characters=[
    dict(id='td', zh='雪帝', vi='Tuyết Đế', gender='nam', role='Nam chính (易, «Nhất ca»), chủ Phàm Điện (凡殿), tự xưng 本帝', relations={'nlk':'đối thủ, đàm phán rồi tuyên chiến','thuocha':'chủ'}),
    dict(id='nlk', zh='月轮客', vi='Nguyệt Luân Khách', gender='nam', role='Chủ Nguyệt Luân Ma Cung / Nguyệt Luân đế quốc (giới tính giả định nam — không có 他/她 nhắc tới)', relations={'td':'đối thủ'}),
    dict(id='cc', zh='公主', vi='công chúa (Nguyệt Luân)', gender='nữ', role='Chỉ được bẩm báo, không nói trong mẫu', relations={}),
    dict(id='truyenlenh', zh='', vi='lính truyền lệnh', gender='?', role='Báo tin Tuyết Đế cầu kiến', relations={}),
    dict(id='thuocha', zh='属下', vi='thuộc hạ Phàm Điện', gender='?', role='Hỏi kết quả đàm phán', relations={'td':'gọi 殿主大人'}),
    dict(id='team', zh='', vi='đồng đội (Chu Tiểu Béo…)', gender='?', role='Nhóm người nghe ở s1', relations={}),
  ],
  pairs=[
    dict(spk='td', to='nlk', self=['ta','bổn đế','bản đế'], call=['ngươi','các ngươi'], evidence=[1836,1848,1910,1916], confidence='high',
         note='Cường giả đối đầu, tự xưng 本帝 — glossary tone: «đối thoại giữa cường giả dùng ta – ngươi». «tôi/cậu» (vi_system) lệch giọng'),
    dict(spk='nlk', to='td', self=['ta','bổn tọa','bản tọa'], call=['ngươi','các ngươi'], evidence=[1854,1863,1864,1922], confidence='medium',
         note='Mở đầu khách sáo gọi «Tuyết Đế» (ngôi 3), từ 1854 thù địch: ta–ngươi'),
    dict(spk='td', to='team', self=['ta','tôi'], call=['các cậu','mọi người','các ngươi'], evidence=[1815,1816], confidence='medium',
         note='Đồng đội thân (gọi 一哥/义父) — chấp nhận cả hiện đại lẫn cổ phong'),
    dict(spk='thuocha', to='td', self=['thuộc hạ'], call=['điện chủ'], evidence=[1953], confidence='high', note='殿主大人'),
  ],
  idioms={
    1822: dict(zh='袖手旁观', accept=['khoanh tay đứng nhìn','đứng ngoài','bàng quan','làm ngơ','ngồi nhìn'], forbid=[]),
    1824: dict(zh='大祸临头', accept=['đại họa','họa lớn','tai họa ập','tai ương'], forbid=[]),
    1831: dict(zh='大驾光临', accept=['giá lâm','quang lâm','đại giá','ghé thăm'], forbid=[]),
    1832: dict(zh='有失远迎', accept=['không ra đón từ xa','không đón từ xa','thất lễ','tiếp đón chậm trễ','đón tiếp không chu đáo','chưa ra đón'], forbid=['có kẻ']),
    1843: dict(zh='罢兵言和', accept=['giảng hòa','bãi binh','nghị hòa','hòa đàm','dừng binh','cầu hòa'], forbid=[]),
    1854: dict(zh='欺人太甚', accept=['khinh người quá đáng','ức hiếp người quá đáng','quá đáng','được đằng chân lân đằng đầu'], forbid=[]),
    1859: dict(zh='炮灰', accept=['bia đỡ đạn','pháo hôi','tốt thí','vật hy sinh'], forbid=[]),
    1861: dict(zh='拱火', accept=['châm ngòi','thổi bùng','đổ thêm dầu','khơi mào','kích nổ'], forbid=[]),
    1865: dict(zh='昏天黑地(ASR 血)', accept=['tối tăm mặt mũi','trời đất tối tăm','long trời lở đất','trời long đất lở','hôn thiên hắc địa','máu chảy thành sông','sống mái'], forbid=['huyết địa']),
    1866: dict(zh='趁虚而入', accept=['thừa cơ','thừa lúc','nhân cơ hội','thừa hư','nhân lúc'], forbid=[]),
    1867: dict(zh='抄老巢', accept=['sào huyệt','hang ổ','đánh úp','tập kích','đánh thẳng'], forbid=['tịch thu']),
    1873: dict(zh='倾巢而出', accept=['dốc toàn lực','dốc hết','dốc toàn bộ','khuynh sào','kéo hết','toàn bộ xuất'], forbid=[]),
    1884: dict(zh='弹指间', accept=['búng tay','chớp mắt','nháy mắt','phẩy tay'], forbid=[]),
    1886: dict(zh='吃不吃得下', accept=['nuốt trôi','nuốt nổi','ăn nổi','chịu nổi','đỡ nổi','kham nổi'], forbid=[]),
    1887: dict(zh='一派胡言', accept=['nói bậy','nói nhảm','nói càn','hồ ngôn loạn ngữ','nói hươu nói vượn','xằng bậy','bịa đặt','láo toét'], forbid=['một phái']),
    1894: dict(zh='谈不拢便动手', accept=['đàm phán','thương lượng','thỏa thuận','nói không thông','không xong'], forbid=[]),
    1898: dict(zh='将进酒 杯莫停', accept=['tương tiến tửu','chén chớ dừng','chén đừng ngừng','chén đừng dừng','cạn chén'], forbid=['sắp vào ly']),
    1899: dict(zh='永远的神', accept=['mãi là thần','mãi đỉnh','vĩnh viễn là thần','thần thánh mãi mãi','đỉnh của chóp'], forbid=[]),
    1903: dict(zh='货真价实', accept=['hàng thật giá thật','đích thực','chính hiệu','đúng là','thật sự'], forbid=[]),
    1923: dict(zh='引狼入室', accept=['dẫn sói vào nhà','rước sói vào nhà','cõng rắn cắn gà nhà','rước họa'], forbid=[]),
    1925: dict(zh='一句准话', accept=['dứt khoát','chắc chắn','rõ ràng','chắc nịch'], forbid=[]),
    1932: dict(zh='蚕食', accept=['gặm nhấm','tằm ăn dâu','xâu xé','thôn tính','gặm dần'], forbid=[]),
    1933: dict(zh='不可一世', accept=['không ai bì nổi','ngạo mạn','kiêu ngạo','ngông cuồng','coi trời bằng vung','bất khả nhất thế','hống hách'], forbid=[]),
    1942: dict(zh='溯源归宗', accept=['cội nguồn','truy nguyên','về nguồn','tìm về gốc','nhận tổ quy tông','quy tông'], forbid=['tố nguyên']),
    1947: dict(zh='彻头彻尾', accept=['triệt để','hoàn toàn','từ đầu đến cuối','trăm phần trăm','đích thị'], forbid=[]),
  },
)

META['2d3316593ea9'] = dict(
  title='无敌财神！我觉醒了唯一隐藏职业第六季',
  title_vi='Thần Tài Vô Địch! Tôi Thức Tỉnh Nghề Ẩn Duy Nhất (mùa 6)',
  genre='do_thi',
  genre_note='Đô thị hiện đại / hệ thống nghề nghiệp (thành Hy Vọng số 1, nhiệm vụ – điểm tích lũy – bảng xếp hạng). '
             'Cửa sổ đầu job: thẩm tra gian tế + hai bạn thân bàn nhiệm vụ (lóng: bật hack, tốc độ tay, cướp đơn, phá của). '
             'Không có cổ phong. V2 work không có job đô thị «thuần» khác có asr.json (365baf19df54 là cùng phim với golden 32da3d8f225b).',
  heldout=True,
  range=[0, 141],
  characters=[
    dict(id='lp', zh='陆放', vi='Lục Phóng', gender='nam', role='Nam chính, «Thiên Long bảng nhất», chủ mèo Lai Tài', relations={'lc':'bạn thân/đồng đội («lão Chu»)'}),
    dict(id='lc', zh='周成宇', vi='Chu Thành Vũ (lão Chu)', gender='nam', role='Đồng đội, nghề tiên tri (cát hung)', relations={'lp':'bạn thân/đồng đội («lão Lục»)'}),
    dict(id='laitai', zh='来财', vi='Lai Tài', gender='?', role='Mèo của Lục Phóng', relations={}),
    dict(id='nghipham', zh='', vi='người bị tạm giữ (gian tế Ma Nhân)', gender='?', role='Nghi phạm, lộ mặt ở 41–42', relations={'lp':'gọi 长官'}),
    dict(id='nghipham2', zh='', vi='người bị tạm giữ khác', gender='?', role='Kêu oan', relations={}),
  ],
  pairs=[
    dict(spk='lp', to='lc', self=['tôi','tao','mình'], call=['cậu','mày','ông'], evidence=[25,64,120], confidence='high', note='Bạn thân cùng lứa (老周/老陆): tôi–cậu hoặc tao–mày, «ông» (ông bạn) chấp nhận'),
    dict(spk='lc', to='lp', self=['tôi','tao','mình'], call=['cậu','mày','ông'], evidence=[47,91,128], confidence='high', note='như trên'),
    dict(spk='nghipham', to='lp', self=['tôi','chúng tôi'], call=['các anh','anh','ngài','các ngài','các người','trưởng quan','ông','mày'], evidence=[7,8,10,11,41], confidence='medium',
         note='Người bị giữ gọi 两位长官: các anh/các ngài; lộ mặt thì «các người». «các cậu» (vi_system) lệch vai; «chúng ta» (gồm người nghe) sai → «chúng tôi»'),
    dict(spk='lp', to='group', self=['tôi','chúng tôi'], call=['mọi người','các người','các anh'], evidence=[13,15,21], confidence='medium', note='Trưởng quan nói với người bị giữ: «chúng tôi» (không gồm người nghe)'),
  ],
  idioms={
    39: dict(zh='欲加之罪何患无辞', accept=['thiếu gì cớ','lo gì không có cớ','lo gì không có lý do','muốn đổ tội','muốn buộc tội','vu oan','vu khống'], forbid=[]),
    41: dict(zh='赶尽杀绝', accept=['đuổi tận giết tuyệt','dồn đến đường cùng','dồn vào đường cùng','tận diệt'], forbid=[]),
    46: dict(zh='干得漂亮', accept=['làm tốt lắm','làm tốt','giỏi lắm','đẹp lắm','làm hay lắm','ngon lành'], forbid=[]),
    54: dict(zh='干得漂亮', accept=['làm tốt lắm','làm tốt','giỏi lắm','đẹp lắm','làm hay lắm','ngon lành'], forbid=[]),
    55: dict(zh='不划算', accept=['lỗ','không đáng','chẳng bõ','không bõ','không lời','thiệt','không hời'], forbid=[]),
    73: dict(zh='性价比', accept=['đáng làm','đáng tiền','hời','lời lỗ','giá trị','hiệu quả','đáng công'], forbid=['tỷ giá','so sánh giá']),
    74: dict(zh='性价比', accept=['đáng làm','đáng tiền','hời','lời lỗ','giá trị','hiệu quả','đáng công'], forbid=['tỷ giá','so sánh giá','giá cả']),
    95: dict(zh='有舍才有得', accept=['có bỏ mới có được','có mất mới có được','có bỏ ra mới có thu về','bỏ con săn sắt bắt con cá rô','có buông mới có được'], forbid=[]),
    101: dict(zh='败家', accept=['phá của','phá gia chi tử','hoang phí','phung phí'], forbid=[]),
    103: dict(zh='开挂', accept=['hack','gian lận','bật hack','dùng hack'], forbid=[]),
    104: dict(zh='手速', accept=['tốc độ tay','tay nhanh','nhanh tay'], forbid=[]),
    114: dict(zh='抢单之王', accept=['vua cướp đơn','vua giành đơn','vua săn đơn','vua chốt đơn'], forbid=[]),
    120: dict(zh='反向指标', accept=['chỉ số ngược','chỉ báo ngược','chỉ báo đảo ngược','chỉ số đảo ngược','chỉ báo nghịch'], forbid=[]),
    134: dict(zh='性价比太高', accept=['hời','đáng tiền','đáng làm','lời to','quá lời','đáng công'], forbid=['so sánh giá','tỷ giá']),
    137: dict(zh='开刷', accept=['cày','farm','càn quét','quét sạch'], forbid=['mở quét']),
  },
)
