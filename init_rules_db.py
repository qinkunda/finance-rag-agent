"""
init_rules_db.py — 企业标准库初始化（模拟从业务系统同步标准数据到标准层）
运行: python init_rules_db.py
说明: 生产环境此步骤由同步任务(CDC/DataX)从业务库(OA/财务系统)完成；
      本脚本模拟该环节，数据值摘自《差旅费用管理办法》第三/六/七/十/十四条。
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "biz_rules.db")

def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE dim_city_tier(
        city TEXT PRIMARY KEY,
        tier TEXT NOT NULL
    );
    CREATE TABLE dim_expense_standard(
        expense_type TEXT NOT NULL,   -- 住宿/伙食/市内交通
        level TEXT NOT NULL,          -- 普通员工/经理级/总监级
        tier TEXT NOT NULL,           -- 一类/新一线/二类/三类
        limit_amount INTEGER NOT NULL,
        PRIMARY KEY(expense_type, level, tier)
    );
    CREATE TABLE dim_approval_chain(
        role TEXT NOT NULL,           -- 普通员工/经理级
        node_order INTEGER NOT NULL,  -- 审批顺序
        node_name TEXT NOT NULL,
        PRIMARY KEY(role, node_order)
    );
    """)

    # 城市分级（文档第三条；演示只录语料中涉及城市，生产环境由主数据系统维护）
    cities = [
        ("北京", "一类"), ("上海", "一类"), ("广州", "一类"), ("深圳", "一类"),
        ("成都", "新一线"), ("武汉", "新一线"), ("杭州", "新一线"),
    ]
    cur.executemany("INSERT INTO dim_city_tier VALUES(?,?)", cities)

    # 住宿标准 元/晚（文档第七条）
    lodging = [
        ("住宿", "普通员工", "一类", 500), ("住宿", "普通员工", "新一线", 400),
        ("住宿", "普通员工", "二类", 350), ("住宿", "普通员工", "三类", 300),
        ("住宿", "经理级", "一类", 700), ("住宿", "经理级", "新一线", 600),
        ("住宿", "经理级", "二类", 500), ("住宿", "经理级", "三类", 450),
        ("住宿", "总监级", "一类", 900), ("住宿", "总监级", "新一线", 800),
        ("住宿", "总监级", "二类", 700), ("住宿", "总监级", "三类", 600),
    ]
    # 伙食补助 元/人/天（文档第十条）
    meal = [
        ("伙食", "通用", "一类", 150), ("伙食", "通用", "新一线", 120),
        ("伙食", "通用", "二类", 100), ("伙食", "通用", "三类", 80),
    ]
    # 市内交通包干 元/人/天（文档第六条）
    transport = [("市内交通", "通用", "通用", 80)]
    cur.executemany("INSERT INTO dim_expense_standard VALUES(?,?,?,?)", lodging + meal + transport)

    # 审批链（文档第十四条）
    chain = [
        ("普通员工", 1, "直属上级"), ("普通员工", 2, "部门负责人"),
        ("经理级", 1, "部门负责人"), ("经理级", 2, "中心总监"),
    ]
    cur.executemany("INSERT INTO dim_approval_chain VALUES(?,?,?)", chain)

    conn.commit()

    # 校验输出
    print("== 城市分级 ==");   [print(r) for r in cur.execute("SELECT * FROM dim_city_tier")]
    print("== 费用标准 ==");   [print(r) for r in cur.execute("SELECT * FROM dim_expense_standard")]
    print("== 审批链 ==");     [print(r) for r in cur.execute("SELECT * FROM dim_approval_chain")]
    conn.close()
    print(f"\n标准库已创建: {DB_PATH}")

if __name__ == "__main__":
    main()