# 京津冀 AI/CS 岗监控

49 校就业公众号 · 近 14 天 · 手机浏览器可用

> **注意：** GitHub 仓库地址**不能直接当网页打开**。需要本地运行，或部署到 Render 后使用 Render 提供的 URL。

## 功能

- 实习岗 / 27 届校招 一键扫描
- 历史 HTML 报告浏览
- 不依赖 GenericAgent / 微信 Bot

## 本地运行（推荐）

```bash
pip install -r requirements.txt
python app.py
# 打开 http://localhost:8765
# 手机同 WiFi: http://<本机局域网IP>:8765
```

首次「在线拉取」需本机登录微信公众平台：

```bash
python assets/wechat_mp_fetch.py
# 更新云端数据：复制到 data/ 后 git push
cp temp/wechat_mp_data.json data/wechat_mp_data.json
```

## 部署到 Render（免费公网访问）

1. 打开 [Render Dashboard](https://dashboard.render.com/) → **New → Blueprint**
2. 连接 GitHub 仓库 `Zephverve/GenericAgent`
3. Render 读取 `render.yaml` 创建 Web Service
4. 部署完成后使用 Render 显示的 URL（形如 `https://jjj-job-monitor-xxxx.onrender.com`）

若打开链接显示 **Not Found**，说明 **还没在 Render 创建服务**，不是代码坏了。

或用 Docker 本地验证：

```bash
docker build -t jjj-job-monitor .
docker run -p 8765:8765 jjj-job-monitor
```

> **说明：** 云端默认用 `data/wechat_mp_data.json` 缓存扫描；「在线拉取」请在本地 Mac 运行 `wechat_mp_fetch.py` 后更新 `data/` 再 push。

## 目录

```
app.py                  # FastAPI Web 控制台
assets/                 # 扫描、过滤、配置
temp/job_matches/       # HTML 报告（运行时生成）
```
