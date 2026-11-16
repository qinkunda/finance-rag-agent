"""兼容旧导入名称；平台实现已收敛为统一编排，而非多个独立 Agent 入口。"""
from .orchestrator import UnifiedFinancePlatform


class Supervisor(UnifiedFinancePlatform):
    """旧代码兼容层。新代码请直接使用 UnifiedFinancePlatform。"""
    pass


__all__ = ["UnifiedFinancePlatform", "Supervisor"]
