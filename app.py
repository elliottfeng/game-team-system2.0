import streamlit as st
import pandas as pd
import hashlib
import os
import time
import requests
from io import BytesIO
from typing import List, Dict, Optional, Set
from supabase import create_client, Client
from datetime import datetime
from functools import wraps

# ========================
# 配置部分
# ========================
st.set_page_config(layout="wide", page_title="游戏组队系统")


# 游戏配置
class Config:
    SUPABASE_URL = os.getenv('SUPABASE_URL', st.secrets["SUPABASE_URL"])
    SUPABASE_KEY = os.getenv('SUPABASE_KEY', st.secrets["SUPABASE_KEY"])
    ADMIN_PASSWORD_HASH = hashlib.sha256(st.secrets["ADMIN_PASSWORD"].encode()).hexdigest()
    TENCENT_DOC_URL = st.secrets.get("TENCENT_DOC_URL", "")
    GAME_CLASSES = ['大理', '峨眉', '丐帮', '明教', '天山', '无尘', '武当', '逍遥', '星宿', '玄机', '白驼']


# 初始化Supabase客户端
supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)


# ========================
# 工具函数
# ========================
def handle_db_errors(func):
    """数据库操作错误处理装饰器"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            st.error(f"操作失败: {str(e)}")
            return False

    return wrapper


def convert_tencent_doc_url(doc_url: str) -> Optional[str]:
    """将腾讯文档普通链接转换为导出链接"""
    if not doc_url or "docs.qq.com" not in doc_url:
        return None
    doc_id = doc_url.split('/')[-1].split('?')[0]
    return f"https://docs.qq.com/dop-api/opendoc?id={doc_id}&outformat=1&normal=1"


# ========================
# 数据操作模块
# ========================
@handle_db_errors
def load_players() -> pd.DataFrame:
    """从Supabase加载玩家数据"""
    response = supabase.table('players').select("display_id, game_id, class, is_selected").order("display_id").execute()
    return pd.DataFrame(response.data if response.data else [])


@handle_db_errors
def load_teams() -> List[Dict]:
    """从Supabase加载队伍数据"""
    response = supabase.table('teams').select("*").order("created_at", desc=True).execute()
    return response.data if response.data else []


@handle_db_errors
def add_player(game_id: str, game_class: str) -> bool:
    """添加新玩家"""
    response = supabase.table('players').insert({
        "game_id": game_id,
        "class": game_class,
        "is_selected": False
    }).execute()
    return bool(response.data)


@handle_db_errors
def update_player_selection_status(game_id: str, is_selected: bool) -> bool:
    """更新玩家选择状态"""
    response = supabase.table('players').update({"is_selected": is_selected}).eq("game_id", game_id).execute()
    return bool(response.data)


@handle_db_errors
def create_team_in_db(captain: str, members: List[str]) -> bool:
    """在数据库中创建队伍"""
    members = [m for m in members if m != captain]  # 移除队长

    # 获取下一个队伍ID
    max_id_response = supabase.table('teams').select("id").order("id", desc=True).limit(1).execute()
    next_id = 1 if not max_id_response.data else max_id_response.data[0]['id'] + 1

    response = supabase.table('teams').insert({
        "id": next_id,
        "captain": captain,
        "members": members,
        "created_at": datetime.now().isoformat()
    }).execute()

    if response.data:
        update_player_selection_status(captain, True)
        for member in members:
            update_player_selection_status(member, True)
        return True
    return False


@handle_db_errors
def delete_team_from_db(team_id: int, members: List[str]) -> bool:
    """从数据库删除队伍"""
    for member in members:
        update_player_selection_status(member, False)
    response = supabase.table('teams').delete().eq("id", team_id).execute()
    return bool(response.data)


@handle_db_errors
def update_team_members(team_id: int, members: List[str]) -> bool:
    """更新队伍成员"""
    if len(members) != len(set(members)):
        st.error("成员列表包含重复项")
        return False

    response = supabase.table('teams').update({"members": members}).eq("id", team_id).execute()
    return bool(response.data)


@handle_db_errors
def create_change_request(game_id: str, new_game_id: str, new_class: str, status: str = "pending") -> bool:
    """创建更改请求"""
    response = supabase.table('change_requests').insert({
        "game_id": game_id,
        "new_game_id": new_game_id,
        "new_class": new_class,
        "status": status,
        "created_at": datetime.now().isoformat()
    }).execute()
    return bool(response.data)


@handle_db_errors
def load_change_requests(status: str = None) -> List[Dict]:
    """加载更改请求"""
    query = supabase.table('change_requests').select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    response = query.execute()
    return response.data if response.data else []


@handle_db_errors
def update_change_request(request_id: int, status: str) -> bool:
    """更新更改请求状态"""
    response = supabase.table('change_requests').update({"status": status}).eq("id", request_id).execute()
    return bool(response.data)


@handle_db_errors
def approve_change_request(request: Dict) -> bool:
    """批准更改请求"""
    old_game_id = request['game_id']
    new_game_id = request['new_game_id'] if request['new_game_id'] and request[
        'new_game_id'] != old_game_id else old_game_id
    new_class = request['new_class']

    # 更新玩家信息
    update_data = {}
    if new_game_id != old_game_id:
        update_data['game_id'] = new_game_id
    if new_class:
        update_data['class'] = new_class

    if update_data:
        supabase.table('players').update(update_data).eq("game_id", old_game_id).execute()

    # 更新队伍数据
    if new_game_id != old_game_id:
        supabase.table('teams').update({"captain": new_game_id}).eq("captain", old_game_id).execute()

        teams_response = supabase.table('teams').select("*").execute()
        if teams_response.data:
            for team in teams_response.data:
                if old_game_id in team['members']:
                    updated_members = [new_game_id if m == old_game_id else m for m in team['members']]
                    update_team_members(team['id'], updated_members)

    return update_change_request(request['id'], "approved")


@handle_db_errors
def check_and_fix_selection_consistency() -> bool:
    """
    全面检查并修复players表的is_selected字段与teams表实际组队情况的一致性
    修复两种不一致情况:
    1. is_selected=True但不在任何队伍中的玩家 → 设为False
    2. 在队伍中但is_selected=False的玩家 → 设为True
    """
    try:
        # 获取所有玩家选择状态
        players_response = supabase.table('players').select("game_id, is_selected").execute()
        all_players = {p['game_id']: p['is_selected'] for p in players_response.data} if players_response.data else {}

        # 获取所有队伍中的玩家(队长和成员)
        teams_response = supabase.table('teams').select("captain, members").execute()
        team_players = set()

        if teams_response.data:
            for team in teams_response.data:
                # 处理队长
                captain = str(team['captain']) if not isinstance(team['captain'], str) else team['captain']
                team_players.add(captain)

                # 处理队员
                if isinstance(team['members'], list):
                    for member in team['members']:
                        member_str = str(member) if not isinstance(member, str) else member
                        team_players.add(member_str)

        # 找出两种不一致情况
        false_positives = set()  # 被标记为已选择但不在队伍中的玩家
        false_negatives = set()  # 在队伍中但未被标记为已选择的玩家

        for game_id, is_selected in all_players.items():
            if is_selected and game_id not in team_players:
                false_positives.add(game_id)
            elif not is_selected and game_id in team_players:
                false_negatives.add(game_id)

        # 执行修复
        update_count = 0

        # 修复false_positives (设为False)
        if false_positives:
            update_response = supabase.table('players').update({"is_selected": False}).in_('game_id', list(
                false_positives)).execute()
            if update_response.data:
                update_count += len(false_positives)

        # 修复false_negatives (设为True)
        if false_negatives:
            update_response = supabase.table('players').update({"is_selected": True}).in_('game_id', list(
                false_negatives)).execute()
            if update_response.data:
                update_count += len(false_negatives)

        # 显示结果
        if false_positives or false_negatives:
            st.success(f"数据一致性检查完成，已修复 {update_count} 条不一致记录!")
            st.json({
                "错误标记为已选择的玩家(已修正)": list(false_positives),
                "未标记但实际在队伍中的玩家(已修正)": list(false_negatives)
            })
            return True
        else:
            st.info("数据一致性检查完成，未发现不一致记录")
            return True

    except Exception as e:
        st.error(f"数据一致性检查失败: {str(e)}")
        return False


# ========================
# 页面功能模块
# ========================
def initialize_data():
    """初始化数据"""
    if 'players' not in st.session_state:
        st.session_state.players = load_players()
    if 'teams' not in st.session_state:
        st.session_state.teams = load_teams()
    if 'admin_logged_in' not in st.session_state:
        st.session_state.admin_logged_in = False
    if 'change_requests' not in st.session_state:
        st.session_state.change_requests = load_change_requests()


def check_admin_password():
    """管理员登录验证"""
    with st.sidebar:
        st.header("管理员登录")
        password = st.text_input("密码:", type="password", key="admin_pwd")
        if st.button("登录"):
            if hashlib.sha256(password.encode()).hexdigest() == Config.ADMIN_PASSWORD_HASH:
                st.session_state.admin_logged_in = True
                st.success("登录成功!")
                st.rerun()
            else:
                st.error("密码错误!")
        if st.session_state.admin_logged_in and st.button("退出"):
            st.session_state.admin_logged_in = False
            st.rerun()


def display_team_info(team: Dict, show_disband_button: bool = False) -> None:
    """显示队伍信息"""
    # 获取成员信息
    members_info = []
    for member in team['members']:
        if member == team['captain']:
            continue
        player = st.session_state.players[st.session_state.players['game_id'] == member]
        members_info.append({
            '游戏ID': member,
            '游戏职业': player['class'].values[0] if not player.empty else "未知"
        })

    # 显示队伍信息
    cols = st.columns([1, 3])
    with cols[0]:
        st.metric("队伍ID", team['id'])
        st.metric("队长", team['captain'])
        st.metric("当前人数", f"{len(members_info) + 1}/6")
        if 'created_at' in team:
            created_time = pd.to_datetime(team['created_at']).strftime('%Y-%m-%d %H:%M')
            st.metric("创建时间", created_time)

    with cols[1]:
        # 创建成员表格
        df_data = {
            '角色': ['队长'],
            '游戏ID': [team['captain']],
            '游戏职业': [
                st.session_state.players[st.session_state.players['game_id'] == team['captain']]['class'].values[0]
                if not st.session_state.players[st.session_state.players['game_id'] == team['captain']].empty
                else "未知"
            ]
        }

        if members_info:
            df_data['角色'].extend(['队员'] * len(members_info))
            df_data['游戏ID'].extend([m['游戏ID'] for m in members_info])
            df_data['游戏职业'].extend([m['游戏职业'] for m in members_info])

        st.dataframe(pd.DataFrame(df_data), hide_index=True, use_container_width=True)

    # 解散按钮
    if show_disband_button and st.button(f"解散队伍{team['id']}", key=f"disband_{team['id']}"):
        if delete_team_from_db(team['id'], [team['captain']] + team['members']):
            st.session_state.teams = load_teams()
            st.session_state.players = load_players()
            st.rerun()


def create_team(team_members: List[str], captain: str) -> bool:
    """创建队伍"""
    if len(team_members) < 3 or len(team_members) > 6:
        st.error("队伍需要至少3名成员且最多6名成员!")
        return False

    existing_players = set(st.session_state.players['game_id'].values)
    for member in team_members:
        if member not in existing_players:
            st.error(f"玩家 {member} 不存在!")
            return False

    selected_players = {m for team in st.session_state.teams for m in team['members']}
    if any(m in selected_players for m in team_members):
        st.error("有成员已被其他队伍选中!")
        return False

    if create_team_in_db(captain, team_members):
        st.session_state.teams = load_teams()
        st.session_state.players = load_players()
        st.success("组队成功!")
        return True
    return False


def add_member_to_team(team_id: int, new_member: str) -> bool:
    """添加成员到队伍"""
    response = supabase.table('teams').select("*").eq("id", team_id).execute()
    if not response.data:
        st.error("找不到该队伍!")
        return False

    team = response.data[0]
    current_members = team['members']

    player_data = st.session_state.players[st.session_state.players['game_id'] == new_member]
    if not player_data.empty and player_data['is_selected'].iloc[0]:
        st.error("该玩家已被其他队伍选中!")
        return False

    if new_member in current_members or new_member == team['captain']:
        st.error("该玩家已在当前队伍中!")
        return False

    if len(current_members) >= 5:
        st.error("队伍人数已达上限!")
        return False

    updated_members = current_members + [new_member]
    if not update_team_members(team_id, updated_members):
        return False

    update_player_selection_status(new_member, True)
    st.session_state.players = load_players()
    st.session_state.teams = load_teams()
    return True


# ========================
# 页面模块
# ========================
def show_activity_page():
    """显示活动页面"""
    st.title("🗡️ 四大恶人活动安排")

    if not Config.TENCENT_DOC_URL:
        st.warning("当前未配置活动文档，请联系管理员")
        return

    st.markdown(f"""
    <iframe src="{Config.TENCENT_DOC_URL}" 
            width="100%" 
            height="800"
            frameborder="0"
            allowfullscreen>
    </iframe>
    """, unsafe_allow_html=True)


def show_change_info_page():
    """显示信息更改页面"""
    st.title("✏️ 信息更改")
    players = st.session_state.players

    game_id = st.selectbox("选择您的游戏ID", options=players['game_id'].tolist(), key="change_info_game_id")

    if game_id:
        player_info = players[players['game_id'] == game_id].iloc[0]
        st.subheader("当前信息")
        cols = st.columns(2)
        with cols[0]:
            st.text_input("当前游戏ID", value=player_info['game_id'], disabled=True)
        with cols[1]:
            st.text_input("当前职业", value=player_info['class'], disabled=True)

        st.subheader("更改信息")
        new_game_id = st.text_input("新游戏ID (如不需更改请留空)", key="new_game_id")
        new_class = st.selectbox(
            "新职业 (如不需更改请选择当前职业)",
            options=Config.GAME_CLASSES,
            index=Config.GAME_CLASSES.index(player_info['class']) if player_info['class'] in Config.GAME_CLASSES else 0,
            key="new_class"
        )

        if st.button("提交更改请求"):
            if not new_game_id and new_class == player_info['class']:
                st.warning("请至少修改一项信息")
            else:
                if create_change_request(
                        game_id,
                        new_game_id if new_game_id else game_id,
                        new_class
                ):
                    st.success("更改请求已提交，请等待管理员审核！")
                else:
                    st.error("提交更改请求失败")


def show_incomplete_teams():
    """显示未满队伍"""
    st.title("🟡 未满的队伍")

    if not st.session_state.teams:
        st.info("暂无组队记录")
        return

    available_players = set(st.session_state.players[~st.session_state.players['is_selected']]['game_id'])
    incomplete_teams = [team for team in st.session_state.teams if (1 + len(team['members'])) < 6]

    if not incomplete_teams:
        st.success("🎉 所有队伍都已满员!")
        return

    st.subheader(f"当前共有 {len(incomplete_teams)} 支队伍未满6人")

    for team in incomplete_teams:
        member_count = 1 + len(team['members'])
        with st.expander(f"队伍 {team['id']} - 队长: {team['captain']} ({member_count}/6)", expanded=True):
            display_team_info(team)

            if available_players:
                st.subheader("添加新成员")
                new_member = st.selectbox(
                    "选择要添加的成员",
                    options=list(available_players),
                    key=f"add_member_{team['id']}"
                )

                if st.button(f"添加到队伍 {team['id']}", key=f"add_btn_{team['id']}"):
                    with st.spinner("添加中，请稍候..."):
                        if add_member_to_team(team['id'], new_member):
                            st.success(f"✅ 已成功将 {new_member} 添加到队伍 {team['id']}!")
                            time.sleep(1.5)
                            st.rerun()
            else:
                st.warning("没有可用的玩家可以添加")


def show_team_list():
    """显示队伍列表"""
    st.title("🏆 组队列表")

    if not st.session_state.teams:
        st.info("暂无组队记录")
        return

    st.subheader(f"当前共有 {len(st.session_state.teams)} 支队伍")

    for team in st.session_state.teams:
        with st.expander(f"队伍 {team['id']} - 队长: {team['captain']}", expanded=True):
            display_team_info(team)


def main_page():
    """主页面"""
    st.title("🎮 游戏组队系统")

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

    st.header("🛠️ 创建队伍")
    available_captains = st.session_state.players[~st.session_state.players['is_selected']]['game_id']
    if len(available_captains) == 0:
        st.warning("没有可选的队长，所有玩家已被组队")
        return

    captain = st.selectbox("选择队长:", options=available_captains, key='captain')

    available = st.session_state.players[
        (~st.session_state.players['is_selected']) &
        (st.session_state.players['game_id'] != captain)
        ]['game_id']
    selected = st.multiselect("选择队员 (2-5人):", options=available, key='members')

    if captain and selected:
        st.subheader("队伍预览")
        try:
            team_members = [captain] + selected
            roles = ['队长'] + ['队员'] * len(selected)
            classes = []
            for member in team_members:
                player_data = st.session_state.players[st.session_state.players['game_id'] == member]
                classes.append(player_data['class'].values[0] if not player_data.empty else '未知职业')

            st.dataframe(pd.DataFrame({
                '角色': roles,
                '游戏ID': team_members,
                '游戏职业': classes
            }), hide_index=True)
        except Exception as e:
            st.error(f"创建预览失败: {str(e)}")

    if st.button("✅ 确认组队"):
        if 2 <= len(selected) <= 5:
            if create_team([captain] + selected, captain):
                st.rerun()
        else:
            st.error("请选择2到5名队员!")


def admin_panel():
    """管理员面板"""
    st.header("📊 管理员后台")
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["玩家管理", "队伍管理", "数据维护", "活动配置", "更改审批"])

    with tab1:
        st.subheader("玩家名单管理")
        with st.expander("添加玩家", expanded=True):
            cols = st.columns(2)
            with cols[0]:
                new_id = st.text_input("游戏ID", key="new_id")
            with cols[1]:
                new_class = st.selectbox("职业", Config.GAME_CLASSES, key="new_class")
            if st.button("添加") and new_id:
                if add_player(new_id, new_class):
                    st.session_state.players = load_players()
                    st.rerun()

        st.subheader("当前玩家")
        edited_df = st.data_editor(
            st.session_state.players.rename(columns={
                'display_id': '序号',
                'game_id': '游戏ID',
                'class': '游戏职业',
                'is_selected': '已选择'
            }),
            column_order=["序号", "游戏ID", "游戏职业", "已选择"],
            num_rows="dynamic",
            column_config={
                "序号": st.column_config.NumberColumn(width="small", disabled=True),
                "游戏ID": st.column_config.TextColumn(width="medium"),
                "游戏职业": st.column_config.SelectboxColumn(options=Config.GAME_CLASSES),
                "已选择": st.column_config.CheckboxColumn(disabled=True)
            },
            hide_index=True
        )

        if st.button("保存修改"):
            updated_players = edited_df.rename(columns={
                '序号': 'display_id',
                '游戏ID': 'game_id',
                '游戏职业': 'class',
                '已选择': 'is_selected'
            })
            try:
                for _, row in updated_players.iterrows():
                    supabase.table('players').update({
                        'game_id': row['game_id'],
                        'class': row['class'],
                        'is_selected': row['is_selected']
                    }).eq('display_id', row['display_id']).execute()
                st.session_state.players = load_players()
                st.success("修改已保存!")
                st.rerun()
            except Exception as e:
                st.error(f"保存失败: {str(e)}")

        if st.button("重置选择状态"):
            try:
                supabase.table('players').update({"is_selected": False}).neq("game_id", "").execute()
                st.session_state.players = load_players()
                st.rerun()
            except Exception as e:
                st.error(f"重置失败: {str(e)}")

    with tab2:
        st.subheader("队伍管理")
        if not st.session_state.teams:
            st.info("暂无队伍")
            return
        for team in st.session_state.teams:
            with st.expander(f"队伍{team['id']}-队长:{team['captain']}"):
                display_team_info(team, show_disband_button=True)

    with tab3:
        st.subheader("数据一致性维护")
        st.markdown("""
        **功能说明**:
        - 此功能将对比`players`表中的`is_selected`字段与`teams`表中的实际组队情况
        - 如果发现玩家标记为已选择(`is_selected=True`)但实际不在任何队伍中，将自动修正
        """)

        if st.button("执行数据一致性检查"):
            with st.spinner("正在检查数据一致性..."):
                if check_and_fix_selection_consistency():
                    st.session_state.players = load_players()
                    st.session_state.teams = load_teams()
                    st.rerun()

        st.subheader("当前数据状态")
        selected_players = set(st.session_state.players[st.session_state.players['is_selected']]['game_id'])
        team_players = set()
        for team in st.session_state.teams:
            team_players.add(team['captain'])
            team_players.update(team['members'])

        inconsistent_players = selected_players - team_players
        if inconsistent_players:
            st.warning(f"发现 {len(inconsistent_players)} 条不一致记录:")
            st.dataframe(st.session_state.players[
                st.session_state.players['game_id'].isin(inconsistent_players)
            ][['display_id', 'game_id', 'class']].rename(columns={
                'display_id': '序号',
                'game_id': '游戏ID',
                'class': '职业'
            }), hide_index=True)
        else:
            st.success("未发现数据不一致情况")

    with tab4:
        st.subheader("四大恶人活动配置")
        st.markdown(f"""
        **当前配置的文档链接**:
        ```
        {Config.TENCENT_DOC_URL or "未配置"}
        ```
        """)
        if Config.TENCENT_DOC_URL:
            st.success("✅ 有效配置")
            st.markdown(f"[点击测试打开文档]({Config.TENCENT_DOC_URL})")
        else:
            st.warning("⚠️ 未配置文档链接")

    with tab5:
        st.subheader("待审批的更改请求")
        pending_requests = load_change_requests("pending")
        if not pending_requests:
            st.info("没有待审批的更改请求")
        else:
            for request in pending_requests:
                with st.container():
                    st.markdown(f"### 请求ID: {request['id']} - 玩家: {request['game_id']}")
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**当前信息**")
                        st.write(f"游戏ID: `{request['game_id']}`")
                        player_data = st.session_state.players[
                            st.session_state.players['game_id'] == request['game_id']]
                        current_class = player_data['class'].values[0] if not player_data.empty else "未知"
                        st.write(f"职业: `{current_class}`")
                        st.markdown("**提交时间**")
                        st.write(pd.to_datetime(request['created_at']).strftime('%Y-%m-%d %H:%M:%S'))
                    with col2:
                        st.markdown("**请求更改**")
                        changes = []
                        if request['new_game_id'] and request['new_game_id'] != request['game_id']:
                            changes.append(f"游戏ID: `{request['game_id']}` → `{request['new_game_id']}`")
                        if request['new_class'] and request['new_class'] != current_class:
                            changes.append(f"职业: `{current_class}` → `{request['new_class']}`")
                        if changes:
                            for change in changes:
                                st.write(change)
                        else:
                            st.warning("没有有效的更改内容")
                    st.markdown("---")
                    action_col1, action_col2, _ = st.columns([1, 1, 2])
                    with action_col1:
                        if st.button(f"✅ 批准", key=f"approve_{request['id']}"):
                            with st.spinner("处理中..."):
                                if approve_change_request(request):
                                    st.success("已批准更改请求")
                                    st.session_state.players = load_players()
                                    st.session_state.teams = load_teams()
                                    st.session_state.change_requests = load_change_requests()
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("批准失败")
                    with action_col2:
                        if st.button(f"❌ 拒绝", key=f"reject_{request['id']}"):
                            with st.spinner("处理中..."):
                                if update_change_request(request['id'], "rejected"):
                                    st.success("已拒绝更改请求")
                                    st.session_state.change_requests = load_change_requests()
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("拒绝失败")
                    st.markdown("---")
                    if st.checkbox(f"显示原始请求数据 [ID: {request['id']}]", key=f"raw_{request['id']}"):
                        st.json(request)
                    st.markdown("---")


# ========================
# 主程序
# ========================
def main():
    initialize_data()
    check_admin_password()

    if not st.session_state.admin_logged_in:
        with st.sidebar:
            st.title("导航菜单")
            st.image(
                "https://cdn.biubiu001.com/p/ping/20250410/img/b1b152ffc1697af5cfa95e0d05b3aa26.png?x-oss-process=image/resize,w_400/format,webp/quality,Q_90",
                width=150, use_container_width=True)
            page = st.radio(
                "选择页面",
                ["组队系统", "查看组队列表", "未满的队伍", "信息更改", "四大恶人活动"],
                index=0
            )

        if page == "组队系统":
            main_page()
        elif page == "查看组队列表":
            show_team_list()
        elif page == "未满的队伍":
            show_incomplete_teams()
        elif page == "信息更改":
            show_change_info_page()
        elif page == "四大恶人活动":
            show_activity_page()
    else:
        admin_panel()


if __name__ == "__main__":
    main()
