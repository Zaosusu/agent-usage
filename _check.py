import json
d = json.load(open('data/usage.json', encoding='utf-8'))
sessions = [s for s in d['sessions'] if s['agent'] == 'doubao']
print(f'豆包工作: {len(sessions)} 条')
for s in sessions[:5]:
    print(f"  {s['title']} - {s['last_activity_at']} - {s['total_tokens']}")

# 看看所有 agent 的记录数
from collections import Counter
c = Counter(s['agent'] for s in d['sessions'])
print(f'\n各 Agent 记录数:')
for k, v in c.most_common():
    print(f'  {k}: {v}')
