"""Bilibili — tải bằng API gốc (DASH) thay vì yt-dlp.

Chuyển từ «bilibili_downloader» (2026-11-16) của chủ dự án: ký WBI → playurl DASH (fnval 4048) → né Akamai / MCDN, đổi
host sang cụm UPOS → tải đa luồng theo Range (thử mirror khi đoạn lỗi) → ghép ffmpeg -c copy. Thêm cho Pineline:
cookie từ cookies/bilibili_cookies.* (Netscape hoặc JSON), chọn codec (AVC mặc định, HEVC theo DOWNLOAD_PREFER_HEVC,
không AV1) + trần chiều cao như engines/download, dừng giữa chừng, tiến độ ghi manifest, bóc multi-P / hợp tập / series.

    api.py     WBI, nhận dạng link, thông tin video / hợp tập / series, playurl
    cdn.py     mirror UPOS + đo tốc độ chọn node
    fetch.py   tải đa luồng + ghép
    cookies.py đọc / lưu / kiểm cookie Bilibili
"""
