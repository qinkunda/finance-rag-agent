# desensitize.py
import re

def mask_sensitive_data(text):
    """
    对文本进行脱敏处理
    """
    if not text:
        return text

    # 1. 手机号脱敏 (保留前3后4)
    text = re.sub(r'(1[3-9]\d)\d{4}(\d{4})', r'\1****\2', text)

    # 2. 身份证号脱敏 (保留前6后4)
    text = re.sub(r'(\d{6})\d{8}(\d{4})', r'\1********\2', text)

    # 3. 简单的人名脱敏 (假设是2-4个汉字，这里只是示例，实际可能需要更复杂的NLP或字典)
    # 注意：财务制度里通常不会出现大量随机人名，主要是报销单里会有
    # 这里做一个简单的示例：把 "张三" 变成 "张*"
    # text = re.sub(r'([\u4e00-\u9fa5])([\u4e00-\u9fa5]{1,3})', r'\1*', text)

    # 4. 金额脱敏 (可选，看需求。如果是查制度，金额不用脱敏；如果是查具体单据，建议脱敏)
    # text = re.sub(r'(\d+,\d+|\d+)\.\d{2}', '***.**', text)

    return text