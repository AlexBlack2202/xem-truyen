# xem-truyen

## Build trang đọc HTML tĩnh

`templates/page.html` là template dùng chung. `index.html` ở gốc vẫn giữ trình đọc
JavaScript cũ để hoạt động nếu host còn phát hành trực tiếp từ nhánh. Bản HTML tĩnh
cần phát hành thư mục `dist/` được tạo bởi `build_site.py`. Python 3.10 trở lên;
generator chỉ dùng thư viện chuẩn và không cần dịch vụ API hay JavaScript để đọc truyện.

```bash
python3 build_site.py
python3 -m unittest discover -s tests -p 'test_build_site.py' -v
```

Đầu ra gồm thư viện, mục lục từng truyện, và mỗi chương ở một URL riêng như
`/xem-truyen/tru-tien/chuong-1/`. Tên truyện, nội dung chương, liên kết trước/sau,
title, description và canonical có sẵn trong HTML. CSS Pearl Glass được dùng
chung; JavaScript chỉ bổ sung tìm chương, cỡ chữ, nền giấy và phím ←/→.
Phím điều hướng không kích hoạt khi đang thao tác với biểu mẫu hoặc liên kết.

URL cũ `?type=story&story=...` và `?type=chapter&story=...&chapter=...&name=...`
được trình duyệt chuyển sang URL canonical bằng manifest gọn. Dữ liệu JSON đầy đủ
không được chép vào bản phát hành. `name` cũ không quyết định route; truyện và chương
phải khớp. Query không hợp lệ hiển thị trang lỗi `noindex`; JavaScript không thể đổi
HTTP 200 thành 404. URL đường dẫn không tồn tại phải do host trả HTTP 404 bằng
`404.html` được cung cấp. Không cấu hình rewrite mọi URL về trang chủ.

Các chương trong `suggestedNoindexChapterIds` của `data/integrity-audit.json`
vẫn đọc và điều hướng được, nhưng có `noindex,follow` và không vào sitemap cho đến
khi đối chiếu được nguồn nội dung. Generator dừng nếu thiếu file, trùng route,
sai mã truyện/chương hoặc nội dung rỗng; bản build thành công trước đó được giữ lại
nếu build mới lỗi.

Thay địa chỉ canonical hoặc thư mục đầu ra khi cần:

```bash
python3 build_site.py --base-url https://www.phamduytung.com/xem-truyen/ --output dist
```

Để xem thử đúng tiền tố URL, tạo thư mục cha chứa `xem-truyen/`:

```bash
python3 build_site.py --output /tmp/reader-preview/xem-truyen
python3 -m http.server 8000 --directory /tmp/reader-preview
```

Sau đó mở `http://localhost:8000/xem-truyen/`. Chỉ định
`--base-url http://localhost:8000/xem-truyen/` nếu muốn canonical cũng trỏ về bản thử.

### GitHub Pages

Workflow `.github/workflows/pages.yml` chạy kiểm thử, audit dữ liệu, build và chỉ
upload `dist/` rồi triển khai từ nhánh mặc định. Pull request chỉ kiểm tra build.
Trong Settings → Pages, chọn nguồn **GitHub Actions** khi chuyển sang generator.
Workflow dùng các action chính thức theo
[hướng dẫn GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[checkout](https://github.com/actions/checkout) và
[setup-python](https://github.com/actions/setup-python).

`sitemap.xml` chỉ chứa canonical cần index. `robots.txt` ở thư mục con mang thông
tin tham khảo; crawler chỉ áp dụng `robots.txt` ở gốc domain. Cần thêm dòng dưới vào
`https://www.phamduytung.com/robots.txt` của website chính và gửi sitemap qua Search Console:

```text
Sitemap: https://www.phamduytung.com/xem-truyen/sitemap.xml
```

Template giữ AdSense và GA4, không có Ezoic/Adcash hay vị trí quảng cáo thủ công
trước nội dung. Mật độ, anchor và vignette của AdSense Auto ads thuộc bảng điều khiển
AdSense; phải kiểm tra riêng ở đó nếu muốn tắt những định dạng này. Các liên kết
footer dùng `/blog/`, `/contact/`, `/privacy/` của website chính.

Chạy toàn bộ bộ kiểm thử (bao gồm công cụ crawler cũ):

```bash
python3 -m pip install -r requirements-crawler.txt
python3 -m unittest discover -s tests -v
```

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
