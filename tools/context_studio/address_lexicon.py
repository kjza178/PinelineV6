"""Kho xưng hô cổ trang / võ hiệp / tu tiên (VN dịch Trung).

Nguồn tham chiếu (quy ước văn học–phim, không phải sử liệu tuyệt đối):
- https://vandamphongkhuynh.com/2024/04/02/cach-xung-ho-trong-truyen-co-dai/
- https://dembuon.vn/threads/cach-xung-ho-thoi-phong-kien-trung-quoc.109332/
- https://ngotoc.vn/Van-hoa-Doi-song/mot-so-ten-goi-va-cach-xung-ho-thoi-phong-kien-630.html
- https://vozer.net/threads/cach-xung-ho-trong-truyen-co-trang-trung-quoc.6391/

Nguyên tắc dùng trong Pineline:
1. Chọn theo QUAN HỆ + địa vị, không nhồi hết vào mọi câu.
2. Cặp khiêm–tôn thường đi đôi: tại hạ ↔ các hạ; hạ thần ↔ bệ hạ.
3. «Ta/ngươi» chỉ là một lớp đại từ — thiếu cô nương/tại hạ/nô tài… là lỗ hổng lớn.
"""
from __future__ import annotations

from typing import Any

# --- Theo vai trò ngữ dụng -------------------------------------------------

SELF_HUMBLE: list[str] = [
    # giang hồ / gặp mặt
    "tại hạ", "tiểu nhân", "tiểu sinh", "vãn bối", "hậu bối", "tiểu bối",
    "tiểu nữ", "tiện thiếp", "thiếp",
    # hầu hạ
    "nô tài", "nô tỳ", "nô gia", "lão nô", "tiểu đích",
    # quan / thần
    "hạ thần", "vi thần", "thần", "hạ quan", "ti chức", "tiểu chức",
    "thảo dân", "nhi thần",
    # tu / đạo
    "bần tăng", "bần đạo", "bần ni", "lão nạp", "đệ tử", "đồ nhi",
    # già khiêm
    "lão phu", "lão hủ", "lão thân", "lão nương",
]

SELF_NOBLE: list[str] = [
    "ta", "bổn tọa", "bản tôn", "bản nhân",
    "bản vương", "bổn vương", "bản cung", "bổn cung",
    "bổn cô nương", "bổn tiểu thư", "bổn công tử", "bổn phu nhân",
    "bổn giáo chủ", "bản quan", "bổn tướng quân", "cô gia",
    "trẫm", "quả nhân", "ai gia",
]

CALL_PEER: list[str] = [
    "ngươi", "các ngươi", "các hạ", "túc hạ",
    "huynh đài", "hiền huynh", "hiền đệ", "huynh đệ",
    "bằng hữu", "đồng đạo", "võ lâm đồng đạo",
    "chư vị", "vị này",
]

CALL_MALE: list[str] = [
    "công tử", "thiếu gia", "lão gia", "gia chủ",
    "tiên sinh", "đại hiệp", "thiếu hiệp", "lão hiệp",
    "đại nhân", "tướng quân", "tráng sĩ",
]

CALL_FEMALE: list[str] = [
    "cô nương", "tiểu thư", "phu nhân", "lão phu nhân",
    "nương nương", "nương tử", "nữ hiệp",
]

SECT_ORDER: list[str] = [
    "sư phụ", "sư tôn", "ân sư", "sư nương", "sư trượng", "sư công",
    "sư tổ", "thái sư phụ", "tổ sư",
    "sư bá", "sư thúc", "sư muội", "sư tỷ", "sư huynh", "sư đệ",
    "đại sư huynh", "đồng môn", "cao đệ", "đồ tôn",
    "chưởng môn", "môn chủ", "bang chủ", "giáo chủ", "minh chủ",
    "đạo hữu", "đạo huynh", "đạo muội", "tiền bối", "lão tiền bối",
]

COURT: list[str] = [
    "bệ hạ", "thánh thượng", "hoàng thượng", "thiên tử",
    "điện hạ", "thái tử", "vương gia", "công chúa", "quận chúa",
    "phò mã", "thế tử",
    "hoàng huynh", "hoàng đệ", "hoàng tỷ", "hoàng muội",
    "hoàng bá", "hoàng thúc", "phụ hoàng", "mẫu hậu",
    "ái khanh", "chư khanh", "chúng khanh", "ái phi",
    "thần thiếp", "nương nương", "chủ tử",
]

SERVANT_HOUSE: list[str] = [
    "tiểu đồng", "nha hoàn", "tổng quản", "công công", "ma ma",
    "tiểu tư", "thị vệ", "hộ vệ",
]

COUPLE: list[str] = [
    "phu quân", "tướng công", "lang quân", "phu nhân",
    "nương tử", "ái khanh", "ý trung nhân",
]

# Nhắc người thân: gia- (của mình) / lệnh- (của đối phương)
KIN_SELF: list[str] = [
    "gia phụ", "gia mẫu", "gia huynh", "gia đệ", "gia tỷ", "gia muội",
    "gia tổ", "tệ nội", "tiện nội", "tệ phu", "tiện phu", "tệ nhi",
]

KIN_OTHER: list[str] = [
    "lệnh tôn", "lệnh đường", "lệnh sư", "lệnh huynh", "lệnh đệ",
    "lệnh tỷ", "lệnh muội", "lệnh lang", "lệnh ái", "lệnh thiên kim",
]

FAMILY_PLAIN: list[str] = [
    "phụ thân", "mẫu thân", "ca ca", "muội muội", "đệ đệ",
    "gia gia", "nãi nãi", "thúc phụ", "bá phụ", "cữu cữu", "di mẫu",
    "biểu ca", "biểu đệ", "đường huynh", "đường đệ",
]

THIRD_PERSON: list[str] = [
    "hắn", "nàng", "y", "kẻ đó", "vị kia",
]

# Flat prefer = gộp mọi nhóm (giữ tương thích style_lock.prefer)
XIANXIA_PREFER: list[str] = (
    THIRD_PERSON
    + SELF_HUMBLE
    + SELF_NOBLE
    + CALL_PEER
    + CALL_MALE
    + CALL_FEMALE
    + SECT_ORDER
    + COURT
    + SERVANT_HOUSE
    + COUPLE
    + KIN_SELF
    + KIN_OTHER
    + FAMILY_PLAIN
)

XIANXIA_FORBID: list[str] = [
    "bạn", "mình", "tớ", "cậu",  # thân mật hiện đại
    "ok", "okay", "oke",
    "bestie", "bro", "sis",
]

# Hiện đại: cấm lộ cổ (bản rút gọn + hay lộ nhất)
MODERN_FORBID: list[str] = [
    "ta", "ngươi", "các ngươi",
    "tại hạ", "các hạ", "túc hạ", "huynh đài",
    "tiểu nhân", "vãn bối", "nô gia", "nô tài", "nô tỳ",
    "bổn tọa", "bản tôn", "bản vương", "bổn vương", "bản cung", "bổn cung",
    "trẫm", "quả nhân", "ai gia", "hạ thần", "vi thần", "thần thiếp",
    "điện hạ", "bệ hạ", "thánh thượng", "hoàng thượng",
    "cô nương", "công tử", "tiểu thư", "thiếu gia", "nương nương",
    "đạo hữu", "chưởng môn", "sư tôn", "đại hiệp", "thiếu hiệp",
    "phu quân", "lang quân", "ái khanh", "thảo dân",
]

MODERN_PREFER: list[str] = [
    "tôi", "anh", "em", "chị", "cô", "chú", "bác", "ông", "bà",
    "mày", "tao", "hắn", "cô ấy", "anh ấy", "họ",
]

# ZH → VI gợi ý cho heuristic / glossary terms
ZH_ADDRESS_MAP: list[tuple[str, str, str]] = [
    ("在下", "tại hạ", "xưng hô"),
    ("阁下", "các hạ", "xưng hô"),
    ("足下", "túc hạ", "xưng hô"),
    ("本座", "bổn tọa", "xưng hô"),
    ("本尊", "bản tôn", "xưng hô"),
    ("本王", "bản vương", "xưng hô"),
    ("本宫", "bản cung", "xưng hô"),
    ("姑娘", "cô nương", "danh xưng"),
    ("公子", "công tử", "danh xưng"),
    ("小姐", "tiểu thư", "danh xưng"),
    ("少爷", "thiếu gia", "danh xưng"),
    ("老爷", "lão gia", "danh xưng"),
    ("夫人", "phu nhân", "danh xưng"),
    ("前辈", "tiền bối", "danh xưng"),
    ("晚辈", "vãn bối", "danh xưng"),
    ("道友", "đạo hữu", "danh xưng"),
    ("兄台", "huynh đài", "danh xưng"),
    ("殿下", "điện hạ", "danh xưng"),
    ("陛下", "bệ hạ", "danh xưng"),
    ("皇上", "hoàng thượng", "danh xưng"),
    ("奴才", "nô tài", "xưng hô"),
    ("奴婢", "nô tỳ", "xưng hô"),
    ("臣妾", "thần thiếp", "xưng hô"),
    ("微臣", "vi thần", "xưng hô"),
    ("下官", "hạ quan", "xưng hô"),
    ("哀家", "ai gia", "xưng hô"),
    ("寡人", "quả nhân", "xưng hô"),
    ("师父", "sư phụ", "danh xưng"),
    ("师尊", "sư tôn", "danh xưng"),
    ("弟子", "đệ tử", "danh xưng"),
    ("掌门", "chưởng môn", "danh xưng"),
    ("帮主", "bang chủ", "danh xưng"),
    ("教主", "giáo chủ", "danh xưng"),
    ("大侠", "đại hiệp", "danh xưng"),
    ("少侠", "thiếu hiệp", "danh xưng"),
    ("娘子", "nương tử", "danh xưng"),
    ("相公", "tướng công", "danh xưng"),
    ("夫君", "phu quân", "danh xưng"),
]


def address_bank() -> dict[str, list[str]]:
    """Nhóm theo ngữ dụng — đưa vào style_lock.address_bank."""
    return {
        "self_humble": list(SELF_HUMBLE),
        "self_noble": list(SELF_NOBLE),
        "call_peer": list(CALL_PEER),
        "call_male": list(CALL_MALE),
        "call_female": list(CALL_FEMALE),
        "sect_order": list(SECT_ORDER),
        "court": list(COURT),
        "servant_house": list(SERVANT_HOUSE),
        "couple": list(COUPLE),
        "kin_self": list(KIN_SELF),
        "kin_other": list(KIN_OTHER),
        "family_plain": list(FAMILY_PLAIN),
        "third_person": list(THIRD_PERSON),
    }


# Đô thị dị năng + võ/KH: giữ xưng hô hiện đại, nhưng cho phép sắc võ hiệp/cổ nhẹ
MIXED_LITERARY_PREFER: list[str] = [
    "hắn", "y", "nàng", "kẻ đó",
    "chiêu", "chiêu thức", "khí tức", "linh lực", "chiến ý", "ấn ký",
    "công chúa", "thiếu gia", "đại nhân",  # danh phận
    # sắc cổ phong (không phải đại từ cung đình)
    "vi cường", "thần tốc", "áp chế", "chiến lực", "thánh", "huyết",
]

# Cấm đại từ/địa chỉ cung đình nặng — vẫn mở cửa văn phong võ
MIXED_FORBID: list[str] = [
    "tại hạ", "các hạ", "túc hạ", "huynh đài",
    "tiểu nhân", "vãn bối", "nô gia", "nô tài", "nô tỳ",
    "bổn tọa", "bản tôn", "bản vương", "bổn vương", "bản cung", "bổn cung",
    "trẫm", "quả nhân", "ai gia", "hạ thần", "vi thần", "thần thiếp",
    "bệ hạ", "thánh thượng", "hoàng thượng", "ái khanh", "thảo dân",
    # Cấm calque máy / Hán Việt giả thoại
    "nhạo báng xem", "nháo báng",
]


def style_lock_policy(mode: str) -> str:
    if mode == "modern":
        return (
            "Contemporary Vietnamese; CẤM Ta–Ngươi và địa chỉ cổ "
            "(tại hạ/các hạ/cô nương/bổn tọa/điện hạ/nô tài…)."
        )
    if mode == "xianxia":
        return (
            "Ancient fantasy VN — chọn xưng hô theo quan hệ & địa vị "
            "(xem address_bank: self_humble/self_noble/call_*/sect/court/servant/couple). "
            "Cặp thường gặp: tại hạ↔các hạ; hạ thần↔bệ hạ; đồ nhi↔sư phụ; "
            "nô tài↔chủ tử; thần thiếp↔bệ hạ/phu quân. "
            "KHÔNG mặc định đổ ta/ngươi mọi câu. "
            "CẤM bạn/mình/tớ/cậu hiện đại. Không thơ hóa."
        )
    if mode == "mixed":
        return (
            "Đô thị dị năng / tinh cầu / hệ thống + võ chiến (mixed) — CÓ CỔ PHONG: "
            "xưng hô đời thường = tôi·anh·em·mày·tao "
            "(CẤM mặc định tại hạ/bổn tọa/trẫm/bệ hạ). "
            "BẮT BUỘC giữ sắc CỔ PHONG Hán Việt cho: chiêu thức, "
            "khí tức, ấn ký, cấp bậc, tinh cầu, danh hiệu chiến; "
            "vài 成语 hay dùng (vd «Tiên hạ thủ vi cường») — không nhồi thành ngữ hiếm. "
            "Danh phận (công chúa, thiếu gia…) giữ khi là chức vị. "
            "«嘲讽» / «嘲讽看»: dịch theo ngữ cảnh (không khóa cứng); "
            "CẤM «nhạo báng xem»; đối chiếu câu trước/sau nếu ASR lạ."
        )
    return ""


def merge_unique(base: list[str], extra: list[str]) -> list[str]:
    seen = {x.casefold() for x in base}
    out = list(base)
    for x in extra:
        k = x.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def enrich_style_lock(lock: dict[str, Any], mode: str) -> dict[str, Any]:
    """Đổ lexicon đầy đủ vào style_lock; modern thì tẩy địa chỉ cổ khỏi prefer."""
    lock = dict(lock or {})
    prefer = [str(x).strip() for x in (lock.get("prefer") or []) if str(x).strip()]
    forbid = [str(x).strip() for x in (lock.get("forbid") or []) if str(x).strip()]
    ancient_leak = {
        x.casefold() for x in (
            MODERN_FORBID + ["các ngươi", "đồ nhi", "sư phụ", "đệ tử", "bổn tọa"]
        )
    }

    if mode == "modern":
        lock["pronoun_policy"] = style_lock_policy("modern")
        # prefer: chỉ hiện đại — LLM hay nhét ta/ngươi vào
        prefer = [x for x in prefer if x.casefold() not in ancient_leak]
        prefer = merge_unique(prefer, MODERN_PREFER)
        # forbid: lexicon cổ; bỏ nhầm «tôi/anh/em» nếu LLM nhét vào forbid
        modern_ok = {x.casefold() for x in MODERN_PREFER}
        forbid = [x for x in forbid if x.casefold() not in modern_ok]
        forbid = merge_unique(forbid, MODERN_FORBID)
        lock.pop("address_bank", None)
    elif mode == "mixed":
        lock["pronoun_policy"] = style_lock_policy("mixed")
        # Xưng hô nền = hiện đại; thêm sắc võ/cổ nhẹ; CẤM cung đình nặng
        prefer = [x for x in prefer if x.casefold() not in {
            x.casefold() for x in MIXED_FORBID
        }]
        prefer = merge_unique(prefer, MODERN_PREFER)
        prefer = merge_unique(prefer, MIXED_LITERARY_PREFER)
        modern_ok = {x.casefold() for x in MODERN_PREFER}
        forbid = [x for x in forbid if x.casefold() not in modern_ok]
        forbid = merge_unique(forbid, MIXED_FORBID)
        # bank rút gọn: danh phận + ngôi 3 — không nhồi tại hạ/trẫm
        bank = lock.get("address_bank") if isinstance(lock.get("address_bank"), dict) else {}
        slim = {
            "call_male": list(CALL_MALE),
            "call_female": list(CALL_FEMALE),
            "third_person": list(THIRD_PERSON),
            "sect_order": list(SECT_ORDER),  # sư môn / giác tỉnh đôi khi dùng
        }
        for k, v in slim.items():
            cur = bank.get(k) if isinstance(bank.get(k), list) else []
            bank[k] = merge_unique([str(x) for x in cur if str(x).strip()], v)
        lock["address_bank"] = bank
        lock["time_period"] = lock.get("time_period") or "contemporary+combat-literary"
    elif mode == "xianxia":
        if not lock.get("pronoun_policy") or "address_bank" not in (lock.get("pronoun_policy") or ""):
            lock["pronoun_policy"] = style_lock_policy("xianxia")
        if len(forbid) < 4:
            forbid = merge_unique(forbid, XIANXIA_FORBID)
        if len(prefer) < 40:
            prefer = merge_unique(prefer, XIANXIA_PREFER)
        bank = lock.get("address_bank") if isinstance(lock.get("address_bank"), dict) else {}
        full = address_bank()
        for k, v in full.items():
            cur = bank.get(k) if isinstance(bank.get(k), list) else []
            bank[k] = merge_unique([str(x) for x in cur if str(x).strip()], v)
        lock["address_bank"] = bank

    lock["forbid"] = forbid
    lock["prefer"] = prefer
    return lock
