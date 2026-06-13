"""
ZLearn Real-Time Dashboard
Real-time monitoring of batch enrollments and session attendance
"""

import streamlit as st
import pandas as pd
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh
import os

# -----------------------------------------------------------------------------
# Page Configuration
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="ZLearn Real-Time Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Auto-refresh every 30 seconds
count = st_autorefresh(interval=30000, key="dashboard_refresh")

# -----------------------------------------------------------------------------
# MongoDB Connection
# -----------------------------------------------------------------------------
@st.cache_resource
def get_mongo_client():
    """Get MongoDB client - cached so we don't reconnect on every refresh."""
    mongo_uri = st.secrets.get("MONGO_URI", os.getenv("MONGO_URI"))
    return MongoClient(mongo_uri)

def get_db():
    """Get database instance."""
    client = get_mongo_client()
    db_name = st.secrets.get("DB_NAME", os.getenv("DB_NAME", "nism-platform"))
    return client[db_name]

# -----------------------------------------------------------------------------
# Data Fetching Functions (cached for performance)
# -----------------------------------------------------------------------------

@st.cache_data(ttl=30)  # Cache for 30 seconds
def fetch_categories():
    """Fetch all batch categories. Uses 'name' field if available, otherwise 'title' field."""
    db = get_db()
    categories = list(db.batchcategories.find(
        {},
        {"_id": 1, "name": 1, "title": 1}
    ))
    
    # Build category list using name first, fallback to title
    cat_list = []
    for c in categories:
        # Get the best name: name -> title -> Unknown
        cat_name = c.get('name') or c.get('title') or 'Unknown'
        
        # Skip testing categories
        if 'testing' in cat_name.lower():
            continue
        
        cat_list.append({
            'category_id': str(c['_id']),
            'category_name': cat_name
        })
    
    return pd.DataFrame(cat_list)

@st.cache_data(ttl=30)
def fetch_batches():
    """Fetch all batches with their categories."""
    db = get_db()
    
    pipeline = [
        # Filter out batches without batchCategoryId (must have a valid value)
        {
            "$match": {
                "batchCategoryId": {"$exists": True, "$nin": [None, ""]}
            }
        },
        # Convert string batchCategoryId to ObjectId (only if non-empty string)
        {
            "$addFields": {
                "batchCategoryIdObj": {
                    "$cond": {
                        "if": {
                            "$and": [
                                {"$eq": [{"$type": "$batchCategoryId"}, "string"]},
                                {"$ne": ["$batchCategoryId", ""]}
                            ]
                        },
                        "then": {"$toObjectId": "$batchCategoryId"},
                        "else": "$batchCategoryId"
                    }
                }
            }
        },
        # Lookup category info
        {
            "$lookup": {
                "from": "batchcategories",
                "localField": "batchCategoryIdObj",
                "foreignField": "_id",
                "as": "categoryInfo"
            }
        },
        {"$unwind": {"path": "$categoryInfo", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                "_id": 1,
                "name": 1,
                "startDate": 1,
                "endDate": 1,
                "isActive": 1,
                "batchCategoryId": {"$toString": "$batchCategoryId"},
                # Use 'name' first, fall back to 'title' if name doesn't exist
                "categoryName": {
                    "$ifNull": [
                        "$categoryInfo.name",
                        "$categoryInfo.title"
                    ]
                }
            }
        }
    ]
    
    batches = list(db.batches.aggregate(pipeline))
    
    df = pd.DataFrame([
        {
            'batch_id': str(b['_id']),
            'batch_name': b.get('name', 'Unknown'),
            'category_id': b.get('batchCategoryId', ''),
            'category_name': b.get('categoryName') if b.get('categoryName') else 'Unknown',
            'start_date': pd.to_datetime(b.get("startDate"), errors="coerce", dayfirst=True),
            'end_date': pd.to_datetime(b.get("endDate"), errors="coerce", dayfirst=True),
            'is_active': b.get('isActive', False)
        }
        for b in batches
    ])
    
    # Filter out testing categories (matches both name and title containing 'testing')
    if not df.empty:
        df = df[~df['category_name'].fillna('').str.contains('testing', case=False, na=False)]
    
    return df

@st.cache_data(ttl=30)
def fetch_enrollment_counts():
    """
    Get enrollment counts per batch.
    
    IMPORTANT: This does NOT deduplicate users across batches.
    If a user is enrolled in Batch 14 AND Batch 15, they count in BOTH batches.
    This shows ACTUAL enrollment counts, not unique user assignments.
    """
    db = get_db()
    
    pipeline = [
        {
            "$group": {
                "_id": "$batch_id",
                "total_enrollments": {"$sum": 1},
                "active_enrollments": {
                    "$sum": {"$cond": [{"$eq": ["$isActive", True]}, 1, 0]}
                },
                "unique_users": {"$addToSet": "$user_id"}
            }
        },
        {
            "$project": {
                "total_enrollments": 1,
                "active_enrollments": 1,
                "unique_user_count": {"$size": "$unique_users"}
            }
        }
    ]
    
    counts = list(db.enrollments.aggregate(pipeline))
    
    return pd.DataFrame([
        {
            'batch_id': str(c['_id']),
            'total_enrollments': c['total_enrollments'],
            'active_enrollments': c['active_enrollments'],
            'unique_user_count': c.get('unique_user_count', c['total_enrollments'])
        }
        for c in counts
    ])

@st.cache_data(ttl=30)
def fetch_sessions(batch_id=None):
    """Fetch sessions, optionally filtered by batch."""
    db = get_db()
    
    query = {}
    if batch_id:
        try:
            query["batch_id"] = ObjectId(batch_id)
        except:
            query["batch_id"] = batch_id
    
    sessions = list(db.batchsessions.find(
        query,
        {"_id": 1, "batch_id": 1, "title": 1, "start_date": 1, "end_date": 1}
    ).sort("start_date", 1))
    
    return pd.DataFrame([
        {
            'session_id': str(s['_id']),
            'batch_id': str(s['batch_id']),
            'title': s.get('title', 'Unknown'),
            'start_date': pd.to_datetime(s.get("start_date"), errors="coerce", dayfirst=True),
            'end_date': pd.to_datetime(s.get("end_date"), errors="coerce", dayfirst=True)
        }
        for s in sessions
    ])

@st.cache_data(ttl=30)
def fetch_session_attendance(batch_id=None):
    """
    Get attendance counts per session.
    
    IMPORTANT: Counts ACTUAL attendance entries.
    If a user attended sessions in both Batch 14 and Batch 15, 
    they're counted separately in each batch's sessions.
    """
    db = get_db()
    
    match_stage = {}
    if batch_id:
        try:
            match_stage["batch_id"] = ObjectId(batch_id)
        except:
            match_stage["batch_id"] = batch_id
    
    pipeline = []
    if match_stage:
        pipeline.append({"$match": match_stage})
    
    pipeline.append({
        "$group": {
            "_id": "$session_id",
            "attendance_count": {"$sum": 1}
        }
    })
    
    counts = list(db.userbatchsessions.aggregate(pipeline))
    
    return pd.DataFrame([
        {
            'session_id': str(c['_id']),
            'attendance_count': c['attendance_count']
        }
        for c in counts
    ])

@st.cache_data(ttl=30)
def fetch_session_users(session_id):
    """Get list of users who attended a specific session."""
    db = get_db()
    
    try:
        session_obj_id = ObjectId(session_id)
    except:
        session_obj_id = session_id
    
    pipeline = [
        {"$match": {"session_id": session_obj_id}},
        {
            "$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "_id",
                "as": "userInfo"
            }
        },
        {"$unwind": "$userInfo"},
        {
            "$project": {
                "user_id": {"$toString": "$user_id"},
                "userName": "$userInfo.name",
                "mobileNumber": "$userInfo.mobileNumber",
                "email": "$userInfo.email",
                "joined_at": "$joined_at"
            }
        }
    ]
    
    users = list(db.userbatchsessions.aggregate(pipeline))
    
    return pd.DataFrame([
        {
            'user_id': u['user_id'],
            'name': u.get('userName', 'Unknown'),
            'mobile': u.get('mobileNumber', ''),
            'email': u.get('email', ''),
            'attended_at': pd.to_datetime(u.get("joined_at"), errors="coerce", dayfirst=True)
        }
        for u in users
    ])

@st.cache_data(ttl=30)
def fetch_batch_enrolled_users(batch_id):
    """Get list of users enrolled in a specific batch."""
    db = get_db()
    
    try:
        batch_obj_id = ObjectId(batch_id)
    except:
        batch_obj_id = batch_id
    
    pipeline = [
        {"$match": {"batch_id": batch_obj_id}},
        {
            "$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "_id",
                "as": "userInfo"
            }
        },
        {"$unwind": "$userInfo"},
        {
            "$project": {
                "user_id": {"$toString": "$user_id"},
                "userName": "$userInfo.name",
                "mobileNumber": "$userInfo.mobileNumber",
                "email": "$userInfo.email",
                "joined_on": 1,
                "isActive": 1
            }
        }
    ]
    
    users = list(db.enrollments.aggregate(pipeline))
    
    return pd.DataFrame([
        {
            'user_id': u['user_id'],
            'name': u.get('userName', 'Unknown'),
            'mobile': u.get('mobileNumber', ''),
            'email': u.get('email', ''),
            'enrolled_at': pd.to_datetime(u.get("joined_on"), errors="coerce", dayfirst=True),
            'is_active': u.get('isActive', False)
        }
        for u in users
    ])

# -----------------------------------------------------------------------------
# Dashboard UI
# -----------------------------------------------------------------------------

def main():
    # Header
    st.title("📊 ZLearn Real-Time Dashboard")
    st.markdown("*Auto-refreshes every 30 seconds*")
    
    # Sidebar
    with st.sidebar:
        st.image("https://www.zfunds.in/static/images/zfunds-logo.svg", width=150)
        st.markdown("### 🔄 Refresh Info")
        st.info(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")
        st.markdown(f"**Refresh count:** {count}")
        
        if st.button("🔄 Refresh Now", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        
        st.markdown("---")
        st.markdown("### 🎯 Navigation")
        view = st.radio(
            "Select View:",
            ["🏠 Overview", "📦 Batches", "🎓 Sessions", "👥 User Details"],
            label_visibility="collapsed"
        )
    
    # Load data
    with st.spinner("Loading data..."):
        categories_df = fetch_categories()
        batches_df = fetch_batches()
        enrollment_counts_df = fetch_enrollment_counts()
    
    # Merge enrollment counts with batches
    if not batches_df.empty and not enrollment_counts_df.empty:
        batches_df = batches_df.merge(
            enrollment_counts_df,
            on='batch_id',
            how='left'
        )
        batches_df['total_enrollments'] = batches_df['total_enrollments'].fillna(0).astype(int)
        batches_df['active_enrollments'] = batches_df['active_enrollments'].fillna(0).astype(int)
        batches_df['unique_user_count'] = batches_df['unique_user_count'].fillna(0).astype(int)
    else:
        batches_df['total_enrollments'] = 0
        batches_df['active_enrollments'] = 0
        batches_df['unique_user_count'] = 0
    
    # =========================================================================
    # OVERVIEW PAGE
    # =========================================================================
    if view == "🏠 Overview":
        st.markdown("## 📈 Category Overview")
        
        # Top-level metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Categories", len(categories_df))
        with col2:
            st.metric("Total Batches", len(batches_df))
        with col3:
            total_enr = batches_df['total_enrollments'].sum() if not batches_df.empty else 0
            st.metric("Total Enrollments", f"{total_enr:,}")
        with col4:
            active_enr = batches_df['active_enrollments'].sum() if not batches_df.empty else 0
            st.metric("Active Enrollments", f"{active_enr:,}")
        
        st.markdown("---")
        
        # Category-wise enrollment summary
        if not batches_df.empty:
            cat_summary = batches_df.groupby('category_name').agg(
                total_batches=('batch_id', 'count'),
                total_enrollments=('total_enrollments', 'sum'),
                active_enrollments=('active_enrollments', 'sum')
            ).reset_index().sort_values('total_enrollments', ascending=False)
            
            # Two columns: Chart and Table
            col_chart, col_table = st.columns([3, 2])
            
            with col_chart:
                st.markdown("### 📊 Enrollments by Category")
                fig = px.bar(
                    cat_summary,
                    x='category_name',
                    y='total_enrollments',
                    color='category_name',
                    title="Total Enrollments per Category",
                    labels={'total_enrollments': 'Enrollments', 'category_name': 'Category'},
                    height=400
                )
                fig.update_layout(showlegend=False, xaxis_tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)
            
            with col_table:
                st.markdown("### 📋 Category Summary")
                st.dataframe(
                    cat_summary,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        'category_name': 'Category',
                        'total_batches': st.column_config.NumberColumn('Batches'),
                        'total_enrollments': st.column_config.NumberColumn('Total Enrollments'),
                        'active_enrollments': st.column_config.NumberColumn('Active'),
                    }
                )
            
            # Pie chart
            st.markdown("### 🥧 Enrollment Distribution")
            fig_pie = px.pie(
                cat_summary,
                values='total_enrollments',
                names='category_name',
                title="Distribution of Enrollments Across Categories",
                height=500
            )
            st.plotly_chart(fig_pie, use_container_width=True)
    
    # =========================================================================
    # BATCHES PAGE
    # =========================================================================
    elif view == "📦 Batches":
        st.markdown("## 📦 Batch-wise Enrollment")
        
        # Filter by category
        col1, col2 = st.columns([2, 1])
        with col1:
            category_filter = st.selectbox(
                "Filter by Category:",
                options=["All Categories"] + sorted(batches_df['category_name'].dropna().unique().tolist()),
                key="batch_cat_filter"
            )
        with col2:
            sort_by = st.selectbox(
                "Sort by:",
                options=["Total Enrollments", "Active Enrollments", "Batch Name", "Start Date"],
                key="batch_sort"
            )
        
        # Apply filter
        filtered_batches = batches_df.copy()
        if category_filter != "All Categories":
            filtered_batches = filtered_batches[filtered_batches['category_name'] == category_filter]
        
        # Apply sort
        sort_map = {
            "Total Enrollments": ("total_enrollments", False),
            "Active Enrollments": ("active_enrollments", False),
            "Batch Name": ("batch_name", True),
            "Start Date": ("start_date", False)
        }
        sort_col, ascending = sort_map[sort_by]
        filtered_batches = filtered_batches.sort_values(sort_col, ascending=ascending)
        
        # Bar chart
        if not filtered_batches.empty:
            st.markdown("### 📊 Enrollment Counts per Batch")
            fig = px.bar(
                filtered_batches.head(20),
                x='batch_name',
                y=['total_enrollments', 'active_enrollments'],
                title=f"Top 20 Batches{' in ' + category_filter if category_filter != 'All Categories' else ''}",
                labels={'value': 'Enrollments', 'batch_name': 'Batch', 'variable': 'Type'},
                barmode='group',
                height=500
            )
            fig.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            
            # Table
            st.markdown("### 📋 Detailed Batch Information")
            display_df = filtered_batches[[
                'category_name', 'batch_name', 'start_date', 'end_date',
                'total_enrollments', 'active_enrollments', 'is_active'
            ]].copy()
            
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    'category_name': 'Category',
                    'batch_name': 'Batch',
                    'start_date': st.column_config.DateColumn('Start Date'),
                    'end_date': st.column_config.DateColumn('End Date'),
                    'total_enrollments': st.column_config.NumberColumn('Total'),
                    'active_enrollments': st.column_config.NumberColumn('Active'),
                    'is_active': st.column_config.CheckboxColumn('Active'),
                }
            )
    
    # =========================================================================
    # SESSIONS PAGE
    # =========================================================================
    elif view == "🎓 Sessions":
        st.markdown("## 🎓 Session-wise Attendance")
        
        # Filters
        col1, col2 = st.columns(2)
        with col1:
            category_filter = st.selectbox(
                "Select Category:",
                options=sorted(batches_df['category_name'].dropna().unique().tolist()),
                key="session_cat_filter"
            )
        
        with col2:
            # Filter batches by category
            cat_batches = batches_df[batches_df['category_name'] == category_filter]
            batch_options = cat_batches[['batch_id', 'batch_name']].drop_duplicates()
            
            if not batch_options.empty:
                batch_filter = st.selectbox(
                    "Select Batch:",
                    options=batch_options['batch_id'].tolist(),
                    format_func=lambda x: batch_options[batch_options['batch_id'] == x]['batch_name'].iloc[0],
                    key="session_batch_filter"
                )
            else:
                batch_filter = None
                st.warning("No batches in this category")
        
        if batch_filter:
            # Fetch sessions and attendance for selected batch
            sessions_df = fetch_sessions(batch_filter)
            attendance_df = fetch_session_attendance(batch_filter)
            
            if not sessions_df.empty:
                # Merge with attendance
                sessions_df = sessions_df.merge(attendance_df, on='session_id', how='left')
                sessions_df['attendance_count'] = sessions_df['attendance_count'].fillna(0).astype(int)
                
                # Get total enrolled in batch
                batch_info = batches_df[batches_df['batch_id'] == batch_filter].iloc[0]
                total_enrolled = batch_info['total_enrollments']
                
                # Calculate attendance percentage
                sessions_df['attendance_pct'] = (
                    sessions_df['attendance_count'] / total_enrolled * 100
                    if total_enrolled > 0 else 0
                ).round(1)
                
                # Metrics
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Sessions", len(sessions_df))
                with col2:
                    st.metric("Total Enrolled", total_enrolled)
                with col3:
                    avg_att = sessions_df['attendance_count'].mean() if not sessions_df.empty else 0
                    st.metric("Avg Attendance", f"{avg_att:.0f}")
                with col4:
                    avg_pct = sessions_df['attendance_pct'].mean() if not sessions_df.empty else 0
                    st.metric("Avg Attendance %", f"{avg_pct:.1f}%")
                
                st.markdown("---")
                
                # Add session number
                sessions_df = sessions_df.reset_index(drop=True)
                sessions_df['session_num'] = sessions_df.index + 1
                sessions_df['session_label'] = "Session " + sessions_df['session_num'].astype(str)
                
                # Chart
                st.markdown("### 📊 Attendance per Session")
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=sessions_df['session_label'],
                    y=sessions_df['attendance_count'],
                    name='Attendance',
                    marker_color='lightblue',
                    text=sessions_df['attendance_count'],
                    textposition='outside'
                ))
                fig.add_trace(go.Scatter(
                    x=sessions_df['session_label'],
                    y=[total_enrolled] * len(sessions_df),
                    name='Total Enrolled',
                    line=dict(color='red', dash='dash')
                ))
                fig.update_layout(
                    title=f"Attendance for {batch_info['batch_name']}",
                    xaxis_title="Session",
                    yaxis_title="Number of Attendees",
                    height=500
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # Table
                st.markdown("### 📋 Session Details")
                
                # Filter and Sort options for Sessions
                st.markdown("#### 🔍 Filter & Sort Sessions")
                col_f1, col_f2, col_f3 = st.columns(3)
                
                with col_f1:
                    session_search = st.text_input(
                        "Search session by name:",
                        placeholder="Type session title...",
                        key="session_search"
                    )
                
                with col_f2:
                    session_sort_by = st.selectbox(
                        "Sort by:",
                        options=["Session #", "Session Title", "Date", "Attendees (High to Low)", "Attendees (Low to High)", "Attendance % (High to Low)", "Attendance % (Low to High)"],
                        key="session_sort"
                    )
                
                with col_f3:
                    min_attendance = st.number_input(
                        "Min attendance count:",
                        min_value=0,
                        value=0,
                        step=1,
                        key="min_attendance_filter"
                    )
                
                # Apply filters
                filtered_sessions = sessions_df.copy()
                if session_search:
                    filtered_sessions = filtered_sessions[
                        filtered_sessions['title'].str.contains(session_search, case=False, na=False)
                    ]
                if min_attendance > 0:
                    filtered_sessions = filtered_sessions[
                        filtered_sessions['attendance_count'] >= min_attendance
                    ]
                
                # Apply sort
                session_sort_map = {
                    "Session #": ("session_num", True),
                    "Session Title": ("title", True),
                    "Date": ("start_date", True),
                    "Attendees (High to Low)": ("attendance_count", False),
                    "Attendees (Low to High)": ("attendance_count", True),
                    "Attendance % (High to Low)": ("attendance_pct", False),
                    "Attendance % (Low to High)": ("attendance_pct", True),
                }
                sort_col, ascending = session_sort_map[session_sort_by]
                filtered_sessions = filtered_sessions.sort_values(sort_col, ascending=ascending)
                
                st.info(f"Showing {len(filtered_sessions)} of {len(sessions_df)} sessions")
                
                display_df = filtered_sessions[[
                    'session_num', 'title', 'start_date',
                    'attendance_count', 'attendance_pct'
                ]].copy()
                
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        'session_num': st.column_config.NumberColumn('#'),
                        'title': 'Session Title',
                        'start_date': st.column_config.DatetimeColumn('Date'),
                        'attendance_count': st.column_config.NumberColumn('Attendees'),
                        'attendance_pct': st.column_config.NumberColumn('Attendance %', format="%.1f%%"),
                    }
                )
                
                # Download sessions
                sessions_csv = display_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download Sessions as CSV",
                    data=sessions_csv,
                    file_name=f"sessions_{batch_info['batch_name'].replace(' ', '_')}.csv",
                    mime="text/csv",
                    key="download_sessions"
                )
                
                # Click to see users
                st.markdown("---")
                st.markdown("### 👥 View Session Attendees")
                
                # Use filtered sessions for the dropdown
                if not filtered_sessions.empty:
                    selected_session = st.selectbox(
                        "Select session to view attendees:",
                        options=filtered_sessions['session_id'].tolist(),
                        format_func=lambda x: f"Session {filtered_sessions[filtered_sessions['session_id']==x]['session_num'].iloc[0]} - {filtered_sessions[filtered_sessions['session_id']==x]['title'].iloc[0]}"
                    )
                else:
                    selected_session = None
                    st.info("No sessions match your filters")
                
                if selected_session:
                    session_users = fetch_session_users(selected_session)
                    
                    if not session_users.empty:
                        # Filter and Sort options for Attendees
                        st.markdown("#### 🔍 Filter & Sort Attendees")
                        col_u1, col_u2, col_u3 = st.columns(3)
                        
                        with col_u1:
                            user_search = st.text_input(
                                "Search by name, mobile, email:",
                                placeholder="Type to search...",
                                key="attendee_search"
                            )
                        
                        with col_u2:
                            user_sort_by = st.selectbox(
                                "Sort attendees by:",
                                options=["Name (A-Z)", "Name (Z-A)", "Attended Time (Newest)", "Attended Time (Oldest)", "Mobile"],
                                key="attendee_sort"
                            )
                        
                        with col_u3:
                            # Date filter
                            date_filter = st.selectbox(
                                "Filter by date:",
                                options=["All time", "Today", "Last 7 days", "Last 30 days"],
                                key="attendee_date_filter"
                            )
                        
                        # Apply filters
                        filtered_users = session_users.copy()
                        
                        # Search filter
                        if user_search:
                            filtered_users = filtered_users[
                                filtered_users['name'].str.contains(user_search, case=False, na=False) |
                                filtered_users['mobile'].astype(str).str.contains(user_search, case=False, na=False) |
                                filtered_users['email'].str.contains(user_search, case=False, na=False)
                            ]
                        
                        # Date filter
                        if date_filter != "All time" and 'attended_at' in filtered_users.columns:
                            now = pd.Timestamp.now(tz='UTC')
                            if date_filter == "Today":
                                cutoff = now.normalize()
                            elif date_filter == "Last 7 days":
                                cutoff = now - pd.Timedelta(days=7)
                            elif date_filter == "Last 30 days":
                                cutoff = now - pd.Timedelta(days=30)
                            
                            # Ensure attended_at is timezone-aware for comparison
                            attended_at_tz = pd.to_datetime(filtered_users['attended_at'], utc=True, errors='coerce')
                            filtered_users = filtered_users[attended_at_tz >= cutoff]
                        
                        # Apply sort
                        user_sort_map = {
                            "Name (A-Z)": ("name", True),
                            "Name (Z-A)": ("name", False),
                            "Attended Time (Newest)": ("attended_at", False),
                            "Attended Time (Oldest)": ("attended_at", True),
                            "Mobile": ("mobile", True),
                        }
                        sort_col, ascending = user_sort_map[user_sort_by]
                        filtered_users = filtered_users.sort_values(sort_col, ascending=ascending)
                        
                        st.info(f"Showing {len(filtered_users)} of {len(session_users)} attendees")
                        
                        st.dataframe(
                            filtered_users,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                'name': 'Name',
                                'mobile': 'Mobile',
                                'email': 'Email',
                                'attended_at': st.column_config.DatetimeColumn('Attended At'),
                            }
                        )
                        
                        # Download attendees
                        attendees_csv = filtered_users.to_csv(index=False)
                        session_title = filtered_sessions[filtered_sessions['session_id']==selected_session]['title'].iloc[0]
                        st.download_button(
                            label="📥 Download Attendees as CSV",
                            data=attendees_csv,
                            file_name=f"attendees_{session_title.replace(' ', '_')}.csv",
                            mime="text/csv",
                            key="download_attendees"
                        )
                    else:
                        st.info("No attendees yet for this session")
            else:
                st.warning("No sessions found for this batch")
    
    # =========================================================================
    # USER DETAILS PAGE
    # =========================================================================
    elif view == "👥 User Details":
        st.markdown("## 👥 User Details by Batch")
        
        # Filters
        col1, col2 = st.columns(2)
        with col1:
            category_filter = st.selectbox(
                "Select Category:",
                options=sorted(batches_df['category_name'].dropna().unique().tolist()),
                key="user_cat_filter"
            )
        
        with col2:
            cat_batches = batches_df[batches_df['category_name'] == category_filter]
            batch_options = cat_batches[['batch_id', 'batch_name']].drop_duplicates()
            
            if not batch_options.empty:
                batch_filter = st.selectbox(
                    "Select Batch:",
                    options=batch_options['batch_id'].tolist(),
                    format_func=lambda x: batch_options[batch_options['batch_id'] == x]['batch_name'].iloc[0],
                    key="user_batch_filter"
                )
            else:
                batch_filter = None
        
        if batch_filter:
            # Fetch enrolled users
            users_df = fetch_batch_enrolled_users(batch_filter)
            
            if not users_df.empty:
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Users", len(users_df))
                with col2:
                    active_count = users_df['is_active'].sum() if 'is_active' in users_df.columns else 0
                    st.metric("Active", int(active_count))
                with col3:
                    st.metric("Inactive", len(users_df) - int(active_count))
                
                st.markdown("---")
                
                # Search
                search = st.text_input("🔍 Search users (name, mobile, email):", "")
                if search:
                    users_df = users_df[
                        users_df['name'].str.contains(search, case=False, na=False) |
                        users_df['mobile'].astype(str).str.contains(search, case=False, na=False) |
                        users_df['email'].str.contains(search, case=False, na=False)
                    ]
                
                st.dataframe(
                    users_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        'name': 'Name',
                        'mobile': 'Mobile',
                        'email': 'Email',
                        'enrolled_at': st.column_config.DatetimeColumn('Enrolled At'),
                        'is_active': st.column_config.CheckboxColumn('Active'),
                    }
                )
                
                # Download
                csv = users_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download as CSV",
                    data=csv,
                    file_name=f"users_{batch_filter}.csv",
                    mime="text/csv"
                )
            else:
                st.info("No users enrolled in this batch")


if __name__ == "__main__":
    main()
