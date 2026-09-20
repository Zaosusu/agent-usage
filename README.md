# agent-usage

> 鏈湴澶?AI Agent Token 鐢ㄩ噺缁熶竴鐩戞帶鐪嬫澘

鐩戞帶浣犵數鑴戜笂鎵€鏈?AI Agent 鐨?token 鐢ㄩ噺锛岀粺涓€鐪嬫澘灞曠ず銆?*鎻掍欢鍖栨灦鏋?*鈥斺€旇浜嗘柊 Agent锛屼涪涓€涓彃浠舵枃浠跺氨鑳芥帴鍏ワ紱**瀹炴椂鐩戞帶**鈥斺€旀暟鎹簮涓€鍙橈紝鐪嬫澘鑷姩鍒锋柊銆?
## 鍔熻兘

- 鎬荤敤閲?KPI銆佹瘡鏃ヨ秼鍔垮爢鍙犳煴銆佸悇 Agent 瀵规瘮銆佹ā鍨?TOP15銆佷細璇濇槑缁?- 瀹炴椂鎺ㄩ€侊紙SSE锛夛紝鏁版嵁婧愬彉鍖栧嵆鑷姩鍒锋柊
- 鍗曟枃浠?exe锛屾棤闇€瀹夎 Python
- 鏈湴杩愯锛屾暟鎹笉涓婁紶

## 蹇€熷紑濮?
1. 涓嬭浇 `dist/AgentTokenMonitor.exe`
2. 鍙屽嚮杩愯锛岃嚜鍔ㄦ墦寮€ http://127.0.0.1:8765/
3. 鐪嬫暟

鍛戒护琛屽弬鏁帮細

```
AgentTokenMonitor.exe [--port 8765] [--no-open] [--interval 5] [--full]
```

## 鍐呯疆 Agent 鏀寔

| Agent | 绮剧‘搴?| 鏁版嵁婧?|
| --- | --- | --- |
| Codex / Claude | 绮剧‘ | CC Switch `proxy_request_logs` |
| Kimi Code | 绮剧‘ | `~/.kimi/sessions/**/wire.jsonl` |
| WorkBuddy | 绮剧‘+璐圭敤 | `~/.workbuddy/workbuddy.db` |
| CodeBuddy | 绮剧‘ | `~/.codebuddy/projects/**/*.jsonl` |
| 璞嗗寘宸ヤ綔 | 浼扮畻 | IndexedDB + 浼氳瘽杞ㄨ抗 |
| 鍗冮棶宸ヤ綔 | 浼扮畻 | `~/.qwenworkcn/projects/**/*.jsonl` |
| ZCode | 绮剧‘ | `~/.zcode/cli/db/db.sqlite` |

> 浜戠 Agent锛堣眴鍖?鍗冮棶锛夌殑 token 璁¤垂鍦ㄦ湇鍔＄锛屾湰鍦颁笉钀借处锛屽彧鑳戒及绠椼€?
## 鎺ュ叆鏂?Agent

### 鏂瑰紡涓€锛氭彃浠舵枃浠?
鍦?`plugins/` 鏂板缓 `<name>.py`锛?
```python
KEY = 'myagent'
NAME = '鎴戠殑 Agent'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.myagent\\data']

def scan(full, need, mark):
    # 杩斿洖浼氳瘽鍒楄〃锛屾牸寮忚 README 鏂囨。
    return [...]
```

閲嶅惎鍗冲彲銆?
### 鏂瑰紡浜岋細API 鑷姪鎺ュ叆

```bash
curl -X POST http://127.0.0.1:8765/api/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "MyAgent", "data_path": "C:/Users/you/.myagent/sessions/a.jsonl"}'
```

鎵句笉鍒版暟鎹椂浼氳繑鍥炵粨鏋勫寲鎻愮ず锛屽憡璇変綘鍘诲摢鎵俱€?
## 鏋舵瀯

```
app.py          鍏ュ彛
serve.py        HTTP + SSE 鏈嶅姟
engine/
  core.py       鎵弿璋冨害銆佽仛鍚?  registry.py   鎻掍欢鍙戠幇/鍔犺浇
  watcher.py    瀹炴椂鐩戞帶
  common.py     鍏叡宸ュ叿
plugins/        鍚?Agent 閫傞厤鍣?web/            ECharts 鐪嬫澘
```

## 寮€鍙?
```bash
python app.py          # 婧愮爜杩愯
python monitor.py scan # 鍛戒护琛屾壂鎻?build.bat              # 鎵撳寘 exe
```

Python 3.8+锛屾棤绗笁鏂逛緷璧栥€?
## License

MIT


