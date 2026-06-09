# 影片下載面板

本機影片下載面板，支援貼上公開 Facebook、Instagram、YouTube 影片連結下載，也可以加入喜好來源後掃描候選影片。

## 啟動

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

打開：

```text
http://127.0.0.1:8787
```

## 注意

- 下載檔案會存到 `downloads/`，不會提交到 Git。
- `sources.json` 與 `candidates.json` 是本機偏好資料，不會提交到 Git。
- 私人、限好友或需要登入的影片可能無法下載。
- 這個工具適合下載你有權保存的公開內容。
