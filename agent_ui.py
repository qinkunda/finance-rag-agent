"""旧报销 UI 启动入口兼容层：现在统一打开完整财务工作台。"""
import uvicorn


if __name__ == "__main__":
    print("原报销独立界面已合并；正在启动统一财务工作台：http://127.0.0.1:8000")
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)
