"""Scrapling public contact inspector. Run with Python 3.10+."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_DEPS = ROOT.parents[1] / 'work' / 'python-deps'
if LOCAL_DEPS.is_dir():
    sys.path.insert(0, str(LOCAL_DEPS))

import csv
import hashlib
import html as html_lib
import json
import queue
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urljoin, urldefrag
from urllib.robotparser import RobotFileParser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

AGENT = 'PublicPageInspector/1.0'
CACHE = ROOT / 'cache'
CACHE_VER = 'c1'
FIELDS = ('source_url', 'kind', 'value', 'person', 'page_url', 'fetched_at')
MAX_URLS = 50
MAX_CONTACT_PAGES = 4
DEFAULT_DELAY = 3
CONTACT_KINDS = ('电子邮箱', '手机', '电话', '微信', '社交账号', '传真')
KIND_ORDER = {kind: index for index, kind in enumerate(CONTACT_KINDS)}
BLOCKED_HOSTS = (
    'linkedin.com', 'linkedin.cn', 'google.com', 'google.com.hk', 'google.co.jp',
    'bing.com', 'facebook.com', 'instagram.com',
)

EMAIL_RE = re.compile(
    r'(?<![A-Za-z0-9._%+\-])'
    r'([A-Za-z0-9](?:[A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9])?)'
    r'@'
    r'([A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+)',
    re.I,
)
SPACED_EMAIL_RE = re.compile(
    r'([A-Za-z0-9][A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9])'
    r'\s+(?:at|AT)\s+'
    r'([A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9])'
    r'\s+(?:dot|DOT)\s+'
    r'([A-Za-z]{2,24})',
    re.I,
)
PHONE_CANDIDATE_RE = re.compile(r'(\+?\d[\d\s().\-]{6,22}\d)')
LABELED_PHONE_RE = re.compile(
    r'(?is)(tel(?:ephone)?|phone|mobile(?:\s*phone)?|cell|whatsapp|hotline|'
    r'电话|手机|座机|热线|联系电话|传真|fax)\s*[:：]?\s*(\+?\d[\d\s().\-]{6,22}\d)'
)
WECHAT_RE = re.compile(
    r'(?i)(?:wechat|weixin|微信(?:号|ID)?|\bwx\b)\s*[:：]\s*'
    r'([A-Za-z][-_A-Za-z0-9]{5,19})'
)
SKYPE_RE = re.compile(r'(?i)skype\s*[:：]\s*([A-Za-z][-_A-Za-z0-9.]{5,31})')
WHATSAPP_RE = re.compile(r'(?i)(?:wa\.me/|whatsapp\.com/send\?phone=)(\+?\d{8,15})')
FILE_TLDS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp', 'tif', 'tiff',
    'css', 'js', 'mjs', 'map', 'json', 'xml', 'txt', 'pdf', 'zip', 'rar', '7z',
    'gz', 'tgz', 'woff', 'woff2', 'ttf', 'otf', 'eot', 'mp3', 'mp4', 'avi',
    'mov', 'webm', 'wav', 'exe', 'dll', 'bin', 'dat', 'apk', 'dmg', 'iso',
}
JUNK_DOMAINS = {
    'example.com', 'example.org', 'example.net', 'test.com', 'email.com',
    'domain.com', 'yourdomain.com', 'yourname.com', 'sentry.io', 'w3.org',
    'schema.org', 'googleapis.com', 'gstatic.com', 'cloudflare.com',
    'wixpress.com', 'localhost',
}
JUNK_LOCALS = {
    'example', 'email', 'user', 'username', 'yourname', 'firstname', 'lastname',
    'name', 'test', 'sample', 'placeholder', 'nobody',
}
EMAIL_ROLES = {
    'sales': '销售', 'sale': '销售', 'info': '咨询', 'admin': '管理员',
    'support': '客服', 'service': '客服', 'cs': '客服', 'contact': '联系人',
    'office': '办公室', 'market': '市场', 'marketing': '市场', 'hr': '人事',
    'job': '招聘', 'recruit': '招聘', 'press': '媒体', 'media': '媒体',
    'pr': '公关', 'finance': '财务', 'legal': '法务', 'wholesale': '批发',
}
WEAK_CONTEXT = {
    'call', 'click', 'here', 'more', 'link', 'contact', 'email us', 'read more',
    '详情', '点击', '更多', '链接',
}
GENERIC_LABELS = {
    'email', 'e-mail', 'mail', 'tel', 'tel.', 'telephone', 'phone', 'fax',
    'mobile', 'mobile phone', 'cell', 'wechat', 'weixin', 'skype', 'website',
    'address', 'home', '邮箱', '电子邮箱', '电话', '传真', '手机', '微信',
    '网址', '地址', 'e-mail：', 'tel：',
}
CONTACT_STRONG = (
    'contact', 'contacts', 'contact-us', 'contactus', 'kontakt', 'impressum',
    'imprint', 'get-in-touch', 'getintouch', 'enquiry', 'inquiry', 'enquire',
    'feedback', 'reach-us', 'reachus', 'write-us', 'mail-us',
    '联系', '联系我们', '联络', 'お問い合わせ',
)
CONTACT_MEDIUM = (
    'about', 'about-us', 'aboutus', 'company', 'team', 'staff', 'people',
    'support', 'service', 'legal', 'privacy', 'connect',
    '关于', '关于我们', '公司', '团队', '客服',
)
SKIP_LINK_EXT = (
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.zip', '.rar', '.doc',
    '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.mp4', '.mp3', '.css', '.js',
)
COMMON_CONTACT_PATHS = (
    '/contact', '/contact-us', '/contactus', '/contacts', '/about',
    '/about-us', '/impressum', '/company/contact', '/cn/contact', '/en/contact',
    '/team',
)


def validate_url(value):
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('请输入完整的 http:// 或 https:// 网页地址，不含登录凭据。')
    host = host_key(value)
    if any(host == blocked or host.endswith('.' + blocked) for blocked in BLOCKED_HOSTS):
        raise ValueError('不支持抓取 LinkedIn / Google 等需登录或搜索页。请改用公司自己的公开官网。')
    return urldefrag(value)[0]


def host_key(url):
    host = (urlsplit(url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def same_site(left, right):
    a, b = host_key(left), host_key(right)
    return bool(a and a == b)


def normalize_page_url(url):
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    netloc = host if not parts.port else f'{host}:{parts.port}'
    path = re.sub(r'/+', '/', parts.path or '/').lower()
    if path != '/' and path.endswith('/'):
        path = path[:-1]
    return urldefrag(f'{parts.scheme.lower()}://{netloc}{path}')[0]


def clean(text):
    return ' '.join((text or '').split())[:200]


def digits_only(value):
    return re.sub(r'\D', '', value or '')


def national_digits(value):
    d = digits_only(value)
    if d.startswith('86') and len(d) >= 12:
        return d[2:]
    return d


def contact_row(source, kind, value, person, page, stamp):
    return dict(zip(FIELDS, (source, kind, value, person or '', page, stamp)))


def parse_mailto(href):
    payload = href.split(':', 1)[-1]
    payload = unquote(payload.split('?', 1)[0])
    payload = payload.replace('mailto:', '')
    return payload.strip().strip('<>').split(',')[0].strip()


def parse_tel(href):
    payload = unquote(href.split(':', 1)[-1].split('?', 1)[0])
    return payload.strip()


def decode_cfemail(hexstr):
    hexstr = (hexstr or '').strip()
    try:
        data = bytes.fromhex(hexstr)
    except ValueError:
        return ''
    if len(data) < 2:
        return ''
    key = data[0]
    try:
        return bytes(byte ^ key for byte in data[1:]).decode('utf-8')
    except UnicodeDecodeError:
        return ''


def deobfuscate(text):
    text = html_lib.unescape(text or '')
    text = text.replace('\u00a0', ' ').replace('＠', '@').replace('．', '.')
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)
    text = re.sub(r'\s*[\(\[\{]\s*(?:at|AT)\s*[\)\]\}]\s*', '@', text)
    text = re.sub(r'\s*[\(\[\{]\s*(?:dot|DOT)\s*[\)\]\}]\s*', '.', text)
    text = SPACED_EMAIL_RE.sub(lambda match: f'{match.group(1)}@{match.group(2)}.{match.group(3)}', text)
    return text


def normalize_email(value):
    value = html_lib.unescape(value or '').strip().strip('.,;:<>()[]{}"\'')
    value = value.replace('＠', '@').replace('．', '.')
    value = re.sub(r'[\u200b\u200c\u200d\ufeff\s]', '', value)
    if value.lower().startswith('mailto:'):
        value = parse_mailto(value)
    return value


def is_plausible_email(value):
    value = normalize_email(value)
    if not value or value.count('@') != 1 or len(value) > 254:
        return False
    local, domain = value.rsplit('@', 1)
    local, domain = local.lower(), domain.lower().rstrip('.')
    if not local or not domain or '.' not in domain:
        return False
    tld = domain.rsplit('.', 1)[-1]
    if tld in FILE_TLDS or len(tld) < 2 or len(tld) > 24 or not tld.isalpha():
        return False
    if domain in JUNK_DOMAINS or any(domain.endswith('.' + junk) for junk in JUNK_DOMAINS):
        return False
    if local in JUNK_LOCALS or local.startswith('noreply') or local.startswith('no-reply'):
        return False
    if re.fullmatch(r'[a-f0-9]{24,}', local):
        return False
    if '..' in local or '..' in domain or local.startswith('.') or domain.startswith('.'):
        return False
    return bool(EMAIL_RE.fullmatch(local + '@' + domain))


def classify_phone(value, label=''):
    raw = (value or '').strip()
    d = digits_only(raw)
    if not d or d in {'00000000', '12345678', '1234567890'}:
        return None, ''
    if re.fullmatch(r'20\d{2}', d) or re.fullmatch(r'19\d{2}', d):
        return None, ''
    national = d[2:] if d.startswith('86') and len(d) >= 12 else d
    label = (label or '').lower()
    kind = None
    if re.fullmatch(r'1[3-9]\d{9}', national):
        kind = '手机'
        display = '+86-' + national if d.startswith('86') or raw.startswith('+') else national
    elif re.fullmatch(r'0\d{9,11}', national):
        kind = '电话'
        display = raw if '+' in raw or '-' in raw else national
    elif raw.startswith('+') and 8 <= len(d) <= 15:
        kind = '电话'
        display = '+' + d
    else:
        return None, ''
    if 'fax' in label or '传真' in label:
        kind = '传真'
    return kind, display


def role_from_email(email):
    local = email.split('@', 1)[0].lower()
    local = re.split(r'[._+\-]', local)[0]
    return EMAIL_ROLES.get(local, '')


def company_from_title(text):
    text = clean(text)
    if not text:
        return ''
    for sep in (' -', '|', '–', '—', '_'):
        if sep.strip() in text:
            parts = [clean(part) for part in re.split(r'\s*[-|–—_]\s*', text) if clean(part)]
            if parts:
                return max(parts, key=len)
    return text[:80]


def useful_context(line):
    text = clean(line)
    if not text or len(text) < 2 or len(text) > 80:
        return ''
    lowered = text.lower().rstrip(':：')
    if lowered in GENERIC_LABELS or lowered in WEAK_CONTEXT:
        return ''
    if is_plausible_email(text) or classify_phone(text)[0]:
        return ''
    if WECHAT_RE.search(text) or SKYPE_RE.search(text) or LABELED_PHONE_RE.search(text):
        return ''
    if EMAIL_RE.search(text):
        return ''
    if lowered.startswith(('http://', 'https://', 'www.', 'address', '地址')):
        return ''
    if re.fullmatch(r'[\d\s+\-().]+', text):
        return ''
    return text


def infer_person(lines, index, fallback=''):
    candidates = []
    for pos in range(max(0, index - 3), min(len(lines), index + 2)):
        if pos == index:
            continue
        context = useful_context(lines[pos])
        if context:
            candidates.append((abs(pos - index), 0 if pos < index else 1, context))
    candidates.sort()
    if candidates:
        return candidates[0][2]
    return fallback


def json_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value:
        return json_text(value[0])
    if isinstance(value, dict):
        return json_text(value.get('name') or value.get('@value') or value.get('email') or '')
    return ''


def walk_jsonld(payload, people, orgs):
    if isinstance(payload, dict):
        types = payload.get('@type') or payload.get('type') or ''
        types = [str(item).lower() for item in (types if isinstance(types, list) else [types]) if item]
        if any('person' in item for item in types):
            people.append(payload)
        if any('organization' in item or 'localbusiness' in item for item in types):
            orgs.append(payload)
        for value in payload.values():
            walk_jsonld(value, people, orgs)
    elif isinstance(payload, list):
        for item in payload:
            walk_jsonld(item, people, orgs)


def contact_score(url, text=''):
    if any(url.lower().split('?', 1)[0].endswith(ext) for ext in SKIP_LINK_EXT):
        return 0
    path = unquote(urlsplit(url).path or '').lower()
    caption = ' '.join((text or '').lower().split())
    if any(token in path for token in CONTACT_STRONG) or any(token in caption for token in CONTACT_STRONG):
        return 3
    if any(token in path for token in CONTACT_MEDIUM):
        return 2
    if caption in ('about', 'about us', 'about-us', 'our team', 'company', '公司', '关于我们', '关于', '公司简介'):
        return 2
    return 0


def css_all(page, query):
    try:
        return page.css(query)
    except Exception:
        return []


GENERIC_ROLES = set(EMAIL_ROLES.values()) | {'LinkedIn', 'WhatsApp'}


def better_person(old, new):
    new = clean(new)
    old = clean(old)
    if not new:
        return False
    if not old:
        return True
    if old in GENERIC_ROLES and new not in GENERIC_ROLES:
        return True
    if old != new and len(new) > len(old) and new not in GENERIC_ROLES:
        return True
    return False


def add_contact(bucket, kind, value, person, source, stamp):
    value = clean(value)
    person = clean(person)
    if not value:
        return
    if kind == '电子邮箱':
        value = normalize_email(value).lower()
        if not is_plausible_email(value):
            return
        key = ('电子邮箱', value)
        person = person or role_from_email(value)
    elif kind in ('电话', '手机', '传真'):
        classified, display = classify_phone(value, '传真' if kind == '传真' else kind)
        if not classified:
            return
        kind, value = classified, display
        key = (kind, national_digits(value))
    else:
        key = (kind, value.lower())
    previous = bucket.get(key)
    if previous is None:
        bucket[key] = contact_row(source, kind, value, person, source, stamp)
        return
    if kind in ('电话', '手机', '传真') and value.startswith('+') and not previous['value'].startswith('+'):
        previous['value'] = value
    if better_person(previous['person'], person):
        previous['person'] = person


def extract_links(page, source):
    links = []
    seen = set()
    for element in css_all(page, 'a[href]'):
        href = element.attrib.get('href', '').strip()
        if not href or href.lower().startswith(('mailto:', 'tel:', 'sms:', 'javascript:', '#')):
            continue
        target = urldefrag(urljoin(source, href))[0]
        if urlsplit(target).scheme not in ('http', 'https') or target in seen:
            continue
        if '/cdn-cgi/' in urlsplit(target).path.lower():
            continue
        seen.add(target)
        links.append({'title': clean(' '.join(element.xpath('.//text()').getall())), 'url': target})
    return links


def extract_contacts(page, source, stamp):
    bucket = {}
    title = clean(' '.join(page.css('title::text').getall()))
    site_name = ''
    for element in css_all(page, 'meta[property="og:site_name"], meta[name="application-name"]'):
        site_name = clean(element.attrib.get('content', ''))
        if site_name:
            break
    company = site_name or company_from_title(title)
    people, orgs = [], []
    for element in css_all(page, 'script[type="application/ld+json"]'):
        raw = str(element.get_all_text(separator=' ', strip=True))
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        walk_jsonld(payload, people, orgs)
    for org in orgs:
        company = company or json_text(org.get('name'))
        add_contact(bucket, '电子邮箱', json_text(org.get('email')), json_text(org.get('name')) or company, source, stamp)
        kind, display = classify_phone(json_text(org.get('telephone')))
        if kind:
            add_contact(bucket, kind, display, json_text(org.get('name')) or company, source, stamp)
    for person in people:
        name = ' '.join(part for part in (json_text(person.get('name')), json_text(person.get('jobTitle'))) if part)
        add_contact(bucket, '电子邮箱', json_text(person.get('email')), name or company, source, stamp)
        kind, display = classify_phone(json_text(person.get('telephone')))
        if kind:
            add_contact(bucket, kind, display, name or company, source, stamp)

    for element in css_all(page, 'a[href]'):
        href = element.attrib.get('href', '').strip()
        label = clean(' '.join(element.xpath('.//text()').getall()))
        person = useful_context(label) if '@' not in label and not classify_phone(label)[0] else ''
        lowered = href.lower()
        if lowered.startswith('mailto:'):
            add_contact(bucket, '电子邮箱', parse_mailto(href), person or company, source, stamp)
        elif lowered.startswith('tel:'):
            kind, display = classify_phone(parse_tel(href), label)
            if kind:
                add_contact(bucket, kind, display, person or company, source, stamp)
        elif 'email-protection' in lowered and '#' in href:
            add_contact(bucket, '电子邮箱', decode_cfemail(href.rsplit('#', 1)[-1]), person or company, source, stamp)
        cfhex = element.attrib.get('data-cfemail', '')
        if cfhex:
            add_contact(bucket, '电子邮箱', decode_cfemail(cfhex), person or company, source, stamp)
        host = host_key(href) or host_key(urljoin(source, href))
        target = urljoin(source, href)
        if host.endswith('linkedin.com') or host.endswith('linkedin.cn'):
            add_contact(bucket, '社交账号', target, person or 'LinkedIn', source, stamp)
        wa = WHATSAPP_RE.search(href)
        if wa:
            kind, display = classify_phone(wa.group(1), 'whatsapp')
            if kind:
                add_contact(bucket, kind, display, person or 'WhatsApp', source, stamp)

    for element in css_all(page, '[data-cfemail]'):
        add_contact(bucket, '电子邮箱', decode_cfemail(element.attrib.get('data-cfemail', '')), company, source, stamp)
    for element in css_all(page, '[itemprop=email], [data-email], [data-mail]'):
        for attr in ('content', 'data-email', 'data-mail', 'href', 'value'):
            add_contact(bucket, '电子邮箱', element.attrib.get(attr, ''), company, source, stamp)
        add_contact(bucket, '电子邮箱', ' '.join(element.xpath('.//text()').getall()), company, source, stamp)
    for element in css_all(page, '[itemprop=telephone], [itemprop=faxNumber]'):
        label = 'fax' if element.attrib.get('itemprop') == 'faxNumber' else ''
        kind, display = classify_phone(element.attrib.get('content', '') or ' '.join(element.xpath('.//text()').getall()), label)
        if kind:
            add_contact(bucket, kind, display, company, source, stamp)

    visible = deobfuscate(str(page.get_all_text(separator='\n', strip=True)))
    markup = deobfuscate(str(page.html_content))
    lines = [clean(line) for line in visible.splitlines() if clean(line)]
    for match in LABELED_PHONE_RE.finditer(visible):
        kind, display = classify_phone(match.group(2), match.group(1))
        if kind:
            add_contact(bucket, kind, display, company, source, stamp)
    for local, domain in EMAIL_RE.findall(visible + ' ' + markup):
        email = f'{local}@{domain}'
        add_contact(bucket, '电子邮箱', email, role_from_email(email), source, stamp)
    for index, line in enumerate(lines):
        for local, domain in EMAIL_RE.findall(line):
            email = f'{local}@{domain}'
            add_contact(bucket, '电子邮箱', email, infer_person(lines, index, role_from_email(email)), source, stamp)
        for match in PHONE_CANDIDATE_RE.finditer(line):
            kind, display = classify_phone(match.group(1), line[:match.start()][-16:])
            if kind:
                add_contact(bucket, kind, display, infer_person(lines, index, ''), source, stamp)
        wechat = WECHAT_RE.search(line)
        if wechat:
            add_contact(bucket, '微信', wechat.group(1), infer_person(lines, index, ''), source, stamp)
        skype = SKYPE_RE.search(line)
        if skype:
            add_contact(bucket, '社交账号', 'Skype: ' + skype.group(1), infer_person(lines, index, ''), source, stamp)
    for item in bucket.values():
        if not item['person']:
            item['person'] = company
    return list(bucket.values())


def extract(page, source, stamp):
    return {
        'contacts': extract_contacts(page, source, stamp),
        'links': extract_links(page, source),
    }


def prefer_contact_url(current, candidate):
    return contact_score(candidate, '') > contact_score(current, '')


def merge_contacts(batches, seed):
    merged = {}
    for rows in batches:
        for item in rows:
            item = dict(item)
            item['source_url'] = seed
            if item['kind'] == '电子邮箱':
                key = ('电子邮箱', item['value'].lower())
            elif item['kind'] in ('电话', '手机', '传真'):
                key = (item['kind'], national_digits(item['value']))
            else:
                key = (item['kind'], item['value'].lower())
            previous = merged.get(key)
            if previous is not None and item['kind'] in ('电话', '手机', '传真'):
                if previous['value'].startswith('+') and not item['value'].startswith('+'):
                    item['value'] = previous['value']
                elif item['value'].startswith('+') and not previous['value'].startswith('+'):
                    previous['value'] = item['value']
            better_page = previous is None or prefer_contact_url(previous['page_url'], item['page_url'])
            richer_person = previous is not None and item['person'] and (
                not previous['person'] or better_person(previous['person'], item['person'])
            )
            if previous is None or better_page or richer_person:
                if previous and previous['person'] and not item['person']:
                    item['person'] = previous['person']
                merged[key] = item
    return sorted(merged.values(), key=lambda item: (KIND_ORDER.get(item['kind'], 9), item['value']))


def contact_targets(seed, links):
    ranked = []
    seen = {normalize_page_url(seed)}
    for item in links:
        target = item['url']
        marker = normalize_page_url(target)
        if marker in seen or not same_site(seed, target):
            continue
        score = contact_score(target, item.get('title', ''))
        if not score:
            continue
        seen.add(marker)
        ranked.append((score, -len(ranked), target))
    ranked.sort(reverse=True)
    return [target for _, _, target in ranked]


def fallback_contact_urls(seed):
    parts = urlsplit(seed)
    origin = f'{parts.scheme}://{parts.netloc}'
    seen = {normalize_page_url(seed)}
    urls = []
    for path in COMMON_CONTACT_PATHS:
        candidate = urldefrag(urljoin(origin + '/', path.lstrip('/')))[0]
        marker = normalize_page_url(candidate)
        if marker not in seen:
            seen.add(marker)
            urls.append(candidate)
    return urls


def has_core_contact(contacts):
    return any(item['kind'] in ('电子邮箱', '手机', '电话') for item in contacts)


class Collector:
    def __init__(self, stop, log, use_cache=True):
        self.stop, self.log, self.use_cache = stop, log, use_cache
        self.last_request = 0
        self.robots = {}

    def get(self, url, delay=DEFAULT_DELAY):
        from scrapling.fetchers import Fetcher
        if self.stop.wait(max(0, delay - (time.monotonic() - self.last_request))):
            raise InterruptedError('已停止')
        self.last_request = time.monotonic()
        return Fetcher.get(url, headers={'User-Agent': AGENT}, timeout=15,
                           retries=0, stealthy_headers=False)

    def robots_delay(self, url):
        parts = urlsplit(url)
        origin = f'{parts.scheme}://{parts.netloc}'.lower()
        if origin in self.robots:
            parser, delay = self.robots[origin]
            if parser and not parser.can_fetch(AGENT, url):
                raise ValueError('robots.txt 不允许读取此页面')
            return delay
        robots_url = f'{parts.scheme}://{parts.netloc}/robots.txt'
        robots = self.get(robots_url)
        delay = DEFAULT_DELAY
        parser = None
        if robots.status == 200:
            parser = RobotFileParser()
            parser.parse(robots.body.decode('utf-8', errors='replace').splitlines())
            delay = max(delay, parser.crawl_delay(AGENT) or 0)
            rate = parser.request_rate(AGENT)
            if rate:
                delay = max(delay, rate.seconds / rate.requests)
        elif robots.status not in (404, 410):
            raise ValueError(f'robots.txt 返回 HTTP {robots.status}，已跳过')
        self.robots[origin] = (parser, delay)
        if parser and not parser.can_fetch(AGENT, url):
            raise ValueError('robots.txt 不允许读取此页面')
        return delay

    def cache_file(self, url):
        return CACHE / (hashlib.sha256(f'{CACHE_VER}:{url}'.encode()).hexdigest() + '.json')

    def load_cache(self, url):
        cached = self.cache_file(url)
        if not (self.use_cache and cached.exists() and time.time() - cached.stat().st_mtime < 86400):
            return None
        try:
            result = json.loads(cached.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return None
        if (
            isinstance(result, dict)
            and isinstance(result.get('contacts'), list)
            and isinstance(result.get('links'), list)
            and all(isinstance(item, dict) and set(FIELDS) <= item.keys() for item in result['contacts'])
        ):
            self.log('读取 24 小时内的缓存：' + url)
            return result
        return None

    def fetch_page(self, url):
        cached = self.load_cache(url)
        if cached is not None:
            return cached
        delay = self.robots_delay(url)
        page = self.get(url, delay)
        if page.status != 200:
            raise ValueError(f'页面返回 HTTP {page.status}，已跳过')
        stamp = datetime.now(timezone.utc).isoformat()
        result = extract(page, str(page.url), stamp)
        CACHE.mkdir(exist_ok=True)
        self.cache_file(url).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        return result

    def collect(self, url, follow_contact=True):
        url = validate_url(url)
        payload = self.fetch_page(url)
        contacts = [payload['contacts']]
        if follow_contact:
            extras = contact_targets(url, payload['links'])
            if not has_core_contact(payload['contacts']):
                seen_extra = {normalize_page_url(item) for item in extras}
                for candidate in fallback_contact_urls(url):
                    marker = normalize_page_url(candidate)
                    if marker not in seen_extra:
                        seen_extra.add(marker)
                        extras.append(candidate)
            for extra in extras[:MAX_CONTACT_PAGES]:
                if self.stop.is_set():
                    break
                self.log('跟进联系页：' + extra)
                try:
                    contacts.append(self.fetch_page(extra)['contacts'])
                except InterruptedError:
                    raise
                except Exception as exc:
                    self.log(f'未完成联系页：{extra} — {type(exc).__name__}: {exc}')
        return merge_contacts(contacts, url)


def export_csv(path, rows):
    def safe(value):
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    with open(path, 'w', newline='', encoding='utf-8-sig') as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: safe(str(row[k])) for k in FIELDS} for row in rows)


def summarize(rows):
    counts = {kind: 0 for kind in CONTACT_KINDS}
    for item in rows:
        if item['kind'] in counts:
            counts[item['kind']] += 1
    return counts


class App:
    def __init__(self, root):
        self.root = root
        self.rows = []
        self.messages = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        root.title('Scrapling 公开联系方式采集')
        root.geometry('1180x780')
        root.minsize(880, 600)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='公开联系方式采集', font=('Microsoft YaHei UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(
            frame,
            text='输入公司官网，提取已公开的邮箱、电话、手机和微信。可跟进同站联系页。不支持 LinkedIn / Google。每行一个 URL，最多 50 个。',
        ).pack(anchor='w', pady=(4, 8))
        self.urls = tk.Text(frame, height=5, wrap='none', font=('Consolas', 11))
        self.urls.pack(fill='x')
        self.urls.insert('1.0', 'https://www.szledcolor.com/')
        bar = ttk.Frame(frame)
        bar.pack(fill='x', pady=10)
        self.start_button = ttk.Button(bar, text='开始采集', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(bar, text='停止', command=self.cancel, state='disabled')
        self.stop_button.pack(side='left', padx=8)
        self.cache = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text='使用 24 小时缓存', variable=self.cache).pack(side='left')
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text='跟进联系/关于页', variable=self.follow).pack(side='left', padx=(10, 0))
        self.export_all_button = ttk.Button(bar, text='导出当前结果 CSV', command=self.export)
        self.export_all_button.pack(side='right')
        self.export_mail_button = ttk.Button(bar, text='仅导出邮箱', command=self.export_emails)
        self.export_mail_button.pack(side='right', padx=8)
        filters = ttk.Frame(frame)
        filters.pack(fill='x', pady=(0, 8))
        ttk.Label(filters, text='结果关键词：').pack(side='left')
        self.keyword = tk.StringVar()
        ttk.Entry(filters, textvariable=self.keyword, width=38).pack(side='left')
        ttk.Label(filters, text='  类型：').pack(side='left')
        self.kind = tk.StringVar(value='全部')
        ttk.Combobox(
            filters, textvariable=self.kind,
            values=['全部', *CONTACT_KINDS],
            state='readonly', width=12,
        ).pack(side='left')
        ttk.Label(filters, text='  双击可复制联系方式。').pack(side='left', padx=(10, 0))
        self.keyword.trace_add('write', lambda *_: self.render())
        self.kind.trace_add('write', lambda *_: self.render())
        table = ttk.Frame(frame)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=FIELDS, show='headings')
        labels = ('来源网站', '类型', '联系方式', '可能所属', '发现页面', '抓取时间 UTC')
        for column, label, width in zip(FIELDS, labels, (210, 80, 220, 220, 250, 160)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=70)
        self.tree.tag_configure('电子邮箱', background='#e8f5e9')
        self.tree.tag_configure('手机', background='#e3f2fd')
        self.tree.tag_configure('电话', background='#e3f2fd')
        self.tree.tag_configure('微信', background='#fff8e1')
        self.tree.tag_configure('社交账号', background='#f3e5f5')
        self.tree.tag_configure('传真', background='#f5f5f5')
        self.tree.bind('<Double-1>', self.copy_value)
        sy = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        sx = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sy.grid(row=0, column=1, sticky='ns')
        sx.grid(row=1, column=0, sticky='ew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value='就绪。只提取公开联系方式，不会抓取标题和产品链接。')
        ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=8)
        self.log = tk.Text(frame, height=6, state='disabled', wrap='word')
        self.log.pack(fill='x')
        root.after(100, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def filtered(self, emails_only=False):
        term = self.keyword.get().casefold().strip()
        kind = '电子邮箱' if emails_only else self.kind.get()
        return [r for r in self.rows if (kind == '全部' or r['kind'] == kind)
                and (not term or term in ' '.join(str(v) for v in r.values()).casefold())]

    def render(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.filtered()
        for item in rows:
            self.tree.insert('', 'end', values=[item[k] for k in FIELDS], tags=(item['kind'],))
        counts = summarize(self.rows)
        extra = ' · 正在采集' if self.running else ''
        self.status.set(
            f'邮箱 {counts["电子邮箱"]} · 手机 {counts["手机"]} · 电话 {counts["电话"]} · '
            f'微信 {counts["微信"]} · 当前显示 {len(rows)} 条' + extra
        )

    def start(self):
        if self.running:
            return
        try:
            urls = list(dict.fromkeys(validate_url(u) for u in self.urls.get('1.0', 'end').splitlines() if u.strip()))
            if not 1 <= len(urls) <= MAX_URLS:
                raise ValueError(f'请输入 1 到 {MAX_URLS} 个不同的网页 URL。')
        except ValueError as exc:
            messagebox.showerror('检查输入', str(exc))
            return
        self.rows = []
        self.stop.clear()
        self.running = True
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.export_all_button.configure(state='disabled')
        self.export_mail_button.configure(state='disabled')
        self.render()
        threading.Thread(target=self.worker, args=(urls, self.cache.get(), self.follow.get()), daemon=True).start()

    def worker(self, urls, cache, follow):
        collector = Collector(self.stop, lambda s: self.messages.put(('log', s)), cache)
        try:
            for url in urls:
                if self.stop.is_set():
                    break
                self.messages.put(('log', '正在读取：' + url))
                try:
                    rows = collector.collect(url, follow_contact=follow)
                    counts = summarize(rows)
                    self.messages.put(('rows', rows))
                    self.messages.put((
                        'log',
                        f'完成：{url}，邮箱 {counts["电子邮箱"]}，手机 {counts["手机"]}，'
                        f'电话 {counts["电话"]}，微信 {counts["微信"]}',
                    ))
                except InterruptedError:
                    break
                except Exception as exc:
                    self.messages.put(('log', f'未完成：{url} — {type(exc).__name__}: {exc}'))
        finally:
            self.messages.put(('done', None))

    def poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == 'rows':
                    self.rows.extend(value)
                    self.render()
                elif kind == 'log':
                    self.log.configure(state='normal')
                    self.log.insert('end', value + '\n')
                    self.log.see('end')
                    self.log.configure(state='disabled')
                elif kind == 'done':
                    self.running = False
                    self.start_button.configure(state='normal')
                    self.stop_button.configure(state='disabled')
                    self.export_all_button.configure(state='normal')
                    self.export_mail_button.configure(state='normal')
                    self.render()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def cancel(self):
        self.stop.set()
        self.status.set('正在停止；当前网络请求结束后停止，最长约 15 秒。')

    def copy_value(self, _event=None):
        item = self.tree.focus()
        if not item:
            return
        values = self.tree.item(item, 'values')
        if len(values) < 3:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(values[2])
        self.status.set(f'已复制：{values[2]}')

    def save_rows(self, rows, initial):
        if not rows:
            messagebox.showinfo('导出', '当前没有可导出的结果。')
            return
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile=initial, filetypes=[('CSV 文件', '*.csv')])
        if not path:
            return
        try:
            export_csv(path, rows)
            messagebox.showinfo('导出完成', f'已导出 {len(rows)} 条。')
        except OSError as exc:
            messagebox.showerror('导出失败', str(exc))

    def export(self):
        self.save_rows(self.filtered(), 'contacts.csv')

    def export_emails(self):
        self.save_rows(self.filtered(emails_only=True), 'emails.csv')

    def close(self):
        self.stop.set()
        self.root.destroy()


if __name__ == '__main__':
    app_root = tk.Tk()
    App(app_root)
    app_root.mainloop()
