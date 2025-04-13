import streamlit as st
import pandas as pd
import hashlib
import os
from typing import List, Dict
from supabase import create_client, Client
from datetime import datetime

# 必须在最前面设置页面配置
st.set_page_config(layout="wide", page_title="游戏组队系统")

# ========================
# 配置部分
# ========================
# Supabase配置
SUPABASE_URL = os.getenv('SUPABASE_URL', st.secrets["SUPABASE_URL"])
SUPABASE_KEY = os.getenv('SUPABASE_KEY', st.secrets["SUPABASE_KEY"])
ADMIN_PASSWORD_HASH = hashlib.sha256(st.secrets["ADMIN_PASSWORD"].encode()).hexdigest()

# 初始化Supabase客户端
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 游戏职业列表
GAME_CLASSES = [
    '大理', '峨眉', '丐帮', '明教', '天山',
    '无尘', '武当', '逍遥', '星宿', '玄机'
]

# 组队配置
MIN_TEAM_MEMBERS = 3  # 最少需要3人组队
MAX_TEAM_MEMBERS = 6  # 最多6人组队

# ========================
# Supabase 数据操作模块
# ========================
def load_players() -> pd.DataFrame:
    """从Supabase加载玩家数据（按display_id排序）"""
    try:
        response = supabase.table('players').select("display_id, game_id, class, is_selected").order("display_id").execute()
        players = response.data if response.data else []
        return pd.DataFrame(players)
    except Exception as e:
        st.error(f"加载玩家数据失败: {str(e)}")
        return pd.DataFrame(columns=['display_id', 'game_id', 'class', 'is_selected'])

def load_teams() -> List[Dict]:
    """从Supabase加载队伍数据"""
    try:
        response = supabase.table('teams').select("*").order("created_at", desc=True).execute()
        return response.data if response.data else []
    except Exception as e:
        st.error(f"加载队伍数据失败: {str(e)}")
        return []

def add_player(game_id: str, game_class: str) -> bool:
    """添加新玩家到Supabase"""
    try:
        response = supabase.table('players').insert({
            "game_id": game_id,
            "class": game_class,
            "is_selected": False
        }).execute()
        return True if response.data else False
    except Exception as e:
        st.error(f"添加玩家失败: {str(e)}")
        return False

def update_player_selection_status(game_id: str, is_selected: bool) -> bool:
    """更新玩家选择状态"""
    try:
        response = supabase.table('players').update({
            "is_selected": is_selected
        }).eq("game_id", game_id).execute()
        return True if response.data else False
    except Exception as e:
        st.error(f"更新玩家状态失败: {str(e)}")
        return False

def create_team_in_db(captain: str, members: List[str]) -> bool:
    """在Supabase中创建队伍"""
    try:
        # 获取下一个可用的队伍ID
        max_id_response = supabase.table('teams').select("id").order("id", desc=True).limit(1).execute()
        next_id = 1 if not max_id_response.data else max_id_response.data[0]['id'] + 1
        
        response = supabase.table('teams').insert({
            "id": next_id,
            "captain": captain,
            "members": members,
            "created_at": datetime.now().isoformat(),
            "team_size": len(members) + 1  # 队长+队员数
        }).execute()
        
        if response.data:
            # 更新所有成员的选择状态
            for member in members:
                update_player_selection_status(member, True)
            return True
        return False
    except Exception as e:
        st.error(f"创建队伍失败: {str(e)}")
        return False

def delete_team_from_db(team_id: int, members: List[str]) -> bool:
    """从Supabase删除队伍"""
    try:
        # 先更新成员状态
        for member in members:
            update_player_selection_status(member, False)
        
        # 删除队伍
        response = supabase.table('teams').delete().eq("id", team_id).execute()
        return True if response.data else False
    except Exception as e:
        st.error(f"解散队伍失败: {str(e)}")
        return False

def check_and_fix_selection_consistency() -> bool:
    """检查并修复players和teams表之间的选择状态一致性"""
    try:
        # 获取所有已选择的玩家
        selected_players_response = supabase.table('players').select("game_id").eq("is_selected", True).execute()
        selected_players = {p['game_id'] for p in selected_players_response.data} if selected_players_response.data else set()
        
        # 获取所有队伍中的玩家(队长和成员)
        teams_response = supabase.table('teams').select("captain, members").execute()
        team_players = set()
        if teams_response.data:
            for team in teams_response.data:
                team_players.add(team['captain'])
                team_players.update(team['members'])
        
        # 找出不一致的记录
        inconsistent_players = selected_players - team_players
        
        # 修复不一致的记录
        if inconsistent_players:
            for player_id in inconsistent_players:
                supabase.table('players').update({"is_selected": False}).eq("game_id", player_id).execute()
            
            st.success(f"已修复 {len(inconsistent_players)} 条不一致的记录!")
            return True
        
        st.info("数据一致性检查完成，未发现不一致记录")
        return True
        
    except Exception as e:
        st.error(f"数据一致性检查失败: {str(e)}")
        return False

# ========================
# 核心功能模块
# ========================
def initialize_data():
    """初始化数据"""
    if 'players' not in st.session_state:
        st.session_state.players = load_players()
    if 'teams' not in st.session_state:
        st.session_state.teams = load_teams()
    if 'admin_logged_in' not in st.session_state:
        st.session_state.admin_logged_in = False

def check_admin_password():
    """管理员登录验证"""
    with st.sidebar:
        st.header("管理员登录")
        password = st.text_input("密码:", type="password", key="admin_pwd")
        if st.button("登录"):
            if hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH:
                st.session_state.admin_logged_in = True
                st.success("登录成功!")
                st.rerun()
            else:
                st.error("密码错误!")
        if st.session_state.admin_logged_in and st.button("退出"):
            st.session_state.admin_logged_in = False
            st.rerun()

def create_team(team_members: List[str], captain: str) -> bool:
    """创建队伍"""
    try:
        team_size = len(team_members) + 1  # 包括队长
        
        if team_size < MIN_TEAM_MEMBERS:
            st.error(f"队伍至少需要{MIN_TEAM_MEMBERS}名成员!")
            return False
        if team_size > MAX_TEAM_MEMBERS:
            st.error(f"队伍最多只能有{MAX_TEAM_MEMBERS}名成员!")
            return False
        
        # 验证所有成员存在
        existing_players = set(st.session_state.players['game_id'].values)
        for member in team_members:
            if member not in existing_players:
                st.error(f"玩家 {member} 不存在!")
                return False
        
        # 检查是否已被选择
        selected_players = {m for team in st.session_state.teams for m in team['members']}
        if any(m in selected_players for m in team_members):
            st.error("有成员已被其他队伍选中!")
            return False
        
        # 添加到数据库
        if create_team_in_db(captain, team_members):
            # 更新本地状态
            st.session_state.teams = load_teams()
            st.session_state.players = load_players()
            st.success(f"组队成功! 队伍人数: {team_size}人")
            return True
        return False
    except Exception as e:
        st.error(f"组队失败: {str(e)}")
        return False

# ========================
# 页面模块
# ========================
def show_team_list():
    """显示组队列表页面"""
    st.title("🏆 组队列表")
    
    if not st.session_state.teams:
        st.info("暂无组队记录")
        return
    
    # 显示队伍统计信息
    total_teams = len(st.session_state.teams)
    total_players = sum(len(team['members']) + 1 for team in st.session_state.teams)  # 每个队伍有队长+成员
    
    cols = st.columns(3)
    cols[0].metric("总队伍数", total_teams)
    cols[1].metric("总参与人数", total_players)
    cols[2].metric("平均队伍人数", f"{total_players/total_teams:.1f}" if total_teams > 0 else 0)
    
    for team in st.session_state.teams:
        with st.expander(f"队伍 {team['id']} - 队长: {team['captain']} ({len(team['members'])+1}人)", expanded=True):
            # 获取队伍成员详细信息
            members_info = []
            for member in team['members']:
                player = st.session_state.players[
                    st.session_state.players['game_id'] == member
                ]
                members_info.append({
                    '游戏ID': member,
                    '游戏职业': player['class'].values[0] if not player.empty else "未知"
                })
            
            # 显示队伍信息
            cols = st.columns([1, 3])
            with cols[0]:
                st.metric("队伍ID", team['id'])
                st.metric("队长", team['captain'])
                st.metric("队伍人数", len(team['members']) + 1)
                if 'created_at' in team:
                    created_time = pd.to_datetime(team['created_at']).strftime('%Y-%m-%d %H:%M')
                    st.caption(f"创建时间: {created_time}")
            
            with cols[1]:
                # 显示成员表格
                df = pd.DataFrame({
                    '角色': ['队长'] + ['队员']*(len(team['members'])),
                    '游戏ID': [team['captain']] + [m['游戏ID'] for m in members_info],
                    '游戏职业': [st.session_state.players[
                        st.session_state.players['game_id'] == team['captain']
                        ]['class'].values[0] if not st.session_state.players[
                            st.session_state.players['game_id'] == team['captain']].empty else "未知"
                    ] + [m['游戏职业'] for m in members_info]
                })
                st.dataframe(df, hide_index=True, use_container_width=True)

def main_page():
    """主界面"""
    st.title("🎮 游戏组队系统")
    
    # 玩家列表
    st.header("👥 玩家名单")
    st.dataframe(
        st.session_state.players.rename(columns={
            'display_id': '序号',
            'game_id': '游戏ID',
            'class': '游戏职业',
            'is_selected': '已选择'
        }).style.apply(
            lambda row: ['background: #f5f5f5'] * len(row) if row['已选择'] else [''] * len(row),
            axis=1
        ),
        column_order=["序号", "游戏ID", "游戏职业", "已选择"],
        hide_index=True,
        use_container_width=True,
        height=400
    )
    
    # 组队表单
    st.header("🛠️ 创建队伍")
    st.caption(f"组队要求: 至少{MIN_TEAM_MEMBERS}人，最多{MAX_TEAM_MEMBERS}人 (包括队长)")
    
    # 队长选择
    available_captains = st.session_state.players[~st.session_state.players['is_selected']]['game_id']
    if len(available_captains) == 0:
        st.warning("没有可选的队长，所有玩家已被组队")
        return
    
    captain = st.selectbox(
        "选择队长:",
        options=available_captains,
        key='captain'
    )
    
    # 队员选择
    available = st.session_state.players[
        (~st.session_state.players['is_selected']) & 
        (st.session_state.players['game_id'] != captain)
    ]['game_id']
    selected = st.multiselect(
        f"选择队员 (需要至少{MIN_TEAM_MEMBERS-1}人，最多{MAX_TEAM_MEMBERS-1}人):", 
        options=available, 
        key='members'
    )
    
    # 显示队伍预览
    if captain and selected:
        st.subheader("队伍预览")
        try:
            team_members = [captain] + selected
            team_size = len(team_members)
            roles = ['队长'] + ['队员'] * (team_size - 1)
            
            # 获取职业信息
            classes = []
            for member in team_members:
                player_data = st.session_state.players[
                    st.session_state.players['game_id'] == member
                ]
                classes.append(
                    player_data['class'].values[0] 
                    if not player_data.empty 
                    else '未知职业'
                )
            
            team_df = pd.DataFrame({
                '角色': roles,
                '游戏ID': team_members,
                '游戏职业': classes
            })
            st.dataframe(team_df, hide_index=True)
            
            # 显示队伍人数信息
            st.info(f"当前队伍人数: {team_size}人 (最少需要{MIN_TEAM_MEMBERS}人，最多{MAX_TEAM_MEMBERS}人)")
            
        except Exception as e:
            st.error(f"创建预览失败: {str(e)}")
    
    # 提交按钮
    if st.button("✅ 确认组队"):
        team_size = len(selected) + 1  # 包括队长
        if team_size >= MIN_TEAM_MEMBERS and team_size <= MAX_TEAM_MEMBERS:
            if create_team(selected, captain):
                st.rerun()
        else:
            st.error(f"队伍人数不符合要求! 需要{MIN_TEAM_MEMBERS}-{MAX_TEAM_MEMBERS}人，当前{team_size}人")

# ... [保持admin_panel和其他函数不变] ...

# ========================
# 主程序
# ========================
def main():
    # 初始化数据
    initialize_data()
    
    # 检查管理员登录状态
    check_admin_password()
    
    # 左侧导航栏
    if not st.session_state.admin_logged_in:
        with st.sidebar:
            st.title("导航菜单")
            page = st.radio("选择页面", ["组队系统", "查看组队列表"], index=0)
            
        if page == "组队系统":
            main_page()
        elif page == "查看组队列表":
            show_team_list()
    else:
        # 管理员直接进入后台
        admin_panel()

if __name__ == "__main__":
    main()
