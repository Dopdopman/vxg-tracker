# Hướng dẫn Triển khai & Vận hành Production (`vxg-tracker`)

Tài liệu này hướng dẫn chi tiết từng bước triển khai **Phase 1** của **VXG Tracker** theo kiến trúc **Phương án A**:
- **Frontend (`apps/web`):** Vercel (region `sin1` Singapore).
- **Backend API (`apps/api`), Worker (`apps/worker`) & Managed PostgreSQL 16:** Railway (region Singapore).
- **CI/CD:** GitHub Actions ([.github/workflows/ci.yml](../.github/workflows/ci.yml)).

---

## 1. Khởi tạo Repository GitHub & CI/CD

1. Tạo repository mới trên GitHub (ví dụ `vxg-tracker`) và push nhánh `main`.
   - **Kiểm tra an toàn trước khi push:** Đảm bảo `.env` không bao giờ được commit (đã được chặn sẵn trong `.gitignore`). Chỉ commit `.env.example`.
2. GitHub Actions workflow `.github/workflows/ci.yml` sẽ tự động chạy 4 job trên mọi `push` và `pull_request` tới `main`:
   - `backend-quality-and-unit`: Kiểm tra `ruff`, `mypy` strict và chạy `pytest tests/unit` (toàn bộ Golden Fixtures offline).
   - `backend-integration-postgres`: Dựng `postgres:16-alpine`, chạy `alembic upgrade head`, chạy `seed.py` 2 lần để kiểm chứng idempotency, và chạy `pytest tests/integration`.
   - `frontend-ci`: Chạy `pnpm lint`, `pnpm typecheck`, `pnpm build` và quét bảo mật thư mục `.next/static`.
   - `docker-build-verify`: Build thử cả 3 Dockerfile (`api`, `worker`, `web`) khi 3 job trên đạt.

---

## 2. Triển khai Backend & PostgreSQL trên Railway (Region Singapore)

### Bước 2.1. Tạo Project & Managed PostgreSQL 16
1. Đăng nhập Railway -> **New Project** -> **Provision PostgreSQL** (chọn region **Southeast Asia / Singapore**).
2. Trong service PostgreSQL -> tab **Variables**, ghi nhận biến `DATABASE_URL` nội bộ dạng `postgresql://postgres:...@postgres.railway.internal:5432/railway` (hoặc dùng reference `${{Postgres.DATABASE_URL}}` từ các service khác).
   - *Lưu ý:* `vxg_shared.settings.SharedSettings` tự động chuẩn hóa scheme `postgres://` / `postgresql://` sang `postgresql+asyncpg://` (cho runtime async) và `postgresql+psycopg://` (cho Alembic migrations).

### Bước 2.2. Tạo khóa bảo mật `ADMIN_API_KEY` mạnh
Khi chạy với `APP_ENV=production`, `vxg-api` sẽ **từ chối khởi động (fail-fast)** nếu `ADMIN_API_KEY` để mặc định (`change-me-local-admin-key`), rỗng, hoặc ngắn hơn 24 ký tự. Sinh khóa ngẫu nhiên tối thiểu 32 byte:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Bước 2.3. Deploy Service `vxg-api`
1. Trong cùng Railway Project -> **New** -> **GitHub Repo** -> chọn repo `vxg-tracker`.
2. Vào **Settings** của service:
   - Đặt tên service: `vxg-api`.
   - Chọn **Region**: **Singapore**.
   - **Config as Code (Config File Path):** `apps/api/railway.json` *(file này đã cấu hình sẵn `apps/api/Dockerfile`, `preDeployCommand` chạy `alembic upgrade head && python -m vxg_shared.db.seed`, `$PORT`, và healthcheck `/ready`)*.
3. Trong tab **Variables** của `vxg-api`, thiết lập:

| Biến môi trường | Giá trị mẫu / Tham chiếu | Bắt buộc | Ghi chú |
| :--- | :--- | :---: | :--- |
| `APP_ENV` | `production` | Bắt buộc | Kích hoạt khóa kiểm tra `ADMIN_API_KEY` nghiêm ngặt |
| `TZ` | `Asia/Ho_Chi_Minh` | Bắt buộc | Khóa cứng múi giờ UTC+7 |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Bắt buộc | Dùng private network URL của Railway Postgres |
| `CORS_ORIGINS` | `https://<domain-vercel-cua-ban>` | Bắt buộc | Nhiều origin phân cách bằng dấu `,` |
| `ADMIN_API_KEY` | `<chuỗi ngẫu nhiên >= 32 ký tự>` | Bắt buộc | Dùng để mở khóa nút "Chạy cào thủ công" trên `/audit` |

4. Vào **Settings -> Networking -> Generate Domain** để lấy public domain cho API (ví dụ `https://vxg-api-production.up.railway.app`) và kiểm tra `GET https://<domain>/ready` trả về `{"status":"ready","database":"connected"}`.

### Bước 2.4. Deploy Service `vxg-worker`
1. Trong cùng Railway Project -> **New** -> **GitHub Repo** -> chọn lại repo `vxg-tracker`.
2. Vào **Settings** của service:
   - Đặt tên service: `vxg-worker`.
   - Chọn **Region**: **Singapore**.
   - **Config File Path:** `apps/worker/railway.json` *(dùng `apps/worker/Dockerfile` đã cài sẵn Playwright Chromium)*.
   - **Memory limit:** Nên đảm bảo gói từ **1 GB RAM** trở lên để Playwright headless render DOJI/PNJ/Petrolimex không bị OOM.
   - Không cần Generate Public Domain (worker là daemon nền chỉ kết nối PostgreSQL qua internal network).
3. Trong tab **Variables** của `vxg-worker`, thiết lập:

| Biến môi trường | Giá trị mẫu / Tham chiếu | Bắt buộc | Ghi chú |
| :--- | :--- | :---: | :--- |
| `APP_ENV` | `production` | Bắt buộc | Đồng bộ môi trường production |
| `TZ` | `Asia/Ho_Chi_Minh` | Bắt buộc | Khóa lịch cron APScheduler (10 jobs UTC+7) |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Bắt buộc | Trỏ cùng PostgreSQL với `vxg-api` |
| `WORKER_POLL_INTERVAL_SECONDS` | `5` | Tùy chọn | Chu kỳ đọc hàng đợi `QUEUED` trong `crawl_runs` |
| `PRICE_SPIKE_THRESHOLD_PCT` | `15.0` | Tùy chọn | Ngưỡng chặn giá biến động bất thường (%) |
| `SNAPSHOT_RETENTION_DAYS` | `90` | Tùy chọn | Số ngày lưu payload gzip trước khi tự động dọn |
| `ALERT_TELEGRAM_BOT_TOKEN` | `<bot_token>` | Tùy chọn | Token Telegram Bot nhận cảnh báo lỗi nguồn |
| `ALERT_TELEGRAM_CHAT_ID` | `<chat_id>` | Tùy chọn | Chat/Group ID nhận thông báo Telegram |
| `ALERT_DISCORD_WEBHOOK_URL` | `https://discord.com/api/webhooks/...` | Tùy chọn | Webhook kênh Discord vận hành |
| `SCRAPER_HTTP_PROXY` | `` | Tùy chọn | HTTP/SOCKS proxy nếu cần khi nguồn siết IP |

4. Sau khi `vxg-worker` chạy ổn định, mở trang `/audit` trên giao diện hoặc gọi `POST https://<api-domain>/api/v1/admin/crawl-runs` với `X-Admin-API-Key` để chạy phiên cào `ALL` đầu tiên làm đầy dữ liệu thật.

---

## 3. Triển khai Frontend (`apps/web`) trên Vercel

1. Truy cập Vercel -> **Add New... -> Project** -> Import repo GitHub `vxg-tracker`.
2. Trong phần **Configure Project**:
   - **Framework Preset:** `Next.js`
   - **Root Directory:** **`apps/web`** *(bắt buộc chỉnh đúng thư mục này cho Monorepo)*
   - Vercel sẽ tự động đọc `apps/web/vercel.json` (region `sin1` Singapore, `pnpm install --frozen-lockfile`, và các HTTP security headers).
3. Trong **Environment Variables** trên Vercel (cho cả `Production` và `Preview`):
   - `INTERNAL_API_URL` = `https://<domain-railway-api>` *(dùng cho Server-Side Rendering)*
   - `NEXT_PUBLIC_API_URL` = `https://<domain-railway-api>` *(dùng cho Client Components như Evidence Drawer, đổi thành phố, trang `/audit`)*
   - **TUYỆT ĐỐI KHÔNG** thêm `ADMIN_API_KEY` vào Vercel Environment Variables. Admin sẽ nhập trực tiếp khóa trên hộp thoại giao diện `/audit` (chỉ lưu tạm trong `sessionStorage` của trình duyệt).
4. Bấm **Deploy**. Sau khi có domain Vercel (ví dụ `https://vxg-tracker.vercel.app`), quay lại cập nhật biến `CORS_ORIGINS` trên Railway `vxg-api` khớp chính xác với origin đó (không có dấu `/` ở cuối).

---

## 4. Thiết lập Cảnh báo Tự động (Telegram / Discord)

`AlertNotifier` trong `vxg-worker` tự động gửi cảnh báo ngay sau khi kết thúc phiên cào nếu xảy ra một trong các sự kiện sau:
- Toàn phiên có trạng thái `FAILED`.
- Có nguồn gặp `SCHEMA_DRIFT` (web nguồn đổi cấu trúc DOM) hoặc `BLOCKED` / `HTTP 403`.
- Có bản ghi giá bị Guardrails chặn (`FLAGGED`) hoặc kích hoạt `AnomalyAlert` (ví dụ `SOURCE_DOWN_3X`).

**Cấu hình nhanh:**
- **Telegram:** Tạo bot với `@BotFather` lấy `ALERT_TELEGRAM_BOT_TOKEN`, thêm bot vào nhóm cảnh báo và lấy `ALERT_TELEGRAM_CHAT_ID`.
- **Discord:** Mở *Server Settings -> Integrations -> Webhooks -> New Webhook*, copy URL dán vào `ALERT_DISCORD_WEBHOOK_URL`.
- Nếu cả 2 kênh đều để trống (như môi trường local), Worker sẽ log ở mức `INFO` và bỏ qua an toàn, không ảnh hưởng quá trình cào giá.

---

## 5. Vận hành Dữ liệu, Snapshot Retention & Backup PostgreSQL

### Dọn dẹp `raw_evidence_snapshots`
- Mặc định Worker tự động chạy job `snapshot_retention_0315` vào **03:15 sáng hằng ngày (`Asia/Ho_Chi_Minh`)** để xóa các bản nén gzip cũ hơn `SNAPSHOT_RETENTION_DAYS` (90 ngày).
- **Bảo toàn toàn vẹn dữ liệu:** Mọi bản ghi trong `price_records`, `crawl_runs`, `crawl_source_logs`, `anomaly_alerts` **không bao giờ bị xóa**; đồng thời SHA-256 bằng chứng đang gắn với mức giá `VERIFIED` mới nhất của từng mặt hàng/khu vực luôn được giữ lại vĩnh viễn.
- Chạy thủ công khi cần:
  ```bash
  docker compose exec worker python -m vxg_worker.cli prune-snapshots --retention-days 90
  ```

### Backup & Khôi phục PostgreSQL
1. **Railway Managed Backups:** Bật Automated Daily Backups trong tab **Backups** của service PostgreSQL trên Railway.
2. **Sao lưu thủ công (`pg_dump`) từ local hoặc Railway CLI:**
   ```bash
   pg_dump "$DATABASE_URL_PUBLIC" --format=custom --no-owner --file="vxg_backup_$(date +%Y%m%d_%H%M).dump"
   ```
3. **Khôi phục (`pg_restore`):**
   ```bash
   pg_restore --dbname="$DATABASE_URL_PUBLIC" --clean --if-exists --no-owner "vxg_backup_YYYYMMDD_HHMM.dump"
   ```

