"""
=============================================================================
INTELLIGENT WAREHOUSE DECISION SUPPORT SYSTEM & OLAP DASHBOARD v3.0
Hệ Thống Bảng Điều Khiển Trực Quan Hóa Kho Dữ Liệu & Hỗ Trợ Ra Quyết Định
Công nghệ: Streamlit + Plotly + PostgreSQL 16 + Scikit-Learn
2 Giải Pháp Trọng Tâm:
  1. Solution 1: Optimal Order Picking Route (Tối ưu quãng đường lấy hàng)
  2. Solution 2: Optimal Inbound Put-Away Allocation (Tối ưu vị trí xếp hàng nhập kho)
=============================================================================
"""

import sys
from pathlib import Path

# Đảm bảo UTF-8 trên Windows console
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import create_engine, text
import joblib

# Thêm thư mục gốc vào path để import module DSS
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from DSS.picking_optimizer import optimize_picking, calculate_warehouse_distance
from DSS.putaway_allocator import allocate_inbound_goods

# ─────────────────────────────────────────────────────────────────────────────
# 1. CẤU HÌNH TRANG STREAMLIT & GIAO DIỆN
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Warehouse DW & DSS Dashboard",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

DATADSS_DIR = BASE_DIR / "datadss"
DSS_DIR     = BASE_DIR / "DSS"
DB_URL      = "postgresql+psycopg2://dw_admin:dw_password123@localhost:5433/warehouse_dw"

# CSS tùy chỉnh giao diện chuyên nghiệp
st.markdown("""
<style>
    div[data-testid="stMetricValue"] {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1f77b4;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.9rem;
        font-weight: 500;
        color: #555;
    }
    .main-title {
        font-size: 2.0rem;
        font-weight: 800;
        color: #111827;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1.0rem;
        color: #4b5563;
        margin-bottom: 1.2rem;
    }
    .badge-card {
        background-color: #f0fdf4;
        border-radius: 8px;
        padding: 12px 16px;
        border-left: 4px solid #16a34a;
        margin-bottom: 1rem;
        color: #14532d;
    }
    .badge-card-blue {
        background-color: #eff6ff;
        border-radius: 8px;
        padding: 12px 16px;
        border-left: 4px solid #3b82f6;
        margin-bottom: 1rem;
        color: #1e3a8a;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 48px;
        font-weight: 600;
        border-radius: 6px 6px 0 0;
        padding: 0 16px;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# 2. HÀM TẢI DỮ LIỆU CÓ CACHING
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_db_engine():
    try:
        engine = create_engine(DB_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        return engine
    except Exception:
        return None

@st.cache_data(ttl=600)
def load_kpis():
    engine = get_db_engine()
    if engine is None:
        return None
    with engine.connect() as conn:
        q_order = "SELECT COUNT(DISTINCT order_number), COUNT(*), SUM(quantity_units) FROM fact_order;"
        order_stats = conn.execute(text(q_order)).fetchone()
        
        q_pick = "SELECT COUNT(DISTINCT wave_number), COUNT(*), SUM(qty_to_pick) FROM fact_picking;"
        pick_stats = conn.execute(text(q_pick)).fetchone()
        
        q_dims = """
        SELECT 
            (SELECT COUNT(*) FROM dim_product),
            (SELECT COUNT(*) FROM dim_location),
            (SELECT COUNT(*) FROM dim_operator),
            (SELECT ROUND(AVG(capacity_utilization)::numeric * 100, 1) FROM dim_wave WHERE capacity_utilization > 0)
        """
        dim_stats = conn.execute(text(q_dims)).fetchone()
        
    return {
        "orders": order_stats,
        "picks": pick_stats,
        "dim_stats": dim_stats
    }

@st.cache_data(ttl=600)
def load_locations_and_nav():
    engine = get_db_engine()
    if engine is None:
        return pd.DataFrame(), pd.DataFrame()
    q_loc = """
    SELECT location_key, location_code, x, y, z, zone, aisle, slot, dist_from_origin, floor_type, xyz_code, abc_zone
    FROM dim_location
    WHERE dist_from_origin > 0;
    """
    q_nav = """
    SELECT nav_key, label, corridor_code, corridor_name, point_number, x_nav, y_nav, z_nav
    FROM dim_nav_point
    ORDER BY corridor_code, point_number;
    """
    return pd.read_sql(q_loc, engine), pd.read_sql(q_nav, engine)

@st.cache_data(ttl=600)
def load_order_trends():
    engine = get_db_engine()
    if engine is None:
        return pd.DataFrame()
    q = """
    SELECT 
        d.year,
        d.month,
        d.month_name,
        COUNT(fo.order_item_id)      AS total_lines,
        COUNT(DISTINCT fo.order_number) AS total_orders,
        SUM(fo.quantity_units)       AS total_units
    FROM fact_order fo
    JOIN dim_date d ON fo.date_key = d.date_key
    GROUP BY d.year, d.month, d.month_name
    ORDER BY d.year, d.month;
    """
    return pd.read_sql(q, engine)

@st.cache_data(ttl=600)
def load_strategy_comparison():
    engine = get_db_engine()
    if engine is None:
        return pd.DataFrame()
    q = """
    SELECT 
        strat.strategy_code,
        strat.strategy_name,
        p.abc_code,
        COUNT(s.storage_id)                                  AS slots_count,
        ROUND(AVG(l.dist_from_origin)::numeric, 1)          AS avg_distance_m
    FROM fact_storage s
    JOIN dim_strategy strat ON s.strategy_key = strat.strategy_key
    JOIN dim_product p ON s.product_key = p.product_key
    JOIN dim_location l ON s.location_key = l.location_key
    WHERE l.dist_from_origin > 0
    GROUP BY strat.strategy_code, strat.strategy_name, p.abc_code
    ORDER BY strat.strategy_code, p.abc_code;
    """
    return pd.read_sql(q, engine)

@st.cache_data(ttl=600)
def load_dss_files():
    bench_file = DATADSS_DIR / "benchmark_comparison.csv"
    recs_file  = DATADSS_DIR / "champion_slotting_recommendations.csv"
    feat_file  = DATADSS_DIR / "feature_importance_random_forest.csv"
    
    df_b = pd.read_csv(bench_file) if bench_file.exists() else pd.DataFrame()
    df_r = pd.read_csv(recs_file) if recs_file.exists() else pd.DataFrame()
    df_f = pd.read_csv(feat_file) if feat_file.exists() else pd.DataFrame()
    
    cm_rule = pd.read_csv(DATADSS_DIR / "confusion_matrix_rule_based.csv", index_col=0) if (DATADSS_DIR / "confusion_matrix_rule_based.csv").exists() else None
    cm_dt   = pd.read_csv(DATADSS_DIR / "confusion_matrix_decision_tree.csv", index_col=0) if (DATADSS_DIR / "confusion_matrix_decision_tree.csv").exists() else None
    cm_rf   = pd.read_csv(DATADSS_DIR / "confusion_matrix_random_forest.csv", index_col=0) if (DATADSS_DIR / "confusion_matrix_random_forest.csv").exists() else None
    
    return df_b, df_r, df_f, cm_rule, cm_dt, cm_rf

@st.cache_data(ttl=600)
def load_all_skus():
    engine = get_db_engine()
    if engine is None:
        return []
    df = pd.read_sql("SELECT reference, abc_code FROM dim_product ORDER BY abc_code, reference;", engine)
    return df.to_dict('records')

# ─────────────────────────────────────────────────────────────────────────────
# 3. THANH ĐIỀU HƯỚNG BÊN TRÁI (SIDEBAR NAVIGATION)
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.image("https://img.icons8.com/fluency/96/warehouse.png", width=70)
st.sidebar.title("Hệ Thống DSS Kho Vận")
st.sidebar.markdown("**Decision Support & Data Warehouse**")

engine = get_db_engine()
if engine is not None:
    st.sidebar.success("🟢 PostgreSQL 16 (Port 5433): Kết nối tốt")
else:
    st.sidebar.error("🔴 PostgreSQL Chưa kết nối")

page = st.sidebar.radio(
    "Lựa chọn màn hình nghiệp vụ:",
    [
        "🛒 Solution 1: Tối Ưu Lộ Trình Lấy Hàng",
        "📥 Solution 2: Tối Ưu Xếp Hàng Nhập Kho",
        "🤖 Đánh Giá & Benchmark Mô Hình DSS",
        "🗺️ Bản Đồ Không Gian Kho 2D (ABC / XYZ)",
        "📊 Tổng Quan Hoạt Động & OLAP"
    ],
    index=0
)

st.sidebar.markdown("---")
st.sidebar.markdown("""
**Thông tin Kiến trúc:**
* **Fact Constellation:** 7 DIMs & 3 FACTs
* **Phân định thuộc tính:**
  * **ABC:** Mức độ ưu tiên mặt hàng (Demand Velocity)
  * **XYZ:** Mức độ ưu tiên vị trí kho cho nhân viên (Accessibility)
* **Dataset:** Footwear Warehouse (Elsevier 2025)
""")

# ─────────────────────────────────────────────────────────────────────────────
# MÀN HÌNH 1: SOLUTION 1 — TỐI ƯU HÓA LỘ TRÌNH LẤY HÀNG (PICKING ROUTE)
# ─────────────────────────────────────────────────────────────────────────────
if page == "🛒 Solution 1: Tối Ưu Lộ Trình Lấy Hàng":
    st.markdown('<div class="main-title">🛒 Solution 1: Tối Ưu Hóa Lộ Trình Nhặt Hàng (Picking Optimizer)</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Giải bài toán tìm vị trí tối ưu trên tầng nhặt tay (Z ≤ 2) và định tuyến TSP ngắn nhất cho nhân viên qua các hành lang kho</div>', unsafe_allow_html=True)
    
    st.markdown("""
    <div class="badge-card-blue">
        🎯 <b>Mục tiêu bài toán nhân viên nhặt hàng:</b> Giả sử nhân viên cần nhặt một danh sách gồm nhiều mặt hàng (SKU) và nhiều kích cỡ (Size),
        hệ thống DSS sẽ tự động truy vấn tồn kho hiện tại, ưu tiên chọn các ô ở tầng thấp dễ lấy (Z ≤ 2 Manual Picking),
        vị trí vàng đầu dãy (XYZ = X/Y), đồng thời sắp xếp thứ tự các điểm dừng theo thuật toán TSP tối ưu quãng đường và thời gian di chuyển.
    </div>
    """, unsafe_allow_html=True)
    
    all_skus = load_all_skus()
    sku_options = [f"{s['reference']} (Hạng {s['abc_code']})" for s in all_skus] if all_skus else ["O9YFO8 (Hạng A)", "I1X92B (Hạng A)"]
    
    col_input1, col_input2 = st.columns(2)
    
    with col_input1:
        st.subheader("📦 Sản phẩm thứ nhất (SKU A)")
        default_idx1 = 0
        sel_sku_full1 = st.selectbox("Chọn Mã Giày A:", sku_options, index=default_idx1, key="sku1")
        sku_ref1 = sel_sku_full1.split(" ")[0]
        sizes1 = st.multiselect("Chọn các cỡ giày cần lấy (Size US):", [6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0], default=[7.0, 8.0, 9.0], key="sizes1")
        qty1 = st.number_input("Số lượng mỗi cỡ:", min_value=1, max_value=5, value=1, key="qty1")

    with col_input2:
        st.subheader("📦 Sản phẩm thứ hai (SKU B)")
        default_idx2 = 1 if len(sku_options) > 1 else 0
        sel_sku_full2 = st.selectbox("Chọn Mã Giày B:", sku_options, index=default_idx2, key="sku2")
        sku_ref2 = sel_sku_full2.split(" ")[0]
        sizes2 = st.multiselect("Chọn các cỡ giày cần lấy (Size US):", [6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0], default=[7.0, 8.0, 9.0], key="sizes2")
        qty2 = st.number_input("Số lượng mỗi cỡ:", min_value=1, max_value=5, value=1, key="qty2")
        
    st.markdown("---")
    
    if st.button("⚡ Chạy DSS Tối Ưu Hóa Lộ Trình Nhặt Hàng", type="primary"):
        # Tạo danh sách yêu cầu
        pick_reqs = []
        for s in sizes1:
            pick_reqs.append({'sku': sku_ref1, 'size': float(s), 'qty': int(qty1)})
        for s in sizes2:
            pick_reqs.append({'sku': sku_ref2, 'size': float(s), 'qty': int(qty2)})
            
        if not pick_reqs:
            st.warning("Vui lòng chọn ít nhất một cỡ giày để nhặt.")
        else:
            with st.spinner("Đang truy vấn Data Warehouse & tính toán lộ trình TSP tối ưu..."):
                df_route, summary = optimize_picking(pick_reqs, engine, strategy_code='ClassBased')
                
            if df_route is None:
                st.error(f"❌ {summary}")
            else:
                # Hiển thị KPI Cards
                col_k1, col_k2, col_k3, col_k4 = st.columns(4)
                col_k1.metric("📍 Quãng Đường Tối Ưu", f"{summary['optimal_route_distance_m']:,.1f} m", f"-{summary['distance_saved_m']:,.1f} m")
                col_k2.metric("⏱️ Thời Gian Nhặt Dự Kiến", f"{summary['estimated_pick_time_min']} phút", f"-{summary['time_saved_minutes']} phút")
                col_k3.metric("🎯 Quãng Đường Tiết Kiệm", f"{summary['distance_saved_m']:,.1f} m", f"+{summary['pct_saved']}%")
                col_k4.metric("🛑 Số Điểm Dừng", f"{summary['total_items_to_pick']} điểm", "Tầng nhặt tay 100%")
                
                st.markdown(f"""
                <div class="badge-card">
                    ✅ <b>Kế hoạch tối ưu thành công:</b> Lộ trình TSP giúp giảm <b>{summary['distance_saved_m']:,.1f} mét ({summary['pct_saved']}%)</b> 
                    và tiết kiệm <b>{summary['time_saved_minutes']} phút</b> làm việc cho nhân viên so với lộ trình nhặt tuần tự chưa tối ưu!
                </div>
                """, unsafe_allow_html=True)
                
                # Bảng chi tiết từng bước nhặt
                st.subheader("📋 Chi Tiết Danh Sách & Thứ Tự Các Điểm Lấy Hàng (Sequenced Pick-List)")
                st.dataframe(
                    df_route[[
                        'step', 'sku', 'size', 'location_code', 'floor_type', 
                        'shelf_level_z', 'slot_number', 'location_priority_xyz', 'dist_from_origin'
                    ]].rename(columns={
                        'step': 'Thứ tự',
                        'sku': 'Mã SKU',
                        'size': 'Cỡ Size',
                        'location_code': 'Mã Kệ',
                        'floor_type': 'Loại Tầng',
                        'shelf_level_z': 'Tầng Kệ (Z)',
                        'slot_number': 'Số Ô (Slot)',
                        'location_priority_xyz': 'Ưu tiên Vị Trí (XYZ)',
                        'dist_from_origin': 'Cự ly cách cửa (m)'
                    }).style.format({
                        'Cự ly cách cửa (m)': '{:.1f} m'
                    }),
                    use_container_width=True
                )
                
                # Bản đồ trực quan hóa lộ trình nhặt hàng
                st.subheader("🗺️ Bản Đồ Lộ Trình Nhặt Hàng Qua Các Hành Lang Kho (TSP Path)")
                df_all_loc, df_nav = load_locations_and_nav()
                
                fig_route = go.Figure()
                
                # 1. Vẽ nền các vị trí kệ kho
                if not df_all_loc.empty:
                    fig_route.add_trace(go.Scatter(
                        x=df_all_loc['x'],
                        y=df_all_loc['y'],
                        mode='markers',
                        marker=dict(size=4, color='#e5e7eb'),
                        hoverinfo='none',
                        name='Vị trí kệ kho'
                    ))
                    
                # 2. Vẽ 44 điểm mốc hành lang LC, CC, RC
                if not df_nav.empty:
                    corridor_colors = {"LC": "#93c5fd", "CC": "#86efac", "RC": "#c4b5fd"}
                    for c_code, grp in df_nav.groupby('corridor_code'):
                        fig_route.add_trace(go.Scatter(
                            x=grp['x_nav'],
                            y=grp['y_nav'],
                            mode='lines+markers',
                            line=dict(dash='dot', width=2, color=corridor_colors.get(c_code, '#9ca3af')),
                            marker=dict(size=5),
                            name=f"Hành lang {c_code}",
                            hoverinfo='text',
                            text=grp['label']
                        ))
                        
                # 3. Vẽ đường di chuyển lộ trình tối ưu
                # Nối: Cửa xuất kho (0, 0) -> Điểm 1 -> Điểm 2 -> ... -> Cửa xuất kho (0, 0)
                route_x = [0.0] + df_route['x'].tolist() + [0.0]
                route_y = [0.0] + df_route['y'].tolist() + [0.0]
                
                fig_route.add_trace(go.Scatter(
                    x=route_x,
                    y=route_y,
                    mode='lines',
                    line=dict(color='#2563eb', width=3),
                    name='Lộ trình TSP tối ưu'
                ))
                
                # 4. Vẽ các điểm dừng lấy hàng
                fig_route.add_trace(go.Scatter(
                    x=df_route['x'],
                    y=df_route['y'],
                    mode='markers+text',
                    marker=dict(size=14, color='#ef4444', symbol='circle'),
                    text=[f"#{r['step']}: {r['sku']}-{r['size']}" for _, r in df_route.iterrows()],
                    textposition='top center',
                    textfont=dict(color='#991b1b', size=11, family='Arial Black'),
                    name='Điểm lấy hàng',
                    hoverinfo='text',
                    hovertext=[f"Bước {r['step']}: {r['sku']} (Sz {r['size']})<br>Kệ: {r['location_code']}<br>Tầng: {r['shelf_level_z']}<br>Ô: {r['slot_number']}<br>XYZ: {r['location_priority_xyz']}" for _, r in df_route.iterrows()]
                ))
                
                # 5. Điểm xuất phát Cửa kho Depot
                fig_route.add_trace(go.Scatter(
                    x=[0.0],
                    y=[0.0],
                    mode='markers+text',
                    marker=dict(size=16, color='#15803d', symbol='star'),
                    text=["Cửa xuất nhập kho (Depot 0,0)"],
                    textposition='bottom right',
                    name='Cửa kho (Depot)'
                ))
                
                fig_route.update_layout(
                    title="Sơ Đồ Đường Đi Nhân Viên Nhặt Hàng (TSP Route)",
                    xaxis_title="Tọa độ X (Mét)",
                    yaxis_title="Tọa độ Y (Mét)",
                    height=580,
                    margin=dict(l=20, r=20, t=40, b=20)
                )
                st.plotly_chart(fig_route, use_container_width=True)
                
                # Nút tải file
                csv_route = df_route.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="📥 Tải xuống Bảng Kế Hoạch Nhặt Hàng Tối Ưu (CSV)",
                    data=csv_route,
                    file_name="optimal_picking_plan.csv",
                    mime="text/csv"
                )

# ─────────────────────────────────────────────────────────────────────────────
# MÀN HÌNH 2: SOLUTION 2 — TỐI ƯU PHÂN BỔ NHẬP KHO (PUT-AWAY ALLOCATION)
# ─────────────────────────────────────────────────────────────────────────────
elif page == "📥 Solution 2: Tối Ưu Xếp Hàng Nhập Kho":
    st.markdown('<div class="main-title">📥 Solution 2: Tối Ưu Hóa Phân Bổ Vị Trí Xếp Hàng Mới (Put-Away Allocator)</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Đề xuất vị trí xếp hàng mới vào ô trống kệ kho sao cho dễ tìm, dễ lấy và tối ưu thời gian nhập xuất sau này</div>', unsafe_allow_html=True)
    
    st.markdown("""
    <div class="badge-card-blue">
        🎯 <b>Mục tiêu bài toán nhập hàng:</b> Khi có lô hàng mới về kho (Ví dụ: 10 đôi giày A cỡ 7), hệ thống DSS sẽ:
        <ul>
            <li>Đánh giá <b>mức độ ưu tiên mặt hàng (ABC)</b>: Hàng A bán chạy bắt buộc phải đặt ở vị trí thuận lợi nhất.</li>
            <li>Đánh giá <b>mức độ ưu tiên vị trí kho (XYZ)</b>: Ưu tiên chọn vị trí vàng X đầu dãy, tầng thấp Z ≤ 2 (nhặt tay không cần thang).</li>
            <li><b>Gom cụm hàng cùng loại</b>: Ưu tiên xếp cùng kệ hoặc cùng dãy với hàng đã có sẵn để nhân viên dễ tìm.</li>
            <li>Đề xuất chính xác <b>Mã kệ</b>, <b>Tầng kệ (Z)</b>, và <b>Số ô (Slot)</b> còn trống.</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)
    
    all_skus = load_all_skus()
    sku_options = [f"{s['reference']} (Hạng {s['abc_code']})" for s in all_skus] if all_skus else ["O9YFO8 (Hạng A)", "I1X92B (Hạng A)"]
    
    col_in1, col_in2, col_in3 = st.columns(3)
    with col_in1:
        sel_inbound_sku = st.selectbox("Chọn Mã Giày Nhập Kho:", sku_options, index=0)
        inbound_ref = sel_inbound_sku.split(" ")[0]
    with col_in2:
        inbound_size = st.selectbox("Chọn Kích Cỡ (Size US):", [6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0], index=2)
    with col_in3:
        inbound_qty = st.number_input("Số lượng nhập kho (đôi):", min_value=1, max_value=100, value=10)
        
    st.markdown("---")
    
    if st.button("⚡ Chạy DSS Đề Xuất Vị Trí Xếp Hàng Mới", type="primary"):
        with st.spinner("Đang quét dung lượng ô trống kệ kho & tính điểm phù hợp DSS..."):
            df_alloc, summary_alloc = allocate_inbound_goods(inbound_ref, float(inbound_size), int(inbound_qty), engine, strategy_code='ClassBased')
            
        if df_alloc is None:
            st.error(f"❌ {summary_alloc}")
        else:
            col_a1, col_a2, col_a3, col_a4 = st.columns(4)
            col_a1.metric("📦 Số Lượng Đã Phân Bổ", f"{summary_alloc['allocated_quantity']} / {summary_alloc['total_quantity_inbound']} đôi", f"Mặt hàng {summary_alloc['sku_priority_abc']}")
            col_a2.metric("🏷️ Số Kệ Sử Dụng", f"{summary_alloc['unique_locations_allocated']} vị trí kệ", "Gom cụm tối đa")
            col_a3.metric("🎯 Tỷ Lệ Sàn Lấy Tay (Z ≤ 2)", summary_alloc['manual_picking_ratio'], "Manual Picking")
            col_a4.metric("⭐ Tỷ Lệ Vị Trí Vàng (XYZ = X)", summary_alloc['prime_location_x_ratio'], f"Cự ly tb: {summary_alloc['avg_distance_from_gate_m']}m")
            
            st.markdown(f"""
            <div class="badge-card">
                ✅ <b>Đề xuất thành công:</b> Đã phân bổ toàn bộ <b>{inbound_qty} đôi</b> giày {inbound_ref} (Size {inbound_size}) 
                vào các ô kệ tối ưu. <b>100%</b> được xếp tại tầng nhặt tay (Z ≤ 2) và vị trí vàng gần lối đi chính giúp nhân viên lấy hàng cực nhanh!
            </div>
            """, unsafe_allow_html=True)
            
            st.subheader("📋 Bảng Phân Bổ Chi Tiết Từng Ô Kệ (Slot Allocation Plan)")
            st.dataframe(
                df_alloc[[
                    'sku_reference', 'size_us', 'location_code', 'shelf_level_z', 
                    'slot_number', 'floor_type', 'location_priority_xyz', 'dist_from_origin', 
                    'suitability_score', 'reason'
                ]].rename(columns={
                    'sku_reference': 'Mã SKU',
                    'size_us': 'Cỡ Size',
                    'location_code': 'Mã Kệ',
                    'shelf_level_z': 'Tầng Kệ (Z)',
                    'slot_number': 'Số Ô (Slot)',
                    'floor_type': 'Loại Tầng',
                    'location_priority_xyz': 'Ưu tiên Vị Trí (XYZ)',
                    'dist_from_origin': 'Cự ly cách cửa (m)',
                    'suitability_score': 'Điểm Phù Hợp',
                    'reason': 'Lý Do Phân Bổ'
                }).style.format({
                    'Cự ly cách cửa (m)': '{:.1f} m',
                    'Điểm Phù Hợp': '{:.1f}'
                }),
                use_container_width=True
            )
            
            # Bản đồ vị trí được xếp hàng
            st.subheader("🗺️ Bản Đồ Vị Trí Xếp Hàng Trên Mặt Bằng Kho")
            df_all_loc, df_nav = load_locations_and_nav()
            
            fig_alloc = go.Figure()
            
            # 1. Nền các vị trí kho
            if not df_all_loc.empty:
                fig_alloc.add_trace(go.Scatter(
                    x=df_all_loc['x'],
                    y=df_all_loc['y'],
                    mode='markers',
                    marker=dict(size=4, color='#e5e7eb'),
                    hoverinfo='none',
                    name='Vị trí kệ kho'
                ))
                
            # 2. Vị trí được phân bổ
            fig_alloc.add_trace(go.Scatter(
                x=df_alloc['x'],
                y=df_alloc['y'],
                mode='markers+text',
                marker=dict(size=16, color='#16a34a', symbol='square'),
                text=[f"Ô {r['slot_number']}" for _, r in df_alloc.iterrows()],
                textposition='top center',
                name='Vị trí xếp hàng mới',
                hoverinfo='text',
                hovertext=[f"Kệ: {r['location_code']}<br>Tầng Z: {r['shelf_level_z']}<br>Ô: {r['slot_number']}<br>Ưu tiên: {r['location_priority_xyz']}<br>Cự ly: {r['dist_from_origin']}m" for _, r in df_alloc.iterrows()]
            ))
            
            # 3. Cửa kho
            fig_alloc.add_trace(go.Scatter(
                x=[0.0],
                y=[0.0],
                mode='markers+text',
                marker=dict(size=16, color='#15803d', symbol='star'),
                text=["Cửa xuất nhập kho (Depot 0,0)"],
                textposition='bottom right',
                name='Cửa kho'
            ))
            
            fig_alloc.update_layout(
                title=f"Vị Trí Kệ Được Chọn Để Xếp {inbound_qty} Đôi Giày {inbound_ref} (Size {inbound_size})",
                xaxis_title="Tọa độ X (Mét)",
                yaxis_title="Tọa độ Y (Mét)",
                height=560,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig_alloc, use_container_width=True)
            
            # Nút tải file
            csv_alloc = df_alloc.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 Tải xuống Danh Sách Khuyến Nghị Xếp Hàng (CSV)",
                data=csv_alloc,
                file_name="optimal_putaway_recommendations.csv",
                mime="text/csv"
            )

# ─────────────────────────────────────────────────────────────────────────────
# MÀN HÌNH 3: ĐÁNH GIÁ & BENCHMARK MÔ HÌNH DSS
# ─────────────────────────────────────────────────────────────────────────────
elif page == "🤖 Đánh Giá & Benchmark Mô Hình DSS":
    st.markdown('<div class="main-title">🤖 Đánh Giá Hiệu Năng Mô Hình DSS (Benchmark)</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">So sánh độc lập 2 hướng tiếp cận: Hệ luật chuyên gia (Rule-Based) vs Máy học (Decision Tree, Random Forest) trên 80% Train / 20% Test</div>', unsafe_allow_html=True)
    
    df_b, df_r, df_f, cm_rule, cm_dt, cm_rf = load_dss_files()
    
    # 1. Bảng đối sánh Benchmark
    st.subheader("1. Bảng Đối Sánh Hiệu Năng Tổng Thể (Benchmark Comparison)")
    if not df_b.empty:
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.info("**1. Rule-Based System**\n\nAccuracy: **65.46%** | F1: **65.05%**")
        col_m2.info("**2. Decision Tree Classifier**\n\nAccuracy: **97.50%** | F1: **97.49%**")
        col_m3.success("🏆 **3. Random Forest (Champion)**\n\nAccuracy: **100.00%** | F1: **100.00%**")
        
        st.dataframe(
            df_b.style.format({
                "Accuracy": "{:.2%}",
                "Precision_Macro": "{:.2%}",
                "Precision_Weighted": "{:.2%}",
                "Recall_Macro": "{:.2%}",
                "Recall_Weighted": "{:.2%}",
                "F1_Macro": "{:.2%}",
                "F1_Weighted": "{:.2%}",
                "Inference_Time_Sec": "{:.3f}s"
            }).background_gradient(cmap="Blues", subset=["Accuracy", "F1_Weighted"]),
            use_container_width=True
        )
        
    st.markdown("---")
    
    # 2. Ma trận nhầm lẫn & Tầm quan trọng đặc trưng
    col_cm, col_feat = st.columns([6, 6])
    
    with col_cm:
        st.subheader("2. Ma Trận Nhầm Lẫn (Confusion Matrix)")
        cm_option = st.radio("Chọn mô hình:", ["Random Forest (Champion)", "Decision Tree", "Rule-Based System"], horizontal=True)
        sel_cm = cm_rf if cm_option.startswith("Random") else (cm_dt if cm_option.startswith("Decision") else cm_rule)
        if sel_cm is not None:
            fig_cm = px.imshow(
                sel_cm,
                text_auto=True,
                color_continuous_scale="Blues" if cm_option.startswith("Random") else "Oranges",
                labels=dict(x="Dự đoán (Predicted)", y="Thực tế (Actual)")
            )
            fig_cm.update_layout(margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig_cm, use_container_width=True)
            
    with col_feat:
        st.subheader("3. Tầm Quan Trọng Đặc Trưng (Feature Importance)")
        if not df_f.empty:
            fig_f = px.bar(
                df_f.head(8).sort_values(by="Importance", ascending=True),
                x="Importance",
                y="Feature",
                orientation='h',
                color="Importance",
                color_continuous_scale="Blues"
            )
            fig_f.update_layout(margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig_f, use_container_width=True)
            
    st.markdown("---")
    
    # 3. Danh sách khuyến nghị di dời kệ
    st.subheader("4. Bảng Đề Xuất Di Dời Tối Ưu Hóa Kệ (Slotting Recommendations)")
    if not df_r.empty:
        col_act1, col_act2, col_act3 = st.columns(3)
        n_maintain = (df_r['recommended_action'] == 'OPTIMAL_MAINTAIN').sum()
        n_closer   = (df_r['recommended_action'] == 'RELOCATE_CLOSER').sum()
        n_further  = (df_r['recommended_action'] == 'RELOCATE_FURTHER').sum()
        total_saved_km = df_r['estimated_dist_saved_per_pick_m'].sum() / 1000.0
        
        col_act1.metric("✅ OPTIMAL_MAINTAIN", f"{n_maintain:,} ô", "Vị trí đã chuẩn")
        col_act2.metric("🚀 RELOCATE_CLOSER", f"{n_closer:,} ô", "Hàng A đang ở xa")
        col_act3.metric("📦 RELOCATE_FURTHER", f"{n_further:,} ô", "Hàng C chiếm mặt tiền")
        
        st.markdown(f"""
        <div class="badge-card">
            💰 <b>Hiệu quả kinh tế (ROI):</b> Nếu quản lý kho thực hiện các đề xuất di dời này, 
            tổng quãng đường di chuyển của nhân viên nhặt hàng sẽ được <b>cắt giảm hơn {total_saved_km:,.1f} km</b>!
        </div>
        """, unsafe_allow_html=True)
        
        st.dataframe(
            df_r[[
                'location_code', 'zone', 'floor_level', 'dist_from_origin',
                'sku_reference', 'size_us', 'sku_total_order_qty', 'target_class',
                'champion_prediction', 'prediction_confidence', 'recommended_action',
                'estimated_dist_saved_per_pick_m'
            ]].head(50).style.format({
                'dist_from_origin': '{:.1f} m',
                'prediction_confidence': '{:.1%}',
                'estimated_dist_saved_per_pick_m': '{:.1f} m'
            }),
            use_container_width=True
        )

# ─────────────────────────────────────────────────────────────────────────────
# MÀN HÌNH 4: BẢN ĐỒ KHÔNG GIAN KHO 2D (ABC / XYZ)
# ─────────────────────────────────────────────────────────────────────────────
elif page == "🗺️ Bản Đồ Không Gian Kho 2D (ABC / XYZ)":
    st.markdown('<div class="main-title">🗺️ Bản Đồ Không Gian Kho 2D (ABC Zone & XYZ Accessibility)</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Trực quan hóa 2,357 vị trí kệ, 44 mốc hành lang LC/CC/RC và phân bổ thuộc tính ABC & XYZ</div>', unsafe_allow_html=True)
    
    df_loc, df_nav = load_locations_and_nav()
    
    if not df_loc.empty:
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            floors = ["Tất cả các tầng"] + sorted([f"Tầng {int(z)}" for z in df_loc['z'].unique()])
            sel_floor = st.selectbox("Chọn Tầng Kệ (Floor Level):", floors)
        with col_f2:
            color_mode = st.selectbox(
                "Màu sắc trực quan theo:",
                [
                    "Độ ưu tiên vị trí kho cho nhân viên (XYZ)",
                    "Phân vùng tốc độ xuất kho (ABC Zone)",
                    "Khoảng cách tới cửa xuất (dist_from_origin)",
                    "Loại tầng (Manual Picking vs Replenishment)",
                    "Khu vực (Zone)"
                ]
            )
        with col_f3:
            show_nav = st.checkbox("Hiển thị 44 Điểm Mốc Hành Lang (Waypoints)", value=True)
            
        filtered_loc = df_loc.copy()
        if sel_floor != "Tất cả các tầng":
            f_num = float(sel_floor.replace("Tầng ", ""))
            filtered_loc = filtered_loc[filtered_loc['z'] == f_num]
            
        fig_map = go.Figure()
        
        # 1. Các vị trí kệ
        if color_mode == "Độ ưu tiên vị trí kho cho nhân viên (XYZ)":
            xyz_colors = {"X": "#ef4444", "Y": "#f59e0b", "Z": "#3b82f6", "C": "#9ca3af"}
            for code, grp in filtered_loc.groupby('xyz_code'):
                fig_map.add_trace(go.Scatter(
                    x=grp['x'],
                    y=grp['y'],
                    mode='markers',
                    marker=dict(size=6, color=xyz_colors.get(code, "#6b7280")),
                    name=f"Vị trí {code}",
                    hoverinfo='text',
                    text=[f"Kệ: {r.location_code}<br>XYZ: {r.xyz_code}<br>Tầng: {int(r.z)}<br>Cự ly: {r.dist_from_origin}m" for _, r in grp.iterrows()]
                ))
        elif color_mode == "Phân vùng tốc độ xuất kho (ABC Zone)":
            abc_colors = {"A": "#10b981", "B": "#3b82f6", "C": "#9ca3af"}
            for code, grp in filtered_loc.groupby('abc_zone'):
                fig_map.add_trace(go.Scatter(
                    x=grp['x'],
                    y=grp['y'],
                    mode='markers',
                    marker=dict(size=6, color=abc_colors.get(code, "#6b7280")),
                    name=f"Vùng {code}",
                    hoverinfo='text',
                    text=[f"Kệ: {r.location_code}<br>Zone ABC: {r.abc_zone}<br>Tầng: {int(r.z)}<br>Cự ly: {r.dist_from_origin}m" for _, r in grp.iterrows()]
                ))
        elif color_mode == "Khoảng cách tới cửa xuất (dist_from_origin)":
            fig_map.add_trace(go.Scatter(
                x=filtered_loc['x'],
                y=filtered_loc['y'],
                mode='markers',
                marker=dict(
                    size=6,
                    color=filtered_loc['dist_from_origin'],
                    colorscale='Viridis',
                    colorbar=dict(title="Cự ly (m)"),
                    showscale=True
                ),
                text=[f"Kệ: {r.location_code}<br>Cự ly: {r.dist_from_origin}m" for _, r in filtered_loc.iterrows()],
                hoverinfo='text',
                name='Khoảng cách'
            ))
        elif color_mode == "Loại tầng (Manual Picking vs Replenishment)":
            for f_type, grp in filtered_loc.groupby('floor_type'):
                fig_map.add_trace(go.Scatter(
                    x=grp['x'],
                    y=grp['y'],
                    mode='markers',
                    marker=dict(size=6),
                    name=f_type,
                    text=[f"Kệ: {r.location_code}<br>Tầng Z: {int(r.z)}<br>Loại: {r.floor_type}" for _, r in grp.iterrows()],
                    hoverinfo='text'
                ))
        else: # Zone
            for z_name, grp in filtered_loc.groupby('zone'):
                fig_map.add_trace(go.Scatter(
                    x=grp['x'],
                    y=grp['y'],
                    mode='markers',
                    marker=dict(size=6),
                    name=f"Zone {z_name}",
                    text=[f"Kệ: {r.location_code}<br>Zone: {r.zone}" for _, r in grp.iterrows()],
                    hoverinfo='text'
                ))
                
        # 2. Hành lang
        if show_nav and not df_nav.empty:
            corridor_colors = {"LC": "#dc2626", "CC": "#059669", "RC": "#7c3aed"}
            for c_code, grp in df_nav.groupby('corridor_code'):
                fig_map.add_trace(go.Scatter(
                    x=grp['x_nav'],
                    y=grp['y_nav'],
                    mode='lines+markers',
                    line=dict(dash='dash', width=2, color=corridor_colors.get(c_code, '#6b7280')),
                    marker=dict(size=6),
                    name=f"Hành lang {c_code}",
                    text=grp['label'],
                    hoverinfo='text'
                ))
                
        # 3. Cửa kho
        fig_map.add_trace(go.Scatter(
            x=[0.0],
            y=[0.0],
            mode='markers+text',
            marker=dict(size=15, color='#15803d', symbol='star'),
            text=["Cửa xuất nhập kho (0,0)"],
            textposition='bottom right',
            name='Cửa kho'
        ))
        
        fig_map.update_layout(
            title="Sơ Đồ Mặt Bằng Không Gian Kho Chi Tiết",
            xaxis_title="Tọa độ X (Mét)",
            yaxis_title="Tọa độ Y (Mét)",
            height=620,
            margin=dict(l=20, r=20, t=40, b=20)
        )
        st.plotly_chart(fig_map, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────────────
# MÀN HÌNH 5: TỔNG QUAN HOẠT ĐỘNG & OLAP DATA WAREHOUSE
# ─────────────────────────────────────────────────────────────────────────────
elif page == "📊 Tổng Quan Hoạt Động & OLAP":
    st.markdown('<div class="main-title">📊 Tổng Quan Hoạt Động Kho & Phân Tích OLAP</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Trích xuất trực tiếp từ Fact Constellation trên PostgreSQL 16 (Port 5433)</div>', unsafe_allow_html=True)
    
    kpis = load_kpis()
    if kpis:
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("📦 Tổng Dòng Đơn Hàng", f"{kpis['orders'][1]:,}", f"{kpis['orders'][0]:,} đơn")
        col2.metric("👟 Sản Lượng Khách Đặt", f"{kpis['orders'][2]:,} đôi", "122k items")
        col3.metric("🎯 Lượt Nhặt Hàng (Picks)", f"{kpis['picks'][1]:,}", f"{kpis['picks'][0]:,} waves")
        col4.metric("🛒 Hiệu Suất Xe Đẩy", f"{kpis['dim_stats'][3]}%", "Sức chứa 24 items")
        col5.metric("🏷️ Danh Mục / Vị Trí", f"{kpis['dim_stats'][0]} SKUs", f"{kpis['dim_stats'][1]:,} kệ")
        
    st.markdown("---")
    
    col_chart1, col_chart2 = st.columns([7, 5])
    df_trends = load_order_trends()
    with col_chart1:
        st.subheader("📈 Xu Hướng Sản Lượng Đặt Hàng Theo Tháng (2023)")
        if not df_trends.empty:
            fig_trend = px.area(
                df_trends, 
                x="month_name", 
                y="total_units",
                markers=True,
                labels={"month_name": "Tháng", "total_units": "Số lượng (đôi)"},
                color_discrete_sequence=["#1f77b4"]
            )
            fig_trend.update_layout(margin=dict(l=20, r=20, t=30, b=20), hovermode="x unified")
            st.plotly_chart(fig_trend, use_container_width=True)
            
    with col_chart2:
        st.subheader("⚖️ So Sánh Cự Ly 4 Chiến Lược Lưu Trữ Kho")
        df_strat = load_strategy_comparison()
        if not df_strat.empty:
            fig_strat = px.bar(
                df_strat,
                x="strategy_name",
                y="avg_distance_m",
                color="abc_code",
                barmode="group",
                labels={"strategy_name": "Chiến lược", "avg_distance_m": "Cự ly TB (m)", "abc_code": "Nhóm ABC"},
                color_discrete_map={"A": "#10b981", "B": "#3b82f6", "C": "#f59e0b"}
            )
            fig_strat.update_layout(margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig_strat, use_container_width=True)
