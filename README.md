# xem-truyen

## Crawl ebook từ danh sách Google Drive

Script `crawl_drive_links.py` đọc một file HTML/text, loại link trùng và tải các
file Google Drive công khai vào `tmp/files`. Link thư mục được duyệt đệ quy và
lưu vào `tmp/folders`.

```bash
python3 -m pip install -r requirements-crawler.txt
python3 crawl_drive_links.py /duong/dan/danh-sach.html --dry-run
python3 crawl_drive_links.py /duong/dan/danh-sach.html --output tmp
```

Các tùy chọn hữu ích:

- `--limit 1`: chỉ thử link đầu tiên.
- `--workers 3`: giới hạn số lượt tải đồng thời.
- `--overwrite`: tải đè file đã tồn tại.
- `--dry-run`: chỉ sinh `tmp/links.json`, không truy cập mạng.

Sau khi chạy thật, kết quả chi tiết được ghi vào `tmp/manifest.json`. File
`.part` được giữ lại nếu tải gián đoạn để lần chạy sau có thể tiếp tục.

## Tiêu chí kiểm tra URL và link trong nội dung

Trước khi giao các file JSON, phải rà trường `datacontent` sau khi giải mã HTML
và không để lại:

- URL có giao thức (`http`, `https`, `ftp`, `mailto`), URL dạng `//...`, tiền tố
  `www.`, domain phổ biến (kể cả domain nhiều cấp như `.co.uk`), email hoặc IP
  có dạng địa chỉ mạng.
- Thẻ liên kết HTML (`<a ...>...</a>`), link Markdown (`[nhãn](địa_chỉ)`) và
  đích `href`/`src` của link.

Khi link có chữ hiển thị có nghĩa, chỉ bỏ địa chỉ/đích và giữ lại chữ đó. Sau
  bước làm sạch, `find_links()` phải trả về danh sách rỗng cho mọi nội dung;
  các tên có dấu chấm nhưng không khớp domain hợp lệ (ví dụ `Mr.Su`) không bị
  coi là URL.
