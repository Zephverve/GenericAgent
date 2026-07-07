"""微信公众号岗位监控（搜狗搜索 + RSS + 手动 inbox）。"""
import os, re, sys, time
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'assets'))

from job_filter import filter_and_dedup, get_mode_config, save_match_report, _load_config
from job_fetch import fetch_url
from job_notify import send_wechat_matches


def _resolve_platform_file(cfg):
    from wechat_mp_fetch import resolve_platform_file
    return resolve_platform_file(cfg)


_LAST_FETCH_AT = 0
FETCH_TTL_SEC = 300


def _online_fetch(on_progress=None, force=False):
    """在线拉取 mp 数据；5 分钟内重复调用会跳过（避免双模式扫两次）。"""
    from runtime_env import online_fetch_allowed
    if not online_fetch_allowed():
        raise RuntimeError(
            '云端不支持「在线拉取」（无法扫码登录微信公众平台）。'
            '请直接点扫描，使用已缓存的 data/wechat_mp_data.json；'
            '更新数据请在本地 Mac 运行: python assets/wechat_mp_fetch.py')
    global _LAST_FETCH_AT
    if not force and _LAST_FETCH_AT and time.time() - _LAST_FETCH_AT < FETCH_TTL_SEC:
        print('[mp_scan] skip fetch (5min 内已拉取)')
        return
    from wechat_mp_fetch import fetch_platform_data, FetchNeedsLogin
    try:
        fetch_platform_data(on_progress=on_progress)
    except FetchNeedsLogin as e:
        raise RuntimeError('mp 登录已过期，请在 Mac 运行: python assets/wechat_mp_fetch.py') from e
    except ImportError as e:
        raise RuntimeError('缺少 playwright，请安装: pip install playwright && playwright install chromium') from e
    _LAST_FETCH_AT = time.time()


def _parse_rss(xml_text, account_name, school=''):
    jobs = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return jobs
    channel = root.find('channel')
    items = root.findall('.//item') if channel is None else channel.findall('item')
    for item in items:
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        desc = re.sub(r'<[^>]+>', ' ', item.findtext('description') or '')
        desc = re.sub(r'\s+', ' ', desc).strip()
        pub = item.findtext('pubDate') or ''
        date = datetime.now().strftime('%Y-%m-%d')
        if pub:
            try:
                date = parsedate_to_datetime(pub).strftime('%Y-%m-%d')
            except Exception:
                pass
        if not title:
            continue
        jobs.append({
            'title': title,
            'company': '',
            'url': link,
            'location': school or '',
            'description': desc or title,
            'date': date,
            'source_school': school,
            'source_account': account_name,
            'source_type': 'wechat_mp',
        })
    return jobs


def _parse_inbox(inbox_dir):
    jobs = []
    if not os.path.isdir(inbox_dir):
        return jobs
    for fn in sorted(os.listdir(inbox_dir)):
        if not fn.endswith('.txt'):
            continue
        path = os.path.join(inbox_dir, fn)
        try:
            text = open(path, encoding='utf-8').read().strip()
        except Exception:
            continue
        if not text:
            continue
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        title = lines[0]
        url = next((l for l in lines if l.startswith('http')), '')
        school = next((l.replace('school:', '').strip() for l in lines if l.startswith('school:')), '')
        account = fn.replace('.txt', '')
        jobs.append({
            'title': title,
            'company': '',
            'url': url,
            'location': school,
            'description': text,
            'source_school': school,
            'source_account': account,
            'source_type': 'wechat_mp',
            'date': datetime.now().strftime('%Y-%m-%d'),
        })
        done = os.path.join(inbox_dir, '_done')
        os.makedirs(done, exist_ok=True)
        os.rename(path, os.path.join(done, f'{datetime.now():%Y%m%d_%H%M%S}_{fn}'))
    return jobs


def scan_wechat_accounts(mode='internship', dedup=None, refresh=None, on_fetch_progress=None):
    cfg = _load_config().get('wechat_accounts') or {}
    if not cfg.get('enabled'):
        return []

    if dedup is None:
        dedup = cfg.get('dedup_on_scan', False)

    if refresh is None:
        refresh = cfg.get('refresh_on_scan', True)
    if refresh:
        _online_fetch(on_progress=on_fetch_progress)

    all_jobs = []
    method = cfg.get('method', 'platform')

    # RSS（仅 enabled 且有 rss_url 的账号）
    rss_count = 0
    for acc in cfg.get('accounts') or []:
        if not acc.get('enabled') or not acc.get('rss_url'):
            continue
        name, school, url = acc['name'], acc.get('school', ''), acc['rss_url']
        try:
            xml = fetch_url(url, timeout=20)
            if xml:
                jobs = _parse_rss(xml, name, school)
                all_jobs.extend(jobs)
                rss_count += len(jobs)
                print(f'  [mp/rss] {name}: {len(jobs)} 条')
        except Exception as e:
            print(f'  [mp/rss fail] {name}: {e}')
        time.sleep(0.5)

    # 微信公众平台（在线拉取后读缓存文件）
    platform_file = _resolve_platform_file(cfg)
    if method == 'platform' or cfg.get('use_mp_platform', True):
        try:
            from wechat_mp_platform import load_platform_jobs
            platform_jobs = load_platform_jobs(platform_file, cfg)
            all_jobs.extend(platform_jobs)
        except Exception as e:
            print(f'  [mp/platform fail] {e}')

    # 搜狗微信搜索（method=sogou 时启用，当前默认关闭）
    if method == 'sogou':
        try:
            from wechat_mp_search import collect_sogou_jobs
            sogou_jobs = collect_sogou_jobs(cfg)
            all_jobs.extend(sogou_jobs)
            print(f'  [mp/sogou] 合计 {len(sogou_jobs)} 条原始')
        except Exception as e:
            print(f'  [mp/sogou fail] {e}')

    # 手动 inbox
    inbox = cfg.get('inbox_dir', 'temp/mp_inbox')
    if not os.path.isabs(inbox):
        inbox = os.path.join(_ROOT, inbox)
    inbox_jobs = _parse_inbox(inbox)
    all_jobs.extend(inbox_jobs)
    if inbox_jobs:
        print(f'  [mp/inbox] {len(inbox_jobs)} 条')

    if not all_jobs:
        print('[mp_scan] 无原始数据（RSS/搜狗/inbox 均为空）')
        return []

    # 按模式过滤（默认 dedup_on_scan=false → 14 天内全部匹配）
    matched_all = []
    for j in all_jobs:
        matched = filter_and_dedup([j], source_school=j.get('source_school', ''),
                                   mode=mode, source_type='wechat_mp', dedup=dedup)
        matched_all.extend(matched)
    tag = '新增' if dedup else '匹配'
    print(f'[mp_scan] {len(all_jobs)} 原始 → {len(matched_all)} {tag}')
    return matched_all


def run_mp_scan(modes=('internship', 'campus2027'), push=True, batch='', refresh=None):
    cfg = _load_config().get('wechat_accounts') or {}
    if not cfg.get('enabled'):
        print('[mp_scan] 公众号监控未启用（wechat_accounts.enabled=false）')
        return {}

    results = {}
    for mode in modes:
        print(f'[mp_scan] 模式={mode}')
        new = scan_wechat_accounts(mode=mode, refresh=refresh)
        results[mode] = new
        if not push:
            continue
        if new:
            footer = f'\n\n——\n📱 公众号来源 · 共 {len(new)} 条'
            send_wechat_matches(new, batch=batch, mode=mode, footer=footer)
            print(f'[mp_scan] {mode} 已推送 {len(new)} 条')
        save_match_report(new, batch=batch or 'mp', mode=mode)
    return results


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='公众号搜狗/RSS/inbox 扫描')
    p.add_argument('--mode', default='all', choices=['all', 'internship', 'campus2027'])
    p.add_argument('--no-push', action='store_true')
    p.add_argument('--no-refresh', action='store_true', help='跳过在线拉取，仅用已有缓存')
    args = p.parse_args()
    modes = ('internship', 'campus2027') if args.mode == 'all' else (args.mode,)
    run_mp_scan(modes=modes, push=not args.no_push, refresh=not args.no_refresh)
