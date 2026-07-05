# 京津冀 AI/CS 岗监控

49 校就业公众号 · 近 14 天 · 手机浏览器可用

**在线访问（部署后）：** https://jjj-job-monitor.onrender.com （Render 部署完成后）

## 功能

- 实习岗 / 27 届校招 一键扫描
- 历史 HTML 报告浏览
- 不依赖 GenericAgent / 微信 Bot

## 本地运行

```bash
pip install -r requirements.txt
python app.py
# 打开 http://localhost:8765
```

首次「在线拉取」需本机登录微信公众平台：

```bash
python assets/wechat_mp_fetch.py
```

## 部署到 Render（免费）

1. 打开 [Render Dashboard](https://dashboard.render.com/) → **New → Blueprint**
2. 连接 GitHub 仓库 `Zephverve/GenericAgent`
3. Render 会读取 `render.yaml` 自动创建 Web Service
4. 部署完成后访问 Render 提供的 URL

或用 Docker 本地验证：

```bash
docker build -t jjj-job-monitor .
docker run -p 8765:8765 jjj-job-monitor
```

> **说明：** 云端实例无持久化存储，「在线拉取」需在本地 Mac 运行 `wechat_mp_fetch.py` 生成缓存后上传 `temp/wechat_mp_data.json`，或勾选扫描时使用已有缓存。

## 目录

```
app.py                  # FastAPI Web 控制台
assets/                 # 扫描、过滤、配置
temp/job_matches/       # HTML 报告（运行时生成）
```
