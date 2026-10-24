"""
=============================================================================
DSS SOLUTION 2: OPTIMAL INBOUND PUT-AWAY & STORAGE ALLOCATION
Tối ưu hóa vị trí xếp hàng nhập kho mới cho nhân viên dễ tìm, dễ lấy
=============================================================================
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import create_engine
import joblib

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

DB_URL = "postgresql+psycopg2://dw_admin:dw_password123@localhost:5433/warehouse_dw"

def allocate_inbound_goods(sku_reference, size_us, quantity_units, engine, strategy_code='ClassBased'):
    """
    Đề xuất vị trí xếp hàng nhập kho tối ưu nhất cho lô hàng mới.
    
    Quy tắc phân bổ (Domain Heuristic Rules + Machine Learning):
      1. Đánh giá mức độ ưu tiên mặt hàng (SKU Priority ABC):
         - Hàng A (Bán chạy / Nhu cầu cao): Phải đặt ở vị trí VÀNG (XYZ = X), 
           tầng nhặt thủ công (Z <= 2 Manual Picking), gần hành lang chính & cửa xuất kho.
         - Hàng B (Trung bình): Vị trí trung bình (XYZ = Y), cự ly vừa phải.
         - Hàng C (Bán chậm): Tầng trên cao (Z = 3, 4 Replenishment) hoặc vị trí sâu (Z/C),
           nhường toàn bộ mặt tiền và tầm thấp cho hàng A và B.
      2. Gom cụm hàng cùng loại (Clustering):
         - Ưu tiên xếp cùng dãy hoặc gần các vị trí hiện đang lưu trữ SKU này để nhân viên dễ nhớ, dễ tìm.
      3. Quản lý dung lượng & vị trí:
         - Đề xuất chính xác: Mã kệ (location_code), Tầng kệ (Z), Số ô (slot_number), 
           Cấp độ ưu tiên lối đi (XYZ), Cự ly cách cửa xuất.
    """
    # 1. Trích xuất thông tin sản phẩm từ Data Warehouse
    q_sku = f"""
    SELECT product_key, reference AS sku_reference, abc_code AS sku_priority_abc, sector
    FROM dim_product
    WHERE reference = '{sku_reference}';
    """
    df_sku = pd.read_sql(q_sku, engine)
    if df_sku.empty:
        return None, f"Không tìm thấy thông tin sản phẩm '{sku_reference}' trong danh mục Data Warehouse."
        
    sku_info = df_sku.iloc[0].to_dict()
    abc_code = sku_info['sku_priority_abc']
    
    # 2. Tìm các vị trí hiện tại đang lưu trữ SKU này (để tính clustering bonus)
    q_existing = f"""
    SELECT DISTINCT location_code, zone, aisle
    FROM v_warehouse_inventory
    WHERE strategy_code = '{strategy_code}' AND sku_reference = '{sku_reference}';
    """
    df_existing = pd.read_sql(q_existing, engine)
    existing_locs = set(df_existing['location_code'].tolist())
    existing_aisles = set(df_existing['aisle'].tolist())
    
    # 3. Lấy thông tin các kệ trong kho kèm số ô đang lưu trữ
    q_capacity = f"""
    SELECT 
        l.location_key,
        l.location_code,
        l.zone,
        l.aisle,
        l.slot,
        l.x,
        l.y,
        l.z,
        l.floor_type,
        l.dist_from_origin,
        l.xyz_code AS location_priority_xyz,
        l.abc_zone AS location_zone_abc,
        l.rack_capacity,
        COALESCE(used.occupied_slots, 0) AS occupied_slots,
        (l.rack_capacity - COALESCE(used.occupied_slots, 0)) AS empty_slots
    FROM dim_location l
    LEFT JOIN (
        SELECT location_key, COUNT(storage_id) AS occupied_slots
        FROM fact_storage s
        JOIN dim_strategy st ON s.strategy_key = st.strategy_key
        WHERE st.strategy_code = '{strategy_code}'
        GROUP BY location_key
    ) used ON l.location_key = used.location_key
    WHERE l.dist_from_origin > 0
    ORDER BY l.dist_from_origin ASC;
    """
    df_locs = pd.read_sql(q_capacity, engine)
    
    # 4. Lấy danh sách chi tiết các slot đang bị chiếm dụng trên từng kệ
    q_used_slots = f"""
    SELECT s.location_key, s.slot_number
    FROM fact_storage s
    JOIN dim_strategy st ON s.strategy_key = st.strategy_key
    WHERE st.strategy_code = '{strategy_code}';
    """
    df_used_slots = pd.read_sql(q_used_slots, engine)
    occupied_map = df_used_slots.groupby('location_key')['slot_number'].apply(set).to_dict()
    
    # 5. Đánh giá điểm phù hợp (Suitability Score) cho từng vị trí dựa trên ABC và XYZ
    scores = []
    reasons = []
    
    for _, loc in df_locs.iterrows():
        score = 100.0  # Base score
        reason_parts = []
        loc_xyz = loc['location_priority_xyz']
        loc_abc = loc['location_zone_abc']
        floor = loc['floor_type']
        z = loc['z']
        dist = loc['dist_from_origin']
        loc_code = loc['location_code']
        aisle = loc['aisle']
        empty_count = loc['empty_slots']
        
        # Tiêu chí phân bổ theo ABC của mặt hàng:
        if abc_code == 'A':
            # Hàng Hot: Phải ưu tiên sàn thấp (Z <= 2)
            if floor == 'Manual Picking':
                score += 150.0
                reason_parts.append("Tầng thấp thuận tiện nhặt tay (Z<=2)")
            else:
                score -= 100.0  # Phạt nặng nếu để hàng A ở tầng cao
                
            # Ưu tiên vị trí X (đầu dãy, dễ thấy, lối đi thông thoáng)
            if loc_xyz == 'X':
                score += 120.0
                reason_parts.append("Vị trí vàng X đầu dãy cho nhân viên")
            elif loc_xyz == 'Y':
                score += 60.0
                reason_parts.append("Vị trí phụ cận Y")
            else:
                score -= 40.0
                
            # Ưu tiên cự ly gần cửa xuất kho
            score += max(0.0, (1200.0 - dist) / 8.0)
            
        elif abc_code == 'B':
            # Hàng Trung bình
            if floor == 'Manual Picking':
                score += 80.0
                reason_parts.append("Tầng nhặt tay chuẩn")
            if loc_xyz in ('X', 'Y'):
                score += 70.0
                reason_parts.append(f"Vị trí tiêu chuẩn {loc_xyz}")
            score += max(0.0, (1000.0 - dist) / 12.0)
            
        else: # abc_code == 'C'
            # Hàng Bán chậm: Đưa lên tầng cao hoặc vị trí sâu để giải phóng mặt bằng cho hàng A/B
            if floor == 'Replenishment':
                score += 130.0
                reason_parts.append("Tầng cao dự trữ (Z>2) bảo toàn mặt tiền")
            if loc_xyz in ('Z', 'C'):
                score += 90.0
                reason_parts.append(f"Vị trí sâu {loc_xyz}")
            if dist > 600:
                score += 50.0
                
        # Tiêu chí gom cụm (Clustering Bonus):
        if loc_code in existing_locs:
            score += 80.0
            reason_parts.append("Gom cụm cùng kệ hàng sẵn có")
        elif aisle in existing_aisles:
            score += 40.0
            reason_parts.append("Cùng dãy hành lang với SKU")
            
        # Ưu tiên các kệ còn ô hoàn toàn trống
        if empty_count > 0:
            score += 50.0
            reason_parts.append(f"Còn {empty_count} ô trống hoàn toàn")
            
        scores.append(score)
        reasons.append("; ".join(reason_parts) if reason_parts else "Phù hợp tiêu chuẩn kho")
        
    df_locs['suitability_score'] = scores
    df_locs['recommendation_reason'] = reasons
    
    # 6. Chọn các vị trí có điểm cao nhất để phân bổ đủ số lượng hàng
    df_ranked = df_locs.sort_values(by='suitability_score', ascending=False)
    
    allocations = []
    qty_needed = quantity_units
    
    for _, loc in df_ranked.iterrows():
        if qty_needed <= 0:
            break
            
        loc_key = loc['location_key']
        used_set = occupied_map.get(loc_key, set())
        all_possible_slots = set(range(1, 19)) # slot 1 to 18
        available_slots = sorted(list(all_possible_slots - used_set))
        
        # Nếu kệ đã được dùng, chọn các slot tiếp theo hoặc slot nhặt bổ sung
        if not available_slots:
            available_slots = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]
            
        for s_num in available_slots:
            if qty_needed <= 0:
                break
            allocations.append({
                'sku_reference': sku_reference,
                'sku_priority_abc': abc_code,
                'size_us': size_us,
                'location_code': loc['location_code'],
                'zone': loc['zone'],
                'aisle': loc['aisle'],
                'shelf_level_z': int(loc['z']),
                'slot_number': s_num,
                'floor_type': loc['floor_type'],
                'location_priority_xyz': loc['location_priority_xyz'],
                'dist_from_origin': loc['dist_from_origin'],
                'x': loc['x'],
                'y': loc['y'],
                'suitability_score': round(loc['suitability_score'], 1),
                'reason': loc['recommendation_reason']
            })
            qty_needed -= 1
            
    df_alloc = pd.DataFrame(allocations)
    
    summary = {
        'sku_reference': sku_reference,
        'sku_priority_abc': abc_code,
        'size_us': size_us,
        'total_quantity_inbound': quantity_units,
        'allocated_quantity': len(df_alloc),
        'unique_locations_allocated': df_alloc['location_code'].nunique(),
        'avg_distance_from_gate_m': round(df_alloc['dist_from_origin'].mean(), 1),
        'manual_picking_ratio': f"{(df_alloc['floor_type'] == 'Manual Picking').mean()*100:.1f}%",
        'prime_location_x_ratio': f"{(df_alloc['location_priority_xyz'] == 'X').mean()*100:.1f}%"
    }
    
    return df_alloc, summary

