"""就业网 HTTP 抓取（curl 后端，规避 requests SSL 问题）。"""
import subprocess
from urllib.parse import urlparse

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
DEFAULT_TIMEOUT = 20


def core_domain(host: str) -> str:
    """scc.pku.edu.cn → pku.edu.cn"""
    for p in ('career.', 'job.', 'jy.', 'scc.', 'zhaopin.'):
        if host.startswith(p):
            return host[len(p):]
    return host


def build_xxfb_urls(base_url: str) -> list[str]:
    host = urlparse(base_url).netloc
    core = core_domain(host)
    seen, out = set(), []
    candidates = [
        f'https://{host}/xsglxt/f/jyxt/anony/xxfb',
        f'https://job.{core}/xsglxt/f/jyxt/anony/xxfb',
        f'https://career.{core}/xsglxt/f/jyxt/anony/xxfb',
        f'https://career.cic.{core}/xsglxt/f/jyxt/anony/xxfb',
    ]
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """用 curl 抓取，比 requests 更稳（尤其 .edu.cn）。"""
    try:
        r = subprocess.run(
            ['curl', '-sL', '-A', UA, '--max-time', str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if r.returncode == 0 and r.stdout and len(r.stdout) > 100:
            return r.stdout
    except Exception as e:
        print(f'  [fetch fail] {url}: {e}')
    return ''


def probe_xxfb(url: str) -> bool:
    html = fetch_url(url, timeout=12)
    return bool(html and ('showZwxx' in html or '————' in html))
