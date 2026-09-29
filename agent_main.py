"""
agent_main.py — 差旅报销 Agent（Tool Calling + 人工确认门 + 模型自动降级）
运行: python agent_main.py
"""
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from openai import PermissionDeniedError, RateLimitError

from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
from agent_tools import get_travel_standard, overlimit_check, create_expense_report

SYSTEM_PROMPT = """你是企业差旅报销助手。规则：
1. 用户提出报销需求时，先调用 get_travel_standard 查询标准，涉及具体价格再调用 overlimit_check 预检。
2. 调用 create_expense_report 前，必须向用户汇总：费用明细、超标情况、审批链，并征得用户明确同意。
3. 数据以工具返回为准，不得自行编造标准。"""

# (模型名, 额外参数) —— 按优先级探测，403/429 自动切下一个
FALLBACK = [
    ("qwen-plus", {}),
    ("qwen3.7-plus", {"extra_body": {"enable_thinking": False}}),
    ("qwen-turbo", {}),
    ("qwen3.6-flash", {}),
]


def build_llm(tools):
    for model, extra in FALLBACK:
        try:
            t = ChatOpenAI(
                model=model, base_url=DASHSCOPE_BASE_URL,
                api_key=DASHSCOPE_API_KEY, temperature=0.1, **extra,
            )
            t.invoke("ping")
            print(f"模型 [{model}] 额度正常，已激活")
            return t.bind_tools(tools)
        except (PermissionDeniedError, RateLimitError):
            print(f"模型 [{model}] 额度耗尽，自动切换下一个...")
            continue
        except Exception as e:
            print(f"模型 [{model}] 连接失败: {e}，尝试下一个...")
            continue
    raise RuntimeError("所有候选模型均不可用，请检查额度或充值")


def main():
    tools = [get_travel_standard, overlimit_check, create_expense_report]
    tool_map = {t.name: t for t in tools}
    llm = build_llm(tools)

    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    print("差旅报销 Agent 已启动（输入 exit 退出）")
    print("示例：帮我报销北京出差4天，普通员工，住宿一晚480")

    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("exit", "quit", "退出"):
            break
        messages.append(HumanMessage(content=user_input))
        user_confirmed = any(k in user_input for k in ("确认", "同意", "可以", "提交"))

        while True:
            resp = llm.invoke(messages)
            messages.append(resp)
            if not resp.tool_calls:
                print("Agent:", resp.content)
                break
            for tc in resp.tool_calls:
                print("  [工具调用] {}({})".format(tc["name"], tc["args"]))
                if tc["name"] == "create_expense_report" and not user_confirmed:
                    print("  [人工确认门] 建单为写操作，请核对上方汇总后回复【确认】")
                    break
                result = tool_map[tc["name"]].invoke(tc["args"])
                print("  [工具返回] {}".format(result))
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
            else:
                continue
            break

if __name__ == "__main__":
    main()