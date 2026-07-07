"""运行环境检测（本地 Mac vs 云端 Render 等）。"""
import os


def is_cloud_runtime():
    """无图形界面、无法扫码登录的云容器。"""
    if os.environ.get('RENDER') or os.environ.get('RENDER_EXTERNAL_URL'):
        return True
    if os.environ.get('JOB_MONITOR_CLOUD', '').lower() in ('1', 'true', 'yes'):
        return True
    return False


def must_headless_browser():
    """Playwright 是否必须 headless（Linux 无 DISPLAY）。"""
    if is_cloud_runtime():
        return True
    return not bool(os.environ.get('DISPLAY'))


def online_fetch_allowed():
    """云端不支持在线拉取（需浏览器登录 + 易触发微信频控）。"""
    return not is_cloud_runtime()
