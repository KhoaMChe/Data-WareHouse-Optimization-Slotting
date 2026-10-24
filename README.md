# 📦 Hệ Thống Data Warehouse & DSS Vận Hành Kho Hàng (Warehouse Order Picking)

Hệ thống Kho dữ liệu (Data Warehouse) kết hợp Hệ hỗ trợ ra quyết định (DSS) phục vụ quản lý xuất nhập kho, phân tích hiệu suất lấy hàng (Order Picking) và tối ưu hóa vị trí lưu trữ sản phẩm (Slotting Optimization).

---

## 🏛️ 1. Kiến Trúc Tổng Quan Hệ Thống

```
             ┌──────────────────────────┐
             │       RAW CSV DATA       │  (9 file dữ liệu thô: Customer Order,
             └─────────────┬────────────┘   Picking Wave, Product, 4 Chiến lược kho...)
                           │
                           ▼
             ┌──────────────────────────┐
             │       EDA ANALYSIS       │  (Khám phá phân phối, tọa độ 3D,
             │      (EDA/EDA.ipynb)     │   phát hiện null, bất thường dữ liệu)
             └─────────────┬────────────┘
                           │
                           ▼
             ┌──────────────────────────┐
             │       ETL PIPELINE       │  (Làm sạch, điền missing, unpivot slot,
             │      (ETL/ETL.ipynb)     │   xuất ra data/staging/)
             └─────────────┬────────────┘
                           │
                           ▼
      ┌─────────────────────────────────────────┐
      │        DATA WAREHOUSE (POSTGRESQL)      │ ◄─── Kết nối DBeaver
      │           (Docker Container)            │      (Quản trị, xem ERD)
      │                                         │
      │  5 DIMENSIONS:                          │
      │    • dim_product   • dim_location       │
      │    • dim_operator  • dim_strategy       │
      │    • dim_date                           │
      │                                         │
      │  3 FACT TABLES:                         │
      │    • fact_order    • fact_picking       │
      │    • fact_storage                       │
      └────────────────────┬────────────────────┘
                           │
            ┌──────────────┴──────────────┐
            ▼                             ▼
   ┌─────────────────┐           ┌─────────────────┐
   │  OLAP ANALYSIS  │           │   DSS MODULES   │
   │  (Slicing, Dice,│           │  ① Slotting     │
   │   Rollup, Cube) │           │  ② Replenish    │
   └─────────────────┘           └────────┬────────┘
                                          │
                                          ▼
                                 ┌─────────────────┐
                                 │    DASHBOARD    │
                                 │   (Streamlit)   │
                                 └─────────────────┘
```

---

## 📂 2. Cấu Trúc Thư Mục Dự Án

```text
BTL/
├── data/
│   ├── raw/                      # 9 file CSV thô ban đầu (Product, Orders, Wave, Storage...)
│   └── staging/                  # 6 file CSV sạch sau khi làm sạch từ ETL
│       ├── stg_products.csv
│       ├── stg_locations.csv
│       ├── stg_orders.csv
│       ├── stg_picking.csv
│       ├── stg_nav_points.csv
│       └── stg_storage.csv
│
├── EDA/
│   └── EDA.ipynb                 # Khám phá toàn diện 9 dataset (35 cells, kèm outputs)
│
├── ETL/
│   ├── ETL.ipynb                 # TẦNG 1: Data Cleaning + Transformation -> Staging
│   └── DataWH.ipynb              # TẦNG 2: Xây dựng 5 DIM + 3 FACT -> Nạp vào PostgreSQL
│
├── warehouse/
│   └── schema.sql                # DDL chuẩn PostgreSQL (Tự động chạy khi khởi động Docker)
│
├── OLAP/
│   └── OLAP_Analysis.ipynb       # Phân tích đa chiều: Roll-up, Drill-down, Slice, Dice, Cube, Pareto
│
├── docker-compose.yml            # Khởi chạy PostgreSQL 16 trên Docker (Port 5433)
└── README.md                     # Tài liệu hướng dẫn dự án
```

---

## 🚀 3. Hướng Dẫn Khởi Chạy Nhanh (Quick Start)

### Bước 1: Khởi động Database PostgreSQL trên Docker

Mở PowerShell tại thư mục dự án và chạy:

```powershell
docker compose up -d
```

Container `warehouse_dw_postgres` sẽ khởi động và sẵn sàng nhận kết nối trên cổng **5433**.

### Bước 2: Chạy Tầng 1 — ETL Làm sạch dữ liệu (`ETL/ETL.ipynb`)

* Mở và chạy file [`ETL/ETL.ipynb`](<file:///d:/Cong%20Viec/Tailieu261/DataWarehouse/BTL/ETL/ETL.ipynb>).
* Pipeline sẽ xử lý lỗi, chuẩn hóa kiểu dữ liệu, unpivot 4 chiến lược kho và xuất 6 file sạch vào thư mục `data/staging/`.

### Bước 3: Chạy Tầng 2 — Mô hình hóa & Nạp Data Warehouse (`ETL/DataWH.ipynb`)

* Mở và chạy file [`ETL/DataWH.ipynb`](<file:///d:/Cong%20Viec/Tailieu261/DataWarehouse/BTL/ETL/DataWH.ipynb>).
* Dữ liệu từ `data/staging/` sẽ được ánh xạ khóa thay thế (Surrogate Keys) và nạp tự động vào PostgreSQL trong container Docker.

### Bước 4: Chạy Phân tích Đa Chiều OLAP (`OLAP/OLAP_Analysis.ipynb`)

* Mở và xem kết quả tại [`OLAP/OLAP_Analysis.ipynb`](<file:///d:/Cong%20Viec/Tailieu261/DataWarehouse/BTL/OLAP/OLAP_Analysis.ipynb>).
* Bao gồm đầy đủ 6 thao tác phân tích nâng cao (Roll-up, Drill-down, Slice, Dice, Pivot/Cube, Pareto 80/20) kèm 6 biểu đồ trực quan hóa.

---

## 🔌 4. Hướng Dẫn Kết Nối DBeaver & Xem Sơ Đồ ERD

### Thông tin kết nối:

| Thông số                | Giá trị                                        |
| ------------------------- | ------------------------------------------------ |
| **Database Server** | PostgreSQL 16                                    |
| **Host**            | `localhost`                                    |
| **Port**            | `5433` *(tránh trùng cổng 5432 cục bộ)* |
| **Database Name**   | `warehouse_dw`                                 |
| **Username**        | `dw_admin`                                     |
| **Password**        | `dw_password123`                               |

### Các bước kết nối trên DBeaver:

1. Nhấn nút **New Connection** $\rightarrow$ Chọn **PostgreSQL** $\rightarrow$ Bấm **Next**.
2. Nhập các thông số trên $\rightarrow$ Nhấn **Test Connection** (hiện màu xanh là thành công) $\rightarrow$ Nhấn **Finish**.

### Xem sơ đồ ER Diagram:

1. Trong cửa sổ **Database Navigator**, mở rộng:
   `warehouse_dw` $\rightarrow$ `Databases` $\rightarrow$ `warehouse_dw` $\rightarrow$ `Schemas` $\rightarrow$ `public`.
2. **Nhấp đúp chuột vào thư mục `Tables`**.
3. Chuyển sang tab **ER Diagram** ở màn hình chính bên phải để xem sơ đồ trực quan các liên kết bảng khóa ngoại.

---

## 📊 5. Cấu Trúc Các Bảng Trong Data Warehouse

| Bảng            |   Loại   | Khóa chính      |     Số dòng     | Mô tả nghiệp vụ                                                                              |
| ---------------- | :-------: | ----------------- | :---------------: | ------------------------------------------------------------------------------------------------ |
| `dim_product`  | Dimension | `product_key`   |   **317**   | Toàn bộ danh mục SKU (gồm 208 catalog + 109 layout kho), phân loại ABC.                    |
| `dim_location` | Dimension | `location_key`  |  **2,357**  | Toàn bộ vị trí ô kệ (2,292 vị trí chuẩn + 65 vị trí layout/cửa xuất), tọa độ 3D. |
| `dim_operator` | Dimension | `operator_key`  |   **24**   | Danh sách nhân viên kho / Pickers.                                                            |
| `dim_strategy` | Dimension | `strategy_key`  |    **4**    | 4 chiến lược lưu trữ: Random, ClassBased, Dedicated, Hybrid.                                |
| `dim_date`     | Dimension | `date_key`      |   **288**   | Chiều thời gian: Ngày, tháng, quý, năm, tuần, thứ, cờ cuối tuần.                      |
| `fact_order`   |   Fact   | `order_item_id` | **122,370** | Đơn hàng từ khách hàng: Mã đơn, item, số lượng đặt, size, ngày đặt.             |
| `fact_picking` |   Fact   | `pick_id`       | **215,192** | Hoạt động lấy hàng thực tế theo Wave: Wave number, vị trí kệ, tọa độ, cự ly.       |
| `fact_storage` |   Fact   | `storage_id`    | **167,634** | Hiện trạng phân bổ tồn kho của 4 chiến lược lưu trữ (slot 1–18).                     |

---

## 🎯 6. Kế Hoạch Các Bước Tiếp Theo (Roadmap)

1. **Phân tích đa chiều OLAP (`OLAP/`):**
   - Viết các truy vấn `ROLLUP`, `CUBE`, `GROUPING SETS`, Window functions trên PostgreSQL để phân tích xu hướng xuất kho, phân tích Pareto 80/20 và hiệu suất nhân viên.
2. **Hệ hỗ trợ ra quyết định DSS (`DSS/`):**
   - **Module 1 (Slotting Optimization):** Thuật toán xếp hàng nhóm A vào tầng Golden Zone ($Z=1, 2$) và gần cửa xuất ($X, Y$ nhỏ) để giảm tối đa quãng đường di chuyển (Travel Distance).
   - **Module 2 (Replenishment Alert):** Cảnh báo khi ô kệ picking sắp hết hàng dựa trên nhu cầu đơn hàng từ `fact_order`.
3. **Trực quan hóa Dashboard (`dashboard/`):**
   - Xây dựng giao diện Streamlit kết nối trực tiếp PostgreSQL hiển thị Heatmap kho 2D/3D và module gợi ý vị trí ô kệ.
   - Test thử chuyển về branch main
