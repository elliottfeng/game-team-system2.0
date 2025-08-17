import hashlib
import logging
import os
import random
import time
from datetime import datetime
from functools import wraps
from typing import List, Dict, Optional
import pandas as pd
import streamlit as st
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

# ========================
# 配置部分
# ========================
st.set_page_config(layout="wide", page_title="游戏组队系统")

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Config:
    SUPABASE_URL = os.getenv('SUPABASE_URL', st.secrets["SUPABASE_URL"])
    SUPABASE_KEY = os.getenv('SUPABASE_KEY', st.secrets["SUPABASE_KEY"])
    ADMIN_PASSWORD_HASH = hashlib.sha256(st.secrets["ADMIN_PASSWORD"].encode()).hexdigest()
    TENCENT_DOC_URL = st.secrets.get("TENCENT_DOC_URL", "")
    GAME_CLASSES = ['大理', '峨眉', '丐帮', '明教', '天山', '无尘', '武当', '逍遥', '星宿', '玄机', '白驼']
    MAX_TEAM_SIZE = 6
    MIN_TEAM_SIZE = 2


# 初始化Supabase客户端
supabase: Client = create_client(
    Config.SUPABASE_URL,
    Config.SUPABASE_KEY,
    options=ClientOptions(postgrest_client_timeout=10)
)


# ========================
# 工具函数
# ========================
def handle_db_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Database operation failed: {str(e)}")
            st.error(f"操作失败: {str(e)}")
            return False

    return wrapper


def convert_tencent_doc_url(doc_url: str) -> Optional[str]:
    """转换腾讯文档URL为API格式"""
    if not doc_url or "docs.qq.com" not in doc_url:
        return None
    doc_id = doc_url.split('/')[-1].split('?')[0]
    return f"https://docs.qq.com/dop-api/opendoc?id={doc_id}&outformat=1&normal=1"


def notify_team_members(team_id: int, title: str, message: str) -> None:
    """发送通知给队伍成员（模拟函数）"""
    logger.info(f"Notification to team {team_id}: {title} - {message}")


# ========================
# 数据操作模块
# ========================
@handle_db_errors
def load_players() -> pd.DataFrame:
    """加载所有玩家数据"""
    response = supabase.table('players').select("display_id, game_id, class, is_selected").order("display_id").execute()
    return pd.DataFrame(response.data if response.data else [])


@handle_db_errors
def load_teams() -> List[Dict]:
    """加载所有队伍数据"""
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
    members = [m for m in members if m != captain]

    if len(members) + 1 > Config.MAX_TEAM_SIZE:
        st.error(f"队伍人数不能超过{Config.MAX_TEAM_SIZE}人")
        return False

    if len(members) + 1 < Config.MIN_TEAM_SIZE:
        st.error(f"队伍人数不能少于{Config.MIN_TEAM_SIZE}人")
        return False

    # 获取下一个ID
    max_id_response = supabase.table('teams').select("id").order("id", desc=True).limit(1).execute()
    next_id = 1 if not max_id_response.data else max_id_response.data[0]['id'] + 1

    response = supabase.table('teams').insert({
        "id": next_id,
        "captain": captain,
        "members": members,
        "created_at": datetime.now().isoformat()
    }).execute()

    if response.data:
        # 批量更新玩家状态
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
    old_id = request['game_id']
    new_id = request['new_game_id'] or old_id
    new_class = request['new_class']

    # Initialize temp_changes before try block
    temp_changes = []

    try:
        # 1. 获取所有相关队伍（作为队长或成员）
        teams_response = supabase.table('teams') \
            .select('id, captain, members') \
            .or_(f'captain.eq.{old_id},members.cs.["{old_id}"]') \
            .execute()

        related_teams = teams_response.data if teams_response.data else []

        # 2. 处理队长身份的临时转移
        temp_changes = []
        for team in related_teams:
            if team['captain'] == old_id:
                if not team['members']:
                    raise ValueError(f"队伍 {team['id']} 没有可用的临时队长")

                # 随机选择临时队长（排除自己）
                available_members = [m for m in team['members'] if m != old_id]
                if not available_members:
                    raise ValueError(f"队伍 {team['id']} 没有其他可用成员")

                temp_captain = random.choice(available_members)
                temp_changes.append({
                    'team_id': team['id'],
                    'old_captain': old_id,
                    'temp_captain': temp_captain
                })

                # 更新临时队长
                supabase.table('teams') \
                    .update({'captain': temp_captain}) \
                    .eq('id', team['id']) \
                    .execute()

        # 3. 执行玩家信息更新
        update_data = {}
        if new_id != old_id:
            update_data['game_id'] = new_id
        if new_class:
            update_data['class'] = new_class

        if update_data:
            supabase.table('players') \
                .update(update_data) \
                .eq('game_id', old_id) \
                .execute()

        # 4. 更新所有相关队伍信息
        for team in related_teams:
            # 准备更新数据
            update_team_data = {}

            # 更新队长信息
            if team['captain'] == old_id:
                update_team_data['captain'] = new_id

            # 更新成员列表
            if old_id in team['members']:
                updated_members = [new_id if m == old_id else m for m in team['members']]
                update_team_data['members'] = updated_members

            # 执行更新
            if update_team_data:
                supabase.table('teams') \
                    .update(update_team_data) \
                    .eq('id', team['id']) \
                    .execute()

        # 5. 更新请求状态
        supabase.table('change_requests') \
            .update({'status': 'approved'}) \
            .eq('id', request['id']) \
            .execute()

        return True

    except Exception as e:
        # 自动回滚机制
        for change in temp_changes:
            supabase.table('teams') \
                .update({'captain': change['old_captain']}) \
                .eq('id', change['team_id']) \
                .execute()
        st.error(f"操作失败: {str(e)}")
        return False


@handle_db_errors
def check_and_fix_selection_consistency() -> bool:
    """检查并修复数据一致性"""
    try:
        players_response = supabase.table('players').select("game_id, is_selected").execute()
        all_players = {p['game_id']: p['is_selected'] for p in players_response.data} if players_response.data else {}

        teams_response = supabase.table('teams').select("captain, members").execute()
        team_players = set()
        if teams_response.data:
            for team in teams_response.data:
                captain = str(team['captain'])
                team_players.add(captain)
                if isinstance(team['members'], list):
                    for member in team['members']:
                        member_str = str(member)
                        team_players.add(member_str)

        false_positives = set()
        false_negatives = set()
        for game_id, is_selected in all_players.items():
            if is_selected and game_id not in team_players:
                false_positives.add(game_id)
            elif not is_selected and game_id in team_players:
                false_negatives.add(game_id)

        update_count = 0
        if false_positives:
            supabase.table('players').update({"is_selected": False}).in_('game_id', list(false_positives)).execute()
            update_count += len(false_positives)
        if false_negatives:
            supabase.table('players').update({"is_selected": True}).in_('game_id', list(false_negatives)).execute()
            update_count += len(false_negatives)

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


@handle_db_errors
def create_team_change_request(
        team_id: int,
        request_type: str,
        requester_id: str,
        proposed_captain: str = None,
        member_to_add: str = None,
        member_to_remove: str = None,
        reason: str = None
) -> bool:
    """
    创建队伍变更请求（完整版）
    参数：
    - team_id: 队伍ID
    - request_type: 请求类型（change_captain/add_member/remove_member）
    - requester_id: 申请者游戏ID
    - proposed_captain: 新队长ID（仅change_captain类型需要）
    - member_to_add: 要添加的成员ID（仅add_member类型需要）
    - member_to_remove: 要移除的成员ID（仅remove_member类型需要）
    - reason: 申请理由
    """
    try:
        # ===== 1. 验证申请者身份 =====
        team = supabase.table('teams') \
            .select('captain, members') \
            .eq('id', team_id) \
            .single().execute().data

        is_captain = requester_id == team['captain']
        is_member = is_captain or (requester_id in team['members'])

        if not is_member:
            raise ValueError("❌ 只有队伍成员可以提交申请")

        # ===== 2. 验证请求类型 =====
        request_data = {
            "team_id": team_id,
            "request_type": request_type,
            "requester_id": requester_id,
            "current_captain": team['captain'],  # 总是包含当前队长
            "status": "pending",
            "reason": reason,
            "created_at": datetime.now().isoformat()
        }

        if request_type == "change_captain":
            if not is_captain:
                raise ValueError("❌ 只有队长可以发起队长变更")
            if proposed_captain not in team['members']:
                raise ValueError("❌ 新队长必须是当前队员")
            request_data["proposed_captain"] = proposed_captain

        elif request_type == "add_member":
            # 验证新成员是否已在其他队伍
            player_status = supabase.table('players') \
                .select('is_selected') \
                .eq('game_id', member_to_add) \
                .single().execute().data
            if player_status['is_selected']:
                raise ValueError("❌ 该玩家已加入其他队伍")
            request_data["member_to_add"] = member_to_add

        elif request_type == "remove_member":
            # 如果要移除的是队长
            if member_to_remove == team['captain']:
                if len(team['members']) < 1:
                    raise ValueError("❌ 不能移除队伍的最后一名成员")

                # 判断是否是队长自己在申请移除自己
                if requester_id == team['captain']:
                    # 队长移除自己，将队长给到队员中的第一位
                    new_captain = team['members'][0]
                    request_data.update({
                        "request_type": "change_captain",  # 自动转换请求类型
                        "proposed_captain": new_captain,  # 第一位队员成为新队长
                        "original_request": "remove_member",
                        "member_to_remove": member_to_remove
                    })
                else:
                    # 队员申请移除队长，将队长给到申请人
                    request_data.update({
                        "request_type": "change_captain",  # 自动转换请求类型
                        "proposed_captain": requester_id,  # 申请人成为新队长
                        "original_request": "remove_member",
                        "member_to_remove": member_to_remove
                    })
            else:
                if member_to_remove not in team['members']:
                    raise ValueError("❌ 目标成员不在本队伍中")
                request_data["member_to_remove"] = member_to_remove
        else:
            raise ValueError("❌ 无效的请求类型")

        # ===== 3. 创建请求 =====
        response = supabase.table('team_change_requests') \
            .insert(request_data) \
            .execute()

        # 发送通知（可选）
        notify_team_members(
            team_id,
            f"新的队伍变更请求: {request_type}",
            f"由 {requester_id} 发起"
        )

        return bool(response.data)

    except Exception as e:
        st.error(str(e))
        return False


@handle_db_errors
def load_team_change_requests(status: str = None) -> List[Dict]:
    """加载队伍变更请求"""
    query = supabase.table('team_change_requests').select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    response = query.execute()
    return response.data if response.data else []


@handle_db_errors
def approve_team_change_request(request: Dict) -> bool:
    """审批队伍变更请求（整合智能降级）"""
    try:
        # 获取队伍信息
        team_response = supabase.table('teams') \
            .select('*') \
            .eq('id', request['team_id']) \
            .single().execute()
        team = team_response.data if team_response.data else None

        if not team:
            raise ValueError("找不到该队伍")

        # 情况1：移除成员（含队长智能降级）
        if request['request_type'] == "remove_member":
            member_to_remove = request['member_to_remove']

            # 智能降级逻辑：当移除的是队长时
            if member_to_remove == team['captain']:
                if len(team['members']) == 0:
                    raise ValueError("不能移除最后一名成员")

                # 自动选择首位队员为新队长
                new_captain = team['members'][0]
                supabase.table('teams') \
                    .update({
                    'captain': new_captain,
                    'members': [m for m in team['members'] if m != new_captain]
                }) \
                    .eq('id', team['id']) \
                    .execute()
            # 普通成员移除
            else:
                supabase.table('teams') \
                    .update({
                    'members': [m for m in team['members'] if m != member_to_remove]
                }) \
                    .eq('id', team['id']) \
                    .execute()

            # 更新玩家状态
            supabase.table('players') \
                .update({'is_selected': False}) \
                .eq('game_id', member_to_remove) \
                .execute()

        # 情况2：变更队长
        elif request['request_type'] == "change_captain":
            supabase.table('teams') \
                .update({
                'captain': request['proposed_captain'],
                'members': [m for m in team['members'] if m != request['proposed_captain']] + [team['captain']]
            }) \
                .eq('id', team['id']) \
                .execute()

        # 情况3：添加成员
        elif request['request_type'] == "add_member":
            # 检查成员是否已在其他队伍
            player_status = supabase.table('players') \
                .select('is_selected') \
                .eq('game_id', request['member_to_add']) \
                .single().execute().data

            if player_status and player_status['is_selected']:
                raise ValueError("该玩家已加入其他队伍")

            # 添加成员到队伍
            supabase.table('teams') \
                .update({
                'members': team['members'] + [request['member_to_add']]
            }) \
                .eq('id', team['id']) \
                .execute()

            # 更新玩家状态
            supabase.table('players') \
                .update({'is_selected': True}) \
                .eq('game_id', request['member_to_add']) \
                .execute()

        # 更新请求状态
        supabase.table('team_change_requests') \
            .update({'status': 'approved'}) \
            .eq('id', request['id']) \
            .execute()

        return True

    except Exception as e:
        st.error(f"审批失败: {str(e)}")
        return False


@handle_db_errors
def update_team_change_request(request_id: int, status: str) -> bool:
    """
    更新队伍变更请求状态

    参数:
        request_id: 请求ID
        status: 要更新的状态 ('approved'/'rejected')
    """
    update_data = {
        "status": status,
        "processed_at": datetime.now().isoformat()
    }

    response = supabase.table('team_change_requests') \
        .update(update_data) \
        .eq('id', request_id) \
        .execute()

    return bool(response.data)

@handle_db_errors
def update_team_captain(team_id: int, new_captain: str) -> bool:
    """更新队伍队长"""
    response = supabase.table('teams').select("*").eq("id", team_id).execute()
    if not response.data:
        st.error("找不到该队伍!")
        return False

    team = response.data[0]
    current_captain = team['captain']
    current_members = team['members']

    if new_captain not in current_members and new_captain != current_captain:
        st.error("新队长必须已经是队伍成员!")
        return False

    updated_members = [m for m in current_members if m != new_captain]
    updated_members.append(current_captain)

    response = supabase.table('teams').update({
        "captain": new_captain,
        "members": updated_members
    }).eq("id", team_id).execute()

    return bool(response.data)


@handle_db_errors
def remove_member_from_team(team_id: int, member_to_remove: str) -> bool:
    """从队伍中移除成员"""
    response = supabase.table('teams').select("*").eq("id", team_id).execute()
    if not response.data:
        st.error("找不到该队伍!")
        return False

    team = response.data[0]
    current_members = team['members']

    if member_to_remove not in current_members:
        st.error("该玩家不在当前队伍中!")
        return False

    updated_members = [m for m in current_members if m != member_to_remove]

    if update_team_members(team_id, updated_members):
        update_player_selection_status(member_to_remove, False)
        return True
    return False


# ========================
# 页面模块
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
    if 'team_change_requests' not in st.session_state:
        st.session_state.team_change_requests = load_team_change_requests()


def display_team_info(team: Dict, show_disband_button: bool = False) -> None:
    """显示队伍信息"""
    members_info = []
    for member in team['members']:
        if member == team['captain']:
            continue
        player = st.session_state.players[st.session_state.players['game_id'] == member]
        members_info.append({
            '游戏ID': member,
            '游戏职业': player['class'].values[0] if not player.empty else "未知"
        })

    cols = st.columns([1, 3])
    with cols[0]:
        st.metric("队伍ID", team['id'])
        st.metric("队长", team['captain'])
        st.metric("当前人数", f"{len(members_info) + 1}/{Config.MAX_TEAM_SIZE}")
        if 'created_at' in team:
            created_time = pd.to_datetime(team['created_at']).strftime('%Y-%m-%d %H:%M')
            st.metric("创建时间", created_time)

    with cols[1]:
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

    if show_disband_button and st.button(f"解散队伍{team['id']}", key=f"disband_{team['id']}"):
        if delete_team_from_db(team['id'], [team['captain']] + team['members']):
            st.session_state.teams = load_teams()
            st.session_state.players = load_players()
            st.rerun()


def display_request_details(request: Dict) -> None:
    """显示请求详情"""
    player = supabase.table('players') \
        .select('class') \
        .eq('game_id', request['game_id']) \
        .single().execute().data

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**当前信息**")
        st.write(f"游戏ID: `{request['game_id']}`")
        st.write(f"职业: `{player['class'] if player else '未知'}`")

    with col2:
        st.markdown("**请求更改**")
        changes = []
        if request['new_game_id']:
            changes.append(f"ID: `{request['game_id']}` → `{request['new_game_id']}`")
        if request['new_class']:
            changes.append(f"职业: `{player['class']}` → `{request['new_class']}`")

        if changes:
            for change in changes:
                st.write(change)
        else:
            st.warning("无有效更改内容")

    st.write(f"提交时间: `{pd.to_datetime(request['created_at']).strftime('%Y-%m-%d %H:%M')}`")
    if request.get('reason'):
        st.text_area("申请理由", value=request['reason'], disabled=True)


# ========================
# 页面函数
# ========================
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
        if Config.MIN_TEAM_SIZE <= len(selected) + 1 <= Config.MAX_TEAM_SIZE:
            if create_team_in_db(captain, selected):
                st.success("组队成功!")
                time.sleep(1)
                st.rerun()
        else:
            st.error(f"请选择{Config.MIN_TEAM_SIZE - 1}到{Config.MAX_TEAM_SIZE - 1}名队员!")


def check_admin_password():
    """管理员密码检查"""
    with st.sidebar:
        st.header("管理员登录")
        password = st.text_input("密码:", type="password", key="admin_pwd")
        if st.button("登录"):
            if hashlib.sha256(password.encode()).hexdigest() == Config.ADMIN_PASSWORD_HASH:
                st.session_state.admin_logged_in = True
                st.success("登录成功!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("密码错误!")
        if st.session_state.admin_logged_in and st.button("退出"):
            st.session_state.admin_logged_in = False
            st.rerun()


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
    incomplete_teams = [team for team in st.session_state.teams if (1 + len(team['members'])) < Config.MAX_TEAM_SIZE]

    if not incomplete_teams:
        st.success("🎉 所有队伍都已满员!")
        return

    st.subheader(f"当前共有 {len(incomplete_teams)} 支队伍未满{Config.MAX_TEAM_SIZE}人")

    for team in incomplete_teams:
        member_count = 1 + len(team['members'])
        with st.expander(f"队伍 {team['id']} - 队长: {team['captain']} ({member_count}/{Config.MAX_TEAM_SIZE})",
                         expanded=True):
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
                        if update_team_members(team['id'], team['members'] + [new_member]):
                            update_player_selection_status(new_member, True)
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


def show_team_modification_page():
    """显示队伍变更请求页面"""
    st.title("🔄 队伍变更请求")

    if not st.session_state.teams:
        st.info("暂无队伍")
        return

    # 创建队伍选项列表，格式为"队伍ID - 队长名称"
    team_options = [(team['id'], team['captain']) for team in st.session_state.teams]

    # 使用selectbox显示队伍选择
    selected_option = st.selectbox(
        "选择要修改的队伍",
        options=team_options,
        format_func=lambda x: f"队伍 {x[0]} - 队长: {x[1]}",
        key="modify_team_select"
    )

    # 获取选择的队伍ID
    team_id = selected_option[0] if selected_option else None

    if not team_id:
        return

    selected_team = next((team for team in st.session_state.teams if team['id'] == team_id), None)
    if not selected_team:
        st.error("找不到该队伍!")
        return

    st.subheader(f"队伍 {team_id} 当前信息")
    display_team_info(selected_team)

    st.markdown("---")
    st.subheader("提交变更请求")

    requester_id = st.text_input("您的游戏ID", key=f"requester_id_{team_id}")
    if not requester_id:
        st.warning("请输入您的游戏ID以提交请求")
        return

    with st.expander("申请变更队长"):
        all_members = [selected_team['captain']] + selected_team['members']
        current_captain = selected_team['captain']

        proposed_captain = st.selectbox(
            "选择新队长",
            options=all_members,
            index=all_members.index(current_captain),
            key=f"new_captain_{team_id}"
        )

        reason = st.text_area("变更原因", key=f"captain_reason_{team_id}")

        if st.button("提交队长变更申请", key=f"submit_captain_change_{team_id}"):
            if proposed_captain == current_captain:
                st.warning("请选择不同的玩家作为新队长")
            else:
                with st.spinner("提交中..."):
                    if create_team_change_request(
                            team_id=team_id,
                            request_type="change_captain",
                            requester_id=requester_id,
                            proposed_captain=proposed_captain,
                            reason=reason
                    ):
                        st.success("队长变更申请已提交，请等待管理员审批!")
                    else:
                        st.error("提交申请失败")

    with st.expander("申请移除成员"):
        if not selected_team['members']:
            st.info("该队伍没有可移除的成员")
        else:
            member_to_remove = st.selectbox(
                "选择要移除的成员",
                options=selected_team['members'],
                key=f"remove_member_{team_id}"
            )

            reason = st.text_area("移除原因", key=f"remove_reason_{team_id}")

            if st.button("提交成员移除申请", key=f"submit_remove_{team_id}"):
                with st.spinner("提交中..."):
                    if create_team_change_request(
                            team_id=team_id,
                            request_type="remove_member",
                            requester_id=requester_id,
                            member_to_remove=member_to_remove,
                            reason=reason
                    ):
                        st.success("成员移除申请已提交，请等待管理员审批!")
                    else:
                        st.error("提交申请失败")

    with st.expander("申请新增成员"):
        available_players = set(st.session_state.players[~st.session_state.players['is_selected']]['game_id'])
        if not available_players:
            st.info("没有可用的玩家可以添加")
        else:
            member_to_add = st.selectbox(
                "选择要添加的成员",
                options=list(available_players),
                key=f"add_member_{team_id}"
            )

            reason = st.text_area("添加原因", key=f"add_reason_{team_id}")

            if st.button("提交新增成员申请", key=f"submit_add_{team_id}"):
                with st.spinner("提交中..."):
                    if create_team_change_request(
                            team_id=team_id,
                            request_type="add_member",
                            requester_id=requester_id,
                            member_to_add=member_to_add,
                            reason=reason
                    ):
                        st.success("新增成员申请已提交，请等待管理员审批!")
                    else:
                        st.error("提交申请失败")


def admin_panel():
    """管理员面板"""
    st.header("📊 管理员后台")
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "玩家管理", "队伍管理", "数据维护", "活动配置", "信息更改审批", "队伍变更审批"
    ])

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
        st.subheader("待审批的玩家信息更改请求")

        def show_pending_requests():
            requests = load_change_requests("pending")
            if not requests:
                st.info("没有待审批的更改请求")
                return

            # 分页控制
            page_size = 5
            total_pages = (len(requests) + page_size - 1) // page_size
            page = st.number_input("页码", min_value=1, max_value=total_pages, value=1)

            start_idx = (page - 1) * page_size
            end_idx = min(start_idx + page_size, len(requests))

            for i in range(start_idx, end_idx):
                req = requests[i]
                with st.container():
                    st.markdown(f"### 请求ID: {req['id']} - 玩家: {req['game_id']}")

                    # 显示队长影响提示
                    captain_teams = supabase.table('teams') \
                        .select("id") \
                        .eq("captain", req['game_id']) \
                        .execute().data
                    if captain_teams:
                        st.warning(f"⚠️ 该玩家是 {len(captain_teams)} 支队伍的队长")

                    cols = st.columns([3, 1])
                    with cols[0]:
                        display_request_details(req)
                    with cols[1]:
                        if st.button("批准", key=f"approve_{req['id']}"):
                            if approve_change_request(req):
                                st.success("批准成功")
                                time.sleep(1)
                                st.rerun()
                        if st.button("拒绝", key=f"reject_{req['id']}"):
                            if update_change_request(req['id'], "rejected"):
                                st.success("已拒绝")
                                time.sleep(1)
                                st.rerun()

                    st.markdown("---")

        show_pending_requests()

    with tab6:
        st.subheader("待审批的队伍变更请求")
        pending_requests = load_team_change_requests("pending")

        if not pending_requests:
            st.info("没有待审批的队伍变更请求")
        else:
            for request in pending_requests:
                with st.container():
                    st.markdown(f"### 请求ID: {request['id']} - 队伍: {request['team_id']}")

                    team_response = supabase.table('teams').select("*").eq("id", request['team_id']).execute()
                    team = team_response.data[0] if team_response.data else None

                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**基本信息**")
                        st.write(
                            f"请求类型: `{'变更队长' if request['request_type'] == 'change_captain' else '移除成员' if request['request_type'] == 'remove_member' else '新增成员'}`")
                        st.write(f"请求者: `{request['requester_id']}`")
                        st.write(f"提交时间: `{pd.to_datetime(request['created_at']).strftime('%Y-%m-%d %H:%M:%S')}`")
                        if request['reason']:
                            st.markdown("**申请理由**")
                            st.write(request['reason'])

                    with col2:
                        st.markdown("**变更详情**")
                        if request['request_type'] == "change_captain":
                            st.write(f"当前队长: `{request['current_captain']}`")
                            st.write(f"新队长: `{request['proposed_captain']}`")
                        elif request['request_type'] == "add_member":
                            st.write(f"要添加的成员: `{request['member_to_add']}`")
                        else:
                            st.write(f"要移除的成员: `{request['member_to_remove']}`")

                    st.markdown("---")
                    action_col1, action_col2, _ = st.columns([1, 1, 2])
                    with action_col1:
                        if st.button(f"✅ 批准", key=f"approve_team_req_{request['id']}"):
                            with st.spinner("处理中..."):
                                if approve_team_change_request(request):
                                    st.success("已批准队伍变更请求")
                                    st.session_state.teams = load_teams()
                                    st.session_state.players = load_players()
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("批准失败")
                    with action_col2:
                        if st.button(f"❌ 拒绝", key=f"reject_team_req_{request['id']}"):
                            with st.spinner("处理中..."):
                                if update_team_change_request(request['id'], "rejected"):
                                    st.success("已拒绝队伍变更请求")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("拒绝失败")
                    st.markdown("---")
                    if st.checkbox(f"显示原始请求数据 [ID: {request['id']}]", key=f"raw_team_req_{request['id']}"):
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
                ["组队系统", "查看组队列表", "未满的队伍", "信息更改", "四大恶人活动", "队伍变更请求"],
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
        elif page == "队伍变更请求":
            show_team_modification_page()
    else:
        admin_panel()


if __name__ == "__main__":
    main()