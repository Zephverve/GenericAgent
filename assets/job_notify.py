"""微信岗位推送 helper。需先配置 wechat_user_id。"""
import json, os, socket, sys, uuid
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(_ROOT, 'assets', 'job_monitor_config.json')
UID_CACHE = os.path.join(_ROOT, 'temp', 'wechat_notify_uid.json')
PUSH_QUEUE = os.path.join(_ROOT, 'temp', 'wechat_push_queue.json')
SESSION_FILE = os.path.join(_ROOT, 'temp', 'wechat_session.json')
WECHATAPP_PORT = 19531


def _load_config():
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f)


def _save_uid(uid):
    os.makedirs(os.path.dirname(UID_CACHE), exist_ok=True)
    with open(UID_CACHE, 'w', encoding='utf-8') as f:
        json.dump({'wechat_user_id': uid, 'saved_at': datetime.now().isoformat()},
                  f, ensure_ascii=False, indent=2)
    cfg = _load_config()
    cfg['notify']['wechat_user_id'] = uid
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f'[job_notify] uid saved: {uid}')


def get_notify_uid():
    cfg = _load_config()
    uid = (cfg.get('notify') or {}).get('wechat_user_id', '').strip()
    if uid:
        return uid
    if os.path.exists(UID_CACHE):
        with open(UID_CACHE, encoding='utf-8') as f:
            uid = json.load(f).get('wechat_user_id', '').strip()
            if uid:
                return uid
    last_uid = os.path.join(_ROOT, 'temp', 'wechat_last_uid.txt')
    if os.path.exists(last_uid):
        return open(last_uid, encoding='utf-8').read().strip()
    return os.environ.get('WECHAT_NOTIFY_UID', '').strip()


def wechatapp_running():
    """wechatapp 占用 19531 端口锁时表示已在运行。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', WECHATAPP_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _load_push_queue():
    if not os.path.isfile(PUSH_QUEUE):
        return []
    try:
        with open(PUSH_QUEUE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_push_queue(items):
    os.makedirs(os.path.dirname(PUSH_QUEUE), exist_ok=True)
    with open(PUSH_QUEUE, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def enqueue_push(item):
    queue = _load_push_queue()
    item.setdefault('id', uuid.uuid4().hex[:12])
    item.setdefault('created_at', datetime.now().isoformat(timespec='seconds'))
    queue.append(item)
    _save_push_queue(queue)
    return item['id']


def _load_session():
    if not os.path.isfile(SESSION_FILE):
        return {}
    try:
        with open(SESSION_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _send_ok(resp):
    if not isinstance(resp, dict):
        return False
    ret = resp.get('ret')
    if ret is None:
        return True
    return ret == 0


def _get_bot():
    uid = get_notify_uid()
    if not uid:
        raise RuntimeError(
            '未配置微信推送 uid。运行: python assets/job_notify.py --save-uid <你的uid>')
    sys.path.insert(0, os.path.join(_ROOT, 'frontends'))
    from wechatapp import WxBotClient
    bot = WxBotClient()
    if not bot.token:
        raise RuntimeError('微信 Bot 未登录。请先运行 python frontends/wechatapp.py 完成 QR 登录。')
    return uid, bot


def _direct_send_html(summary, path):
    """wechatapp 未运行时直接发送（单客户端）。"""
    uid, bot = _get_bot()
    ctx = _load_session().get('context_token', '')
    resp = bot.send_text(uid, summary[:3000], context_token=ctx)
    if not _send_ok(resp) and ctx:
        resp = bot.send_text(uid, summary[:3000], context_token='')
    if not _send_ok(resp):
        raise RuntimeError(f'文字发送失败: {resp}')
    import time
    time.sleep(0.6)
    resp = bot.send_file(uid, path, context_token=ctx)
    if not _send_ok(resp) and ctx:
        resp = bot.send_file(uid, path, context_token='')
    if not _send_ok(resp):
        raise RuntimeError(f'文件发送失败: {resp}')
    return uid


def send_wechat(text):
    if wechatapp_running():
        qid = enqueue_push({'type': 'text', 'text': text[:3000]})
        print(f'[job_notify] queued text ({qid}), wechatapp 将自动发送')
        return True
    uid, bot = _get_bot()
    ctx = _load_session().get('context_token', '')
    resp = bot.send_text(uid, text[:3000], context_token=ctx)
    if not _send_ok(resp) and ctx:
        resp = bot.send_text(uid, text[:3000], context_token='')
    if not _send_ok(resp):
        raise RuntimeError(f'发送失败: {resp}')
    print(f'[job_notify] sent {len(text)} chars to {uid}')
    return True


def send_wechat_matches(matches, batch='', mode='internship', footer=''):
    """生成 HTML 报告入队，用户发「推送」时发送附件。"""
    import shutil
    sys.path.insert(0, os.path.join(_ROOT, 'assets'))
    from job_filter import get_mode_config, save_jobs_html, MATCHES

    label_cfg = get_mode_config(mode)
    mode_label = label_cfg.get('label', mode)

    if not matches:
        send_wechat(f'📭【{mode_label}】14天内暂无匹配岗位。')
        return 0

    path = os.path.abspath(save_jobs_html(matches, batch=batch, mode=mode, footer=footer))
    # 微信附件显示友好文件名
    nice_name = f'{mode_label}-{len(matches)}条.html'
    nice_path = os.path.join(MATCHES, nice_name)
    shutil.copy2(path, nice_path)

    item = {
        'type': 'html_report',
        'summary': (f'📢【{mode_label}】共 {len(matches)} 条（{label_cfg.get("label", mode)} · 近14天）\n'
                    f'📎 附件 HTML：下载后用浏览器打开，链接可点击'),
        'html_path': nice_path,
        'mode': mode,
        'count': len(matches),
    }

    if wechatapp_running():
        qid = enqueue_push(item)
        print(f'[job_notify] queued HTML {nice_path} ({len(matches)} jobs, id={qid})')
        print('[job_notify] 请给 Bot 发「推送」触发发送')
        return 1

    raise RuntimeError('wechatapp 未运行，请先 python frontends/wechatapp.py')


if __name__ == '__main__':
    if '--save-uid' in sys.argv:
        idx = sys.argv.index('--save-uid')
        if idx + 1 < len(sys.argv):
            _save_uid(sys.argv[idx + 1])
        else:
            uid = get_notify_uid()
            if uid:
                _save_uid(uid)
            else:
                print('用法: python assets/job_notify.py --save-uid <wechat_user_id>')
                print('  或先给 Bot 发消息，再运行 python assets/job_notify.py --save-uid')
    elif '--flush' in sys.argv:
        if not wechatapp_running():
            print('[job_notify] wechatapp 未运行，无法 flush')
            sys.exit(1)
        print(f'[job_notify] 队列 {len(_load_push_queue())} 条，请给 Bot 发消息触发发送')
    elif '--wait-send' in sys.argv:
        import time
        text = '测试推送'
        for a in sys.argv:
            if a.startswith('--text='):
                text = a.split('=', 1)[1]
        print('[job_notify] 请现在用微信给 Bot 发任意消息…')
        for _ in range(40):
            uid = get_notify_uid()
            if uid:
                _save_uid(uid)
                send_wechat(text)
                break
            time.sleep(3)
        else:
            print('[job_notify] 90秒内未收到消息，请先给 Bot 发消息后重试')
    elif len(sys.argv) > 1 and sys.argv[1] == 'send':
        send_wechat(sys.argv[2] if len(sys.argv) > 2 else '测试推送')
    else:
        print('用法:')
        print('  python assets/job_notify.py --save-uid [uid]')
        print('  python assets/job_notify.py --wait-send --text="消息"')
        print('  python assets/job_notify.py send "消息内容"')
