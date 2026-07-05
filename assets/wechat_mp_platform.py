"""从微信公众平台导出的 wechat_mp_data.json 加载公众号文章。"""
import json, os
from datetime import datetime, timedelta

# 旧版 wechat_login.py 15 校 key 兼容
_LEGACY_KEY = {
    '河北工业就业': '河北工业大学',
    '对外经贸大学就业': '对外经济贸易大学',
    '燕山大学就业': '燕山大学',
}


def _build_school_lookup(cfg):
    """config name / alias / query → school"""
    lookup = {}
    for acc in cfg.get('accounts') or []:
        if not acc.get('enabled', True):
            continue
        school = acc.get('school', '')
        for key in [acc.get('name', ''), acc.get('school', '')]:
            if key:
                lookup[key] = school
        for alias in acc.get('account_aliases') or []:
            if alias:
                lookup[alias] = school
        q = acc.get('query', '')
        if q and len(q) < 40:
            lookup[q.split()[0]] = school
    for k, school in _LEGACY_KEY.items():
        lookup[k] = school
    return lookup


def _resolve_school(key, acc, lookup):
    if key in lookup:
        return lookup[key]
    nick = (acc.get('nickname') or '').strip()
    if nick in lookup:
        return lookup[nick]
    for name, school in lookup.items():
        if name and (name in nick or name in key):
            return school
    return ''


def _ts_to_date(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d')
    except (ValueError, OSError, TypeError):
        return datetime.now().strftime('%Y-%m-%d')


def load_platform_jobs(path, cfg=None, max_age_days=None):
    """读取 mp_platform_file，转为 job 列表。"""
    if not path or not os.path.isfile(path):
        return []

    if cfg is None:
        from job_filter import _load_config
        cfg = (_load_config().get('wechat_accounts') or {})

    lookup = _build_school_lookup(cfg)
    max_age = max_age_days if max_age_days is not None else cfg.get('max_age_days', 7)
    cutoff = (datetime.now() - timedelta(days=max_age)).strftime('%Y-%m-%d')

    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    jobs = []
    accounts = data.get('accounts') or {}
    for key, acc in accounts.items():
        school = _resolve_school(key, acc, lookup)
        nickname = acc.get('nickname') or key
        for art in acc.get('articles') or []:
            title = (art.get('title') or '').strip()
            link = (art.get('link') or '').strip()
            if not title:
                continue
            pub_date = _ts_to_date(art.get('time'))
            if pub_date < cutoff:
                continue
            jobs.append({
                'title': title,
                'company': '',
                'url': link,
                'location': school,
                'description': title,
                'date': pub_date,
                'source_school': school,
                'source_account': nickname,
                'source_type': 'wechat_mp',
            })

    print(f'  [mp/platform] {len(accounts)} 号 → {len(jobs)} 条（{max_age}天内）')
    return jobs


if __name__ == '__main__':
    import argparse, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from job_filter import _load_config, filter_and_dedup

    p = argparse.ArgumentParser(description='测试 mp platform 数据加载')
    p.add_argument('--file', default=os.path.expanduser('~/wechat_mp_data.json'))
    p.add_argument('--mode', default='campus2027', choices=['internship', 'campus2027'])
    args = p.parse_args()

    cfg = _load_config().get('wechat_accounts') or {}
    path = cfg.get('mp_platform_file') or args.file
    raw = load_platform_jobs(path, cfg)
    new = []
    for j in raw:
        new.extend(filter_and_dedup([j], source_school=j.get('source_school', ''),
                                    mode=args.mode, source_type='wechat_mp'))
    print(f'匹配 {args.mode}: {len(new)} 条')
    for m in new[:10]:
        print(f"  [{m.get('score')}%] {m['title'][:55]} ({m.get('source_school')})")
