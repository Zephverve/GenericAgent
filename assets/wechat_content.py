"""
微信公众号内容获取工具 —— 整合 weixin-search-mcp 的 URL 解析 + 正文提取能力
配合 wechat_mp_search.py 使用的增强模块
"""
import re, requests, time, warnings
from urllib.parse import quote

warnings.filterwarnings('ignore')

HEADERS_MP = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}
TIMEOUT = 15


def resolve_real_url(sogou_url):
    """从搜狗 /link?url=... 跳转链接解析真实的 mp.weixin.qq.com 文章链接"""
    if not sogou_url or 'mp.weixin.qq.com' in sogou_url:
        return sogou_url  # 已经是真实链接

    try:
        resp = requests.get(sogou_url, headers=HEADERS_MP, timeout=TIMEOUT)
        html_text = resp.text

        # 方法1：搜狗 JavaScript 拼接 URL
        parts = re.findall(r"url \+= '([^']*)'", html_text)
        if parts:
            full = ''.join(parts).replace('@', '')
            if full:
                return 'https://mp.' + full

        # 方法2：HTTP 302 重定向
        if resp.history:
            for h in resp.history:
                loc = h.headers.get('Location', '')
                if 'mp.weixin.qq.com' in loc:
                    return loc

    except Exception:
        pass

    return sogou_url  # 回退


def fetch_article_content(real_url, referer=''):
    """获取微信公众号文章正文（从 div#js_content 提取文本）"""
    if not real_url or not real_url.startswith('http'):
        return ''

    headers = dict(HEADERS_MP)
    if referer:
        headers['Referer'] = referer

    try:
        resp = requests.get(real_url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()

        # 从 div#js_content 提取纯文本
        # 先用正则简单提取，避免引入 lxml 依赖
        match = re.search(r'<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*<script', resp.text, re.DOTALL)
        if not match:
            match = re.search(r'id="js_content"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
        if match:
            html_content = match.group(1)
            # 去除 HTML 标签
            text = re.sub(r'<[^>]+>', '', html_content)
            text = re.sub(r'&nbsp;', ' ', text)
            text = re.sub(r'&amp;', '&', text)
            text = re.sub(r'&lt;', '<', text)
            text = re.sub(r'&gt;', '>', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text

    except Exception:
        pass

    return ''


def enrich_jobs_with_content(jobs, max_fetch=10):
    """为岗位列表补充真实 URL 和正文摘要
    
    Args:
        jobs: wechat_mp_search 返回的岗位列表
        max_fetch: 最多获取几篇文章的正文
    
    Returns:
        增强后的 jobs 列表（新增 real_url 和 content_snippet 字段）
    """
    for i, job in enumerate(jobs):
        sogou_url = job.get('url', '')
        
        # 解析真实 URL
        real_url = resolve_real_url(sogou_url)
        job['real_url'] = real_url or sogou_url
        job['url'] = job['real_url']  # 更新为真实链接
        
        # 获取正文（限制数量）
        if i < max_fetch:
            content = fetch_article_content(real_url, referer=sogou_url)
            if content:
                job['content_snippet'] = content[:500]
                job['content_full'] = content
                job['content_length'] = len(content)
    
    return jobs


if __name__ == '__main__':
    # 快速测试
    test_url = "https://weixin.sogou.com/link?url=dn9a_-gY295K0Rci_xozVXfdMkSQTLW6cwJThYulHEtVjXrGTiVgS-Ce7l9fToAf"
    print(f"解析: {test_url[:60]}...")
    real = resolve_real_url(test_url)
    print(f"真实URL: {real[:80]}")

    if real:
        content = fetch_article_content(real, referer=test_url)
        print(f"正文长度: {len(content)} 字")
        print(f"正文预览: {content[:200]}")
