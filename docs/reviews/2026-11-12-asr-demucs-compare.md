# So sánh ASR FireRed: audio gốc vs giọng tách Demucs (2026-11-12)

Sinh bởi `scripts/asr_demucs_compare.py` (V6). Bản sao ở `D:\PinelineV6_work\asr_demucs_cmp`; V2 work chỉ đọc.
OFF = `ASR_USE_DEMUCS=0` (audio gốc) · ON = `ASR_USE_DEMUCS=1` (vocals.wav, mặc định mới V2).
CER bỏ dấu câu/khoảng trắng. Câu khớp = chồng thời gian IoU ≥ 0.3.

## 2937ffd7989f — 12.0 phút đầu

| | OFF (gốc) | ON (Demucs) | V2 production (cùng cửa sổ) |
|---|---:|---:|---:|
| Số câu | 36 | 32 | 36 |
| Tổng ký tự (bỏ dấu) | 244 | 242 | 248 |
| Thời gian chạy | 87.7s | 82.9s (gồm Demucs) | — |

- Câu khớp: **32** / OFF 36 / ON 32
- Lệch mốc đầu |Δstart|: TB 0.094s · trung vị 0.010s · p90 0.141s
- Lệch mốc cuối |Δend|: TB 0.139s · trung vị 0.010s · p90 0.770s
- CER(ON so với OFF): **4.51%**
- CER(OFF so với production — kiểm tái lập): 6.45%
- CER so với hardsub OCR: _không có_ (chạy với `--ocr` nếu phim có hardsub)

### Câu khác nhau nhiều nhất (top 15) — clip nghe ở `D:\PinelineV6_work\asr_demucs_cmp\2937ffd7989f\clips`

| # | Thời điểm | CER | OFF (gốc) | ON (Demucs) | clip |
|---:|---|---:|---|---|---|
| 1 | 8.8–9.9s | 400% | 我。 | 呵呵呵呵。 | `01_00008.8_*.wav` |
| 2 | 17.9–18.8s | 300% | 斩。 | 斩下一批。 | `02_00017.9_*.wav` |
| 3 | 14.0–14.8s | 150% | 什么？ | 什么下一批？ | `03_00014.0_*.wav` |
| 4 | 10.4–11.3s | 100% | 周家三房， | 周家三房周林已卖。 | `04_00010.4_*.wav` |
| 5 | 27.1–30.5s | 100% | 斩斩斩斩。 | 销账灭口。 | `05_00027.1_*.wav` |
| 6 | 23.3–25.1s | 50% | 下一批周家暗账房， | 下一批周家暗账房负责洗钱， | `06_00023.3_*.wav` |
| 7 | 14.8–17.4s | 27% | 下一批周家旁支周海一脉。 | 周家旁支周海一脉。 | `07_00014.8_*.wav` |
| 8 | 12.2–13.1s | 25% | 晏明无误。 | 验明无误。 | `08_00012.2_*.wav` |

Câu không khớp: chỉ OFF 4 · chỉ ON 0
- OFF 7.8s: 啊。
- OFF 11.3s: 周林一脉。
- OFF 18.8s: 下一批，
- OFF 25.1s: 负责洗钱销账灭口，

## 2d3316593ea9 — 12.0 phút đầu

| | OFF (gốc) | ON (Demucs) | V2 production (cùng cửa sổ) |
|---|---:|---:|---:|
| Số câu | 353 | 355 | 353 |
| Tổng ký tự (bỏ dấu) | 2724 | 2729 | 2724 |
| Thời gian chạy | 233.5s | 224.0s (gồm Demucs) | — |

- Câu khớp: **346** / OFF 353 / ON 355
- Lệch mốc đầu |Δstart|: TB 0.050s · trung vị 0.010s · p90 0.070s
- Lệch mốc cuối |Δend|: TB 0.061s · trung vị 0.010s · p90 0.099s
- CER(ON so với OFF): **1.80%**
- CER(OFF so với production — kiểm tái lập): 0.00%
- CER so với hardsub OCR: _không có_ (chạy với `--ocr` nếu phim có hardsub)

### Câu khác nhau nhiều nhất (top 15) — clip nghe ở `D:\PinelineV6_work\asr_demucs_cmp\2d3316593ea9\clips`

| # | Thời điểm | CER | OFF (gốc) | ON (Demucs) | clip |
|---:|---|---:|---|---|---|
| 1 | 496.6–497.4s | 300% | 啊。 | 那好诶。 | `01_00496.6_*.wav` |
| 2 | 0.6–1.2s | 200% | 啊。 | 嗯嗯。 | `02_00000.6_*.wav` |
| 3 | 5.0–6.1s | 100% | 啊哈？ | 我靠。 | `03_00005.0_*.wav` |
| 4 | 35.6–36.4s | 100% | 奸细。 | 奸细长官。 | `04_00035.6_*.wav` |
| 5 | 278.2–279.1s | 100% | 中修？ | 钟兄。 | `05_00278.2_*.wav` |
| 6 | 511.1–512.2s | 100% | 小翼飞太快。 | 小翼飞太快会有点恶心。 | `06_00511.1_*.wav` |
| 7 | 58.5–60.0s | 50% | 这五个人都有问题。 | 这五个人。 | `07_00058.5_*.wav` |
| 8 | 175.7–176.6s | 50% | 是吉师兄。 | 市集师兄。 | `08_00175.7_*.wav` |
| 9 | 293.0–296.0s | 39% | 九八九九小队也是八十九师第三炮部下哟。 | 鲁八九九小队也是八十级第三号。 | `09_00293.0_*.wav` |
| 10 | 167.8–169.0s | 33% | 这样嘛， | 这样吗？ | `10_00167.8_*.wav` |
| 11 | 701.6–702.7s | 33% | 呃我去。 | 我去。 | `11_00701.6_*.wav` |
| 12 | 284.0–285.2s | 29% | 不愧是中凶任务。 | 不愧是装修任务， | `12_00284.0_*.wav` |
| 13 | 707.0–708.2s | 29% | 实在是太散漫了。 | 你们实在是太散漫了， | `13_00707.0_*.wav` |
| 14 | 544.6–546.0s | 25% | 做了整整四个任务， | 做了整整四个任务试试。 | `14_00544.6_*.wav` |
| 15 | 566.0–567.0s | 20% | 买了狂袭珠。 | 买了狂西珠。 | `15_00566.0_*.wav` |

Câu không khớp: chỉ OFF 7 · chỉ ON 9
- OFF 1.4s: 嗯。
- OFF 15.1s: 喵。
- OFF 36.4s: 长官，
- OFF 56.7s: 他他他。
- OFF 67.8s: 嗯。
- OFF 512.3s: 会有点恶心。
- OFF 643.1s: 龙王。
- ON  10.7s: 行。
- ON  25.9s: 对。
- ON  59.3s: 都有问题。
- ON  89.5s: 呃呃啊。
- ON  91.8s: 喵。
- ON  140.1s: 啊。
- ON  151.4s: 啊。
- ON  282.2s: 不是。

## 9dedde16e35b — 12.0 phút đầu

| | OFF (gốc) | ON (Demucs) | V2 production (cùng cửa sổ) |
|---|---:|---:|---:|
| Số câu | 228 | 232 | 228 |
| Tổng ký tự (bỏ dấu) | 1779 | 1779 | 1779 |
| Thời gian chạy | 95.0s | 221.9s (gồm Demucs) | — |

- Câu khớp: **221** / OFF 228 / ON 232
- Lệch mốc đầu |Δstart|: TB 0.114s · trung vị 0.030s · p90 0.208s
- Lệch mốc cuối |Δend|: TB 0.131s · trung vị 0.020s · p90 0.198s
- CER(ON so với OFF): **2.53%**
- CER(OFF so với production — kiểm tái lập): 0.00%
- CER so với hardsub OCR: _không có_ (chạy với `--ocr` nếu phim có hardsub)

### Câu khác nhau nhiều nhất (top 15) — clip nghe ở `D:\PinelineV6_work\asr_demucs_cmp\9dedde16e35b\clips`

| # | Thời điểm | CER | OFF (gốc) | ON (Demucs) | clip |
|---:|---|---:|---|---|---|
| 1 | 182.8–185.5s | 200% | 秘境开了。 | Let's go逆境开啦。 | `01_00182.8_*.wav` |
| 2 | 338.3–339.1s | 200% | 这是？ | 这是五爪金龙。 | `02_00338.3_*.wav` |
| 3 | 85.8–87.5s | 100% | 怎么了？ | 哈哈哈。 | `03_00085.8_*.wav` |
| 4 | 562.3–563.7s | 83% | 前面三千里处。 | 前面三千里处那座山谷里， | `04_00562.3_*.wav` |
| 5 | 31.9–32.8s | 67% | 寒师兄。 | 韩兄。 | `05_00031.9_*.wav` |
| 6 | 371.4–373.2s | 67% | 嘘傲天。 | 昊天。 | `06_00371.4_*.wav` |
| 7 | 161.9–162.8s | 40% | 总是会变的。 | 人嘛总是会变的。 | `07_00161.9_*.wav` |
| 8 | 563.7–567.1s | 33% | 那座山谷里埋着一尊渡劫巅峰品阶的神龙震天鼎， | 埋着一尊渡劫巅峰品阶的神龙镇天顶， | `08_00563.7_*.wav` |
| 9 | 270.6–272.2s | 29% | 想必是有所觊觎。 | 想必是有所寄语。 | `09_00270.6_*.wav` |
| 10 | 535.9–537.9s | 29% | 蛟龙流尽的底蕴， | 蛟龙流进了底蕴。 | `10_00535.9_*.wav` |
| 11 | 680.9–685.7s | 29% | 整座五爪金龙秘境最大的成仙机缘就在这里面了。 | 整座五爪金龙秘境最大的成仙机缘。 | `11_00680.9_*.wav` |
| 12 | 550.4–553.9s | 28% | 往左三千里那个山洞里有一株万年龙须草。 | 那个山洞里有一株万年龙须草。 | `12_00550.4_*.wav` |
| 13 | 360.6–362.7s | 22% | 一个炼虚巅峰的小子？ | 一个连续巅峰的小子。 | `13_00360.6_*.wav` |
| 14 | 638.6–640.7s | 20% | 谁谁在说话？ | 谁在说话？ | `14_00638.6_*.wav` |
| 15 | 704.4–707.2s | 20% | 啊合体后期。 | 合体后期。 | `15_00704.4_*.wav` |

Câu không khớp: chỉ OFF 7 · chỉ ON 11
- OFF 181.3s: 啊，
- OFF 335.6s: 啊。
- OFF 339.2s: 五爪金龙。
- OFF 351.9s: 啊。
- OFF 435.0s: 哈哈哈就在那边。
- OFF 598.5s: 连地皮都给刮了一层。
- OFF 713.1s: 楚兄真是好运气。
- ON  84.8s: 早安。
- ON  102.6s: 啊。
- ON  138.9s: 行了。
- ON  228.8s: 走。
- ON  514.0s: 前辈！
- ON  550.4s: 往左三千里。
- ON  598.5s: 连地皮都给刮了一层。哎，
- ON  608.8s: 主要是。

## Kết luận

_Điền sau khi nghe clip: ON tốt hơn / ngang / kém hơn? Nếu kém -> rollback `ASR_USE_DEMUCS=0` (xem V2 docs/fixes/2026-11-12-asr-demucs-default.md)._
