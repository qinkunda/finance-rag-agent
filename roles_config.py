# roles_config.py - 角色权限配置

ROLES = {
    "admin": {
        "permissions": ["public", "internal", "confidential"],
        "description": "管理员 - 可查看所有文档"
    },
    "manager": {
        "permissions": ["public", "internal"],
        "description": "经理 - 可查看公开 + 内部文档"
    },
    "employee": {
        "permissions": ["public"],
        "description": "普通员工 - 仅可查看公开文档"
    },
}


def get_user_permissions(role: str) -> list:
    """获取指定角色的可访问文档级别列表"""
    role_info = ROLES.get(role, ROLES["employee"])
    return role_info["permissions"]