-- ============================================================================
-- DATA WAREHOUSE SCHEMA FOR WAREHOUSE ORDER PICKING & DSS SYSTEM
-- RDBMS: PostgreSQL 16
-- Model: Fact Constellation (7 Dimensions, 3 Fact Tables)
-- Phiên bản: v2.0 — Cập nhật sau đánh giá khắt khe thiết kế DIM/FACT
-- ============================================================================

-- Dọn dẹp nếu đã tồn tại (theo thứ tự phụ thuộc FK)
DROP TABLE IF EXISTS fact_storage CASCADE;
DROP TABLE IF EXISTS fact_picking CASCADE;
DROP TABLE IF EXISTS fact_order   CASCADE;

DROP TABLE IF EXISTS dim_nav_point CASCADE;
DROP TABLE IF EXISTS dim_strategy  CASCADE;
DROP TABLE IF EXISTS dim_wave      CASCADE;
DROP TABLE IF EXISTS dim_operator  CASCADE;
DROP TABLE IF EXISTS dim_date      CASCADE;
DROP TABLE IF EXISTS dim_location  CASCADE;
DROP TABLE IF EXISTS dim_product   CASCADE;

-- ============================================================================
-- 1. DIMENSION TABLES (CÁC BẢNG CHIỀU)
-- ============================================================================

-- 1.1 Chiều Sản phẩm
-- THAY ĐỔI v2.0: Thêm xyz_code (phân loại ổn định nhu cầu) và source (xuất xứ SKU)
CREATE TABLE dim_product (
    product_key     SERIAL PRIMARY KEY,
    reference       VARCHAR(50) NOT NULL UNIQUE,
    abc_code        VARCHAR(5)  NOT NULL,            -- A: Fast-moving, B: Medium, C: Slow-moving
    xyz_code        VARCHAR(5),                      -- X: Stable demand, Y: Variable, Z: Irregular, C: Slow
    sector          VARCHAR(50),                     -- Ngành hàng: PF (Protective Footwear)
    source          VARCHAR(20) NOT NULL DEFAULT 'catalog'  -- 'catalog' | 'extended' (từ layout kho)
);

-- 1.2 Chiều Vị trí ô kệ lưu trữ (kèm tọa độ không gian 3D)
-- THAY ĐỔI v2.0: Thêm floor_type (phân loại tầng theo nghiệp vụ) và rack_capacity (sức chứa kệ)
CREATE TABLE dim_location (
    location_key     SERIAL PRIMARY KEY,
    location_code    VARCHAR(50) NOT NULL UNIQUE,     -- Mã định danh: e.g. H-06-13
    position_str     VARCHAR(50),
    x                DOUBLE PRECISION,                -- Trục X (đơn vị theo bản vẽ CAD)
    y                DOUBLE PRECISION,                -- Trục Y (đơn vị theo bản vẽ CAD)
    z                DOUBLE PRECISION,                -- Trục Z (tầng kệ: 1-4)
    zone             VARCHAR(10),                     -- Khu vực: A, B, C, ..., Q
    aisle            INTEGER,                         -- Dãy kệ
    slot             INTEGER,                         -- Ô trên kệ
    dist_from_origin DOUBLE PRECISION,                -- Khoảng cách Euclidean từ điểm I/O (cửa xuất kho)
    floor_type       VARCHAR(30),                     -- 'Manual Picking' (z=1,2) | 'Replenishment' (z=3,4)
    rack_capacity    SMALLINT DEFAULT 18              -- Số ngăn chứa trên mỗi ô kệ (18 slots/vị trí)
);

-- 1.3 Chiều Thời gian (cấp độ Ngày)
CREATE TABLE dim_date (
    date_key        INTEGER PRIMARY KEY,             -- Format: YYYYMMDD (e.g. 20230201)
    full_date       DATE NOT NULL,
    year            SMALLINT NOT NULL,
    quarter         SMALLINT NOT NULL,
    month           SMALLINT NOT NULL,
    month_name      VARCHAR(20) NOT NULL,
    week            SMALLINT NOT NULL,
    day_of_week     SMALLINT NOT NULL,               -- 0=Thứ 2, 6=Chủ nhật
    day_name        VARCHAR(20) NOT NULL,
    is_weekend      SMALLINT NOT NULL,               -- 1: Cuối tuần, 0: Ngày thường
    day_of_year     SMALLINT NOT NULL
);

-- 1.4 Chiều Nhân viên vận hành (Pickers / Operators)
CREATE TABLE dim_operator (
    operator_key    SERIAL PRIMARY KEY,
    operator_name   VARCHAR(50) NOT NULL UNIQUE
);

-- 1.5 Chiều Đợt gom hàng (Picking Wave) — MỚI HOÀN TOÀN trong v2.0
-- Lý do: wave_number là một business entity có thuộc tính riêng, không chỉ là số thô.
-- Mỗi Wave đại diện cho 1 đợt gom hàng được giao cho 1 nhân viên trên 1 xe đẩy (tối đa 27 items).
CREATE TABLE dim_wave (
    wave_key              SERIAL PRIMARY KEY,
    wave_number           INTEGER NOT NULL UNIQUE,   -- Mã Wave gốc từ WMS
    wave_date             DATE,                      -- Ngày thực hiện Wave
    wave_date_key         INTEGER REFERENCES dim_date(date_key),
    wave_hour             SMALLINT,                  -- Giờ thực hiện Wave (6-20)
    wave_shift            VARCHAR(20),               -- 'Morning' (6-12h) | 'Afternoon' (12-20h)
    total_pick_lines      SMALLINT,                  -- Tổng số dòng nhặt trong Wave (≤ 27)
    distinct_products     SMALLINT,                  -- Số SKU khác nhau cần nhặt
    total_qty_to_pick     SMALLINT,                  -- Tổng số lượng cần nhặt
    capacity_utilization  DOUBLE PRECISION           -- Tỷ lệ sử dụng xe đẩy = total_pick_lines / 27.0
);

-- 1.6 Chiều Chiến lược lưu trữ (Storage Strategies)
CREATE TABLE dim_strategy (
    strategy_key    SERIAL PRIMARY KEY,
    strategy_code   VARCHAR(50) NOT NULL UNIQUE,     -- Random, ClassBased, Dedicated, Hybrid
    strategy_name   VARCHAR(100) NOT NULL,
    description     TEXT,
    target_metric   VARCHAR(100)                     -- Mục tiêu tối ưu
);

-- Chèn trước dữ liệu cho DIM_STRATEGY
INSERT INTO dim_strategy (strategy_code, strategy_name, description, target_metric) VALUES
('Random',     'Random Storage Strategy',     'Hàng hóa được đặt ngẫu nhiên vào các ô trống còn trống (Baseline).',          'Không tối ưu'),
('ClassBased', 'Class-Based Storage Strategy','Hàng được gom nhóm theo phân loại ABC (tần suất xuất kho). Hàng A đặt gần cửa xuất.', 'Giảm quãng đường trung bình'),
('Dedicated',  'Dedicated Storage Strategy',  'Mỗi sản phẩm có vị trí cố định duy nhất trong kho theo mã XYZ.',              'Kiểm soát vị trí cố định'),
('Hybrid',     'Hybrid Storage Strategy',     'Kết hợp linh hoạt giữa Class-Based và Dedicated để cân bằng dung lượng và cự ly.', 'Cân bằng tải và quãng đường');

-- 1.7 Chiều Điểm điều hướng / Mạng lưới lối đi kho (Navigation Waypoints cho DSS & AGV)
CREATE TABLE dim_nav_point (
    nav_key         SERIAL PRIMARY KEY,
    label           VARCHAR(20) NOT NULL UNIQUE,     -- LC-01, CC-05, RC-12
    corridor_code   VARCHAR(10) NOT NULL,            -- LC, CC, RC
    corridor_name   VARCHAR(50) NOT NULL,            -- Left Corridor, Central Corridor, Right Corridor
    point_number    INTEGER,                         -- Thứ tự điểm mốc dọc hành lang (1-17)
    x_nav           DOUBLE PRECISION NOT NULL,
    y_nav           DOUBLE PRECISION NOT NULL,
    z_nav           DOUBLE PRECISION NOT NULL
);

-- ============================================================================
-- 2. FACT TABLES (CÁC BẢNG SỰ KIỆN)
-- ============================================================================

-- 2.1 FACT_ORDER: Nhu cầu xuất kho từ đơn hàng khách hàng
-- THAY ĐỔI v2.0: Thêm wave_key (FK đến dim_wave) và UNIQUE constraint đúng hạt.
-- Hạt (Grain): 1 dòng = 1 dòng SKU trong 1 đơn hàng được xử lý trong 1 Wave cụ thể.
-- NOTE: wave_number được giữ lại dưới dạng degenerate dim để backward-compat.
CREATE TABLE fact_order (
    order_item_id    SERIAL PRIMARY KEY,
    order_number     INTEGER  NOT NULL,
    order_to_collect INTEGER,
    customer_code    VARCHAR(50),
    date_key         INTEGER  REFERENCES dim_date(date_key),
    wave_key         INTEGER  REFERENCES dim_wave(wave_key),    -- FK mới đến dim_wave
    product_key      INTEGER  REFERENCES dim_product(product_key),
    operator_key     INTEGER  REFERENCES dim_operator(operator_key),
    wave_number      INTEGER,                                    -- Degenerate dim (giữ lại để tương thích)
    size_us          DOUBLE PRECISION,
    quantity_units   INTEGER  NOT NULL
);

-- 2.2 FACT_PICKING: Hoạt động lấy hàng thực tế theo từng đợt sóng
-- THAY ĐỒI v2.0:
--   (a) Thêm wave_key (FK đến dim_wave)
--   (b) XÓA x, y, z, dist_from_origin — tọa độ là thuộc tính của dim_location, không phải measure của Fact.
--       Truy vấn OLAP/DSS dùng: JOIN dim_location ON location_key để lấy tọa độ khi cần.
CREATE TABLE fact_picking (
    pick_id          SERIAL PRIMARY KEY,
    wave_number      INTEGER  NOT NULL,                          -- Degenerate dim
    wave_key         INTEGER  REFERENCES dim_wave(wave_key),     -- FK mới đến dim_wave
    date_key         INTEGER  REFERENCES dim_date(date_key),
    product_key      INTEGER  REFERENCES dim_product(product_key),
    location_key     INTEGER  REFERENCES dim_location(location_key),
    operator_key     INTEGER  REFERENCES dim_operator(operator_key),
    size_us          DOUBLE PRECISION,
    qty_to_pick      INTEGER  NOT NULL
);

-- 2.3 FACT_STORAGE: Hiện trạng phân bổ vị trí lưu trữ hàng hóa theo từng chiến lược
-- Hạt (Grain): 1 dòng = 1 ngăn (slot) trên 1 vị trí kệ theo 1 chiến lược cụ thể.
CREATE TABLE fact_storage (
    storage_id          SERIAL PRIMARY KEY,
    strategy_key        INTEGER REFERENCES dim_strategy(strategy_key),
    location_key        INTEGER REFERENCES dim_location(location_key),
    product_key         INTEGER REFERENCES dim_product(product_key),
    slot_number         INTEGER,                     -- Vị trí slot từ 1 đến 18 trong ô kệ
    classification_code VARCHAR(10),                 -- Mã nhóm ABC hoặc XYZ theo layout chiến lược
    size_us             DOUBLE PRECISION             -- Kích cỡ giày US (Shoe Size theo bài báo khoa học)
);

-- ============================================================================
-- 3. INDEXES CHO TỐI ƯU HÓA TRUY VẤN OLAP & DSS
-- ============================================================================

-- fact_order
CREATE INDEX idx_fo_date      ON fact_order(date_key);
CREATE INDEX idx_fo_wave_key  ON fact_order(wave_key);
CREATE INDEX idx_fo_product   ON fact_order(product_key);
CREATE INDEX idx_fo_operator  ON fact_order(operator_key);
CREATE INDEX idx_fo_wave      ON fact_order(wave_number);
CREATE INDEX idx_fo_customer  ON fact_order(customer_code);

-- fact_picking
CREATE INDEX idx_fp_date      ON fact_picking(date_key);
CREATE INDEX idx_fp_wave_key  ON fact_picking(wave_key);
CREATE INDEX idx_fp_product   ON fact_picking(product_key);
CREATE INDEX idx_fp_location  ON fact_picking(location_key);
CREATE INDEX idx_fp_operator  ON fact_picking(operator_key);
CREATE INDEX idx_fp_wave      ON fact_picking(wave_number);

-- fact_storage
CREATE INDEX idx_fs_strategy  ON fact_storage(strategy_key);
CREATE INDEX idx_fs_location  ON fact_storage(location_key);
CREATE INDEX idx_fs_product   ON fact_storage(product_key);
CREATE INDEX idx_fs_class     ON fact_storage(classification_code);

-- dim_wave
CREATE INDEX idx_dw_date_key  ON dim_wave(wave_date_key);
CREATE INDEX idx_dw_shift     ON dim_wave(wave_shift);

-- dim_location
CREATE INDEX idx_dl_zone      ON dim_location(zone);
CREATE INDEX idx_dl_floor     ON dim_location(floor_type);

-- dim_location
CREATE INDEX idx_dl_zone      ON dim_location(zone);
CREATE INDEX idx_dl_floor     ON dim_location(floor_type);

