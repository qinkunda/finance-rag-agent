from sentence_transformers import SentenceTransformer
import os

# 1. 设置镜像源（防止它手滑又要去下载）
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

# 2. 指向你电脑里真正的模型路径！
# 注意：我帮你把名字改成了 large，并且指向了它最里面的 master 文件夹
local_model_path = r"C:\Users\39034\Desktop\AI_Project\RAG_Demo\models\BAAI--bge-large-zh-v1.5\snapshots\master"

print("正在加载本地的 bge-large 模型，请稍候...")

# 3. 加载本地模型（这样它就绝对不会去联网了）
model = SentenceTransformer(local_model_path)

# 4. 测试：把一句话变成向量
text = "今天长沙的天气真不错，适合出门散步。"
vector = model.encode(text)

print("=" * 50)
print(f"文本转换成功！")
print(f"这句话变成了 {len(vector)} 个数字组成的向量。")
print(f"向量的前 5 个数字是：{vector[:5]}")
print("=" * 50)