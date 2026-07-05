"""微信公众号岗位监控：基于搜狗微信搜索（无需 Docker / RSS）。"""
import json, os, re, sys, time
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _THIS)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}
TIMEOUT = 20
SOGOU_WX = 'https://weixin.sogou.com'
_SOGOU_CACHE = None

DEFAULT_EXTRA_QUERIES = [
    '校园招聘 大模型 实习 北京 2026',
    '校招 AI 算法 北京 天津 2027届',
    '高校就业 AI 实习 京津冀 2026',
]

# 学校名 → 搜狗搜索简称
_SCHOOL_ALIAS = {
    '北京邮电大学': '北邮 就业 招聘',
    '北京科技大学': '北科大 就业 招聘',
    '北京交通大学': '北京交大 就业 招聘',
    '北京外国语大学': '北外 就业 招聘',
    '中国传媒大学': '中传 就业 招聘',
    '中央财经大学': '央财 就业 招聘',
    '对外经济贸易大学': '对外经贸大学 就业 招聘',
    '中国政法大学': '法大 就业 招聘',
    '华北电力大学': '华电 就业 招聘',
    '中国地质大学(北京)': '地大北京 就业 招聘',
    '中国矿业大学(北京)': '矿大北京 就业 招聘',
    '北京中医药大学': '北中医 就业 招聘',
    '北京工业大学': '北工大 就业 招聘',
    '首都经济贸易大学': '首经贸 就业 招聘',
    '北京信息科技大学': '信息科大 就业 招聘',
    '天津医科大学': '天津医大 就业 招聘',
    '河北工业大学': '河北工业大学 就业 招聘',
    '天津工业大学': '天工大 就业 招聘',
    '天津财经大学': '天津财大 就业 招聘',
    '天津师范大学': '天津师大 就业 招聘',
    '天津科技大学': '天津科大 就业 招聘',
    '天津理工大学': '天理工 就业 招聘',
    '天津商业大学': '天商大 就业 招聘',
    '中国民航大学': '中航大 就业 招聘',
    '石家庄铁道大学': '石铁大 就业 招聘',
    '河北科技大学': '河北科大 就业 招聘',
    '河北经贸大学': '河北经贸 就业 招聘',
    '河北农业大学': '河北农大 就业 招聘',
    '东北大学秦皇岛分校': '东大秦皇岛 就业 招聘',
    '河北工业大学(石家庄)': '河北工业大学 就业 招聘',
}


def _load_mp_config():
    from job_filter import _load_config
    return (_load_config().get('wechat_accounts') or {})


def _build_school_accounts():
    """从 schools 列表生成 49 校搜索目标（scan_all_schools 时用）。"""
    config_path = os.path.join(_ROOT, 'assets', 'job_monitor_config.json')
    if not os.path.exists(config_path):
        return []
    with open(config_path, encoding='utf-8') as f:
        cfg = json.load(f)
    accounts = []
    for s in cfg.get('schools', []):
        name = s['name']
        if name in _SCHOOL_ALIAS:
            query = f"{_SCHOOL_ALIAS[name]} AI 2026"
        else:
            query = f"{name} 就业 招聘 AI 2026"
        accounts.append({'name': name, 'school': name, 'query': query})
    return accounts


def _account_match(source_account, aliases):
    """来源公众号名是否命中官方别名。"""
    if not source_account or not aliases:
        return False
    src = source_account.strip()
    for alias in aliases:
        a = (alias or '').strip()
        if a and (a in src or src in a):
            return True
    return False


def _build_search_targets(cfg):
    """合并 config 公众号账号 + 可选全量学校。"""
    targets = []
    seen_schools = set()
    for acc in cfg.get('accounts') or []:
        if not acc.get('enabled', True):
            continue
        school = acc.get('school', '')
        name = acc.get('name', school)
        query = acc.get('query') or f'{name} 招聘 AI 2026'
        aliases = acc.get('account_aliases') or [name]
        if name not in aliases:
            aliases = [name] + list(aliases)
        targets.append({
            'name': name,
            'school': school,
            'query': query,
            'alt_queries': acc.get('alt_queries') or [],
            'account_aliases': aliases,
        })
        if school:
            seen_schools.add(school)

    if cfg.get('scan_all_schools'):
        for acc in _build_school_accounts():
            if acc['school'] not in seen_schools:
                targets.append(acc)
                seen_schools.add(acc['school'])
    return targets


def _fetch(url, headers=None, timeout=TIMEOUT):
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers or HEADERS,
                                timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                resp.encoding = resp.apparent_encoding or 'utf-8'
                return resp.text
        except Exception as e:
            if attempt >= 2:
                print(f'  [fetch err] {url}: {e}')
            else:
                time.sleep(2 ** attempt)
    return ''


def _extract_timestamp(item):
    """从 li 节点提取日期；s-p 可能在 txt-box 内或 li 下同级。"""
    txt_box = item.find('div', class_='txt-box')
    sp_div = txt_box.find('div', class_='s-p') if txt_box else None
    if not sp_div:
        sp_div = item.find('div', class_='s-p')
    if not sp_div:
        return None
    s2 = sp_div.find('span', class_='s2')
    if not s2:
        return None
    script_tag = s2.find('script')
    if not script_tag:
        return None
    m = re.search(r"timeConvert\('(\d+)'\)", script_tag.string or '')
    if not m:
        return None
    try:
        return datetime.fromtimestamp(int(m.group(1))).strftime('%Y-%m-%d')
    except (ValueError, OSError):
        return None


def _parse_search_item(item, default_school='', default_account='', max_age_days=7):
    txt_box = item.find('div', class_='txt-box')
    if not txt_box:
        return None
    h3 = txt_box.find('h3')
    if not h3:
        return None
    a_tag = h3.find('a')
    if not a_tag:
        return None
    title = a_tag.get_text(strip=True)
    href = a_tag.get('href', '')
    if not title:
        return None

    pub_date = _extract_timestamp(item)
    if pub_date is None:
        return None
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime('%Y-%m-%d')
    if pub_date < cutoff:
        return None

    desc_tag = txt_box.find('p', class_='txt-info')
    desc = desc_tag.get_text(strip=True)[:500] if desc_tag else title

    sp_div = txt_box.find('div', class_='s-p') or item.find('div', class_='s-p')
    source_account = default_account
    if sp_div:
        acc_span = sp_div.find('span', class_='all-time-y2')
        if acc_span:
            source_account = acc_span.get_text(strip=True)

    sogou_link = urljoin(SOGOU_WX, href) if href.startswith('/') else href
    return {
        'title': title,
        'company': '',
        'url': sogou_link,
        'location': default_school,
        'description': desc,
        'date': pub_date,
        'source_school': default_school,
        'source_account': source_account,
        'source_type': 'wechat_mp',
    }


def _search_sogou(query, max_results=15, max_age_days=7,
                  default_school='', default_account=''):
    url = f'{SOGOU_WX}/weixin?type=2&s_from=input&query={quote(query)}&ie=utf8'
    html = _fetch(url, timeout=TIMEOUT)
    if not html:
        return []
    if '验证码' in html or 'antispider' in html:
        print(f'  [sogou] 验证码/反爬: {query[:30]}...')
        return []

    soup = BeautifulSoup(html, 'html.parser')
    news_list = soup.find('ul', class_='news-list')
    if not news_list:
        return []

    jobs = []
    for item in news_list.find_all('li', recursive=False)[:max_results]:
        try:
            job = _parse_search_item(
                item, default_school=default_school,
                default_account=default_account, max_age_days=max_age_days)
            if job:
                jobs.append(job)
        except Exception:
            continue
    return jobs


def collect_sogou_jobs(cfg=None, use_cache=True):
    """搜狗搜索目标公众号 + 广义关键词，返回原始岗位（未过滤）。"""
    global _SOGOU_CACHE
    if use_cache and _SOGOU_CACHE is not None:
        print(f'[sogou] 使用缓存 {len(_SOGOU_CACHE)} 条')
        return _SOGOU_CACHE

    cfg = cfg or _load_mp_config()
    max_per = cfg.get('max_results_per_account', 15)
    max_age = cfg.get('max_age_days', 7)
    delay = cfg.get('sogou_delay_sec', 2)
    extra_queries = cfg.get('extra_queries') or DEFAULT_EXTRA_QUERIES
    targets = _build_search_targets(cfg)

    all_jobs = []
    seen = set()

    def _add(jobs):
        for j in jobs:
            key = f"{j['title'][:40]}|{j.get('source_account', '')}|{j.get('url', '')[:40]}"
            if key in seen:
                continue
            seen.add(key)
            all_jobs.append(j)

    print(f'[sogou] 扫描 {len(targets)} 个搜索目标...')
    for i, acc in enumerate(targets, 1):
        name = acc.get('name', '')
        school = acc.get('school', '')
        aliases = acc.get('account_aliases') or [name]
        queries = [acc.get('query', '')] + list(acc.get('alt_queries') or [])
        queries = [q for q in queries if q]
        print(f'  [{i}/{len(targets)}] {name}... ({len(queries)} 组词)')
        batch = []
        for qi, query in enumerate(queries):
            results = _search_sogou(
                query, max_results=max_per, max_age_days=max_age,
                default_school=school, default_account=name)
            for j in results:
                if _account_match(j.get('source_account', ''), aliases):
                    j['source_school'] = school
            batch.extend(results)
            if qi < len(queries) - 1:
                time.sleep(delay)
        _add(batch)
        official = sum(1 for j in batch if _account_match(j.get('source_account', ''), aliases))
        print(f'    → {len(batch)} 条（{max_age}天内，官方号 {official} 条）')
        if i < len(targets):
            time.sleep(delay)

    print(f'[sogou] 广义搜索 {len(extra_queries)} 组关键词...')
    for query in extra_queries:
        results = _search_sogou(query, max_results=10, max_age_days=max_age)
        _add(results)
        print(f'  [broad] {query[:35]}...: {len(results)} 条')
        time.sleep(delay)

    print(f'[sogou] 原始合计 {len(all_jobs)} 条')
    _SOGOU_CACHE = all_jobs
    return all_jobs


# 兼容旧调用
def search_account(acc, max_results=15):
    cfg = _load_mp_config()
    max_age = cfg.get('max_age_days', 7)
    school = acc.get('school', '')
    name = acc.get('name', '')
    aliases = acc.get('account_aliases') or [name]
    queries = [acc.get('query', '')] + list(acc.get('alt_queries') or [])
    out = []
    seen = set()
    for query in queries:
        if not query:
            continue
        for j in _search_sogou(query, max_results=max_results, max_age_days=max_age,
                               default_school=school, default_account=name):
            if _account_match(j.get('source_account', ''), aliases):
                j['source_school'] = school
            key = f"{j['title'][:40]}|{j.get('source_account', '')}"
            if key in seen:
                continue
            seen.add(key)
            out.append(j)
    return out


def search_broad(query, max_results=10):
    cfg = _load_mp_config()
    return _search_sogou(query, max_results=max_results,
                         max_age_days=cfg.get('max_age_days', 7))


if __name__ == '__main__':
    import argparse
    from job_filter import filter_and_dedup, format_wechat_message, save_match_report
    from job_notify import send_wechat

    p = argparse.ArgumentParser(description='搜狗微信公众号搜索扫描')
    p.add_argument('--mode', default='all', choices=['internship', 'campus2027', 'all'])
    p.add_argument('--no-push', action='store_true')
    p.add_argument('--test', action='store_true', help='只测试单个搜索')
    p.add_argument('--test-acc', type=str, default='', help='测试指定学校，如"清华大学"')
    args = p.parse_args()

    cfg = _load_mp_config()

    if args.test:
        targets = _build_search_targets(cfg)
        if args.test_acc:
            targets = [t for t in targets if args.test_acc in t.get('school', '')]
        if not targets:
            print(f'未找到匹配 "{args.test_acc}" 的目标')
            sys.exit(1)
        for acc in targets[:3]:
            results = search_account(acc, max_results=10)
            print(f'\n=== {acc["school"]} ({acc["query"]}) · {len(results)} 条 ===')
            for r in results[:5]:
                print(f'  [{r["date"]}] {r["title"]}')
                print(f'   来源: {r["source_account"]}')
        sys.exit(0)

    modes = ('internship', 'campus2027') if args.mode == 'all' else (args.mode,)
    for mode in modes:
        print(f'\n{"=" * 50}\n模式: {mode}')
        raw = collect_sogou_jobs(cfg)
        new_all = []
        for j in raw:
            new_all.extend(filter_and_dedup(
                [j], source_school=j.get('source_school', ''),
                mode=mode, source_type='wechat_mp'))
        print(f'[sogou] {len(raw)} 原始 → {len(new_all)} 新增匹配')

        if not args.no_push and new_all:
            from job_notify import send_wechat_matches
            footer = f'\n\n——\n📱 公众号·搜狗 · 新增 {len(new_all)} 条'
            send_wechat_matches(new_all, batch='sogou', mode=mode, footer=footer)
            print(f'[sogou] {mode} 已推送 {len(new_all)} 条')
        save_match_report(new_all, batch='sogou', mode=mode)
