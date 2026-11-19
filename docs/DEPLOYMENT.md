# Hướng dẫn Triển khai Production 100% Miễn phí (`vxg-tracker`)

Tài liệu này hướng dẫn chi tiết từng bước triển khai **Phase 1** của **VXG Tracker** trên hạ tầng **100% miễn phí chuẩn Production** (khu vực Singapore):

- **Database:** **Neon** Serverless PostgreSQL 16 (Singapore)
- **Scraper Worker & Scheduler:** **GitHub Actions** ([`crawl-schedule.yml`](../.github/workflows/crawl-schedule.yml) — Ubuntu `7GB RAM`, chạy Playwright Chromium)
- **REST & Audit API (`apps/api`):** **Render Free** Web Service (Singapore, dùng [`render.yaml`](../render.yaml))
- **Frontend (`apps/web`):** **Vercel Hobby** (region `sin1` Singapore, dùng [`apps/web/vercel.json`](../apps/web/vercel.json))

---

## Bước 1. Tạo PostgreSQL 16 miễn phí trên Neon (Singapore)

1. Đăng ký / đăng nhập tài khoản tại Neon -> **New Project**.
2. Chọn **Postgres version: `16`** và **Region: `AWS Asia Pacific (Singapore)` (`aws-ap-southeast-1`)**.
3. Trong màn hình **Connection Details**, sao chép chuỗi kết nối (thường có dạng):
   ```text
   postgresql://neondb_owner:...@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```
   - **Tương thích tự động:** `vxg_shared.settings` tự động chuyển `sslmode=require` sang `ssl=require` và loại bỏ `channel_binding` cho driver bất đồng bộ `asyncpg`, đồng thời giữ nguyên tham số SSL cho Alembic/`psycopg`, kèm `pool_pre_ping=True` và `pool_recycle=300` để tự phục hồi khi Neon wake up sau auto-suspend.
   - *(Tùy chọn)* Nếu bạn dùng URL **Pooled (`-pooler`)** cho `DATABASE_URL`, bạn có thể copy thêm URL **Direct** (không có `-pooler`) vào biến `DATABASE_URL_DIRECT` để Alembic chạy migration trực tiếp ổn định hơn.

---

## Bước 2. Đẩy mã nguồn lên GitHub, cấu hình Secrets & Fine-grained PAT

1. Tạo repository mới trên GitHub (ví dụ `vxg-tracker`) và push nhánh `main`.
   - Đảm bảo file `.env` cục bộ **không bao giờ** bị push (đã được chặn sẵn trong `.gitignore`).
2. Vào **GitHub Repo -> Settings -> Secrets and variables -> Actions -> New repository secret**, thêm:
   - `DATABASE_URL` *(Bắt buộc)*: Dán chuỗi kết nối PostgreSQL của **Neon** ở Bước 1.
   - `ALERT_TELEGRAM_BOT_TOKEN` *(Tùy chọn)*: Bot token nhận cảnh báo lỗi nguồn/dữ liệu bất thường.
   - `ALERT_TELEGRAM_CHAT_ID` *(Tùy chọn)*: Group/Chat ID nhận tin Telegram.
   - `ALERT_DISCORD_WEBHOOK_URL` *(Tùy chọn)*: URL Discord Webhook nếu dùng Discord.
3. Tạo **Fine-grained Personal Access Token** (`GITHUB_DISPATCH_TOKEN`) để nút **"Chạy cào thủ công"** trên `/audit` kích hoạt được GitHub Actions:
   - Vào **GitHub Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens -> Generate new token**.
   - **Repository access:** Chọn *Only select repositories* -> chọn repo `vxg-tracker`.
   - **Repository permissions:** Chỉ cấp **`Actions: Read and write`** (tối thiểu đặc quyền, không cấp quyền `Contents`/`Secrets` nếu không cần).
   - Sao chép token này để điền cho service `vxg-api` trên Render ở Bước 3.

### Lịch chạy tự động trên GitHub Actions (`.github/workflows/crawl-schedule.yml`)

| Giờ UTC (Cron) | Giờ Việt Nam (`UTC+7`) | Phạm vi thực thi | Mục đích |
| :--- | :--- | :--- | :--- |
| `15 17 * * *` | `00:15` sáng hằng ngày | `ALL` + `prune-snapshots --retention-days 90` | Quét đủ Vàng, Xăng dầu, Gas ngay khi bước qua ngày mới và dọn snapshot quá 90 ngày trong cùng 1 phiên chạy |

*(Ước tính tổng thời gian chạy ~40–75 phút/tháng trên `ubuntu-latest`, tiết kiệm tối đa trong hạn mức **2.000 phút/tháng miễn phí** của GitHub Actions cho private repo hoặc không giới hạn với public repo. Ngoài ra, `vxg-api` tự động hết hạn các bản ghi `QUEUED` quá **15 phút** sang `FAILED` trước khi kiểm tra trùng lặp, tránh khóa nút chạy cào thủ công trên `/audit` nếu GitHub Actions gặp sự cố trước khi kết nối DB.)*

---

## Bước 3. Triển khai `vxg-api` lên Render Free (Singapore)

1. Sinh khóa quản trị `ADMIN_API_KEY` mạnh (>= 24 ký tự; `vxg-api` ở chế độ `APP_ENV=production` sẽ từ chối khởi động nếu dùng key yếu hoặc mặc định):
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Đăng nhập **Render** -> **New -> Blueprint** (hoặc **New -> Web Service** chọn **Docker**):
   - Nếu dùng **Blueprint**, Render sẽ tự đọc [`render.yaml`](../render.yaml) ở thư mục gốc (`plan: free`, `region: singapore`, Dockerfile `./apps/api/Dockerfile`, `healthCheckPath: /health`).
   - Khi khởi động, [`apps/api/entrypoint.sh`](../apps/api/entrypoint.sh) thấy `RUN_MIGRATIONS_ON_START=true` sẽ tự động chạy `alembic upgrade head` và `python -m vxg_shared.db.seed` (idempotent) trước khi mở cổng `$PORT`.
3. Điền đầy đủ **Environment Variables** trên Render cho `vxg-api`:

| Biến môi trường | Giá trị | Bắt buộc | Ghi chú |
| :--- | :--- | :---: | :--- |
| `APP_ENV` | `production` | Bắt buộc | Bật Production Hardening kiểm tra `ADMIN_API_KEY` & GitHub config |
| `TZ` | `Asia/Ho_Chi_Minh` | Bắt buộc | Khóa chuẩn múi giờ UTC+7 |
| `RUN_MIGRATIONS_ON_START` | `true` | Bắt buộc | Tự chạy Alembic migration + seed khi Render khởi động container |
| `DATABASE_URL` | `<Neon Postgres URL>` | Bắt buộc | Chuỗi kết nối Neon ở Bước 1 |
| `DATABASE_URL_DIRECT` | `<Neon Direct URL>` | Khuyên dùng | Bỏ trống sẽ fallback về `DATABASE_URL` |
| `ADMIN_API_KEY` | `<khóa >= 32 ký tự vừa sinh>` | Bắt buộc | Bảo vệ endpoint `POST /api/v1/admin/crawl-runs` |
| `CORS_ORIGINS` | `https://<domain-vercel-cua-ban>` | Bắt buộc | Cập nhật ngay sau Bước 4 (không có dấu `/` cuối) |
| `WORKER_DISPATCH_MODE` | `github_actions` | Bắt buộc | Chuyển nút chạy cào thủ công sang dispatch GitHub Actions |
| `GITHUB_REPO` | `<username>/vxg-tracker` | Bắt buộc | Repository chứa `.github/workflows/crawl-schedule.yml` |
| `GITHUB_WORKFLOW_FILE` | `crawl-schedule.yml` | Mặc định | Tên file workflow trong `.github/workflows/` |
| `GITHUB_WORKFLOW_REF` | `main` | Mặc định | Nhánh mặc định để kích hoạt workflow |
| `GITHUB_DISPATCH_TOKEN` | `<Fine-grained PAT ở Bước 2>` | Bắt buộc | Quyền `Actions: Read and write` |

4. Sau khi deploy xong, mở `https://<ten-app>.onrender.com/ready` để xác nhận `{"status":"ready","database":"connected"}`.
5. Vào tab **Actions** trên GitHub -> **Crawl Schedule & On-Demand Worker -> Run workflow (`scope: ALL`)** để nạp bộ dữ liệu thật đầu tiên vào Neon.

---

## Bước 4. Triển khai Frontend (`apps/web`) lên Vercel Hobby

1. Truy cập **Vercel -> Add New... -> Project** -> Import repo GitHub `vxg-tracker`.
2. Trong phần **Configure Project**:
   - **Framework Preset:** `Next.js`
   - **Root Directory:** **`apps/web`** *(bắt buộc trỏ vào thư mục này của Monorepo)*
   - Vercel tự áp dụng [`apps/web/vercel.json`](../apps/web/vercel.json) (region `sin1` Singapore + security headers).
3. Trong **Environment Variables** của Vercel:
   - `INTERNAL_API_URL` = `https://<ten-app>.onrender.com`
   - `NEXT_PUBLIC_API_URL` = `https://<ten-app>.onrender.com`
   - **TUYỆT ĐỐI KHÔNG** đưa `ADMIN_API_KEY` hay `GITHUB_DISPATCH_TOKEN` lên Vercel.
4. Bấm **Deploy**. Khi có URL chính thức (ví dụ `https://vxg-tracker.vercel.app`), quay lại Render cập nhật `CORS_ORIGINS=https://vxg-tracker.vercel.app`.

---

## Bước 5. Mẹo giữ Render Free luôn thức bằng `cron-job.org` (Miễn phí)

Render Free Web Service sẽ tự ngủ (*spin down*) sau **15 phút** không có HTTP traffic, khiến request đầu tiên mất ~25–45 giây khởi động lại (*cold start*). Dù Frontend đã cấu hình timeout 55 giây để chịu cold start mượt mà, bạn có thể giữ API phản hồi tức thì `24/7`:

1. Tạo tài khoản miễn phí tại `https://cron-job.org`.
2. Chọn **Create cronjob**:
   - **URL:** `https://<ten-app>.onrender.com/health`
   - **Execution schedule:** **Every 14 minutes** (`*/14 * * * *`)
   - **Request method:** `GET`
3. Lưu và bật job — `vxg-api` sẽ luôn giữ trạng thái nóng mà không tốn thêm chi phí (vẫn giữ hạn mức free giờ chạy của Render).

---

## Bước 6. Cảnh báo lỗi & Sao lưu Neon PostgreSQL

- **Cảnh báo Telegram / Discord:** Chỉ cần điền `ALERT_TELEGRAM_BOT_TOKEN` + `ALERT_TELEGRAM_CHAT_ID` và/hoặc `ALERT_DISCORD_WEBHOOK_URL` trong GitHub Actions Secrets. Khi có phiên `FAILED`, nguồn gặp `SCHEMA_DRIFT`/`HTTP 403` hoặc có bản ghi `FLAGGED`, worker sẽ tự gửi thông báo theo giờ `UTC+7`.
- **Sao lưu thủ công từ máy cá nhân:**
  ```bash
  pg_dump "<NEON_DIRECT_DATABASE_URL>" --format=custom --no-owner --file="vxg_neon_$(date +%Y%m%d_%H%M).dump"
  ```
- **Khôi phục (`pg_restore`):**
  ```bash
  pg_restore --dbname="<NEON_DIRECT_DATABASE_URL>" --clean --if-exists --no-owner "vxg_neon_YYYYMMDD_HHMM.dump"
  ```
