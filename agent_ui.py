"""
agent_ui.py — 差旅报销 Agent 的 Web 界面（Gradio 5.x messages 格式）
运行: pip install gradio  然后  python agent_ui.py
"""
import gradio as gr
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agent_main import build_llm, SYSTEM_PROMPT
from agent_tools import get_travel_standard, overlimit_check, create_expense_report

tools = [get_travel_standard, overlimit_check, create_expense_report]
tool_map = {t.name: t for t in tools}
llm = build_llm(tools)

MESSAGES = [SystemMessage(content=SYSTEM_PROMPT)]


def respond(user_input, chat_history, _log):
    if not user_input or not user_input.strip():
        return chat_history, "", ""
    MESSAGES.append(HumanMessage(content=user_input))
    confirmed = any(k in user_input for k in ("确认", "同意", "可以", "提交"))
    logs = []
    while True:
        resp = llm.invoke(MESSAGES)
        MESSAGES.append(resp)
        if not resp.tool_calls:
            chat_history = chat_history + [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": resp.content},
            ]
            return chat_history, "\n".join(logs), ""
        pending = False
        for tc in resp.tool_calls:
            logs.append("[调用] {}({})".format(tc["name"], tc["args"]))
            if tc["name"] == "create_expense_report" and not confirmed:
                logs.append("[人工确认门] 建单是写操作，请在对话中回复【确认】")
                pending = True
                break
            result = tool_map[tc["name"]].invoke(tc["args"])
            logs.append("[返回] {}".format(result))
            MESSAGES.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        if pending:
            MESSAGES.pop()
            chat_history = chat_history + [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": "费用汇总已生成（见右侧工具记录），请核对后输入【确认】"},
            ]
            return chat_history, "\n".join(logs), ""


def clear():
    MESSAGES.clear()
    MESSAGES.append(SystemMessage(content=SYSTEM_PROMPT))
    return [], "", ""


with gr.Blocks(title="差旅报销 Agent") as demo:
    gr.Markdown("# 差旅报销 Agent — LangChain Tool Calling + 人工确认门\n"
                "数据源：企业标准库 biz_rules.db（对应数仓维表层，规则零硬编码）")
    with gr.Row():
        chatbot = gr.Chatbot(label="对话", height=430)
        logbox = gr.Textbox(label="Agent 决策过程（工具调用记录）", lines=18)
    with gr.Row():
        msg = gr.Textbox(label="输入指令，如：帮我报销深圳出差3天，住宿一晚550", scale=5)
        send = gr.Button("发送", variant="primary")
        clr = gr.Button("清空")
    msg.submit(respond, [msg, chatbot, logbox], [chatbot, logbox, msg])
    send.click(respond, [msg, chatbot, logbox], [chatbot, logbox, msg])
    clr.click(clear, None, [chatbot, logbox, msg])

if __name__ == "__main__":
    demo.launch()