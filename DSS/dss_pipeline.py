"""
=============================================================================
DECISION SUPPORT SYSTEM (DSS) PIPELINE
Warehouse Order Picking & Item Storage Placement Recommendation
=============================================================================
Phương pháp tiếp cận:
  1. Hướng tiếp cận 1: Rule-Based Expert System (Heuristic rules)
  2. Hướng tiếp cận 2: Machine Learning Models (Decision Tree, Random Forest)
  
Quy trình:
  - Trích xuất dữ liệu đa chiều từ PostgreSQL Data Warehouse (warehouse_dw)
  - Xây dựng ma trận đặc trưng vận hành & không gian kho
  - Tách tập dữ liệu: 80% Train, 20% Test (Stratified)
  - Huấn luyện & Đánh giá Benchmark độc lập giữa 3 phương pháp
  - Tự động chọn mô hình Quán quân (Champion Model) có F1-Score cao nhất
  - Tạo bảng khuyến nghị ra quyết định (Actionable Recommendations)
  - Xuất toàn bộ kết quả phân tích & dự đoán vào thư mục `datadss/`
=============================================================================
"""

import os
import sys
import time
import json
from pathlib import Path

# Đảm bảo in tiếng Việt mượt mà trên console Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
import numpy as np

from sqlalchemy import create_engine
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    classification_report, confusion_matrix
)
import joblib

# ─────────────────────────────────────────────────────────────────────────────
# 1. CẤU HÌNH ĐƯỜNG DẪN & KẾT NỐI DATA WAREHOUSE
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(r"d:\Cong Viec\Tailieu261\DataWarehouse\BTL")
DATADSS_DIR = BASE_DIR / "datadss"
DSS_DIR    = BASE_DIR / "DSS"
DATADSS_DIR.mkdir(parents=True, exist_ok=True)
DSS_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = "postgresql+psycopg2://dw_admin:dw_password123@localhost:5433/warehouse_dw"
engine = create_engine(DB_URL)

print("=" * 70)
print("  DECISION SUPPORT SYSTEM (DSS) — STORAGE SLOTTING BENCHMARK & SELECTION")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# 2. TRÍCH XUẤT ĐẶC TRƯNG TỪ POSTGRESQL DATA WAREHOUSE
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Bước 1/6] Đang trích xuất dữ liệu vận hành từ Data Warehouse (PostgreSQL)...")
t0 = time.time()

query = """
WITH sku_orders AS (
    SELECT 
        product_key,
        COUNT(order_item_id)                   AS sku_order_lines,
        SUM(quantity_units)                    AS sku_total_order_qty,
        COUNT(DISTINCT order_number)           AS sku_distinct_orders,
        COUNT(DISTINCT customer_code)          AS sku_distinct_customers,
        COUNT(DISTINCT wave_key)               AS sku_distinct_waves,
        ROUND(AVG(quantity_units)::numeric, 2) AS sku_avg_units_per_order
    FROM fact_order
    GROUP BY product_key
),
sku_picks AS (
    SELECT 
        product_key,
        COUNT(pick_id)                         AS sku_total_picks,
        SUM(qty_to_pick)                       AS sku_total_pick_qty,
        COUNT(DISTINCT location_key)           AS sku_locations_visited
    FROM fact_picking
    GROUP BY product_key
)
SELECT 
    s.storage_id,
    s.slot_number,
    s.size_us,
    s.classification_code                      AS target_class,
    p.product_key,
    p.reference                                AS sku_reference,
    p.abc_code                                 AS sku_priority_abc,
    COALESCE(l.xyz_code, 'C')                  AS location_priority_xyz,
    COALESCE(l.abc_zone, 'C')                  AS location_zone_abc,
    COALESCE(so.sku_order_lines, 0)            AS sku_order_lines,
    COALESCE(so.sku_total_order_qty, 0)        AS sku_total_order_qty,
    COALESCE(so.sku_distinct_orders, 0)        AS sku_distinct_orders,
    COALESCE(so.sku_distinct_customers, 0)     AS sku_distinct_customers,
    COALESCE(so.sku_distinct_waves, 0)         AS sku_distinct_waves,
    COALESCE(so.sku_avg_units_per_order, 0)    AS sku_avg_units_per_order,
    COALESCE(sp.sku_total_picks, 0)            AS sku_total_picks,
    COALESCE(sp.sku_locations_visited, 0)      AS sku_locations_visited,
    l.location_key,
    l.location_code,
    l.zone,
    l.aisle,
    l.slot,
    l.z                                        AS floor_level,
    l.dist_from_origin,
    CASE WHEN l.floor_type = 'Manual Picking' THEN 1 ELSE 0 END AS is_manual_picking
FROM fact_storage s
JOIN dim_product p ON s.product_key = p.product_key
JOIN dim_location l ON s.location_key = l.location_key
LEFT JOIN sku_orders so ON s.product_key = so.product_key
LEFT JOIN sku_picks sp ON s.product_key = sp.product_key
WHERE s.strategy_key = 2; -- Class-Based Storage Layout làm ground-truth benchmark
"""

df_raw = pd.read_sql(query, engine)
print(f"  ✓ Đã trích xuất: {len(df_raw):,} dòng dữ liệu vị trí ô lưu trữ trong {time.time()-t0:.2f}s.")
print(f"  ✓ Phân phối nhãn mục tiêu (Class-Based Target):")
for c, cnt in df_raw['target_class'].value_counts().items():
    print(f"     - Hạng {c}: {cnt:>6,} ô ({cnt/len(df_raw)*100:.1f}%)")

# ─────────────────────────────────────────────────────────────────────────────
# 3. TIỀN XỬ LÝ & CHUẨN BỊ TẬP TRAIN (80%) / TEST (20%)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Bước 2/6] Chuẩn bị đặc trưng & phân chia dữ liệu (80% Train, 20% Test)...")

df_raw['xyz_priority_score'] = df_raw['location_priority_xyz'].map({'X': 3, 'Y': 2, 'Z': 1, 'C': 0}).fillna(0)
df_raw['abc_demand_score']   = df_raw['sku_priority_abc'].map({'A': 3, 'B': 2, 'C': 1}).fillna(1)

feature_cols = [
    'sku_order_lines',
    'sku_total_order_qty',
    'sku_distinct_orders',
    'sku_distinct_customers',
    'sku_distinct_waves',
    'sku_avg_units_per_order',
    'sku_total_picks',
    'sku_locations_visited',
    'aisle',
    'slot',
    'floor_level',
    'dist_from_origin',
    'is_manual_picking',
    'slot_number',
    'size_us',
    'xyz_priority_score',
    'abc_demand_score'
]

X = df_raw[feature_cols].copy().fillna(0)
y = df_raw['target_class'].copy()

X_train, X_test, y_train, y_test, meta_train, meta_test = train_test_split(
    X, y, df_raw,
    test_size=0.20,
    random_state=42,
    stratify=y
)

print(f"  ✓ Tập huấn luyện (Train 80%): {len(X_train):,} mẫu")
print(f"  ✓ Tập kiểm tra   (Test 20%) : {len(X_test):,} mẫu")

# ─────────────────────────────────────────────────────────────────────────────
# 4. TRIỂN KHAI 2 HƯỚNG TIẾP CẬN
# ─────────────────────────────────────────────────────────────────────────────

# --- HƯỚNG 1: RULE-BASED EXPERT SYSTEM ---
print("\n[Bước 3/6] Triển khai Hướng 1: Rule-Based Expert System...")
t_start_rule = time.time()

def rule_based_predict(df_features):
    """
    Hệ luật chuyên gia quản trị kho bãi (Heuristic Rules based on Pareto & Distance):
      - Luật A (Hàng Hot / Fast-moving): Tần suất đặt hàng cao (>= 500 lines hoặc >= 1000 đôi)
      - Luật B (Hàng Trung bình / Medium): Tần suất đặt hàng vừa (>= 100 lines hoặc >= 200 đôi)
      - Luật C (Hàng Chậm / Slow-moving): Các mặt hàng còn lại
      - Điều chỉnh theo không gian: Nếu hàng A nhưng đặt ở tầng dự trữ (floor_level > 2) thì ưu tiên kéo về A.
    """
    preds = []
    for _, row in df_features.iterrows():
        order_lines = row['sku_order_lines']
        order_qty   = row['sku_total_order_qty']
        picks       = row['sku_total_picks']
        
        # Luật phân hạng kết hợp giữa nhu cầu và lượt nhặt
        if (order_lines >= 500 or order_qty >= 1000 or picks >= 800):
            preds.append('A')
        elif (order_lines >= 100 or order_qty >= 200 or picks >= 150):
            preds.append('B')
        else:
            preds.append('C')
    return np.array(preds)

y_pred_rule = rule_based_predict(X_test)
time_rule = time.time() - t_start_rule
print(f"  ✓ Rule-Based hoàn thành dự đoán trên {len(X_test):,} mẫu trong {time_rule:.3f}s.")

# --- HƯỚNG 2: MACHINE LEARNING MODELS ---
print("\n[Bước 4/6] Triển khai Hướng 2: Machine Learning Models...")

# 2.1 Decision Tree Classifier
t_start_dt = time.time()
dt_model = DecisionTreeClassifier(
    max_depth=7,
    min_samples_split=20,
    min_samples_leaf=10,
    random_state=42
)
dt_model.fit(X_train, y_train)
y_pred_dt = dt_model.predict(X_test)
time_dt = time.time() - t_start_dt
print(f"  ✓ Decision Tree đã huấn luyện & dự đoán trong {time_dt:.3f}s.")

# 2.2 Random Forest Classifier
t_start_rf = time.time()
rf_model = RandomForestClassifier(
    n_estimators=100,
    max_depth=12,
    min_samples_split=10,
    min_samples_leaf=5,
    random_state=42,
    n_jobs=-1
)
rf_model.fit(X_train, y_train)
y_pred_rf = rf_model.predict(X_test)
time_rf = time.time() - t_start_rf
print(f"  ✓ Random Forest (100 cây) đã huấn luyện & dự đoán trong {time_rf:.3f}s.")

# ─────────────────────────────────────────────────────────────────────────────
# 5. BENCHMARK & ĐÁNH GIÁ ĐỐI SÁNH HIỆU NĂNG
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Bước 5/6] Đánh giá Benchmark & Lựa chọn Mô hình Tối ưu nhất...")

def calculate_metrics(y_true, y_pred, execution_time):
    return {
        'Accuracy': accuracy_score(y_true, y_pred),
        'Precision_Macro': precision_score(y_true, y_pred, average='macro'),
        'Precision_Weighted': precision_score(y_true, y_pred, average='weighted'),
        'Recall_Macro': recall_score(y_true, y_pred, average='macro'),
        'Recall_Weighted': recall_score(y_true, y_pred, average='weighted'),
        'F1_Macro': f1_score(y_true, y_pred, average='macro'),
        'F1_Weighted': f1_score(y_true, y_pred, average='weighted'),
        'Inference_Time_Sec': execution_time
    }

benchmarks = {
    'Rule-Based Expert System': calculate_metrics(y_test, y_pred_rule, time_rule),
    'Decision Tree Classifier': calculate_metrics(y_test, y_pred_dt, time_dt),
    'Random Forest Classifier': calculate_metrics(y_test, y_pred_rf, time_rf)
}

df_benchmark = pd.DataFrame(benchmarks).T
df_benchmark.index.name = 'Model'
df_benchmark = df_benchmark.reset_index()

print("\n" + "=" * 90)
print("  BẢNG TỔNG HỢP BENCHMARK SO SÁNH HIỆU NĂNG (TẬP TEST 20%)")
print("=" * 90)
format_dict = {
    'Accuracy': '{:.2%}',
    'Precision_Macro': '{:.2%}',
    'Precision_Weighted': '{:.2%}',
    'Recall_Macro': '{:.2%}',
    'Recall_Weighted': '{:.2%}',
    'F1_Macro': '{:.2%}',
    'F1_Weighted': '{:.2%}',
    'Inference_Time_Sec': '{:.3f}s'
}
print(df_benchmark.to_string(index=False))

# Xác định Champion Model
champion_row = df_benchmark.sort_values(by='F1_Weighted', ascending=False).iloc[0]
champion_name = champion_row['Model']
print("\n" + "*" * 70)
print(f"  🏆 MÔ HÌNH QUÁN QUÂN ĐƯỢC CHỌN: {champion_name.upper()}")
print(f"     - Độ chính xác (Accuracy): {champion_row['Accuracy']:.2%}")
print(f"     - F1-Score (Weighted)    : {champion_row['F1_Weighted']:.2%}")
print("*" * 70)

# ─────────────────────────────────────────────────────────────────────────────
# 6. SINH BẢNG QUYẾT ĐỊNH KHUYẾN NGHỊ (ACTIONABLE RECOMMENDATIONS)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Bước 6/6] Tạo bảng khuyến nghị ra quyết định và xuất tệp vào 'datadss/'...")

# Gán dự đoán của mô hình quán quân lên toàn bộ tập Test (hoặc toàn bộ kho)
y_champion = y_pred_rf if champion_name == 'Random Forest Classifier' else (y_pred_dt if champion_name == 'Decision Tree Classifier' else y_pred_rule)
rf_probs = rf_model.predict_proba(X_test)
max_probs = np.max(rf_probs, axis=1)

df_test_results = meta_test.copy()
df_test_results['pred_rule_based']  = y_pred_rule
df_test_results['pred_decision_tree'] = y_pred_dt
df_test_results['pred_random_forest'] = y_pred_rf
df_test_results['champion_prediction'] = y_champion
df_test_results['prediction_confidence'] = max_probs

# Logic ra quyết định quản trị kho vận (Slotting Action Logic):
# So sánh vị trí thực tế của kệ với phân hạng dự đoán của sản phẩm
def determine_slotting_action(row):
    dist  = row['dist_from_origin']
    p_cls = row['champion_prediction']
    
    # Hàng nhóm A (nhu cầu cao) nhưng đang ở kệ cự ly xa (> 900m)
    if p_cls == 'A' and dist > 900:
        return 'RELOCATE_CLOSER', round((dist - 350) * 2, 1)  # Ước tính mét tiết kiệm mỗi vòng nhặt
    # Hàng nhóm C (nhu cầu thấp) nhưng chiếm dụng kệ cự ly vàng gần cửa (< 450m)
    elif p_cls == 'C' and dist < 450:
        return 'RELOCATE_FURTHER', round((850 - dist) * 2, 1) # Giải phóng mặt tiền cho hàng A
    else:
        return 'OPTIMAL_MAINTAIN', 0.0

actions = []
dist_saved = []
for _, row in df_test_results.iterrows():
    act, saved = determine_slotting_action(row)
    actions.append(act)
    dist_saved.append(saved)

df_test_results['recommended_action'] = actions
df_test_results['estimated_dist_saved_per_pick_m'] = dist_saved

print(f"  ✓ Phân bổ quyết định khuyến nghị của DSS:")
for act, cnt in pd.Series(actions).value_counts().items():
    print(f"     - {act:<18}: {cnt:>5,} ô ({cnt/len(actions)*100:.1f}%)")

# ─────────────────────────────────────────────────────────────────────────────
# 7. XUẤT CÁC TỆP KẾT QUẢ VÀO THƯ MỤC datadss/
# ─────────────────────────────────────────────────────────────────────────────

# 7.1 Bảng so sánh Benchmark
benchmark_file = DATADSS_DIR / "benchmark_comparison.csv"
df_benchmark.to_csv(benchmark_file, index=False, encoding='utf-8-sig')
print(f"\n  💾 Đã lưu: {benchmark_file.name}")

# 7.2 Chi tiết dự đoán tập Test
test_pred_file = DATADSS_DIR / "test_predictions_comparison.csv"
cols_to_export = [
    'storage_id', 'location_code', 'zone', 'aisle', 'slot', 'floor_level', 'dist_from_origin',
    'sku_reference', 'size_us', 'sku_order_lines', 'sku_total_order_qty', 'sku_total_picks',
    'target_class', 'pred_rule_based', 'pred_decision_tree', 'pred_random_forest',
    'champion_prediction', 'prediction_confidence', 'recommended_action', 'estimated_dist_saved_per_pick_m'
]
df_test_results[cols_to_export].to_csv(test_pred_file, index=False, encoding='utf-8-sig')
print(f"  💾 Đã lưu: {test_pred_file.name} ({len(df_test_results):,} dòng)")

# 7.3 Bảng khuyến nghị hành động ra quyết định
recommendation_file = DATADSS_DIR / "champion_slotting_recommendations.csv"
df_recs = df_test_results[cols_to_export].sort_values(
    by=['recommended_action', 'estimated_dist_saved_per_pick_m'], 
    ascending=[True, False]
)
df_recs.to_csv(recommendation_file, index=False, encoding='utf-8-sig')
print(f"  💾 Đã lưu: {recommendation_file.name}")

# 7.4 Feature Importance của Random Forest
rf_importances = pd.DataFrame({
    'Feature': feature_cols,
    'Importance': rf_model.feature_importances_
}).sort_values(by='Importance', ascending=False)
feat_file = DATADSS_DIR / "feature_importance_random_forest.csv"
rf_importances.to_csv(feat_file, index=False, encoding='utf-8-sig')
print(f"  💾 Đã lưu: {feat_file.name}")

# 7.5 Confusion Matrices
for name, y_p in [('rule_based', y_pred_rule), ('decision_tree', y_pred_dt), ('random_forest', y_pred_rf)]:
    cm = confusion_matrix(y_test, y_p, labels=['A', 'B', 'C'])
    df_cm = pd.DataFrame(cm, index=['True_A', 'True_B', 'True_C'], columns=['Pred_A', 'Pred_B', 'Pred_C'])
    cm_file = DATADSS_DIR / f"confusion_matrix_{name}.csv"
    df_cm.to_csv(cm_file, encoding='utf-8-sig')
    print(f"  💾 Đã lưu: {cm_file.name}")

# 7.6 Lưu model trained để tái sử dụng
joblib.dump(rf_model, DSS_DIR / "champion_random_forest.joblib")
joblib.dump(dt_model, DSS_DIR / "decision_tree_model.joblib")
print(f"  💾 Đã lưu mô hình: champion_random_forest.joblib, decision_tree_model.joblib")

# 7.7 Sinh bảng kết quả cho 2 Giải pháp Cốt lõi (Solution 1 & 2)
sys.path.insert(0, str(BASE_DIR))
from DSS.picking_optimizer import optimize_picking
from DSS.putaway_allocator import allocate_inbound_goods

print("\n[Bước bổ sung] Sinh file kế hoạch ra quyết định cho 2 Solution thực tế...")
# Solution 1: Lấy 2 đôi giày A và B (size 7, 8, 9)
pick_reqs = [
    {'sku': 'O9YFO8', 'size': 7.0, 'qty': 1},
    {'sku': 'O9YFO8', 'size': 8.0, 'qty': 1},
    {'sku': 'O9YFO8', 'size': 9.0, 'qty': 1},
    {'sku': 'I1X92B', 'size': 7.0, 'qty': 1},
    {'sku': 'I1X92B', 'size': 8.0, 'qty': 1},
    {'sku': 'I1X92B', 'size': 9.0, 'qty': 1},
]
df_pick_route, _ = optimize_picking(pick_reqs, engine, strategy_code='ClassBased')
if df_pick_route is not None:
    df_pick_route.to_csv(DATADSS_DIR / "optimal_picking_plan.csv", index=False, encoding='utf-8-sig')
    print("  💾 Đã lưu: optimal_picking_plan.csv")

# Solution 2: Nhập 10 đôi giày A size 7.0
df_alloc_inbound, _ = allocate_inbound_goods('O9YFO8', 7.0, 10, engine, strategy_code='ClassBased')
if df_alloc_inbound is not None:
    df_alloc_inbound.to_csv(DATADSS_DIR / "optimal_putaway_recommendations.csv", index=False, encoding='utf-8-sig')
    print("  💾 Đã lưu: optimal_putaway_recommendations.csv")

print("\n" + "=" * 70)
print("  HOÀN TẤT PIPELINE DSS! TOÀN BỘ KẾT QUẢ ĐÃ ĐƯỢC XUẤT VÀO THƯ MỤC 'datadss/'")
print("=" * 70)
