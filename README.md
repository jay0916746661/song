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

## 部署

這個工具需要 Python 後端，不能只用 GitHub Pages 開啟。

Render 部署方式：

1. 到 Render 建立 `New Web Service`
2. 連接這個 GitHub repo
3. Render 會讀取 `render.yaml`
4. 部署完成後使用 Render 提供的網址開啟面板

如果平台沒有讀取 `render.yaml`，手動設定：

```text
Build Command: pip install -r requirements.txt
Start Command: python app.py
```
