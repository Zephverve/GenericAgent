"""岗位匹配过滤 + 去重。支持 internship / campus2027 双模式。"""
import json, os, hashlib, re, html
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(_ROOT, 'assets', 'job_monitor_config.json')
MATCHES = os.path.join(_ROOT, 'temp', 'job_matches')

MODES = ('internship', 'campus2027')


def _load_config():
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f)


def _seen_path(mode='internship'):
    return os.path.join(_ROOT, 'temp', f'seen_jobs_{mode}.json')


def _load_seen(mode='internship'):
    path = _seen_path(mode)
    if not os.path.exists(path):
        # 兼容旧版单一去重库
        legacy = os.path.join(_ROOT, 'temp', 'seen_jobs.json')
        if mode == 'campus2027' and os.path.exists(legacy):
            with open(legacy, encoding='utf-8') as f:
                return json.load(f)
        return {'jobs': {}, 'mode': mode}
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _save_seen(data, mode='internship'):
    path = _seen_path(mode)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data['mode'] = mode
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_mode_config(mode='internship'):
    cfg = _load_config()
    modes = cfg.get('modes') or {}
    if mode not in modes:
        raise ValueError(f'未知模式 {mode}，可选: {list(modes.keys())}')
    base = dict(cfg.get('filter') or {})
    m = modes[mode]
    base.update(m)
    base['mode'] = mode
    base['label'] = m.get('label', mode)
    return base


def job_id(title, company, url='', mode='internship'):
    url = (url or '').replace('http://', 'https://').rstrip('/')
    raw = f'{mode}|{title}|{company}|{url}'.strip().lower()
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _norm_dedup_key(title, company):
    """同批去重：去掉日期、分隔符、公司后缀。"""
    t = (title or '') + '|' + (company or '')
    t = re.sub(r'————.*', '', t)
    t = re.sub(r'\d{4}[-/年]\d{1,2}[-/月\d日:：\s]*', '', t)
    t = re.sub(r'【[^】]+】', '', t)
    t = re.sub(r'\s+', '', t.lower())
    return t[:100]


def _foreign_school_noise(title, source_school=''):
    """就业网聚合页里其他学校的招聘公告。"""
    if not title:
        return False
    for m in re.finditer(r'([\u4e00-\u9fff]{4,}(?:大学|学院|职业学院|职业技术学院))', title):
        name = m.group(1)
        if source_school and name in source_school:
            continue
        if any(k in name for k in ('就业', '招聘平台', '信息平台的')):
            continue
        return True
    return False


def _is_event_not_job(blob):
    """宣讲会/双选会/已举办活动，不是具体岗位。"""
    event_markers = (
        '已举办', '举办地点', '举办时间', '空中宣讲', '宣讲会', '双选会',
        '专场招聘会', '网络招聘会', '网络联合招聘', '空宣', '招聘会', '行纪｜',
    )
    if any(k in blob for k in event_markers):
        return True
    if re.search(r'20(1[89]|2[0-4])[/年-]', blob):
        return True
    return False


def _kw_match(text, keywords):
    text = text or ''
    return [k for k in keywords if k.lower() in text.lower()]


def _mp_core_title(title):
    """去掉公众号标题前缀，避免「实习信息｜」误触发关键词。"""
    t = (title or '').strip()
    t = re.sub(r'^[【\[](?:实习信息|招聘信息|每日招聘|校招公告|招聘汇总|优选实习)[】\]]\s*', '', t)
    t = re.sub(r'^(?:实习信息|招聘信息|每日招聘|校招公告|招聘汇总|优选实习)[｜|丨|\|]+\s*', '', t)
    return t.strip() or (title or '')


def _tech_hits(blob, core_title, cfg, source_type):
    ai_hits = _kw_match(blob, cfg.get('ai_keywords') or [])
    cs_hits = _kw_match(core_title if source_type == 'wechat_mp' else blob,
                        cfg.get('cs_keywords') or [])
    hits = list(dict.fromkeys(ai_hits + cs_hits))
    if source_type == 'wechat_mp':
        mp_kws = (_load_config().get('filter') or {}).get('mp_tech_keywords') or []
        hits = list(dict.fromkeys(hits + _kw_match(core_title, mp_kws)))
    return hits, ai_hits, cs_hits


_MP_INTERN_SIGNAL = re.compile(
    r'工程师|算法|研发|技术|开发|程序|数据|软件|AI|Java|Python|'
    r'互联网|科技|智能|数字|27届|2027|校招|可转正|Product Engineer|'
    r'Offer|开源|智驾|半导体|芯片|游戏|程序员|实习招聘|实习生',
    re.I,
)
_MP_INTERN_JUNK = re.compile(
    r'证券|保险|银行|浦发|雄安|优选实习|校园大使|就业服务|门头沟|'
    r'社会实践|回家看看|机关和企|创造营|HR精英|行销校招|'
    r'商品交易所|申能股份|国新国际|中工国际|交控|五矿|财险|银河|中化|'
    r'北投置业|格力大学生实践|实习实训岗位',
    re.I,
)
_MP_INTERN_COMPANIES = (
    '京东', '网易', '360', '字节', '腾讯', '阿里', '百度', '美团', '滴滴',
    '华为', '小米', '米哈游', '小红书', '快手', '哔哩', 'B站', '理想', '蔚来',
    '宁德时代', '大疆', '科大讯飞', '商汤', '拼多多', '携程', '联想', 'OPPO',
    '学而思', '有道', '泡泡玛特', '多益', '拓竹', 'MoonBit', '可转正', '北芯',
    '视源', '新石器', '乐鑫', '网易智邮',
)


def _mp_internship_tech(core, blob, cfg, tech_hits):
    """公众号实习：标题常不写 AI，用互联网/科技信号 + 知名公司名补匹配。"""
    if tech_hits:
        return tech_hits
    ind = _kw_match(core, cfg.get('industry_keywords') or [])
    if ind:
        return ind
    if _MP_INTERN_SIGNAL.search(core):
        return ['mp_intern']
    if any(c in core for c in _MP_INTERN_COMPANIES):
        return ['mp_company']
    return []


def _is_internship_blob(blob):
    return any(k in blob for k in ('实习', 'intern', 'Intern', '暑期', '日常实习'))


def matches_mode(blob, mode='internship', cfg=None):
    """判断岗位是否属于该推送模式。"""
    cfg = cfg or get_mode_config(mode)
    include = cfg.get('include_keywords') or []
    exclude = list(cfg.get('exclude_keywords') or [])
    exclude += (_load_config().get('filter') or {}).get('exclude_keywords', [])

    if any(k in blob for k in exclude):
        return False, 'mode_exclude'

    if mode == 'internship':
        if not _is_internship_blob(blob):
            return False, 'not_internship'
        return True, 'internship'

    # campus2027
    if _is_internship_blob(blob) and not any(k in blob for k in ('27届', '2027', '校招', '应届')):
        return False, 'internship_only'

    if any(k in blob for k in include):
        return True, 'campus_keyword'

    # 公众号文章常不写「27届」，强 AI/CS 岗且非纯实习也纳入
    mp_kws = (_load_config().get('filter') or {}).get('mp_tech_keywords') or []
    core = _mp_core_title(blob[:200])
    ai_cs = list(dict.fromkeys(
        _kw_match(blob, cfg.get('ai_keywords') or [])
        + _kw_match(core, (cfg.get('cs_keywords') or []) + mp_kws)))
    if ai_cs and not _is_internship_blob(blob):
        return True, 'ai_campus'

    if cfg.get('allow_general_jobs') and not _is_internship_blob(blob):
        return True, 'general_campus'

    return False, 'no_campus_match'


def score_job(title, company, location, description, cfg=None, mode='internship',
              source_school='', source_type='employment_web'):
    cfg = cfg or get_mode_config(mode)
    blob = ' '.join(filter(None, [title, company, location, description]))

    global_exclude = (_load_config().get('filter') or {}).get('exclude_keywords') or []
    if any(k in blob for k in global_exclude):
        return 0, {'excluded': True}

    # 就业网噪音多；公众号标题相对干净，规则从宽
    if source_type == 'employment_web':
        if _is_event_not_job(blob):
            return 0, {'reason': 'event_not_job'}
        if _foreign_school_noise(title, source_school):
            return 0, {'reason': 'foreign_school'}
    elif source_type == 'wechat_mp' and mode == 'internship' and _MP_INTERN_JUNK.search(blob):
        return 0, {'reason': 'mp_intern_junk'}

    ok, reason = matches_mode(blob, mode, cfg)
    if not ok:
        return 0, {'reason': reason}

    core = _mp_core_title(title)
    loc_hits = _kw_match(blob, cfg.get('location_keywords') or [])
    ind_hits = _kw_match(blob, cfg.get('industry_keywords') or [])
    tech_hits, ai_hits, cs_hits = _tech_hits(blob, core, cfg, source_type)

    if cfg.get('require_ai', True) and not tech_hits:
        if source_type == 'wechat_mp' and mode == 'internship':
            tech_hits = _mp_internship_tech(core, blob, cfg, tech_hits)
        if not tech_hits:
            return 0, {'reason': 'no_tech_keyword'}
    if not loc_hits and '京津冀' not in blob:
        # 公众号来自京津冀学校，生源校即地域
        if source_type == 'wechat_mp' and source_school:
            loc_hits = ['京津冀']
        else:
            return 0, {'reason': 'no_location_match'}

    score = min(100, len(tech_hits) * 25 + len(ind_hits) * 10 + len(loc_hits) * 10)
    if ai_hits and loc_hits:
        score = max(score, 75)
    elif tech_hits and loc_hits:
        score = max(score, 70)
    return score, {'location': loc_hits, 'industry': ind_hits, 'ai': ai_hits,
                   'cs': cs_hits, 'mode_reason': reason}


def filter_and_dedup(jobs, source_school='', mode='internship', source_type='employment_web', dedup=True):
    cfg = get_mode_config(mode)
    seen = _load_seen(mode) if dedup else None
    matches = []
    batch_keys = set()

    for j in jobs:
        title = j.get('title', '').strip()
        company = j.get('company', '').strip()
        if not title:
            continue
        s, detail = score_job(
            title, company, j.get('location', ''), j.get('description', ''), cfg, mode,
            source_school=source_school or j.get('source_school', ''),
            source_type=j.get('source_type', source_type))
        if s < cfg.get('min_match_score', 60):
            continue
        nkey = _norm_dedup_key(title, company)
        if nkey in batch_keys:
            continue
        batch_keys.add(nkey)
        jid = job_id(title, company, j.get('url', ''), mode)
        if dedup and jid in seen['jobs']:
            continue
        entry = {
            'id': jid,
            'mode': mode,
            'title': title,
            'company': company,
            'location': j.get('location', ''),
            'url': j.get('url', ''),
            'description': (j.get('description') or '')[:500],
            'date': j.get('date', datetime.now().strftime('%Y-%m-%d')),
            'source_school': source_school or j.get('source_school', ''),
            'source_type': j.get('source_type', source_type),
            'source_account': j.get('source_account', ''),
            'score': s,
            'match_detail': detail,
            'found_at': datetime.now().isoformat(timespec='seconds'),
        }
        if dedup:
            seen['jobs'][jid] = entry
        matches.append(entry)

    if dedup:
        _save_seen(seen, mode)
    return matches


def get_schools_batch(batch='morning'):
    schools = _load_config()['schools']
    mid = (len(schools) + 1) // 2
    return schools[:mid] if batch == 'morning' else schools[mid:]


def save_match_report(matches, batch='', mode='internship'):
    os.makedirs(MATCHES, exist_ok=True)
    ts = datetime.now().strftime('%Y-%m-%d_%H%M')
    path = os.path.join(MATCHES, f'{ts}_{mode}_{batch or "scan"}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(matches, f, ensure_ascii=False, indent=2)
    return path


def _normalize_wechat_url(url):
    url = (url or '').strip()
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    return url


def _format_job_block(index, m):
    """微信可识别：标题一行 + 裸 URL 单独一行。"""
    title = (m.get('title') or '').strip()
    if len(title) > 55:
        title = title[:52] + '...'
    url = _normalize_wechat_url(m.get('url'))
    lines = [f'{index}. {title}']
    if url:
        lines.append(url)
    return '\n'.join(lines)


def format_wechat_message(matches, batch='', mode='internship', page=1, total_pages=1):
    label_cfg = get_mode_config(mode)
    mode_label = label_cfg.get('label', mode)
    time_label = '上午' if batch == 'morning' else '下午' if batch == 'afternoon' else ''

    if not matches:
        return f'📭【{mode_label}】{time_label}扫描完成，暂无新增匹配岗位。'

    header = f'📢【{mode_label}】京津冀 AI/CS 岗 · 共 {len(matches)} 条'
    if time_label:
        header += f'（{time_label}）'
    if total_pages > 1:
        header += f' ({page}/{total_pages})'
    lines = [header, '']
    for i, m in enumerate(matches, 1):
        lines.append(_format_job_block(i, m))
    return '\n'.join(lines).rstrip()


def format_wechat_chunks(all_matches, batch='', mode='internship', max_chars=2800):
    """将全部匹配拆成多条消息，每条尽量塞满但不截断单条岗位。"""
    if not all_matches:
        return [format_wechat_message([], batch=batch, mode=mode)]

    label_cfg = get_mode_config(mode)
    mode_label = label_cfg.get('label', mode)
    time_label = '上午' if batch == 'morning' else '下午' if batch == 'afternoon' else ''
    total = len(all_matches)

    chunks, current, cur_len, start_idx = [], [], 0, 1

    def _header(page, total_pages):
        h = f'📢【{mode_label}】京津冀 AI/CS 岗 · 共 {total} 条'
        if time_label:
            h += f'（{time_label}）'
        if total_pages > 1:
            h += f' ({page}/{total_pages})'
        return h + '\n\n'

    def _flush():
        nonlocal current, cur_len, start_idx
        if current:
            chunks.append((start_idx, list(current)))
            start_idx += len(current)
            current, cur_len = [], 0

    for m in all_matches:
        block = _format_job_block(start_idx + len(current), m)
        block_len = len(block) + 1
        if current and cur_len + block_len > max_chars:
            _flush()
        current.append(m)
        cur_len += block_len

    _flush()

    total_pages = len(chunks)
    out = []
    for page, (base_idx, group) in enumerate(chunks, 1):
        hdr = _header(page, total_pages)
        body = '\n\n'.join(
            _format_job_block(base_idx + i, m) for i, m in enumerate(group))
        out.append((hdr + body).rstrip())
    return out


def save_jobs_html(matches, batch='', mode='internship', footer=''):
    """生成岗位 HTML 报告，返回文件路径。"""
    label_cfg = get_mode_config(mode)
    mode_label = label_cfg.get('label', mode)
    time_label = '上午' if batch == 'morning' else '下午' if batch == 'afternoon' else ''
    now = datetime.now()
    ts = now.strftime('%Y-%m-%d_%H%M')

    os.makedirs(MATCHES, exist_ok=True)
    batch_tag = batch or 'scan'
    path = os.path.join(MATCHES, f'{ts}_{mode}_{batch_tag}.html')

    title_parts = [f'【{mode_label}】京津冀 AI/CS 岗']
    if time_label:
        title_parts.append(time_label)
    page_title = ' · '.join(title_parts)

    rows = []
    for i, m in enumerate(matches, 1):
        title = html.escape((m.get('title') or '').strip())
        company = html.escape((m.get('company') or '').strip())
        src_raw = (m.get('source_account') or m.get('source_school') or '未知')
        loc_raw = (m.get('location') or '')
        score = m.get('score', 0)
        src_type = '公众号' if m.get('source_type') == 'wechat_mp' else '就业网'
        url = _normalize_wechat_url(m.get('url'))
        link = (f'<a href="{html.escape(url)}" target="_blank">查看详情 →</a>'
                if url else '<span class="muted">无链接</span>')
        head = f'{company} · {title}' if company else title
        meta = ' · '.join(p for p in [src_raw, loc_raw, f'{score}%', src_type] if p)
        rows.append(f'''    <div class="job">
      <div class="idx">{i}</div>
      <div class="body">
        <div class="title">{head}</div>
        <div class="meta">{html.escape(meta)}</div>
        <div class="link">{link}</div>
      </div>
    </div>''')

    empty = '<p class="empty">暂无新增匹配岗位。</p>'
    body = '\n'.join(rows) if rows else empty
    footer_html = f'<div class="footer">{html.escape(footer.strip())}</div>' if footer.strip() else ''

    doc = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page_title)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 16px; background: #f5f6f8; color: #1a1a1a; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .sub {{ color: #666; font-size: 13px; margin-bottom: 16px; }}
  .job {{ display: flex; gap: 10px; background: #fff; border-radius: 8px;
          padding: 12px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .idx {{ flex-shrink: 0; width: 28px; height: 28px; line-height: 28px; text-align: center;
          background: #1677ff; color: #fff; border-radius: 50%; font-size: 12px; font-weight: 600; }}
  .body {{ flex: 1; min-width: 0; }}
  .title {{ font-size: 15px; font-weight: 600; line-height: 1.4; word-break: break-word; }}
  .meta {{ font-size: 12px; color: #888; margin-top: 4px; }}
  .link {{ margin-top: 8px; }}
  .link a {{ color: #1677ff; text-decoration: none; font-size: 14px; }}
  .footer {{ margin-top: 20px; padding-top: 12px; border-top: 1px solid #e8e8e8;
             font-size: 12px; color: #999; white-space: pre-wrap; }}
  .empty {{ text-align: center; color: #999; padding: 40px 0; }}
  .muted {{ color: #bbb; font-size: 13px; }}
</style>
</head>
<body>
  <h1>{html.escape(page_title)}</h1>
  <div class="sub">共 {len(matches)} 条 · 生成于 {now:%Y-%m-%d %H:%M}</div>
{body}
{footer_html}
</body>
</html>'''

    with open(path, 'w', encoding='utf-8') as f:
        f.write(doc)
    return path
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        demo = [
            {'title': '大模型实习工程师', 'company': '字节跳动', 'location': '北京',
             'url': 'https://example.com/1', 'description': '互联网 AI 日常实习'},
            {'title': '大模型应用开发工程师', 'company': '字节跳动', 'location': '北京',
             'url': 'https://example.com/2', 'description': '27届校招 互联网 AI'},
        ]
        print('--- internship ---')
        print(format_wechat_message(filter_and_dedup([demo[0]], '清华大学', 'internship'), mode='internship'))
        print('--- campus2027 ---')
        print(format_wechat_message(filter_and_dedup([demo[1]], '清华大学', 'campus2027'), mode='campus2027'))
