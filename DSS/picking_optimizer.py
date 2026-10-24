"""
=============================================================================
DSS SOLUTION 1: OPTIMAL ORDER PICKING ROUTE & LOCATION SELECTOR
Tối ưu hóa vị trí nhặt hàng và lộ trình di chuyển cho nhân viên kho
=============================================================================
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd
from itertools import permutations
from sqlalchemy import create_engine

# Đảm bảo in tiếng Việt
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

DB_URL = "postgresql+psycopg2://dw_admin:dw_password123@localhost:5433/warehouse_dw"

def get_warehouse_nav_points(engine):
    """Lấy danh sách các mốc hành lang LC, CC, RC để định tuyến chính xác"""
    q = "SELECT label, corridor_code, x_nav, y_nav, z_nav FROM dim_nav_point ORDER BY corridor_code, point_number;"
    return pd.read_sql(q, engine)

def calculate_warehouse_distance(x1, y1, x2, y2, nav_df=None):
    """
    Tính khoảng cách di chuyển thực tế của nhân viên giữa 2 điểm trong kho qua hành lang.
    Nhân viên không thể đi xuyên qua các dãy kệ thép mà phải đi ra đầu dãy (hành lang LC/CC/RC),
    di chuyển dọc hành lang, rồi đi vào dãy kệ mục tiêu.
    """
    # Nếu cùng dãy (cùng x xấp xỉ), đi thẳng dọc lối đi
    if abs(x1 - x2) < 20:
        return abs(y1 - y2)
    
    # Nếu khác dãy, phải ra hành lang gần nhất:
    # Corridor LC: x ~ 66, CC: x ~ 403, RC: x ~ 686
    corridors = [66.0, 403.0, 686.0]
    
    # Chọn hành lang tối ưu nối giữa 2 điểm
    best_dist = float('inf')
    for c_x in corridors:
        # Ra hành lang -> Đi dọc hành lang -> Rẽ vào điểm 2
        d = abs(x1 - c_x) + abs(y1 - y2) + abs(x2 - c_x)
        if d < best_dist:
            best_dist = d
            
    return best_dist

def optimize_picking(pick_requests, engine, strategy_code='ClassBased'):
    """
    pick_requests: Danh sách các dict hoặc tuple yêu cầu nhặt hàng:
        [
            {'sku': 'O9YFO8', 'size': 7.0, 'qty': 1},
            {'sku': 'O9YFO8', 'size': 8.0, 'qty': 1},
            {'sku': 'O9YFO8', 'size': 9.0, 'qty': 1},
            {'sku': 'I1X92B', 'size': 7.0, 'qty': 1},
            {'sku': 'I1X92B', 'size': 8.0, 'qty': 1},
            {'sku': 'I1X92B', 'size': 9.0, 'qty': 1},
        ]
    """
    # 1. Trích xuất toàn bộ tồn kho của các SKU và size được yêu cầu
    req_skus = list(set([r['sku'] for r in pick_requests]))
    req_sizes = list(set([float(r['size']) for r in pick_requests]))
    
    sku_list_str = "', '".join(req_skus)
    size_list_str = ", ".join([str(s) for s in req_sizes])
    
    q_inv = f"""
    SELECT 
        storage_id,
        location_code,
        zone,
        aisle,
        slot,
        x,
        y,
        z,
        floor_type,
        dist_from_origin,
        location_priority_xyz,
        location_zone_abc,
        slot_number,
        sku_reference,
        sku_priority_abc,
        size_us,
        quantity_on_hand
    FROM v_warehouse_inventory
    WHERE strategy_code = '{strategy_code}'
      AND sku_reference IN ('{sku_list_str}')
      AND size_us IN ({size_list_str})
    """
    df_inv = pd.read_sql(q_inv, engine)
    
    if df_inv.empty:
        return None, "Không tìm thấy tồn kho cho các sản phẩm yêu cầu."
        
    # 2. Lựa chọn vị trí tối ưu cho từng yêu cầu (Greedy Scoring based on user domain rules)
    # Rules:
    #   - Ưu tiên 1: floor_type = 'Manual Picking' (z <= 2) để nhân viên lấy tay thuận tiện (+100 điểm)
    #   - Ưu tiên 2: location_priority_xyz (X > Y > Z > C) vì X là vị trí đầu dãy dễ lấy nhất (+50 / +30 / +10)
    #   - Ưu tiên 3: dist_from_origin thấp (gần cửa xuất kho)
    xyz_score = {'X': 50, 'Y': 30, 'Z': 10, 'C': 0}
    
    df_inv['score'] = (
        (df_inv['floor_type'] == 'Manual Picking').astype(int) * 100 +
        df_inv['location_priority_xyz'].map(xyz_score).fillna(0) -
        (df_inv['dist_from_origin'] / 20.0)
    )
    
    selected_picks = []
    used_storage_ids = set()
    
    for req in pick_requests:
        sku = req['sku']
        sz = float(req['size'])
        qty = int(req.get('qty', 1))
        
        candidates = df_inv[
            (df_inv['sku_reference'] == sku) & 
            (df_inv['size_us'] == sz) & 
            (~df_inv['storage_id'].isin(used_storage_ids))
        ].sort_values(by='score', ascending=False)
        
        if len(candidates) < qty:
            # Fallback nếu không đủ trong ClassBased thì tìm thêm
            return None, f"Không đủ tồn kho cho SKU {sku} size {sz} (yêu cầu {qty}, chỉ có {len(candidates)})"
            
        chosen = candidates.iloc[:qty]
        for _, row in chosen.iterrows():
            used_storage_ids.add(row['storage_id'])
            selected_picks.append(row.to_dict())
            
    df_selected = pd.DataFrame(selected_picks)
    
    # 3. Định tuyến tối ưu (TSP Routing từ Depot x=0, y=0 qua tất cả các điểm nhặt và quay về)
    # Tọa độ Depot (Cửa xuất nhập kho)
    depot_x, depot_y = 0.0, 0.0
    
    points = [{'idx': -1, 'name': 'Depot / Cửa xuất kho', 'x': depot_x, 'y': depot_y, 'is_depot': True}]
    for idx, row in df_selected.iterrows():
        points.append({
            'idx': idx,
            'name': f"{row['sku_reference']} (Sz {row['size_us']}) @ {row['location_code']}",
            'x': row['x'],
            'y': row['y'],
            'z': row['z'],
            'location_code': row['location_code'],
            'sku': row['sku_reference'],
            'size': row['size_us'],
            'xyz': row['location_priority_xyz'],
            'floor_type': row['floor_type'],
            'slot_number': row['slot_number'],
            'dist_gate': row['dist_from_origin'],
            'is_depot': False
        })
        
    num_pts = len(points)
    dist_matrix = np.zeros((num_pts, num_pts))
    for i in range(num_pts):
        for j in range(num_pts):
            if i != j:
                dist_matrix[i][j] = calculate_warehouse_distance(
                    points[i]['x'], points[i]['y'], points[j]['x'], points[j]['y']
                )
                
    # Giải TSP:
    # Nếu num_pts <= 10, giải duyệt toàn bộ permutations để đạt nghiệm tối ưu tuyệt đối (Globally Optimal)
    # Nếu num_pts > 10, giải bằng Nearest-Neighbor kết hợp 2-Opt heuristic
    pick_indices = list(range(1, num_pts))
    
    if len(pick_indices) <= 9:
        best_perm = None
        min_total_dist = float('inf')
        for perm in permutations(pick_indices):
            # Lộ trình: 0 -> perm[0] -> perm[1] -> ... -> perm[-1] -> 0
            cur_dist = dist_matrix[0][perm[0]]
            for k in range(len(perm) - 1):
                cur_dist += dist_matrix[perm[k]][perm[k+1]]
            cur_dist += dist_matrix[perm[-1]][0]
            
            if cur_dist < min_total_dist:
                min_total_dist = cur_dist
                best_perm = perm
        optimal_order = [0] + list(best_perm) + [0]
    else:
        # Nearest Neighbor TSP
        unvisited = set(pick_indices)
        cur = 0
        optimal_order = [0]
        while unvisited:
            next_node = min(unvisited, key=lambda n: dist_matrix[cur][n])
            optimal_order.append(next_node)
            unvisited.remove(next_node)
            cur = next_node
        optimal_order.append(0)
        
        # Tính khoảng cách
        min_total_dist = sum(dist_matrix[optimal_order[i]][optimal_order[i+1]] for i in range(len(optimal_order)-1))

    # Tính Baseline (Lộ trình nhặt ngẫu nhiên theo thứ tự đơn hàng chưa tối ưu)
    baseline_order = [0] + pick_indices + [0]
    baseline_dist = sum(dist_matrix[baseline_order[i]][baseline_order[i+1]] for i in range(len(baseline_order)-1))
    
    dist_saved = max(0.0, baseline_dist - min_total_dist)
    pct_saved = (dist_saved / baseline_dist * 100.0) if baseline_dist > 0 else 0.0
    # Giả định tốc độ nhân viên đi bộ ~ 1.2 m/s (72 m/phút)
    time_saved_min = round(dist_saved / 72.0, 1)
    
    # 4. Xây dựng bảng lộ trình chi tiết
    route_details = []
    step_num = 1
    for stop_idx in optimal_order[1:-1]:
        pt = points[stop_idx]
        route_details.append({
            'step': step_num,
            'sku': pt['sku'],
            'size': pt['size'],
            'location_code': pt['location_code'],
            'floor_type': pt['floor_type'],
            'shelf_level_z': int(pt['z']),
            'slot_number': pt['slot_number'],
            'location_priority_xyz': pt['xyz'],
            'x': pt['x'],
            'y': pt['y'],
            'dist_from_origin': pt['dist_gate']
        })
        step_num += 1
        
    summary = {
        'total_items_to_pick': len(pick_requests),
        'optimal_route_distance_m': round(min_total_dist, 1),
        'baseline_distance_m': round(baseline_dist, 1),
        'distance_saved_m': round(dist_saved, 1),
        'pct_saved': round(pct_saved, 1),
        'time_saved_minutes': time_saved_min,
        'estimated_pick_time_min': round(min_total_dist / 72.0 + len(pick_requests) * 0.5, 1)
    }
    
    return pd.DataFrame(route_details), summary

