#!/usr/bin/env python3
"""京津冀 AI/CS 岗监控 — Web 控制台（手机/浏览器可用）。

本地启动:
  pip install -r requirements.txt
  python app.py

部署:
  Docker / Render 见 README.md
"""
import json, os, sys, threading, time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / 'assets'))
MATCHES = _ROOT / 'temp' / 'job_matches'
DATA = _ROOT / 'data'
PORT = int(os.environ.get('PORT') or os.environ.get('JOB_MONITOR_PORT', '8765'))


def _seed_runtime_data():
    """云端无 temp 缓存时，从 data/ 复制公众号数据（本地 Mac 可定期更新 data/wechat_mp_data.json）。"""
    import shutil
    temp_dir = _ROOT / 'temp'
    temp_dir.mkdir(parents=True, exist_ok=True)
    src = DATA / 'wechat_mp_data.json'
    dst = temp_dir / 'wechat_mp_data.json'
    if src.is_file() and not dst.is_file():
        shutil.copy2(src, dst)
        print(f'[app] 已从 data/ 初始化 wechat_mp_data.json')

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

app = FastAPI(title='岗位监控', docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event('startup')
def _on_startup():
    _seed_runtime_data()
    MATCHES.mkdir(parents=True, exist_ok=True)

_scan_lock = threading.Lock()
_STALE_SEC = 600  # 10 分钟（仅本地扫描）
_STALE_SEC_FETCH = 600  # 10 分钟（在线拉取）
_state = {
    'running': False,
    'mode': '',
    'phase': 'idle',
    'progress': '',
    'message': '',
    'count': 0,
    'report_url': '',
    'error': '',
    'started_at': '',
    '_started_ts': 0,
}


def _set_state(**kw):
    _state.update(kw)


def _is_stale_running():
    if not _state.get('running'):
        return False
    ts = _state.get('_started_ts') or 0
    limit = _STALE_SEC_FETCH if _state.get('_slow_fetch') else _STALE_SEC
    return ts and (time.time() - ts > limit)


def _reset_state(msg=''):
    _set_state(running=False, phase='idle', progress='', error='',
               message=msg, count=0, report_url='', _started_ts=0)


def _list_reports(limit=20):
    if not MATCHES.is_dir():
        return []
    files = sorted(MATCHES.glob('*.html'), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:limit]:
        name = p.name
        mode = 'internship' if 'internship' in name else 'campus2027' if 'campus2027' in name else ''
        out.append({
            'name': name,
            'url': f'/reports/{name}',
            'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(p.stat().st_mtime)),
            'mode': mode,
        })
    return out


def _run_scan(mode: str, refresh: bool):
    from runtime_env import online_fetch_allowed
    from wechat_mp_scan import scan_wechat_accounts
    from job_filter import get_mode_config, save_match_report, save_jobs_html

    if refresh and not online_fetch_allowed():
        refresh = False

    label = get_mode_config(mode).get('label', mode)
    try:
        def on_progress(i, total, msg):
            _set_state(phase='fetch', progress=f'{i}/{total}', message=msg)

        if refresh:
            _set_state(phase='fetch',
                       message='正在在线拉取49校（约3–4分钟，有 fakeid 缓存）…',
                       progress='0/49', _slow_fetch=True)
        else:
            _set_state(phase='match', message=f'正在匹配【{label}】…', progress='', _slow_fetch=False)

        matched = scan_wechat_accounts(mode=mode, refresh=refresh, on_fetch_progress=on_progress)

        _set_state(phase='match', message=f'正在匹配【{label}】…', progress='')
        if not matched:
            _set_state(phase='done', count=0, message='14天内暂无匹配',
                       report_url='', error='')
            return

        path = save_jobs_html(matched, batch='web', mode=mode,
                              footer=f'Web · 共 {len(matched)} 条')
        report_name = Path(path).name
        save_match_report(matched, batch='web', mode=mode)
        _set_state(phase='done', count=len(matched),
                   message=f'完成，共 {len(matched)} 条',
                   report_url=f'/reports/{report_name}', error='')
    except Exception as e:
        _set_state(phase='error', error=str(e), message=f'扫描失败: {e}')
    finally:
        _set_state(running=False, _started_ts=0)


def _start_scan(mode: str, refresh: bool):
    from runtime_env import online_fetch_allowed
    if refresh and not online_fetch_allowed():
        return False, '云端不支持在线拉取，请取消勾选，直接使用缓存扫描'
    with _scan_lock:
        if _state['running']:
            if _is_stale_running():
                _reset_state('上次扫描超时，已自动重置')
            else:
                return False, '已有扫描在进行中，请查看下方进度（或点重置）'
        _set_state(running=True, mode=mode, phase='start', progress='', error='',
                   count=0, report_url='',
                   started_at=time.strftime('%H:%M:%S'),
                   _started_ts=time.time(),
                   message='任务已启动…')
    threading.Thread(target=_run_scan, args=(mode, refresh), daemon=True).start()
    return True, 'ok'


INDEX_HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>京津冀 AI/CS 岗监控</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 16px; background: #f0f2f5; color: #1a1a1a; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: #666; font-size: 13px; margin-bottom: 20px; line-height: 1.5; }
  .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
  .card { background: #fff; border: none; border-radius: 12px; padding: 20px 12px;
          font-size: 16px; font-weight: 600; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.08);
          transition: transform .15s; }
  .card:active { transform: scale(.98); }
  .card.intern { color: #1677ff; }
  .card.campus { color: #722ed1; }
  .card small { display: block; font-weight: 400; font-size: 12px; color: #999; margin-top: 4px; }
  .opt { display: flex; align-items: center; gap: 8px; font-size: 14px; margin-bottom: 16px; }
  .status { background: #fff; border-radius: 12px; padding: 16px; margin-bottom: 16px;
            display: none; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .status.show { display: block; }
  .status .msg { font-size: 14px; line-height: 1.6; }
  .status .prog { color: #1677ff; font-size: 13px; margin-top: 8px; }
  .status.error { border-left: 4px solid #ff4d4f; }
  .status.done { border-left: 4px solid #52c41a; }
  .btn-link { display: inline-block; margin-top: 12px; padding: 10px 20px; background: #1677ff;
              color: #fff; border-radius: 8px; text-decoration: none; font-size: 14px; }
  .hist { background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .hist h2 { font-size: 15px; margin: 0 0 12px; }
  .hist a { display: block; padding: 10px 0; border-bottom: 1px solid #f0f0f0;
            color: #1677ff; text-decoration: none; font-size: 14px; }
  .hist a:last-child { border: none; }
  .hist .time { color: #999; font-size: 12px; }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <h1>京津冀 AI/CS 岗监控</h1>
  <div class="sub">49 校就业公众号 · 近 14 天 · 点击即扫<br>手机浏览器打开，链接可点</div>

  <div class="cards">
    <button class="card intern" onclick="scan('internship')">
      实习岗
      <small>AI/CS 实习</small>
    </button>
    <button class="card campus" onclick="scan('campus2027')">
      27届校招
      <small>2027 应届</small>
    </button>
  </div>

  <label class="opt" id="refreshRow" style="display:none"><input type="checkbox" id="refresh"> 在线拉取（约3–4分钟，仅本机可用）</label>
  <div class="sub cloud-note" id="cloudNote" style="display:none;color:#d48806;margin-top:-8px">
    ☁️ 云端模式：使用已打包的公众号缓存，直接点扫描即可（无需在线拉取）
  </div>

  <div id="status" class="status">
    <div class="msg" id="msg"></div>
    <div class="prog" id="prog"></div>
    <a id="openReport" class="btn-link" style="display:none" href="#">查看报告 →</a>
    <button id="resetBtn" onclick="resetScan()" style="display:none;margin-top:10px;padding:8px 14px;
            background:#fff;border:1px solid #ddd;border-radius:8px;font-size:13px;color:#666">重置扫描</button>
  </div>

  <div class="hist">
    <h2>历史报告</h2>
    <div id="histList">加载中…</div>
  </div>

<script>
async function scan(mode) {
  const refresh = document.getElementById('refresh').checked;
  const st = document.getElementById('status');
  st.className = 'status show';
  document.getElementById('msg').innerHTML = '<span class="spin">⏳</span> 启动中…';
  document.getElementById('prog').textContent = '';
  document.getElementById('openReport').style.display = 'none';

  const r = await fetch('/api/scan?mode=' + mode + '&refresh=' + refresh, {method:'POST'});
  const d = await r.json();
  if (!d.ok) {
    document.getElementById('msg').textContent = d.error || '启动失败';
    document.getElementById('resetBtn').style.display = 'inline-block';
    poll();
    return;
  }
  document.getElementById('resetBtn').style.display = 'inline-block';
  poll();
}

async function resetScan() {
  await fetch('/api/reset', {method:'POST'});
  document.getElementById('msg').textContent = '已重置，请重新点击扫描';
  document.getElementById('prog').textContent = '';
  document.getElementById('resetBtn').style.display = 'none';
  document.getElementById('status').className = 'status show';
}

async function poll() {
  const r = await fetch('/api/status');
  const s = await r.json();
  const msg = document.getElementById('msg');
  const prog = document.getElementById('prog');
  const st = document.getElementById('status');
  const link = document.getElementById('openReport');
  const resetBtn = document.getElementById('resetBtn');

  st.className = 'status show';

  if (s.running) {
    msg.innerHTML = '<span class="spin">⏳</span> ' + (s.message || '扫描中…');
    prog.textContent = (s.started_at ? '开始 ' + s.started_at + ' · ' : '') + (s.progress || '');
    resetBtn.style.display = 'inline-block';
    setTimeout(poll, 1500);
    return;
  }
  resetBtn.style.display = 'none';
  if (s.error) {
    st.className = 'status show error';
    msg.textContent = s.message || s.error;
    prog.textContent = '';
    return;
  }
  if (s.report_url) {
    st.className = 'status show done';
    msg.textContent = '✅ ' + (s.message || ('共 ' + s.count + ' 条'));
    link.href = s.report_url;
    link.style.display = 'inline-block';
    loadHist();
    return;
  }
  st.className = 'status show';
  msg.textContent = s.message || '📭 14天内暂无匹配';
  loadHist();
}

async function loadHist() {
  const r = await fetch('/api/reports');
  const list = await r.json();
  const el = document.getElementById('histList');
  if (!list.length) { el.innerHTML = '<span style="color:#999">暂无</span>'; return; }
  el.innerHTML = list.map(x =>
    '<a href="' + x.url + '"><span class="time">' + x.mtime + '</span> ' + x.name + '</a>'
  ).join('');
}

loadHist();
fetch('/api/config').then(r=>r.json()).then(c=>{
  if (!c.online_fetch) {
    document.getElementById('cloudNote').style.display = 'block';
  } else {
    document.getElementById('refreshRow').style.display = 'flex';
  }
});
fetch('/api/status').then(r=>r.json()).then(s=>{ if(s.running) poll(); });
</script>
</body>
</html>'''


@app.get('/', response_class=HTMLResponse)
def index():
    return INDEX_HTML


@app.get('/api/config')
def api_config():
    from runtime_env import online_fetch_allowed, is_cloud_runtime
    return {
        'online_fetch': online_fetch_allowed(),
        'cloud': is_cloud_runtime(),
    }


@app.post('/api/scan')
def api_scan(mode: str = Query(..., pattern='^(internship|campus2027)$'),
             refresh: bool = Query(False)):
    ok, msg = _start_scan(mode, refresh)
    if not ok:
        return JSONResponse({'ok': False, 'error': msg, 'status': dict(_state)})
    return {'ok': True}


@app.post('/api/reset')
def api_reset():
    with _scan_lock:
        _reset_state('已手动重置')
    return {'ok': True}


@app.get('/api/status')
def api_status():
    st = dict(_state)
    st.pop('_started_ts', None)
    return st


@app.get('/api/reports')
def api_reports():
    return _list_reports()


@app.get('/reports/{filename}')
def get_report(filename: str):
    safe = Path(filename).name
    path = MATCHES / safe
    if not path.is_file():
        return HTMLResponse(
            '<h1>报告不存在</h1><p>可能已被清理或服务重启后丢失。</p>'
            '<p><a href="/">返回首页重新扫描</a></p>',
            status_code=404,
        )
    return FileResponse(path, media_type='text/html; charset=utf-8')


if __name__ == '__main__':
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        os.environ.pop(k, None)
    import uvicorn
    _seed_runtime_data()
    MATCHES.mkdir(parents=True, exist_ok=True)
    print(f'[job_monitor_web] http://0.0.0.0:{PORT}')
    print(f'  手机访问: http://<本机局域网IP>:{PORT}')
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning')
