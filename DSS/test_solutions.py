import sys
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from DSS.picking_optimizer import optimize_picking
from DSS.putaway_allocator import allocate_inbound_goods

DB_URL = "postgresql+psycopg2://dw_admin:dw_password123@localhost:5433/warehouse_dw"
engine = create_engine(DB_URL)
DATADSS_DIR = BASE_DIR / "datadss"
DATADSS_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 80)
print("  KIỂM THỬ 2 GIẢI PHÁP RA QUYẾT ĐỊNH (DSS) THEO YÊU CẦU NGƯỜI DÙNG")
print("=" * 80)

# ---------------------------------------------------------
# SOLUTION 1: TỐI ƯU HÓA LỘ TRÌNH LẤY HÀNG (PICKING OPTIMIZATION)
# Kịch bản: Cần lấy 2 đôi giày A (O9YFO8) và B (I1X92B), mỗi đôi 3 size (7.0, 8.0, 9.0)
# ---------------------------------------------------------
print("\n[SOLUTION 1] Tối ưu hóa lộ trình lấy hàng cho nhân viên:")
pick_requests = [
    {'sku': 'O9YFO8', 'size': 7.0, 'qty': 1},
    {'sku': 'O9YFO8', 'size': 8.0, 'qty': 1},
    {'sku': 'O9YFO8', 'size': 9.0, 'qty': 1},
    {'sku': 'I1X92B', 'size': 7.0, 'qty': 1},
    {'sku': 'I1X92B', 'size': 8.0, 'qty': 1},
    {'sku': 'I1X92B', 'size': 9.0, 'qty': 1},
]
print("Yêu cầu lấy hàng:", pick_requests)

df_route, summary_pick = optimize_picking(pick_requests, engine, strategy_code='ClassBased')

if df_route is not None:
    print(f"\n✓ Đã tìm thấy lộ trình tối ưu qua {len(df_route)} điểm dừng:")
    print(df_route[['step', 'sku', 'size', 'location_code', 'floor_type', 'shelf_level_z', 'slot_number', 'location_priority_xyz', 'dist_from_origin']].to_string(index=False))
    print("\n✓ Hiệu quả tối ưu quãng đường & thời gian:")
    for k, v in summary_pick.items():
        print(f"   - {k}: {v}")
    
    out_pick = DATADSS_DIR / "optimal_picking_plan.csv"
    df_route.to_csv(out_pick, index=False, encoding='utf-8-sig')
    print(f"💾 Đã lưu kết quả Solution 1 vào: {out_pick}")
else:
    print(f"Lỗi: {summary_pick}")

# ---------------------------------------------------------
# SOLUTION 2: TỐI ƯU HÓA XẾP HÀNG NHẬP KHO (INBOUND PUT-AWAY ALLOCATION)
# Kịch bản: Nhập thêm 10 đôi giày A (O9YFO8) size 7.0
# ---------------------------------------------------------
print("\n" + "=" * 80)
print("[SOLUTION 2] Tối ưu hóa vị trí xếp hàng nhập kho (Put-Away Allocation):")
inbound_sku = 'O9YFO8'
inbound_size = 7.0
inbound_qty = 10
print(f"Kịch bản nhập hàng: {inbound_qty} đôi giày SKU {inbound_sku} (Hàng A) size {inbound_size}")

df_alloc, summary_alloc = allocate_inbound_goods(inbound_sku, inbound_size, inbound_qty, engine, strategy_code='ClassBased')

if df_alloc is not None:
    print(f"\n✓ Đã phân bổ thành công {len(df_alloc)} vị trí xếp hàng tối ưu:")
    print(df_alloc[['sku_reference', 'size_us', 'location_code', 'shelf_level_z', 'slot_number', 'floor_type', 'location_priority_xyz', 'dist_from_origin', 'suitability_score', 'reason']].to_string(index=False))
    print("\n✓ Thống kê phân bổ vị trí nhập kho:")
    for k, v in summary_alloc.items():
        print(f"   - {k}: {v}")
        
    out_alloc = DATADSS_DIR / "optimal_putaway_recommendations.csv"
    df_alloc.to_csv(out_alloc, index=False, encoding='utf-8-sig')
    print(f"💾 Đã lưu kết quả Solution 2 vào: {out_alloc}")
else:
    print(f"Lỗi: {summary_alloc}")

print("\n" + "=" * 80)
print("  HOÀN THÀNH KIỂM THỬ 2 GIẢI PHÁP!")
print("=" * 80)

