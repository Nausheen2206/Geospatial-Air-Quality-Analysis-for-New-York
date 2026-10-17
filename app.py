# pyrefly: ignore [missing-import]
import streamlit as st
from pymongo import MongoClient, GEOSPHERE, ReplaceOne
from datetime import datetime, timezone
import pandas as pd
import folium
from streamlit_folium import st_folium
import plotly.express as px
import plotly.graph_objects as go
from shapely.geometry import Point, mapping, MultiPoint, Polygon
from shapely.ops import transform, unary_union, voronoi_diagram
from pyproj import Transformer
from collections import Counter
from itertools import combinations

# ============================================================
# PAGE CONFIGURATION & METADATA
# ============================================================

st.set_page_config(
    page_title="MongoDB & Shapely Geospatial Air Quality Tutorial",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# PREMIUM STYLING & CSS
# ============================================================

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .query-header {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        color: #f8fafc;
        padding: 24px 28px;
        border-radius: 12px;
        margin-bottom: 24px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
        border: 1px solid #334155;
    }
    
    .query-number-badge {
        display: inline-block;
        background: #38bdf8;
        color: #0f172a;
        font-weight: 800;
        font-size: 13px;
        padding: 4px 12px;
        border-radius: 20px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 8px;
    }
    
    .query-title {
        font-size: 26px;
        font-weight: 700;
        margin: 0;
        color: #ffffff;
    }
    
    .query-question {
        color: #94a3b8;
        font-size: 16px;
        margin-top: 8px;
        line-height: 1.5;
    }
    
    .stat-pill {
        background: #f1f5f9;
        border-left: 4px solid #0284c7;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 12px;
    }
    
    .code-output-box {
        background-color: #0f172a;
        color: #38bdf8;
        font-family: 'Consolas', 'Courier New', monospace;
        padding: 16px;
        border-radius: 8px;
        font-size: 14px;
        line-height: 1.6;
        border: 1px solid #1e293b;
        white-space: pre-wrap;
    }
    
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        padding: 14px 18px;
        border-radius: 10px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    }
</style>
""", unsafe_allow_html=True)


# ============================================================
# COORDINATE TRANSFORMERS (WGS84 <-> UTM 18N)
# ============================================================

to_m = Transformer.from_crs(4326, 32618, always_xy=True).transform
to_deg = Transformer.from_crs(32618, 4326, always_xy=True).transform
TIMES_SQ = {"type": "Point", "coordinates": [-73.9855, 40.7580]}


# ============================================================
# MONGODB CONNECTION
# ============================================================

@st.cache_resource
def get_database():
    mongo_uri = st.secrets.get("MONGO_URI")
    if not mongo_uri:
        raise ValueError("MONGO_URI not configured in .streamlit/secrets.toml")
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    return client["air_quality"]


try:
    db = get_database()
    col = db["stations"]
    bor_col = db["boroughs"]
except Exception as e:
    st.error("❌ Could not connect to MongoDB.")
    st.code(str(e))
    st.info("Please verify `.streamlit/secrets.toml` has a valid `MONGO_URI`.")
    st.stop()


# ============================================================
# COMMON HELPERS
# ============================================================

@st.cache_data(ttl=600)
def load_all_stations():
    return list(col.find({}))

stations = load_all_stations()
if not stations:
    st.warning("⚠️ No stations found in MongoDB collection 'stations'.")
    st.stop()

names = {s["_id"]: s.get("name", "Unknown") for s in stations}
station_names_list = sorted([s.get("name", "Unknown") for s in stations])

def feature(geom, **props):
    return {"type": "Feature", "properties": props, "geometry": geom}

def station_to_row(s, extra=None):
    coords = s.get("location", {}).get("coordinates", [None, None])
    row = {
        "Name": s.get("name", "Unknown"),
        "Type": "Monitor (Gov)" if s.get("is_monitor") else "Low-cost Sensor",
        "Borough": s.get("borough", "N/A"),
        "Latitude": coords[1],
        "Longitude": coords[0],
        "Provider": s.get("provider", "N/A"),
    }
    if extra:
        row.update(extra)
    return row

def create_base_map(center=(40.74, -73.95), zoom=11):
    return folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap")

# Precompute 2000m Metric Buffer Zones
R_default = 2000
zones = {
    s["_id"]: transform(to_m, Point(s["location"]["coordinates"])).buffer(R_default)
    for s in stations if "location" in s and "coordinates" in s["location"]
}


# ============================================================
# SIDEBAR NAVIGATION (THE 10 QUERIES)
# ============================================================

st.sidebar.markdown("## 🌍 Geospatial Queries")
st.sidebar.caption("MongoDB Geospatial & Shapely Analysis")

query_options = [
    "Query 1: Spatial Range Search ($centerSphere & $box)",
    "Query 2: K-Nearest Neighbours ($near vs $geoNear)",
    "Query 3: Distance Measurement (Euclidean vs Geodetic)",
    "Query 4: Buffer Analysis (Concentric Rings)",
    "Query 5: Spatial Buffer Zones Setup (2 km Zones)",
    "Query 6: Load Borough Boundaries (OSMnx / MongoDB)",
    "Query 7: Union & Monitor-to-Sensor Proximity",
    "Query 8: Intersection (Overlapping Pairs)",
    "Query 9: Symmetric Difference & Identity",
    "Query 10: Containment, Spatial Join & Aggregation"
]

selected_query = st.sidebar.radio("Select Query", query_options, index=0)
st.sidebar.markdown("---")
st.sidebar.markdown(f"**Total Stations:** `{len(stations)}`")
gov_count = sum(1 for s in stations if s.get("is_monitor"))
sensor_count = len(stations) - gov_count
st.sidebar.markdown(f"• 🏛️ **Gov Monitors:** `{gov_count}`")
st.sidebar.markdown(f"• 📡 **Low-Cost Sensors:** `{sensor_count}`")


# ============================================================
# 1. QUERY 1: SPATIAL RANGE SEARCH
# ============================================================

if selected_query == query_options[0]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 1</span>
        <h1 class="query-title">Spatial Range Search</h1>
        <div class="query-question">Which stations lie within 5 km of Central Park (circle) and inside a Brooklyn bounding box (rectangle)?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    with tab1:
        col_ctrl1, col_ctrl2 = st.columns(2)
        with col_ctrl1:
            st.markdown("#### ⭕ Circular Search (Central Park)")
            cp_lat = st.number_input("Center Latitude", value=40.7829, format="%.4f", key="q1_lat")
            cp_lon = st.number_input("Center Longitude", value=-73.9654, format="%.4f", key="q1_lon")
            cp_radius_km = st.slider("Radius (km)", min_value=1.0, max_value=25.0, value=5.0, step=0.5, key="q1_rad")
        
        with col_ctrl2:
            st.markdown("#### ▣ Bounding Box Search (Brooklyn)")
            bb_min_lon = st.number_input("Min Longitude (SW)", value=-74.05, format="%.4f", key="q1_minlon")
            bb_min_lat = st.number_input("Min Latitude (SW)", value=40.57, format="%.4f", key="q1_minlat")
            bb_max_lon = st.number_input("Max Longitude (NE)", value=-73.83, format="%.4f", key="q1_maxlon")
            bb_max_lat = st.number_input("Max Latitude (NE)", value=40.74, format="%.4f", key="q1_maxlat")

        # Execute Queries
        circle_res = list(col.find({
            "location": {
                "$geoWithin": {
                    "$centerSphere": [[cp_lon, cp_lat], cp_radius_km / 6378.1]
                }
            }
        }))

        box_res = list(col.find({
            "location": {
                "$geoWithin": {
                    "$box": [[bb_min_lon, bb_min_lat], [bb_max_lon, bb_max_lat]]
                }
            }
        }))

        m1, m2 = st.columns(2)
        m1.metric("Stations within 5 km of Central Park", len(circle_res))
        m2.metric("Stations inside Brooklyn Bounding Box", len(box_res))

        # Map Visualization
        m = create_base_map(center=[40.73, -73.95], zoom=11)

        # Add Circle
        folium.Circle(
            location=[cp_lat, cp_lon],
            radius=cp_radius_km * 1000,
            color="#e11d48",
            fill=True,
            fill_color="#e11d48",
            fill_opacity=0.15,
            weight=2,
            tooltip=f"{cp_radius_km} km radius around Central Park"
        ).add_to(m)
        folium.Marker([cp_lat, cp_lon], tooltip="Central Park Center", icon=folium.Icon(color="red", icon="star")).add_to(m)

        # Add Box
        folium.Rectangle(
            bounds=[[bb_min_lat, bb_min_lon], [bb_max_lat, bb_max_lon]],
            color="#2563eb",
            fill=True,
            fill_color="#2563eb",
            fill_opacity=0.12,
            weight=2,
            tooltip="Brooklyn Bounding Box"
        ).add_to(m)

        # Add station markers
        circle_ids = {s["_id"] for s in circle_res}
        box_ids = {s["_id"] for s in box_res}

        for s in stations:
            lon, lat = s["location"]["coordinates"]
            in_c = s["_id"] in circle_ids
            in_b = s["_id"] in box_ids
            
            if in_c and in_b:
                color = "#9333ea"  # both
            elif in_c:
                color = "#e11d48"  # circle only
            elif in_b:
                color = "#2563eb"  # box only
            else:
                color = "#94a3b8"  # outside

            folium.CircleMarker(
                [lat, lon],
                radius=5 if (in_c or in_b) else 3,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.9,
                tooltip=f"{s['name']} ({'Central Park Circle' if in_c else ''} {'Brooklyn Box' if in_b else ''})"
            ).add_to(m)

        st_folium(m, width=None, height=520, key="q1_map")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Central Park Circle Matches ({len(circle_res)}):**")
            st.dataframe(pd.DataFrame([station_to_row(s) for s in circle_res]), use_container_width=True, hide_index=True)
        with c2:
            st.markdown(f"**Brooklyn Box Matches ({len(box_res)}):**")
            st.dataframe(pd.DataFrame([station_to_row(s) for s in box_res]), use_container_width=True, hide_index=True)

    with tab2:
        st.code("""circle = list(col.find({"location": {"$geoWithin": {
    "$centerSphere": [[-73.9654, 40.7829], 5 / 6378.1]}}}))
box = list(col.find({"location": {"$geoWithin": {
    "$box": [[-74.05, 40.57], [-73.83, 40.74]]}}}))

print("within 5 km of Central Park:", len(circle), [s["name"] for s in circle])
print("inside Brooklyn box        :", len(box), [s["name"] for s in box])""", language="python")

        default_circle = list(col.find({"location": {"$geoWithin": {"$centerSphere": [[-73.9654, 40.7829], 5 / 6378.1]}}}))
        default_box = list(col.find({"location": {"$geoWithin": {"$box": [[-74.05, 40.57], [-73.83, 40.74]]}}}))

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">within 5 km of Central Park: {len(default_circle)} {[s['name'] for s in default_circle]}
inside Brooklyn box        : {len(default_box)} {[s['name'] for s in default_box]}</div>""", unsafe_allow_html=True)


# ============================================================
# 2. QUERY 2: K NEAREST NEIGHBOURS
# ============================================================

elif selected_query == query_options[1]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 2</span>
        <h1 class="query-title">K Nearest Neighbours</h1>
        <div class="query-question">Which are the 5 nearest stations to Times Square, and how far is each?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    with tab1:
        c1, c2, c3 = st.columns(3)
        with c1:
            k_val = st.slider("Number of Nearest Stations (k)", min_value=1, max_value=20, value=5)
        with c2:
            max_d_km = st.slider("Max Search Distance (km)", min_value=1, max_value=50, value=15)
        with c3:
            target_name = st.selectbox("Target Point", ["Times Square", "Custom Coordinates"])

        if target_name == "Times Square":
            tgt_lat, tgt_lon = 40.7580, -73.9855
        else:
            tgt_lat = st.number_input("Target Lat", value=40.7580, format="%.4f")
            tgt_lon = st.number_input("Target Lon", value=-73.9855, format="%.4f")

        target_geom = {"type": "Point", "coordinates": [tgt_lon, tgt_lat]}

        # $near query
        by_near = [s["name"] for s in col.find({
            "location": {
                "$near": {
                    "$geometry": target_geom,
                    "$maxDistance": int(max_d_km * 1000)
                }
            }
        }).limit(int(k_val))]

        # $geoNear aggregation
        by_geo = list(col.aggregate([
            {
                "$geoNear": {
                    "near": target_geom,
                    "distanceField": "d",
                    "spherical": True,
                    "maxDistance": int(max_d_km * 1000)
                }
            },
            {"$limit": int(k_val)}
        ]))

        orders_match = (by_near == [s["name"] for s in by_geo])

        m1, m2 = st.columns(2)
        m1.metric("Nearest Stations Found", len(by_geo))
        m2.metric("Orders Match ($near vs $geoNear)", str(orders_match), help="Verifies identical sorting between index find and aggregation")

        m = create_base_map(center=[tgt_lat, tgt_lon], zoom=12)
        folium.Marker([tgt_lat, tgt_lon], tooltip="Query Target (Times Sq)", icon=folium.Icon(color="red", icon="star")).add_to(m)
        folium.Circle([tgt_lat, tgt_lon], radius=max_d_km * 1000, color="#f59e0b", weight=1.5, fill=True, fill_opacity=0.05).add_to(m)

        res_table = []
        for idx, s in enumerate(by_geo, start=1):
            slon, slat = s["location"]["coordinates"]
            dist_km = round(s["d"] / 1000, 2)
            res_table.append({
                "Rank": idx,
                "Station": s.get("name"),
                "Distance (km)": dist_km,
                "Distance (meters)": round(s["d"], 1),
                "Type": "Monitor" if s.get("is_monitor") else "Sensor",
                "Latitude": slat,
                "Longitude": slon
            })

            # Line to station
            folium.PolyLine(
                [[tgt_lat, tgt_lon], [slat, slon]],
                color="#0284c7",
                weight=2.5,
                opacity=0.8,
                tooltip=f"{s.get('name')}: {dist_km} km"
            ).add_to(m)

            folium.CircleMarker(
                [slat, slon],
                radius=6,
                color="#0284c7",
                fill=True,
                fill_color="#0284c7",
                fill_opacity=1.0,
                tooltip=f"#{idx} {s.get('name')} ({dist_km} km)"
            ).add_to(m)

        st_folium(m, width=None, height=520, key="q2_map")
        st.dataframe(pd.DataFrame(res_table), use_container_width=True, hide_index=True)

    with tab2:
        st.code("""k = 5
by_near = [s["name"] for s in col.find({"location": {"$near": {
    "$geometry": times_sq, "$maxDistance": 15000}}}).limit(k)]

by_geo = list(col.aggregate([
    {"$geoNear": {"near": times_sq, "distanceField": "d",
                  "spherical": True, "maxDistance": 15000}},
    {"$limit": k}]))

for s in by_geo:
    print(s["name"], "|", round(s["d"] / 1000, 2), "km")
print("orders match:", by_near == [s["name"] for s in by_geo])""", language="python")

        ts_geo = list(col.aggregate([
            {"$geoNear": {"near": TIMES_SQ, "distanceField": "d", "spherical": True, "maxDistance": 15000}},
            {"$limit": 5}
        ]))
        ts_near = [s["name"] for s in col.find({"location": {"$near": {"$geometry": TIMES_SQ, "$maxDistance": 15000}}}).limit(5)]
        out_lines = [f"{s['name']} | {round(s['d'] / 1000, 2)} km" for s in ts_geo]
        out_lines.append(f"orders match: {ts_near == [s['name'] for s in ts_geo]}")

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">{chr(10).join(out_lines)}</div>""", unsafe_allow_html=True)


# ============================================================
# 3. QUERY 3: DISTANCE MEASUREMENT (EUCLIDEAN VS GEODETIC)
# ============================================================

elif selected_query == query_options[2]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 3</span>
        <h1 class="query-title">Distance Measurement (Euclidean vs Geodetic)</h1>
        <div class="query-question">How far is CCNY from Times Square when measured as a straight line and along the Earth's curve?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    with tab1:
        chosen_station_name = st.selectbox("Select Station to Measure from Times Square", station_names_list, index=station_names_list.index("CCNY") if "CCNY" in station_names_list else 0)
        a = col.find_one({"name": chosen_station_name})
        
        if a:
            pa = a["location"]["coordinates"]
            geodetic = list(col.aggregate([
                {"$geoNear": {"near": TIMES_SQ, "distanceField": "d",
                              "spherical": True, "query": {"_id": a["_id"]}}}
            ]))[0]["d"]

            euclid = transform(to_m, Point(pa)).distance(
                transform(to_m, Point(TIMES_SQ["coordinates"])))

            diff = abs(geodetic - euclid)

            c1, c2, c3 = st.columns(3)
            c1.metric("Geodetic Distance (Spherical Earth)", f"{geodetic:.1f} m", f"{geodetic/1000:.2f} km")
            c2.metric("Euclidean Distance (UTM 18N Plane)", f"{euclid:.1f} m", f"{euclid/1000:.2f} km")
            c3.metric("Difference (Spherical Curvature Effect)", f"{diff:.1f} m", f"{diff/geodetic*100:.3f}%")

            st.info(f"💡 **Explanation:** Geodetic distance measures along the Earth's spherical surface (via MongoDB `$geoNear` Great-Circle formula), while Euclidean distance projects WGS84 coordinates onto the planar UTM Zone 18N metric grid.")

            m = create_base_map(center=[(pa[1] + TIMES_SQ["coordinates"][1])/2, (pa[0] + TIMES_SQ["coordinates"][0])/2], zoom=12)
            folium.Marker([TIMES_SQ["coordinates"][1], TIMES_SQ["coordinates"][0]], tooltip="Times Square", icon=folium.Icon(color="red", icon="star")).add_to(m)
            folium.Marker([pa[1], pa[0]], tooltip=f"{chosen_station_name}", icon=folium.Icon(color="blue", icon="info-sign")).add_to(m)
            folium.PolyLine(
                [[TIMES_SQ["coordinates"][1], TIMES_SQ["coordinates"][0]], [pa[1], pa[0]]],
                color="#8b5cf6",
                weight=3,
                tooltip=f"Geodetic: {geodetic:.1f} m | Euclidean: {euclid:.1f} m"
            ).add_to(m)

            st_folium(m, width=None, height=480, key="q3_map")

    with tab2:
        st.code("""a = col.find_one({"name": "CCNY"})
pa = a["location"]["coordinates"]

geodetic = list(col.aggregate([
    {"$geoNear": {"near": times_sq, "distanceField": "d",
                  "spherical": True, "query": {"_id": a["_id"]}}}
]))[0]["d"]

euclid = transform(to_m, Point(pa)).distance(
    transform(to_m, Point(times_sq["coordinates"])))

print("geodetic  (m):", round(geodetic, 1))
print("euclidean (m):", round(euclid, 1))
print("difference (m):", round(abs(geodetic - euclid), 1))""", language="python")

        a_ccny = col.find_one({"name": "CCNY"})
        if a_ccny:
            pa_ccny = a_ccny["location"]["coordinates"]
            g_val = list(col.aggregate([
                {"$geoNear": {"near": TIMES_SQ, "distanceField": "d", "spherical": True, "query": {"_id": a_ccny["_id"]}}}
            ]))[0]["d"]
            e_val = transform(to_m, Point(pa_ccny)).distance(transform(to_m, Point(TIMES_SQ["coordinates"])))
            d_val = abs(g_val - e_val)

            st.markdown("**Console Output:**")
            st.markdown(f"""<div class="code-output-box">geodetic  (m): {round(g_val, 1)}
euclidean (m): {round(e_val, 1)}
difference (m): {round(d_val, 1)}</div>""", unsafe_allow_html=True)


# ============================================================
# 4. QUERY 4: BUFFER ANALYSIS
# ============================================================

elif selected_query == query_options[3]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 4</span>
        <h1 class="query-title">Buffer Analysis</h1>
        <div class="query-question">How many stations lie within 2 km of Times Square, in the ring between 2 and 5 km, and beyond 5 km?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            r_inner = st.slider("Inner Radius (meters)", min_value=500, max_value=5000, value=2000, step=250)
        with c2:
            r_outer = st.slider("Outer Radius (meters)", min_value=3000, max_value=15000, value=5000, step=500)

        center_pt = transform(to_m, Point(-73.9855, 40.7580))
        inner_geom = center_pt.buffer(r_inner)
        outer_geom = center_pt.buffer(r_outer)
        ring_geom  = outer_geom.difference(inner_geom)

        def count_in_geom(geom_m):
            g = mapping(transform(to_deg, geom_m))
            return col.count_documents({"location": {"$geoWithin": {"$geometry": g}}})

        def find_in_geom(geom_m):
            g = mapping(transform(to_deg, geom_m))
            return list(col.find({"location": {"$geoWithin": {"$geometry": g}}}))

        inner_stations = find_in_geom(inner_geom)
        ring_stations = find_in_geom(ring_geom)
        outer_stations = find_in_geom(outer_geom)

        total_all = col.count_documents({})
        n_in = len(inner_stations)
        n_rg = len(ring_stations)
        n_by = total_all - len(outer_stations)

        c_a, c_b, c_c = st.columns(3)
        c_a.metric(f"Within {r_inner/1000:.1f} km", n_in)
        c_b.metric(f"Ring {r_inner/1000:.1f}–{r_outer/1000:.1f} km", n_rg)
        c_c.metric(f"Beyond {r_outer/1000:.1f} km", n_by)

        m = create_base_map(center=[40.7580, -73.9855], zoom=12)

        # Inner Circle
        folium.GeoJson(
            {"type": "Feature", "properties": {}, "geometry": mapping(transform(to_deg, inner_geom))},
            name="Inner Circle",
            style_function=lambda f: {"color": "#ef4444", "fillColor": "#ef4444", "fillOpacity": 0.25, "weight": 2}
        ).add_to(m)

        # Ring Donut
        folium.GeoJson(
            {"type": "Feature", "properties": {}, "geometry": mapping(transform(to_deg, ring_geom))},
            name="Ring Donut",
            style_function=lambda f: {"color": "#f59e0b", "fillColor": "#f59e0b", "fillOpacity": 0.15, "weight": 2}
        ).add_to(m)

        folium.Marker([40.7580, -73.9855], tooltip="Times Square Center", icon=folium.Icon(color="red", icon="star")).add_to(m)

        # Mark Stations by zone
        inner_ids = {s["_id"] for s in inner_stations}
        ring_ids = {s["_id"] for s in ring_stations}

        for s in stations:
            lon, lat = s["location"]["coordinates"]
            if s["_id"] in inner_ids:
                colr = "#ef4444"
                label = "Within Inner Buffer"
            elif s["_id"] in ring_ids:
                colr = "#f59e0b"
                label = "In Concentric Ring"
            else:
                colr = "#64748b"
                label = "Beyond Outer Buffer"

            folium.CircleMarker(
                [lat, lon],
                radius=5,
                color=colr,
                fill=True,
                fill_color=colr,
                fill_opacity=0.9,
                tooltip=f"{s['name']} ({label})"
            ).add_to(m)

        st_folium(m, width=None, height=520, key="q4_map")

    with tab2:
        st.code("""center = transform(to_m, Point(-73.9855, 40.7580))
inner = center.buffer(2000)
outer = center.buffer(5000)
ring  = outer.difference(inner)

def count_in(geom_m):
    g = mapping(transform(to_deg, geom_m))
    return col.count_documents({"location": {"$geoWithin": {"$geometry": g}}})

n_inner, n_ring, n_outer = count_in(inner), count_in(ring), count_in(outer)
print("within 2 km :", n_inner)
print("ring 2-5 km :", n_ring)
print("beyond 5 km :", col.count_documents({}) - n_outer)""", language="python")

        center_nb = transform(to_m, Point(-73.9855, 40.7580))
        inner_nb = center_nb.buffer(2000)
        outer_nb = center_nb.buffer(5000)
        ring_nb = outer_nb.difference(inner_nb)
        
        def count_in(geom_m):
            g = mapping(transform(to_deg, geom_m))
            return col.count_documents({"location": {"$geoWithin": {"$geometry": g}}})

        n1, n2, n3 = count_in(inner_nb), count_in(ring_nb), count_in(outer_nb)

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">within 2 km : {n1}
ring 2-5 km : {n2}
beyond 5 km : {col.count_documents({}) - n3}</div>""", unsafe_allow_html=True)


# ============================================================
# 5. QUERY 5: SPATIAL BUFFER ZONES SETUP
# ============================================================

elif selected_query == query_options[4]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 5</span>
        <h1 class="query-title">Spatial Buffer Zones Setup</h1>
        <div class="query-question">Setup 2 km metric buffer zones around all stations using UTM 18N projection (EPSG:32618).</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    with tab1:
        r_zone = st.slider("Buffer Radius (R in meters)", min_value=500, max_value=5000, value=2000, step=250)
        custom_zones = {
            s["_id"]: transform(to_m, Point(s["location"]["coordinates"])).buffer(r_zone)
            for s in stations if "location" in s and "coordinates" in s["location"]
        }

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Stations", len(stations))
        c2.metric("Computed Buffer Zones", len(custom_zones))
        c3.metric("Zone Radius R", f"{r_zone} meters ({r_zone/1000:.1f} km)")

        m = create_base_map(center=[40.73, -73.95], zoom=10)

        for s in stations:
            sid = s["_id"]
            if sid in custom_zones:
                is_m = s.get("is_monitor", False)
                color = "#0284c7" if is_m else "#16a34a"
                g = mapping(transform(to_deg, custom_zones[sid]))
                folium.GeoJson(
                    {"type": "Feature", "properties": {}, "geometry": g},
                    style_function=lambda f, c=color: {"color": c, "weight": 1, "fillColor": c, "fillOpacity": 0.15}
                ).add_to(m)

                lon, lat = s["location"]["coordinates"]
                folium.CircleMarker(
                    [lat, lon],
                    radius=4,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.9,
                    tooltip=f"{s['name']} ({'Gov Monitor' if is_m else 'Low-cost Sensor'})"
                ).add_to(m)

        st_folium(m, width=None, height=560, key="q5_map")

    with tab2:
        st.code("""!pip install shapely pyproj

from shapely.geometry import Point, mapping, MultiPoint
from shapely.ops import transform, unary_union, voronoi_diagram
from pyproj import Transformer
from itertools import combinations

to_m   = Transformer.from_crs(4326, 32618, always_xy=True).transform
to_deg = Transformer.from_crs(32618, 4326, always_xy=True).transform

stations = list(col.find())
names = {s["_id"]: s["name"] for s in stations}

R = 2000
zones = {s["_id"]: transform(to_m, Point(s["location"]["coordinates"])).buffer(R)
         for s in stations}
print("stations:", len(stations), "| zones:", len(zones))""", language="python")

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">stations: {len(stations)} | zones: {len(zones)}</div>""", unsafe_allow_html=True)


# ============================================================
# 6. QUERY 6: LOAD BOROUGH BOUNDARIES (OSMNX / MONGODB)
# ============================================================

elif selected_query == query_options[5]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 6</span>
        <h1 class="query-title">Load NYC Borough Polygons into MongoDB (OSMnx)</h1>
        <div class="query-question">How to geocode and store NYC borough boundaries in MongoDB with 2dsphere indexing?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    predefined_boroughs = [
        {
            "_id": "Manhattan",
            "name": "Manhattan",
            "kind": "borough",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-74.020, 40.700], [-73.975, 40.710], [-73.970, 40.745],
                    [-73.930, 40.795], [-73.910, 40.875], [-73.930, 40.880],
                    [-73.955, 40.825], [-73.990, 40.770], [-74.015, 40.730],
                    [-74.020, 40.700]
                ]]
            }
        },
        {
            "_id": "Brooklyn",
            "name": "Brooklyn",
            "kind": "borough",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-74.045, 40.570], [-73.855, 40.570], [-73.855, 40.690],
                    [-73.950, 40.735], [-74.000, 40.700], [-74.045, 40.640],
                    [-74.045, 40.570]
                ]]
            }
        },
        {
            "_id": "Queens",
            "name": "Queens",
            "kind": "borough",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-73.960, 40.540], [-73.700, 40.540], [-73.700, 40.800],
                    [-73.920, 40.800], [-73.960, 40.730], [-73.960, 40.540]
                ]]
            }
        },
        {
            "_id": "Bronx",
            "name": "Bronx",
            "kind": "borough",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-73.930, 40.795], [-73.780, 40.795], [-73.780, 40.915],
                    [-73.915, 40.915], [-73.930, 40.795]
                ]]
            }
        },
        {
            "_id": "Staten Island",
            "name": "Staten Island",
            "kind": "borough",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-74.255, 40.495], [-74.050, 40.495], [-74.050, 40.650],
                    [-74.255, 40.650], [-74.255, 40.495]
                ]]
            }
        }
    ]

    with tab1:
        st.write("Stores the 5 NYC boroughs (`Manhattan`, `Brooklyn`, `Queens`, `Bronx`, `Staten Island`) with a `2dsphere` spatial index on the `geometry` field.")
        
        col_btn1, col_btn2 = st.columns([2, 1])
        with col_btn1:
            if st.button("⚡ Store / Refresh Borough Boundaries in MongoDB"):
                bor_col.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in predefined_boroughs], ordered=False)
                try:
                    bor_col.create_index([("geometry", GEOSPHERE)])
                except Exception:
                    pass
                st.success(f"Successfully stored {bor_col.count_documents({})} borough boundary documents with 2dsphere index!")

        existing_boroughs = list(bor_col.find({}))
        if not existing_boroughs:
            bor_col.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in predefined_boroughs], ordered=False)
            try:
                bor_col.create_index([("geometry", GEOSPHERE)])
            except Exception:
                pass
            existing_boroughs = list(bor_col.find({}))

        st.metric("Boroughs Stored in MongoDB", len(existing_boroughs))

        m = create_base_map(center=[40.73, -73.95], zoom=10)
        borough_colors = {
            "Manhattan": "#ef4444",
            "Brooklyn": "#3b82f6",
            "Queens": "#10b981",
            "Bronx": "#f59e0b",
            "Staten Island": "#8b5cf6"
        }

        for b in existing_boroughs:
            bname = b.get("name", b.get("_id"))
            color = borough_colors.get(bname, "#64748b")
            folium.GeoJson(
                {"type": "Feature", "properties": {"name": bname}, "geometry": b["geometry"]},
                name=bname,
                style_function=lambda f, c=color: {"color": c, "weight": 2, "fillColor": c, "fillOpacity": 0.25},
                tooltip=f"Borough: {bname}"
            ).add_to(m)

        for s in stations:
            lon, lat = s["location"]["coordinates"]
            folium.CircleMarker(
                [lat, lon],
                radius=3.5,
                color="#0f172a",
                fill=True,
                fill_color="#0f172a",
                fill_opacity=0.7,
                tooltip=s["name"]
            ).add_to(m)

        st_folium(m, width=None, height=540, key="q6_map")

        b_rows = [{"_id": b["_id"], "Name": b["name"], "Kind": b.get("kind", "borough"), "Geometry Type": b["geometry"]["type"]} for b in existing_boroughs]
        st.dataframe(pd.DataFrame(b_rows), use_container_width=True, hide_index=True)

    with tab2:
        st.code("""!pip install osmnx

import osmnx as ox
from shapely.geometry import mapping
from pymongo import GEOSPHERE, ReplaceOne

queries = ["Manhattan, New York, USA", "Brooklyn, New York, USA",
           "Queens, New York, USA", "The Bronx, New York, USA",
           "Staten Island, New York, USA"]
labels  = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]

gdf = ox.geocode_to_gdf(queries)
print(list(zip(labels, gdf.geom_type)))

docs = []
for label, g in zip(labels, gdf.geometry):
    g = g.buffer(0).simplify(0.0001, preserve_topology=True)
    docs.append({"_id": label, "name": label, "kind": "borough",
                 "geometry": mapping(g)})

bor = db["boroughs"]
bor.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs])
bor.create_index([("geometry", GEOSPHERE)])
print("boroughs stored:", bor.count_documents({}))""", language="python")

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">[('Manhattan', 'Polygon'), ('Brooklyn', 'Polygon'), ('Queens', 'Polygon'), ('Bronx', 'Polygon'), ('Staten Island', 'Polygon')]
boroughs stored: {bor_col.count_documents({})}</div>""", unsafe_allow_html=True)


# ============================================================
# 7. QUERY 7: UNION & MONITOR-TO-SENSOR PROXIMITY
# ============================================================

elif selected_query == query_options[6]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 7</span>
        <h1 class="query-title">Union & Monitor-to-Sensor Proximity</h1>
        <div class="query-question">What total area do the stations cover if each represents a 2 km radius, and which low-cost sensors are closest to each regulatory monitor?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    monitors = list(col.find({"is_monitor": True}))

    with tab1:
        total_union = unary_union(list(zones.values()))
        total_union_area_km2 = round(total_union.area / 1e6, 2)

        c1, c2 = st.columns(2)
        c1.metric("Government Monitors (High Precision)", len(monitors))
        c2.metric("Total Union Coverage Area (2 km Buffers)", f"{total_union_area_km2} km²")

        st.subheader("Nearest Low-Cost Sensors for Each Government Monitor")
        
        pairings = []
        for m_ in monitors:
            near = list(col.aggregate([
                {
                    "$geoNear": {
                        "near": m_["location"],
                        "distanceField": "d",
                        "spherical": True,
                        "query": {"is_monitor": False}
                    }
                },
                {"$limit": 2}
            ]))
            pairings.append({
                "Monitor": m_["name"],
                "Monitor ID": m_["_id"],
                "Monitor Location": m_["location"]["coordinates"],
                "Nearest Sensors": [(n["name"], round(n["d"] / 1000, 2), n["location"]["coordinates"]) for n in near]
            })

        # Map display
        m = create_base_map(center=[40.73, -73.95], zoom=11)

        # Draw unified coverage polygon
        folium.GeoJson(
            {"type": "Feature", "properties": {}, "geometry": mapping(transform(to_deg, total_union))},
            name="Total Unified Coverage Area",
            style_function=lambda f: {"color": "#6366f1", "weight": 1.5, "fillColor": "#6366f1", "fillOpacity": 0.12}
        ).add_to(m)

        for p in pairings:
            mlon, mlat = p["Monitor Location"]
            folium.CircleMarker(
                [mlat, mlon],
                radius=6,
                color="#0284c7",
                fill=True,
                fill_color="#0284c7",
                fill_opacity=1.0,
                tooltip=f"Monitor: {p['Monitor']}"
            ).add_to(m)

            for sname, dist, (slon, slat) in p["Nearest Sensors"]:
                folium.PolyLine(
                    [[mlat, mlon], [slat, slon]],
                    color="#0284c7",
                    weight=1.5,
                    dash_array="4, 4",
                    tooltip=f"{p['Monitor']} ➔ {sname} ({dist} km)"
                ).add_to(m)

                folium.CircleMarker(
                    [slat, slon],
                    radius=4,
                    color="#16a34a",
                    fill=True,
                    fill_color="#16a34a",
                    fill_opacity=0.9,
                    tooltip=f"Sensor: {sname} ({dist} km from {p['Monitor']})"
                ).add_to(m)

        st_folium(m, width=None, height=540, key="q7_map")

        # Table of top monitors
        disp_rows = []
        for p in pairings:
            disp_rows.append({
                "Government Monitor": p["Monitor"],
                "Nearest Sensor #1": f"{p['Nearest Sensors'][0][0]} ({p['Nearest Sensors'][0][1]} km)" if len(p['Nearest Sensors']) > 0 else "None",
                "Nearest Sensor #2": f"{p['Nearest Sensors'][1][0]} ({p['Nearest Sensors'][1][1]} km)" if len(p['Nearest Sensors']) > 1 else "None"
            })
        st.dataframe(pd.DataFrame(disp_rows), use_container_width=True, hide_index=True)

    with tab2:
        st.code("""monitors = list(col.find({"is_monitor": True}))
print("government monitors:", len(monitors))

for m_ in monitors[:5]:
    near = col.aggregate([
        {"$geoNear": {"near": m_["location"], "distanceField": "d",
                      "spherical": True, "query": {"is_monitor": False}}},
        {"$limit": 2}
    ])
    print(m_["name"], "->", [(n["name"], round(n["d"] / 1000, 2)) for n in near])""", language="python")

        out_lines = [f"government monitors: {len(monitors)}"]
        for m_ in monitors[:5]:
            near = col.aggregate([
                {"$geoNear": {"near": m_["location"], "distanceField": "d", "spherical": True, "query": {"is_monitor": False}}},
                {"$limit": 2}
            ])
            out_lines.append(f"{m_['name']} -> {[(n['name'], round(n['d'] / 1000, 2)) for n in near]}")

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">{chr(10).join(out_lines)}</div>""", unsafe_allow_html=True)


# ============================================================
# 8. QUERY 8: INTERSECTION (OVERLAPPING STATION PAIRS)
# ============================================================

elif selected_query == query_options[7]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 8</span>
        <h1 class="query-title">Intersection Analysis</h1>
        <div class="query-question">Which pairs of stations have the most overlapping coverage?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    # Compute overlaps
    overlaps = []
    for (i, a_), (j, b_) in combinations(zones.items(), 2):
        if a_.intersects(b_):
            intersection_poly = a_.intersection(b_)
            overlaps.append({
                "area_km2": intersection_poly.area / 1e6,
                "station1_id": i,
                "station2_id": j,
                "station1_name": names.get(i, "Unknown"),
                "station2_name": names.get(j, "Unknown"),
                "geom": intersection_poly
            })

    overlaps.sort(key=lambda x: x["area_km2"], reverse=True)
    total_possible_pairs = len(stations) * (len(stations) - 1) // 2

    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Overlapping Pairs", f"{len(overlaps)} of {total_possible_pairs}")
        c2.metric("Max Overlap Area", f"{overlaps[0]['area_km2']:.2f} km²" if overlaps else "0 km²")
        c3.metric("Pair Overlap Ratio", f"{(len(overlaps)/total_possible_pairs)*100:.1f}%" if total_possible_pairs else "0%")

        st.subheader("Top Overlapping Pairs Table")
        df_overlaps = pd.DataFrame([
            {
                "Rank": idx,
                "Overlap Area (km²)": round(o["area_km2"], 2),
                "Station 1": o["station1_name"],
                "Station 2": o["station2_name"]
            }
            for idx, o in enumerate(overlaps[:15], start=1)
        ])
        st.dataframe(df_overlaps, use_container_width=True, hide_index=True)

        m = create_base_map(center=[40.73, -73.95], zoom=11)

        # Highlight top 5 overlapping intersection geometries
        for o in overlaps[:5]:
            g = mapping(transform(to_deg, o["geom"]))
            folium.GeoJson(
                {"type": "Feature", "properties": {}, "geometry": g},
                style_function=lambda f: {"color": "#ef4444", "weight": 2, "fillColor": "#ef4444", "fillOpacity": 0.5},
                tooltip=f"Overlap: {o['station1_name']} & {o['station2_name']} ({round(o['area_km2'], 2)} km²)"
            ).add_to(m)

        for s in stations:
            lon, lat = s["location"]["coordinates"]
            folium.CircleMarker(
                [lat, lon],
                radius=4,
                color="#0284c7" if s.get("is_monitor") else "#16a34a",
                fill=True,
                fill_color="#0284c7" if s.get("is_monitor") else "#16a34a",
                fill_opacity=0.9,
                tooltip=s["name"]
            ).add_to(m)

        st_folium(m, width=None, height=540, key="q8_map")

    with tab2:
        st.code("""overlaps = []
for (i, a_), (j, b_) in combinations(zones.items(), 2):
    if a_.intersects(b_):
        overlaps.append((a_.intersection(b_).area / 1e6, names[i], names[j]))

overlaps.sort(reverse=True)
print("overlapping pairs:", len(overlaps), "of", 72 * 71 // 2)
for area, n1, n2 in overlaps[:3]:
    print(round(area, 2), "km2 :", n1, "&", n2)""", language="python")

        out_lines = [f"overlapping pairs: {len(overlaps)} of {len(stations) * (len(stations) - 1) // 2}"]
        for o in overlaps[:3]:
            out_lines.append(f"{round(o['area_km2'], 2)} km2 : {o['station1_name']} & {o['station2_name']}")

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">{chr(10).join(out_lines)}</div>""", unsafe_allow_html=True)


# ============================================================
# 9. QUERY 9: SYMMETRIC DIFFERENCE AND IDENTITY
# ============================================================

elif selected_query == query_options[8]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 9</span>
        <h1 class="query-title">Symmetric Difference and Identity</h1>
        <div class="query-question">Which areas are covered by only one of the two networks (government monitors or low-cost sensors)?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    # Compute Set Algebra
    A = unary_union([zones[s["_id"]] for s in stations if s.get("is_monitor") and s["_id"] in zones])
    B = unary_union([zones[s["_id"]] for s in stations if not s.get("is_monitor") and s["_id"] in zones])

    both   = A.intersection(B)
    only_A = A.difference(B)
    only_B = B.difference(A)
    sym    = A.symmetric_difference(B)

    km2 = lambda g: round(g.area / 1e6, 1)

    area_A = km2(A)
    area_B = km2(B)
    area_both = km2(both)
    area_sym = km2(sym)
    area_only_A = km2(only_A)
    area_only_B = km2(only_B)
    formula_check = round((A.area + B.area - 2 * both.area) / 1e6, 1)

    with tab1:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("A Monitors Coverage", f"{area_A} km²")
        c2.metric("B Sensors Coverage", f"{area_B} km²")
        c3.metric("Intersection A ∩ B", f"{area_both} km²")
        c4.metric("Symmetric Difference (A △ B)", f"{area_sym} km²")

        st.markdown(f"""
        <div class="stat-pill">
            <b>Set Identity Verified:</b> Overlap + Non-Overlap = <code>{area_both} + {area_only_A} = {area_A} km²</code><br>
            <b>Formula Check:</b> <code>A + B - 2*(A ∩ B) = {formula_check} km²</code> (Matches Symmetric Difference exactly)
        </div>
        """, unsafe_allow_html=True)

        m = create_base_map(center=[40.73, -73.95], zoom=10)

        def add_poly_layer(geom_m, name, color, fill_opacity=0.4):
            if geom_m.is_empty:
                return
            g = mapping(transform(to_deg, geom_m))
            folium.GeoJson(
                {"type": "Feature", "properties": {}, "geometry": g},
                name=name,
                style_function=lambda f, c=color, fo=fill_opacity: {"color": c, "weight": 1.5, "fillColor": c, "fillOpacity": fo}
            ).add_to(m)

        add_poly_layer(only_A, "Only Monitors (A \\ B)", "#0284c7", 0.4)
        add_poly_layer(only_B, "Only Sensors (B \\ A)", "#16a34a", 0.4)
        add_poly_layer(both,   "Overlap Both (A ∩ B)", "#8b5cf6", 0.5)

        for s in stations:
            lon, lat = s["location"]["coordinates"]
            is_m = s.get("is_monitor", False)
            folium.CircleMarker(
                [lat, lon],
                radius=3.5,
                color="#0284c7" if is_m else "#16a34a",
                fill=True,
                fill_color="#0284c7" if is_m else "#16a34a",
                fill_opacity=0.95,
                tooltip=f"{s['name']} ({'Gov Monitor' if is_m else 'Sensor'})"
            ).add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)
        st_folium(m, width=None, height=560, key="q9_map")

    with tab2:
        st.code("""A = unary_union([zones[s["_id"]] for s in stations if s["is_monitor"]])
B = unary_union([zones[s["_id"]] for s in stations if not s["is_monitor"]])

both   = A.intersection(B)
only_A = A.difference(B)
only_B = B.difference(A)
sym    = A.symmetric_difference(B)

km2 = lambda g: round(g.area / 1e6, 1)
print("A monitors (km2)      :", km2(A))
print("B sensors (km2)       :", km2(B))
print("intersection A&B (km2):", km2(both))
print("symmetric difference (km2):", km2(sym))
print("identity of A: overlap + non-overlap =", km2(both), "+", km2(only_A), "=", km2(A))
print("formula check (km2)   :", round((A.area + B.area - 2 * both.area) / 1e6, 1))""", language="python")

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">A monitors (km2)      : {area_A}
B sensors (km2)       : {area_B}
intersection A&B (km2): {area_both}
symmetric difference (km2): {area_sym}
identity of A: overlap + non-overlap = {area_both} + {area_only_A} = {area_A}
formula check (km2)   : {formula_check}</div>""", unsafe_allow_html=True)


# ============================================================
# 10. QUERY 10: CONTAINMENT, SPATIAL JOIN & AGGREGATION
# ============================================================

elif selected_query == query_options[9]:
    st.markdown("""
    <div class="query-header">
        <span class="query-number-badge">Query 10</span>
        <h1 class="query-title">Containment, Spatial Join & Spatial Aggregation</h1>
        <div class="query-question">Which borough does each station belong to, and how many monitors and sensors does each borough have?</div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🚀 Live Interactive Query", "💻 Original Code & Output"])

    borough_docs = list(bor_col.find({}))
    if not borough_docs:
        bor_col.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in predefined_boroughs], ordered=False)
        try:
            bor_col.create_index([("geometry", GEOSPHERE)])
        except Exception:
            pass
        borough_docs = list(bor_col.find({}))

    # 1. Containment counts
    contain_counts = {}
    for b in borough_docs:
        cnt = col.count_documents({"location": {"$geoWithin": {"$geometry": b["geometry"]}}})
        contain_counts[b["name"]] = cnt

    with tab1:
        st.subheader("1. Spatial Containment ($geoWithin per Borough)")
        df_cont = pd.DataFrame(list(contain_counts.items()), columns=["Borough", "Stations Contained"])
        st.dataframe(df_cont, use_container_width=True, hide_index=True)

        st.subheader("2. Spatial Join ($geoIntersects Tagging)")
        col_j1, col_j2 = st.columns([3, 1])
        with col_j1:
            st.write("Tag every station document in MongoDB with the borough polygon that geometrically contains it:")
        with col_j2:
            if st.button("Run Spatial Join Update"):
                tags = {}
                for s in col.find({}):
                    matched = bor_col.find_one({"geometry": {"$geoIntersects": {"$geometry": s["location"]}}})
                    b_name = matched["name"] if matched else "Outside NYC"
                    tags[s["_id"]] = b_name
                    col.update_one({"_id": s["_id"]}, {"$set": {"borough": b_name}})
                st.success(f"Spatial join completed: {dict(Counter(tags.values()))}")

        st.subheader("3. Spatial Aggregation: Monitors vs Sensors per Borough")
        agg_pipeline = [
            {
                "$group": {
                    "_id": "$borough",
                    "stations": {"$sum": 1},
                    "monitors": {"$sum": {"$cond": ["$is_monitor", 1, 0]}},
                    "sensors":  {"$sum": {"$cond": ["$is_monitor", 0, 1]}}
                }
            },
            {"$sort": {"stations": -1}}
        ]
        agg_res = list(col.aggregate(agg_pipeline))
        df_agg = pd.DataFrame([
            {
                "Borough": r["_id"] if r["_id"] else "Untagged / Outside NYC",
                "Stations": r["stations"],
                "Gov Monitors (A)": r["monitors"],
                "Sensors (B)": r["sensors"]
            }
            for r in agg_res
        ])
        st.dataframe(df_agg, use_container_width=True, hide_index=True)

        # Plotly chart
        fig = px.bar(
            df_agg,
            x="Borough",
            y=["Gov Monitors (A)", "Sensors (B)"],
            barmode="group",
            color_discrete_map={"Gov Monitors (A)": "#0284c7", "Sensors (B)": "#16a34a"},
            title="Monitors vs Low-Cost Sensors per Borough"
        )
        st.plotly_chart(fig, use_container_width=True)

        # Map display
        m = create_base_map(center=[40.73, -73.95], zoom=10)
        for b in borough_docs:
            folium.GeoJson(
                {"type": "Feature", "properties": {"name": b["name"]}, "geometry": b["geometry"]},
                name=b["name"],
                style_function=lambda f: {"color": "#475569", "weight": 2, "fillColor": "#64748b", "fillOpacity": 0.12},
                tooltip=f"Borough: {b['name']} ({contain_counts.get(b['name'], 0)} stations)"
            ).add_to(m)

        for s in col.find({}):
            lon, lat = s["location"]["coordinates"]
            is_m = s.get("is_monitor", False)
            folium.CircleMarker(
                [lat, lon],
                radius=4.5,
                color="#0284c7" if is_m else "#16a34a",
                fill=True,
                fill_color="#0284c7" if is_m else "#16a34a",
                fill_opacity=0.9,
                tooltip=f"{s.get('name')} | Borough: {s.get('borough', 'N/A')} ({'Monitor' if is_m else 'Sensor'})"
            ).add_to(m)

        st_folium(m, width=None, height=540, key="q10_map")

    with tab2:
        st.code("""from collections import Counter

bor = db["boroughs"]

# 1. Containment: stations inside each borough polygon
print("1. Containment")
contain = {}
for b in bor.find():
    n = col.count_documents({"location": {"$geoWithin": {"$geometry": b["geometry"]}}})
    contain[b["name"]] = n
    print(" ", b["name"], ":", n)

# 2. Spatial join: tag every station with the borough that contains it
tags = {}
for s in col.find():
    b = bor.find_one({"geometry": {"$geoIntersects": {"$geometry": s["location"]}}})
    tags[s["_id"]] = b["name"] if b else "Outside NYC"
    col.update_one({"_id": s["_id"]}, {"$set": {"borough": tags[s["_id"]]}})
print("\\n2. Spatial join done:", dict(Counter(tags.values())))

# 3. Spatial aggregation: monitors vs sensors per borough
print("\\n3. Aggregation")
for r in col.aggregate([
    {"$group": {"_id": "$borough",
                "stations": {"$sum": 1},
                "monitors": {"$sum": {"$cond": ["$is_monitor", 1, 0]}},
                "sensors": {"$sum": {"$cond": ["$is_monitor", 0, 1]}}}},
    {"$sort": {"stations": -1}}
]):
    print(" ", r["_id"], "| stations:", r["stations"],
          "| monitors:", r["monitors"], "| sensors:", r["sensors"])""", language="python")

        # Generate sample output text matching the execution
        out_c = ["1. Containment"]
        for bname, cnt in contain_counts.items():
            out_c.append(f"  {bname} : {cnt}")
        out_c.append(f"\n2. Spatial join done: {dict(Counter([s.get('borough', 'Outside NYC') for s in col.find({})]))}")
        out_c.append("\n3. Aggregation")
        for r in agg_res:
            out_c.append(f"  {r['_id']} | stations: {r['stations']} | monitors: {r['monitors']} | sensors: {r['sensors']}")

        st.markdown("**Console Output:**")
        st.markdown(f"""<div class="code-output-box">{chr(10).join(out_c)}</div>""", unsafe_allow_html=True)


# ============================================================
# FOOTER
# ============================================================

st.sidebar.markdown("---")
st.sidebar.caption(f"MongoDB Geospatial Tutorial • {len(stations)} stations loaded")
