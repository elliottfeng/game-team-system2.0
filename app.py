import streamlit as st
import pandas as pd
import hashlib
import os
import requests
from io import BytesIO
from typing import List, Dict
from supabase import create_client, Client
from datetime import datetime

# ========================
# 配置部分
# ========================
# 必须在最前面设置页面配置
st.set_page_config(layout="wide", page_title="游戏组队系统")

# Supabase配置
SUPABASE_URL = os.getenv('SUPABASE_URL', st.secrets["SUPABASE_URL"])
SUPABASE_KEY = os.getenv('SUPABASE_KEY', st.secrets["SUPABASE_KEY"])
ADMIN_PASSWORD_HASH = hashlib.sha256(st.secrets["ADMIN_PASSWORD"].encode()).hexdigest()

# 腾讯文档配置
TENCENT_DOC_URL = st.secrets.get("TENCENT_DOC_URL", "")

# 初始化Supabase客户端
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 游戏职业列表
GAME_CLASSES = [
    '大理', '峨眉', '丐帮', '明教', '天山',
    '无尘', '武当', '逍遥', '星宿', '玄机'
]


# ========================
# Supabase 数据操作模块 (完全保留原有代码)
# ========================
def load_players() -> pd.DataFrame:
    """从Supabase加载玩家数据（按display_id排序）"""
    try:
        response = supabase.table('players').select("display_id, game_id, class, is_selected").order(
            "display_id").execute()
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
            "created_at": datetime.now().isoformat()
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
        selected_players = {p['game_id'] for p in
                            selected_players_response.data} if selected_players_response.data else set()

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
# 腾讯文档功能模块 (新增部分)
# ========================
def convert_tencent_doc_url(doc_url):
    """将腾讯文档普通链接转换为导出链接"""
    if not doc_url or "docs.qq.com" not in doc_url:
        return None
    doc_id = doc_url.split('/')[-1].split('?')[0]
    return f"https://docs.qq.com/dop-api/opendoc?id={doc_id}&outformat=1&normal=1"


def load_activity_data():
    """加载腾讯文档数据"""
    if not TENCENT_DOC_URL:
        return None

    export_url = convert_tencent_doc_url(TENCENT_DOC_URL)
    if not export_url:
        return None

    try:
        response = requests.get(export_url, timeout=10)
        response.raise_for_status()
        return BytesIO(response.content)
    except Exception as e:
        st.error(f"获取腾讯文档失败: {str(e)}")
        return None


def show_activity_page():
    st.title("🗡️ 四大恶人活动安排")

    if not st.secrets.get("TENCENT_DOC_URL"):
        st.warning("当前未配置活动文档，请联系管理员")
        return

    doc_url = st.secrets.TENCENT_DOC_URL

    # 方法1：直接嵌入网页（需要文档开启"所有人可查看"）
    st.markdown(f"""
    <iframe src="{doc_url}" 
            width="100%" 
            height="800"
            frameborder="0"
            allowfullscreen>
    </iframe>
    """, unsafe_allow_html=True)


# 在admin_panel函数中添加配置界面
def admin_panel(tab4=None):
    # ... [其他管理功能不变] ...

    with tab4:
        st.subheader("四大恶人活动配置")

        st.markdown("""
        ### 配置指南
        1. 访问[腾讯文档](https://docs.qq.com/)创建/上传文件
        2. 点击右上角「分享」→ 设置「所有人可查看」
        3. 复制浏览器地址栏的链接
        """)

        current_url = st.text_input(
            "腾讯文档链接",
            value=st.secrets.get("TENCENT_DOC_URL", ""),
            key="tencent_doc_url"
        )

        if st.button("保存配置"):
            # 实际部署时应保存到数据库
            st.success("配置已更新！请重新部署应用使更改生效")
            st.markdown(f"当前配置链接: {current_url}")


# ========================
# 核心功能模块 (完全保留原有代码)
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
        if len(team_members) < 3 or len(team_members) > 6:
            st.error("队伍需要至少3名成员且最多6名成员!")
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
            st.success("组队成功!")
            return True
        return False
    except Exception as e:
        st.error(f"组队失败: {str(e)}")
        return False


# ========================
# 页面模块 (修改部分)
# ========================
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
    selected = st.multiselect("选择队员 (2-5人):", options=available, key='members')

    # 显示队伍预览
    if captain and selected:
        st.subheader("队伍预览")
        try:
            team_members = [captain] + selected
            roles = ['队长'] + ['队员'] * len(selected)

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

        except Exception as e:
            st.error(f"创建预览失败: {str(e)}")

    # 提交按钮
    if st.button("✅ 确认组队"):
        if 2 <= len(selected) <= 5:  # 3-6人队伍 (队长+2-5队员)
            if create_team([captain] + selected, captain):
                st.rerun()
        else:
            st.error("请选择2到5名队员!")


def show_team_list():
    """显示组队列表页面"""
    st.title("🏆 组队列表")

    if not st.session_state.teams:
        st.info("暂无组队记录")
        return

    # 显示队伍统计信息
    st.subheader(f"当前共有 {len(st.session_state.teams)} 支队伍")

    for team in st.session_state.teams:
        with st.expander(f"队伍 {team['id']} - 队长: {team['captain']}", expanded=True):
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
                if 'created_at' in team:
                    created_time = pd.to_datetime(team['created_at']).strftime('%Y-%m-%d %H:%M')
                    st.metric("创建时间", created_time)

            with cols[1]:
                # 显示成员表格
                df = pd.DataFrame({
                    '角色': ['队长'] + ['队员'] * (len(team['members'])),
                    '游戏ID': [team['captain']] + [m['游戏ID'] for m in members_info],
                    '游戏职业': [
                                    st.session_state.players[st.session_state.players['game_id'] == team['captain']][
                                        'class'].values[0]
                                    if not st.session_state.players[
                                        st.session_state.players['game_id'] == team['captain']].empty
                                    else "未知"
                                ] + [m['游戏职业'] for m in members_info]
                })
                st.dataframe(df, hide_index=True, use_container_width=True)


def admin_panel():
    """管理员界面"""
    st.header("📊 管理员后台")

    tab1, tab2, tab3, tab4 = st.tabs(["玩家管理", "队伍管理", "数据维护", "活动配置"])

    with tab1:
        st.subheader("玩家名单管理")

        # 添加新玩家
        with st.expander("添加玩家", expanded=True):
            cols = st.columns(2)
            with cols[0]:
                new_id = st.text_input("游戏ID", key="new_id")
            with cols[1]:
                new_class = st.selectbox("职业", GAME_CLASSES, key="new_class")
            if st.button("添加"):
                if new_id:
                    if add_player(new_id, new_class):
                        st.session_state.players = load_players()
                        st.rerun()

        # 玩家列表编辑
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
                "游戏职业": st.column_config.SelectboxColumn(options=GAME_CLASSES),
                "已选择": st.column_config.CheckboxColumn(disabled=True)
            },
            hide_index=True
        )

        if st.button("保存修改"):
            # 重命名回原始列名
            updated_players = edited_df.rename(columns={
                '序号': 'display_id',
                '游戏ID': 'game_id',
                '游戏职业': 'class',
                '已选择': 'is_selected'
            })

            # 更新数据库
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
                # 获取成员信息
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
                df = pd.DataFrame({
                    '角色': ['队长'] + ['队员'] * (len(team['members'])),
                    '游戏ID': [team['captain']] + [m['游戏ID'] for m in members_info],
                    '游戏职业': [
                                    st.session_state.players[st.session_state.players['game_id'] == team['captain']][
                                        'class'].values[0]
                                    if not st.session_state.players[
                                        st.session_state.players['game_id'] == team['captain']].empty
                                    else "未知"
                                ] + [m['游戏职业'] for m in members_info]
                })
                st.dataframe(df, hide_index=True)

                if st.button(f"解散队伍{team['id']}", key=f"disband_{team['id']}"):
                    if delete_team_from_db(team['id'], team['members']):
                        st.session_state.teams = load_teams()
                        st.session_state.players = load_players()
                        st.rerun()

    with tab3:
        st.subheader("数据一致性维护")

        st.markdown("""
        **功能说明**:
        - 此功能将对比`players`表中的`is_selected`字段与`teams`表中的实际组队情况
        - 如果发现玩家标记为已选择(`is_selected=True`)但实际不在任何队伍中，将自动修正
        """)

        if st.button("执行数据一致性检查", help="点击检查并修复数据不一致问题"):
            with st.spinner("正在检查数据一致性..."):
                if check_and_fix_selection_consistency():
                    # 刷新本地数据
                    st.session_state.players = load_players()
                    st.session_state.teams = load_teams()
                    st.rerun()

        # 显示当前数据状态对比
        st.subheader("当前数据状态")

        # 获取已选择但不在队伍中的玩家
        selected_players = set(st.session_state.players[st.session_state.players['is_selected']]['game_id'])
        team_players = set()
        for team in st.session_state.teams:
            team_players.add(team['captain'])
            team_players.update(team['members'])

        inconsistent_players = selected_players - team_players

        if inconsistent_players:
            st.warning(f"发现 {len(inconsistent_players)} 条不一致记录:")
            inconsistent_df = st.session_state.players[
                st.session_state.players['game_id'].isin(inconsistent_players)
            ][['display_id', 'game_id', 'class']]
            st.dataframe(inconsistent_df.rename(columns={
                'display_id': '序号',
                'game_id': '游戏ID',
                'class': '职业'
            }), hide_index=True)
        else:
            st.success("未发现数据不一致情况")

    with tab4:
        st.subheader("四大恶人活动配置")

        st.markdown("""
        ### 腾讯文档使用说明
        1. 访问[腾讯文档官网](https://docs.qq.com/)
        2. 上传或创建Excel文件
        3. 点击右上角「分享」→ 设置为「所有人可查看」
        4. 将分享链接粘贴到下方：
        """)

        # 显示当前配置
        st.markdown(f"""
        **当前配置的文档链接**:
        ```
        {TENCENT_DOC_URL or "未配置"}
        ```
        """)

        if TENCENT_DOC_URL:
            st.success("✅ 有效配置")
            st.markdown(f"[点击测试打开文档]({TENCENT_DOC_URL})")
        else:
            st.warning("⚠️ 未配置文档链接")


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
            st.image(
                "https://cdn.biubiu001.com/p/ping/20250410/img/b1b152ffc1697af5cfa95e0d05b3aa26.png?x-oss-process=image/resize,w_400/format,webp/quality,Q_90",
                width=150, use_container_width=True)
            page = st.radio(
                "选择页面",
                ["组队系统", "查看组队列表", "四大恶人活动"],
                index=0
            )

        if page == "组队系统":
            main_page()
        elif page == "查看组队列表":
            show_team_list()
        elif page == "四大恶人活动":
            show_activity_page()
    else:
        # 管理员直接进入后台
        admin_panel()


if __name__ == "__main__":
    main()
