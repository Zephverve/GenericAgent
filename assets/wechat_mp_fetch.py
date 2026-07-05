#!/usr/bin/env python3
"""从 GenericAgent config 读取 49 校就业公众号，登录 mp 后台在线拉取文章。

用法:
  HTTP_PROXY="" HTTPS_PROXY="" python assets/wechat_mp_fetch.py

依赖: playwright (pip install playwright && playwright install chromium)
输出: temp/wechat_mp_data.json（路径见 job_monitor_config.json → mp_platform_file）
"""
import json, os, re, sys, time, threading

import requests
from datetime import datetime, timedelta
from requests.exceptions import Timeout as RequestsTimeout

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(_ROOT, 'assets', 'job_monitor_config.json')
DEFAULT_OUTPUT = os.path.join(_ROOT, 'temp', 'wechat_mp_data.json')
LEGACY_OUTPUT = os.path.expanduser('~/wechat_mp_data.json')
ARTICLE_COUNT = 20
SEARCH_DELAY = 0.8
QUERY_DELAY = 0.35
HTTP_TIMEOUT = 30  # 秒，单校 API 超时
DEFAULT_SLOW_DELAY = 55  # 仅 --slow 时使用
DEFAULT_FETCH_DELAY = 4.5  # 49校约 3.5–4 分钟，兼顾速度与频控
RATE_LIMIT_COOLDOWN = 15
STATE_PATH = os.path.join(_ROOT, 'temp', 'mp_wx_storage.json')
FAKEID_CACHE = os.path.join(_ROOT, 'temp', 'mp_account_cache.json')
_FETCH_LOCK = threading.Lock()
_HTTP_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    'Referer': 'https://mp.weixin.qq.com/',
}

# 搜号结果里常见误匹配
_BAD_NICK = ('特斯拉', '国家大学生就业服务平台', '直聘网', '人工智能学院')


class FetchNeedsLogin(Exception):
    """mp session 过期，需扫码登录。"""


def resolve_platform_file(cfg=None):
    if cfg is None:
        with open(CONFIG, encoding='utf-8') as f:
            cfg = json.load(f).get('wechat_accounts') or {}
    path = cfg.get('mp_platform_file') or DEFAULT_OUTPUT
    if not os.path.isabs(path):
        path = os.path.join(_ROOT, path)
    return path


def _load_fetch_settings():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            mp = json.load(f).get('wechat_accounts') or {}
    except Exception:
        mp = {}
    return {
        'slow_delay': float(mp.get('fetch_slow_delay_sec') or DEFAULT_SLOW_DELAY),
        'fetch_delay': float(mp.get('fetch_delay_sec') or DEFAULT_FETCH_DELAY),
        'slow_on_refresh': mp.get('fetch_slow_on_refresh', False),
        'cooldown': int(mp.get('fetch_rate_limit_cooldown_sec') or RATE_LIMIT_COOLDOWN),
    }


def _load_targets():
    with open(CONFIG, encoding='utf-8') as f:
        cfg = json.load(f)
    mp = cfg.get('wechat_accounts') or {}
    out_path = resolve_platform_file(mp)
    article_count = int(mp.get('max_results_per_account') or ARTICLE_COUNT)
    max_age = int(mp.get('max_age_days') or 14)
    targets, meta = [], {}
    for acc in mp.get('accounts') or []:
        if not acc.get('enabled', True):
            continue
        name = acc['name']
        school = acc.get('school', '')
        targets.append(name)
        queries = [name]
        for a in acc.get('account_aliases') or []:
            if a not in queries:
                queries.append(a)
        if school:
            for q in (school, school.split('(')[0], f'{school}就业指导中心'):
                if q and q not in queries:
                    queries.append(q)
        if acc.get('query') and acc['query'] not in queries:
            queries.append(acc['query'])  # 长 query 放最后，避免搜错号
        meta[name] = {
            'school': school,
            'fakeid': acc.get('fakeid', ''),
            'queries': queries,
        }
    return targets, meta, out_path, article_count, max_age


def _pick_best_biz(candidates, name, school='', want_fakeid=''):
    if not candidates:
        return None
    seen, uniq = set(), []
    for c in candidates:
        fid = c.get('fakeid', '')
        if fid and fid not in seen:
            seen.add(fid)
            uniq.append(c)
    if want_fakeid:
        for c in uniq:
            if c.get('fakeid') == want_fakeid:
                return c
    hints = [name, name.replace('就业', ''), school, school.replace('(北京)', ''), '就业']
    hints = [h for h in hints if h]

    def score(item):
        nick = item.get('nickname') or ''
        s = 0
        for h in hints:
            if len(h) >= 2 and h in nick:
                s += 12
        if nick == name:
            s += 30
        if '就业' in nick:
            s += 8
        for bad in _BAD_NICK:
            if bad in nick:
                s -= 25
        return s

    best = max(uniq, key=score)
    return best if score(best) > 0 else uniq[0]


def _extract_token(page):
    m = re.search(r'token=(\d+)', page.url)
    if m:
        return m.group(1)
    try:
        m = re.search(r'token=(\d+)', page.content())
        return m.group(1) if m else ''
    except Exception:
        return ''


def _extract_token_http(url, text):
    m = re.search(r'token=(\d+)', url or '')
    if m:
        return m.group(1)
    m = re.search(r'token=(\d+)', text or '')
    return m.group(1) if m else ''


def _session_from_storage(path=STATE_PATH):
    sess = requests.Session()
    sess.headers.update(_HTTP_HEADERS)
    if not os.path.isfile(path):
        return sess
    try:
        st = json.load(open(path, encoding='utf-8'))
    except Exception:
        return sess
    for c in st.get('cookies') or []:
        domain = c.get('domain') or 'mp.weixin.qq.com'
        if domain.startswith('.'):
            domain = domain[1:]
        sess.cookies.set(c.get('name', ''), c.get('value', ''), domain=domain, path=c.get('path') or '/')
    return sess


def _mp_get(session, path, token, extra=None):
    params = {'token': token, 'lang': 'zh_CN', 'f': 'json', 'ajax': '1'}
    if extra:
        params.update(extra)
    r = session.get(f'https://mp.weixin.qq.com/cgi-bin/{path}', params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _search_biz(session, token, query):
    return _mp_get(session, 'searchbiz', token, {
        'action': 'search_biz', 'begin': 0, 'count': 5, 'query': query,
    })


def _list_articles(session, token, fakeid, count, begin=0):
    return _mp_get(session, 'appmsg', token, {
        'action': 'list_ex', 'begin': begin, 'count': count,
        'fakeid': fakeid, 'type': 9, 'query': '',
    })


def _call_with_retry(fn, *args, max_attempts=4):
    """调用 mp API，处理频控 ret=200013 与网络超时。"""
    last_err = None
    for attempt in range(max_attempts):
        try:
            data = fn(*args)
        except RequestsTimeout:
            last_err = 'timeout'
            print('  [mp_fetch] API 超时，重试…')
            time.sleep(5)
            continue
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(3)
            continue
        ret = (data.get('base_resp') or {}).get('ret')
        if ret == 200013:
            wait = 15 + attempt * 5
            print(f'  [mp_fetch] 频控 ret=200013，等待 {wait}s…')
            time.sleep(wait)
            continue
        return data, None
    return None, last_err or '重试耗尽'


def _fetch_articles(session, token, fakeid, page_size, max_age_days, max_pages=2):
    """分页拉取，直到覆盖 max_age_days 或达到 max_pages。"""
    cutoff = int((datetime.now() - timedelta(days=max_age_days)).timestamp())
    all_arts, seen_links = [], set()

    for page_idx in range(max_pages):
        begin = page_idx * page_size
        art_data, err = _call_with_retry(_list_articles, session, token, fakeid, page_size, begin)
        if err or not art_data:
            if err == 'timeout':
                print(f'  [mp_fetch] API 超时 fakeid={fakeid[:12]}…')
            return all_arts
        ret = (art_data.get('base_resp') or {}).get('ret')
        if ret != 0:
            return all_arts

        batch = art_data.get('app_msg_list') or []
        if not batch:
            break
        stop = False
        for a in batch:
            link = a.get('link', '')
            ts = int(a.get('create_time') or 0)
            if link in seen_links:
                continue
            seen_links.add(link)
            if ts and ts < cutoff:
                stop = True
                continue
            all_arts.append({
                'title': a.get('title', ''),
                'link': link,
                'time': ts,
            })
        if stop or len(batch) < page_size:
            break
        time.sleep(0.4)
    return all_arts


def _load_existing_accounts(*paths):
    merged = {}
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            data = json.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        for k, v in (data.get('accounts') or {}).items():
            old = merged.get(k)
            if not old or len(v.get('articles') or []) > len(old.get('articles') or []):
                merged[k] = v
    return merged


def _merge_account(old_acc, new_acc):
    """合并同一公众号文章，新拉取为空时保留旧数据。"""
    if not new_acc:
        return old_acc
    if not old_acc:
        return new_acc
    new_arts = new_acc.get('articles') or []
    old_arts = old_acc.get('articles') or []
    if not new_arts and old_arts:
        return old_acc
    if new_acc.get('fakeid') and old_acc.get('fakeid') and new_acc['fakeid'] != old_acc['fakeid']:
        return new_acc if new_arts else old_acc
    by_link = {}
    for a in old_arts + new_arts:
        link = a.get('link') or a.get('title', '')
        if link:
            by_link[link] = a
    nick = new_acc.get('nickname') or old_acc.get('nickname', '')
    fakeid = new_acc.get('fakeid') or old_acc.get('fakeid', '')
    arts = sorted(by_link.values(), key=lambda x: int(x.get('time') or 0), reverse=True)
    return {'nickname': nick, 'fakeid': fakeid, 'articles': arts}


def _merge_all_results(new_results, out_path):
    old = _load_existing_accounts(out_path, LEGACY_OUTPUT)
    merged = dict(old)
    for name, new_acc in new_results.items():
        merged[name] = _merge_account(old.get(name), new_acc)
    return merged


def _load_fakeid_cache(out_path):
    """从上次拉取结果加载 fakeid，有则跳过搜号（大幅加速）。"""
    cache = {}
    for path in (FAKEID_CACHE, out_path, LEGACY_OUTPUT):
        if not path or not os.path.isfile(path):
            continue
        try:
            data = json.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        for k, v in (data.get('accounts') or {}).items():
            fid = v.get('fakeid')
            if fid and k not in cache:
                cache[k] = {'fakeid': fid, 'nickname': v.get('nickname', '')}
    return cache


def _save_fakeid_cache(results, out_path):
    cache = _load_fakeid_cache(out_path)
    for k, v in results.items():
        if v.get('fakeid'):
            cache[k] = {'fakeid': v['fakeid'], 'nickname': v.get('nickname', '')}
    os.makedirs(os.path.dirname(FAKEID_CACHE) or '.', exist_ok=True)
    with open(FAKEID_CACHE, 'w', encoding='utf-8') as f:
        json.dump({'accounts': cache, 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S')},
                  f, ensure_ascii=False, indent=2)


def _resolve_account(session, token, name, info, fakeid_cache):
    """优先用缓存 fakeid 直拉文章，失败再搜号。"""
    cached = fakeid_cache.get(name)
    if cached and cached.get('fakeid'):
        return {'nickname': cached.get('nickname') or name, 'fakeid': cached['fakeid']}, 'cache'

    queries = (info.get('queries') or [name])[:3]  # 最多试 3 个搜索词
    school = info.get('school', '')
    want_fakeid = info.get('fakeid', '')
    candidates, last_err = [], None
    for q in queries:
        data, err = _call_with_retry(_search_biz, session, token, q)
        if err:
            last_err = err
        elif data:
            ret = (data.get('base_resp') or {}).get('ret')
            if ret != 0:
                last_err = f'ret={ret}'
            else:
                candidates.extend(data.get('list') or [])
                if candidates:
                    break
        time.sleep(QUERY_DELAY)

    acc = _pick_best_biz(candidates, name, school, want_fakeid)
    if acc:
        return acc, 'search'
    return None, last_err or '未找到'


def _probe_api(session, token, fakeid_cache, targets):
    """探测 API 是否被频控。返回 'ok' 或 'rate_limited'（不中断，改慢速拉取）。"""
    for name in targets[:3]:
        fid = (fakeid_cache.get(name) or {}).get('fakeid')
        if not fid:
            continue
        data, err = _call_with_retry(_list_articles, session, token, fid, 1, 0, max_attempts=2)
        if data and (data.get('base_resp') or {}).get('ret') == 0:
            return 'ok'
        if err == '重试耗尽':
            return 'rate_limited'
    return 'ok'


def _login_via_playwright(sync_playwright, headless):
    """Playwright 仅用于登录/刷新 cookie，API 走 requests。"""
    has_session = os.path.isfile(STATE_PATH)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=['--no-sandbox'])
        ctx_opts = {'viewport': {'width': 1280, 'height': 900}}
        if has_session:
            ctx_opts['storage_state'] = STATE_PATH
        context = browser.new_context(**ctx_opts)
        page = context.new_page()
        page.goto('https://mp.weixin.qq.com/', wait_until='domcontentloaded', timeout=30000)
        time.sleep(1)
        token = _extract_token(page)
        if not token:
            if headless:
                browser.close()
                raise FetchNeedsLogin('mp session 过期，需扫码登录')
            token = _wait_login(page)
        if not token:
            browser.close()
            raise FetchNeedsLogin('未获取 token')
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        context.storage_state(path=STATE_PATH)
        browser.close()
        return token


def _wait_login(page, timeout=180):
    print('请扫码登录 mp.weixin.qq.com（等待最多 3 分钟）...')
    deadline = time.time() + timeout
    while time.time() < deadline:
        token = _extract_token(page)
        if token:
            return token
        time.sleep(2)
    return _extract_token(page)


def fetch_platform_data(headless=None, out_path=None, article_count=None,
                        max_age_days=None, on_progress=None, slow=None):
    from playwright.sync_api import sync_playwright

    if not _FETCH_LOCK.acquire(blocking=False):
        raise RuntimeError('已有在线拉取在进行中，请等待完成或点「重置扫描」')

    try:
        return _fetch_platform_data_impl(
            headless, out_path, article_count, max_age_days, on_progress, slow, sync_playwright)
    finally:
        _FETCH_LOCK.release()


def _fetch_platform_data_impl(headless, out_path, article_count, max_age_days, on_progress, slow, sync_playwright):
    settings = _load_fetch_settings()
    targets, meta, default_out, default_count, default_age = _load_targets()
    out_path = out_path or default_out
    article_count = article_count or default_count
    max_age_days = max_age_days if max_age_days is not None else default_age
    total = len(targets)

    has_session = os.path.isfile(STATE_PATH)
    if headless is None:
        headless = has_session

    print(f'[mp_fetch] 在线拉取 {total} 校 → {out_path} (headless={headless})')
    if on_progress:
        on_progress(0, total, '登录验证…')

    session = _session_from_storage()
    token = ''
    if has_session:
        try:
            r = session.get('https://mp.weixin.qq.com/', timeout=HTTP_TIMEOUT, allow_redirects=True)
            token = _extract_token_http(r.url, r.text)
        except requests.RequestException:
            token = ''

    if not token:
        if on_progress:
            on_progress(0, total, '启动浏览器登录…')
        token = _login_via_playwright(sync_playwright, headless)
        session = _session_from_storage()

    print(f'[mp_fetch] 已登录 token={token[:8]}…')

    fakeid_cache = _load_fakeid_cache(out_path)
    cached_n = sum(1 for n in targets if fakeid_cache.get(n, {}).get('fakeid'))
    print(f'[mp_fetch] fakeid 缓存 {cached_n}/{total} 校（有缓存则跳过搜号）')
    if on_progress:
        on_progress(0, total, f'登录完成，{cached_n} 校走缓存')

    rate_status = _probe_api(session, token, fakeid_cache, targets)
    if slow is None:
        slow = bool(settings['slow_on_refresh'])
    per_school_delay = settings['slow_delay'] if slow else settings['fetch_delay']
    eta_min = max(1, int(total * per_school_delay / 60))
    mode_label = f'慢速({per_school_delay:.0f}s/校)' if slow else f'常速(约{eta_min}–{eta_min + 1}分钟)'
    print(f'[mp_fetch] 拉取模式: {mode_label}')

    if rate_status == 'rate_limited':
        warn = '微信 API 频控中，部分学校可能拉不到（会保留旧缓存）'
        print(f'[mp_fetch] {warn}')
        if on_progress:
            on_progress(0, total, warn)

    results = {}
    for i, name in enumerate(targets, 1):
        info = meta.get(name, {})
        if on_progress:
            on_progress(i, total, f'({i}/{total}) 拉取 {name}…')

        acc, src = _resolve_account(session, token, name, info, fakeid_cache)
        if not acc:
            msg = f'{name} -> {src}'
            print(f'[{i}/{total}] {msg}')
            if on_progress:
                on_progress(i, total, msg)
            time.sleep(SEARCH_DELAY)
            continue

        nickname, fakeid = acc['nickname'], acc['fakeid']
        tag = '⚡' if src == 'cache' else ''
        try:
            articles = _fetch_articles(session, token, fakeid, article_count, max_age_days)
            results[name] = {'nickname': nickname, 'fakeid': fakeid, 'articles': articles}
            msg = f'{tag}{nickname} -> {len(articles)} 篇'
            print(f'[{i}/{total}] {msg}')
        except Exception as e:
            msg = f'{nickname} -> {e}'
            print(f'[{i}/{total}] {msg}')

        if on_progress:
            on_progress(i, total, msg)
        time.sleep(max(per_school_delay, SEARCH_DELAY) if src == 'search' else per_school_delay)

    _save_fakeid_cache(results, out_path)

    merged = _merge_all_results(results, out_path)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    payload = {
        'token': token,
        'accounts': merged,
        'total': len(merged),
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    arts_total = sum(len(a.get('articles') or []) for a in merged.values())
    print(f'[mp_fetch] 完成 {len(results)}/{total} 校 · 合并后 {arts_total} 篇 -> {out_path}')
    return {'path': out_path, 'accounts': len(merged), 'targets': total, 'articles': arts_total}


def main():
    import argparse
    for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        os.environ.pop(_k, None)
    p = argparse.ArgumentParser(description='在线拉取 49 校公众号文章')
    p.add_argument('--fast', action='store_true', help='常速拉取（易触发频控，不推荐）')
    args = p.parse_args()
    try:
        fetch_platform_data(headless=False, slow=not args.fast)
    except FetchNeedsLogin as e:
        print(f'错误: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
