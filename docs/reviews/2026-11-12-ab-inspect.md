# Soi A/B muc cau: `simple` (B) vs `base` (A) tren golden

_2026-11-12 07:54 - `scripts/ab_inspect.py` - golden `data\golden` - job tam `D:\PinelineV6_work\golden_ab`. Golden la SILVER; chrF tung cau rat nhieu khi cau ngan - doc kem gold._

## Tong theo job

| Job | chrF base | chrF simple | cau ca 2 ghep | B tut / tang | sai xung ho A / B | khong ghep A / B |
|---|---|---|---|---|---|---|
| 32da3d8f225b | 61.77 | 59.19 | 141 | 54 / 76 | 57 / 2 | 0 / 0 |
| 9dedde16e35b | 74.31 | 67.03 | 140 | 68 / 54 | 37 / 2 | 0 / 0 |
| d1e00b9e7393 | 48.92 | 54.27 | 144 | 43 / 81 | 34 / 7 | 0 / 0 |
| f0834acf0cc0 | 73.49 | 62.77 | 141 | 73 / 53 | 29 / 8 | 0 / 1 |

Cau tut >= 10 diem chrF: 191 / 566 cau ghep. Theo error_tags cua gold (cau co the nhieu tag): (khong tag)=162, pronoun=14, meaning=12, style=3, halfcue_dup=2, name=1

## 30 cau chrF(simple) tut manh nhat so voi base

| # | job | i | chrF A->B | zh | gold | base | simple |
|---|---|---|---|---|---|---|---|
| 1 | 9dedde16e35b | 560 | 100.0 -> 0.0 (-100.0) | 啊。 | À. | À. | A! |
| 2 | 9dedde16e35b | 608 | 100.0 -> 0.0 (-100.0) | 啊。 | À. | À. | Á! |
| 3 | 9dedde16e35b | 609 | 100.0 -> 0.0 (-100.0) | 啊。 | À. | À. | Á! |
| 4 | f0834acf0cc0 | 523 | 100.0 -> 2.7 (-97.3) | 再说了， | Vả lại, | Vả lại, | Hơn nữa, |
| 5 | f0834acf0cc0 | 473 | 100.0 -> 5.5 (-94.5) | 老大就是老大。 | Lão đại chính là lão đại. | Lão đại chính là lão đại. | Trần tiểu tử đúng là Trần tiểu tử! |
| 6 | 32da3d8f225b | 3041 | 100.0 -> 8.5 (-91.5) | 学长。 | Học trưởng. | Học trưởng. | Anh. |
| 7 | 32da3d8f225b | 3081 | 100.0 -> 8.5 (-91.5) | 学长。 | Học trưởng. | Học trưởng. | Anh. |
| 8 | 32da3d8f225b | 3098 | 100.0 -> 8.5 (-91.5) | 学长。 | Học trưởng. | Học trưởng. | Anh. |
| 9 | f0834acf0cc0 | 536 | 100.0 -> 9.1 (-90.9) | 少辰。 | Thiếu Thần. | Thiếu Thần. | Thôi đi. |
| 10 | 32da3d8f225b | 3070 | 100.0 -> 9.5 (-90.5) | 学妹。 | Học muội. | Học muội. | Em. |
| 11 | d1e00b9e7393 | 2562 | 100.0 -> 9.9 (-90.1) | 走啊。 | Đi thôi. | Đi thôi. | Chạy đi! |
| 12 | 32da3d8f225b | 3063 | 100.0 -> 10.2 (-89.8) | 胡说八道。 | Nói hươu nói vượn. | Nói hươu nói vượn. | Nói nhảm! |
| 13 | 9dedde16e35b | 572 | 100.0 -> 12.6 (-87.4) | 玄澈师弟。 | Huyền Triệt sư đệ. | Huyền Triệt sư đệ. | Âm Dương sư huynh. |
| 14 | 32da3d8f225b | 3047 | 100.0 -> 13.0 (-87.0) | 这样啊。 | Thế à. | Thế à. | Vậy à. |
| 15 | 32da3d8f225b | 2987 | 100.0 -> 15.1 (-84.9) | 等着。 | Đợi đấy. | Đợi đấy. | Cứ chờ đấy! |
| 16 | 32da3d8f225b | 3052 | 89.1 -> 4.8 (-84.3) | 学长， | Học trưởng ơi. | Học trưởng ơi, | Anh, |
| 17 | f0834acf0cc0 | 513 | 100.0 -> 15.9 (-84.1) | 见过？ | Gặp qua? | Gặp qua? | Từng gặp rồi à? |
| 18 | f0834acf0cc0 | 496 | 100.0 -> 16.6 (-83.4) | 老大。 | Lão đại. | Lão đại. | Đại ca! |
| 19 | 32da3d8f225b | 3085 | 100.0 -> 16.7 (-83.3) | 那是什么？ | Đó là cái gì? | Đó là cái gì? | Thế là gì? |
| 20 | 9dedde16e35b | 605 | 100.0 -> 17.5 (-82.5) | 唉。 | Ối. | Ối. | Haiz... |
| 21 | f0834acf0cc0 | 528 | 100.0 -> 17.5 (-82.5) | 唉。 | Ối. | Ối. | Haiz... |
| 22 | 9dedde16e35b | 606 | 85.9 -> 3.5 (-82.4) | 不好意思， | Xin lỗi nhé. | Xin lỗi nhé, | Ngại quá, |
| 23 | 32da3d8f225b | 3078 | 100.0 -> 19.2 (-80.8) | 呵呵。 | Ha | Ha | Hê hê. |
| 24 | f0834acf0cc0 | 455 | 100.0 -> 19.5 (-80.5) | 这种情况。 | Tình huống này. | Tình huống này. | Trong trường hợp này... |
| 25 | 9dedde16e35b | 694 | 100.0 -> 19.7 (-80.3) | 既然如此。 | Đã như vậy. | Đã như vậy. | Nếu đã vậy, |
| 26 | 9dedde16e35b | 604 | 100.0 -> 20.8 (-79.2) | 呃。 | Ừ. | Ừ. | Ờ... |
| 27 | d1e00b9e7393 | 2575 | 100.0 -> 20.8 (-79.2) | 呃。 | Ừ. | Ừ. | Ờ... |
| 28 | 32da3d8f225b | 3111 | 100.0 -> 21.9 (-78.1) | 怎么刹车啊？ | Phanh kiểu gì vậy? | Phanh kiểu gì vậy? | Phanh thế nào đây? |
| 29 | f0834acf0cc0 | 569 | 100.0 -> 22.2 (-77.8) | 哼。 | Hử. | Hử. | Hừ. |
| 30 | 9dedde16e35b | 659 | 100.0 -> 22.6 (-77.4) | 恐怕连半年都撑不过去， | Chỉ sợ ngay cả nửa năm cũng không chống đỡ nổi | Chỉ sợ ngay cả nửa năm cũng không chống đỡ nổi | E là ngươi không trụ nổi quá nửa năm. |

## Cau khong ghep

- **f0834acf0cc0 / simple / i=542** - lech zh: lap ASR chua go (stutter) -> script giu nguyen ASR, gold da go. Gold zh `杀吧，`; script tai i: `杀吧杀吧，` -> Cứ giết đi, cứ giết đi,; ke ben: i=541 `呵，`, i=543 `最好把整个华夏全灭了。`

## Moi cau simple sai xung ho (19)

| job | i | spk->to | ky vong self / call | vi pham B | A | zh | gold | simple |
|---|---|---|---|---|---|---|---|---|
| 32da3d8f225b | 2973 | hahaikhon->minuong | tôi / các cô/cô | call:các người | wrong (call:các cậu) | 我给你们策反了个未来执事。 | Tôi đã kéo về cho các cô một chấp sự tương lai. | Tôi dụ được một chấp sự tương lai cho các người. |
| 32da3d8f225b | 3035 | cothanh->hathunhien | anh / - | kin:em | wrong (self:tôi) | 就算我不说， | dù anh không nói, | dù em không nói, |
| 9dedde16e35b | 572 | dth->adht | - / sư đệ | kin:sư huynh | correct | 玄澈师弟。 | Huyền Triệt sư đệ. | Âm Dương sư huynh. |
| 9dedde16e35b | 638 | ?->mkl | - / lão nhân gia | call:Ngài | wrong (call:cậu) | 你老人家还不出手吗？ | lão nhân gia còn chưa ra tay sao? | Ngài còn chưa ra tay sao? |
| d1e00b9e7393 | 2524 | chunguyet->tanvuphi | - / cô/mày | kin:Anh | wrong (call:Cậu) | 你比以前聪明了一些。 | Cô thông minh hơn trước rồi đấy. | Anh cũng khôn hơn trước một chút rồi. |
| d1e00b9e7393 | 2570 | tranhuyen->chunguyet | tôi/tao / anh/mày | call:cậu | wrong (call:cậu) | 我现在把你第三条腿也废掉。 | Tôi phế luôn cái chân thứ ba của anh. | tôi phế luôn chân thứ ba của cậu. |
| d1e00b9e7393 | 2610 | tranhuyen->tanvuphi | - / cô | kin:cháu | wrong (call:cậu) | 别怕，是你父亲来了。 | Đừng sợ, bố cô tới rồi. | Đừng sợ, bố cháu đến rồi. |
| d1e00b9e7393 | 2626 | canhsat->tranhuyen | - / anh | call:ngài | wrong (call:ngài) | 范围虽然您打断了他两条腿， | Tuy anh đánh gãy hai chân hắn, | Dù ngài đã đánh gãy hai chân hắn, |
| d1e00b9e7393 | 2629 | canhsat->tranhuyen | - / anh | call:ngài | wrong (call:Ngài) | 您出手制服他， | anh ra tay khống chế hắn, | ngài ra tay khống chế hắn, |
| d1e00b9e7393 | 2634 | canhsat->tranhuyen | - / anh | call:ngài | omitted | 他活该您无需承担刑事责任， | anh không phải chịu trách nhiệm hình sự, | Hắn đáng đời, ngài không phải chịu trách nhiệm hình sự. |
| d1e00b9e7393 | 2638 | canhsat->tranhuyen | - / anh | call:ngài | wrong (call:ngài) | 不过后续还需要请您配合做一份详细笔录，可以。 | nhưng sau đó vẫn cần anh phối hợp làm một bản tường trình chi tiết, được chứ? | Nhưng sau đó, mong ngài phối hợp lấy lời khai chi tiết, được chứ? |
| f0834acf0cc0 | 465 | dongdoi->tranphong | - / lão đại | kin:tiểu tử | correct | 老大啥意思啊？ | Lão đại có ý gì vậy? | Trần tiểu tử, ý gì vậy? |
| f0834acf0cc0 | 466 | dongdoi2->tranphong | - / anh/lão đại | kin:tiểu tử | wrong (call:cậu) | 你是说这玩意儿是有人故意弄出来的。 | Ý anh là thứ này có người cố tình tạo ra? | Ý Trần tiểu tử là có kẻ cố tình gây ra chuyện này à? |
| f0834acf0cc0 | 469 | tranphong->dongdoi | tôi/anh / - | self:Lão tử, kin:tiểu tử | correct | 我也觉得是。 | Tôi cũng thấy vậy. | Lão tử cũng thấy vậy, Trần tiểu tử. |
| f0834acf0cc0 | 485 | tranphong->tudu | tôi / - | self:ta | correct | 其他办法我还真不知道。 | Những cách khác tôi thật sự không biết. | Ngoài cách đó, ta thật sự không biết. |
| f0834acf0cc0 | 496 | dongdoi->tranphong | - / lão đại | kin:Đại ca | correct | 老大。 | Lão đại. | Đại ca! |
| f0834acf0cc0 | 504 | dangxa->tranphong | lão tử / tiểu tử/nhóc/Trần tiểu tử/mày | self:tôi | wrong (call:Cậu, self:tôi) | 你不知道找个空空点的地方召唤我吗？ | Nhóc không biết tìm chỗ nào trống trải mà triệu hồi lão tử à? | Không biết tìm chỗ rộng hơn rồi hãy triệu hồi tôi à? |
| f0834acf0cc0 | 525 | dangxa->tranphong | lão tử/anh mày / - | kin:tiểu tử | omitted | 也是哦哥也是神级了怕个屁。 | Cũng phải, lão tử đây cũng là Thần cấp rồi, sợ cái quái gì. | Cũng phải, Trần tiểu tử cũng Thần Cấp rồi, sợ cái quái gì! |
| f0834acf0cc0 | 529 | tranphong->dangxa | - / ông/lão Đằng | kin:tiểu tử | wrong (call:cậu) | 好吧那你去吧加油。 | Thôi được, vậy ông đi đi, cố lên. | Thôi được, Trần tiểu tử cứ đi đi, cố lên! |
