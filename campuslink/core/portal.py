# -*- coding: utf-8 -*-
"""
门户协议层：拿登录页、解析表单、下载验证码、提交、判定结果。

这一层完全平台无关（只有 requests + BeautifulSoup），
OpenWrt 上原样可用。

从现有 captcha-login/autologin.py 迁移过来的关键经验：

1. **每次都要重新 GET 登录页。**
   这个门户（ASP.NET WebForms）的 `__VIEWSTATE` / `__EVENTVALIDATION`、
   以及 Panabit 系门户的 `t` / `sccpTimestamp` 都是每次变的。
   写死提交必然失败，所以 action/method/字段一律从页面现解析。

2. **提交按钮必须带上。**
   ASP.NET 靠 `<input type="submit" name="xxx">` 的 name 判断点了哪个按钮，
   漏掉会导致整个回发不触发。所以 submit 类型不排除。

3. **成功判定只能直接匹配文案。**
   该门户成功和失败共用同一个提示容器 `<p class="validation-summary-errors">`，
   而且成功时登录表单照旧渲染、不跳转 ——
   任何「容器为空」「表单消失」这类推断式条件都会误判。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

from .exceptions import NetworkUnreachable, PortalChanged
from .models import Credentials, PortalProfile

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 提交按钮之外的非数据字段
NON_DATA_TYPES = {"button", "reset", "image", "file"}

CAPTCHA_HINT = re.compile(
    r"(captcha|verify|valid|checkcode|randcode|rand|yzm|vcode|authcode|imgcode)",
    re.I,
)

# 服务端提示容器的候选 class 名。ASP.NET 门户常把成功和失败
# 都塞进 validation-summary-errors 里。
MESSAGE_CLASSES = (
    "validation-summary-errors",
    "field-validation-error",
    "error",
    "tips",
    "msg",
)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

#: 「当前在线状态」字段的三种可能取值（2026-11-18 实测）
#:
#: 实测 HTML 形态（注意换行和可选 font 标签）：
#:   <span id="MainContent_LabelOnline" style="color:OrangeRed;">
#:       <font color="OrangeRed">未联网</font></span>
#:   <span id="MainContent_LabelOnline"><font>已联网</font></span>
#:   <span id="MainContent_LabelOnline" style="x">已联网</span>
#:
#: 所以正则要跨行、允许 style、允许 font、允许两者之间有任意空白。
#: [\s\S]*? 只吃到第一个 > ，避免跨越太多节点。
ONLINE_STATE_RE = re.compile(
    r'MainContent_LabelOnline[^>]*>'           # 到 span 开标签结束
    r'(?:[\s\S]{0,120}?)'                      # 中间可能有空白/font 开标签
    r'([已未]联网)',                            # 目标文本
    re.I)

#: 「当前用户IP」字段
USER_IP_RE = re.compile(
    r'MainContent_LocalIP[^>]*>\s*([\d.]+)', re.I)


def extract_online_state(html: str) -> str:
    """
    从注销页抠「当前在线状态」。

    实测 HTML 形态（2026-11-18）：
        <span id="MainContent_LabelOnline" style="color:OrangeRed;">
            <font color="OrangeRed">未联网</font></span>

    注意 font 标签是**可选**的 —— 不同状态渲染不同，
    所以正则里用 (?:<font[^>]*>)? 兼容。

    返回 "已联网" / "未联网" / ""（没找到）
    """
    m = ONLINE_STATE_RE.search(html)
    return m.group(1) if m else ""


def extract_user_ip(html: str) -> str:
    """从注销页抠「当前用户IP」。"""
    m = USER_IP_RE.search(html)
    return m.group(1) if m else ""


@dataclass
class LoginPage:
    """一次「打开登录页」的产物。"""

    base_url: str
    action: str
    method: str
    fields: dict[str, str] = field(default_factory=dict)   # 含全部隐藏域
    captcha_url: str = ""
    html: str = ""            # 仅内存保留，用于排查；不写日志
    cookie_names: list[str] = field(default_factory=list)


@dataclass
class LogoutPage:
    """
    一次「打开注销页」的产物（2026-11-18 新增）。

    比 LoginPage 多两个字段 —— online_state / user_ip，
    这两个是**服务端权威状态**，比 generate_204 探针可靠：
    探针只能间接推断，而这里门户直接告诉你「已联网 / 未联网」。
    """

    base_url: str
    action: str
    method: str
    fields: dict[str, str] = field(default_factory=dict)
    captcha_url: str = ""
    online_state: str = ""    # "已联网" / "未联网" / ""
    user_ip: str = ""         # 服务端看到的客户端 IP
    html: str = ""


def make_session(profile: PortalProfile) -> requests.Session:
    """
    新建会话。

    trust_env=False 是刻意的：校园网环境里经常有系统代理，
    而认证门户走的是内网直连，走代理必然失败。
    """
    s = requests.Session()
    s.trust_env = False
    s.verify = profile.verify_ssl
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})
    return s


class PortalClient:
    """针对某个门户档案的协议客户端。一个实例对应一个登录会话。"""

    def __init__(self, profile: PortalProfile, session: requests.Session | None = None):
        self.profile = profile
        self.session = session or make_session(profile)

    # ------------------------------------------------------------ 打开登录页
    def open_login_page(self) -> LoginPage:
        url = self.profile.portal_url
        if not url or url == "auto":
            raise PortalChanged("门户地址尚未确定", "还没有配置门户地址")

        try:
            resp = self.session.get(url, timeout=self.profile.timeout)
        except requests.exceptions.SSLError as exc:
            raise NetworkUnreachable(
                str(exc), "证书校验失败，内网门户通常需要关闭 SSL 校验") from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkUnreachable(str(exc)) from exc

        resp.encoding = resp.apparent_encoding or resp.encoding
        html, base = resp.text, resp.url

        soup = BeautifulSoup(html, "lxml")
        form = self._pick_login_form(soup)
        if form is None:
            # 页面里没有表单，两种情况要分开说：
            # 一是已经在线（门户给的是「已在线」页），二是页面结构变了
            if any(k in html for k in ("已在线", "已经在线", "断开网络", "注销", "已登录")):
                raise PortalChanged("页面提示已在线", "当前似乎已经在线，不需要重复认证")
            raise PortalChanged(
                f"登录页里没有解析到表单（长度 {len(html)}）",
                "认证页面结构已变化，可能需要更新门户配置")

        return LoginPage(
            base_url=base,
            action=urljoin(base, form.get("action") or base),
            method=(form.get("method") or "POST").upper(),
            fields=self._parse_fields(form),
            captcha_url=self._locate_captcha(soup, base),
            html=html,
            cookie_names=list(self.session.cookies.keys()),
        )

    @staticmethod
    def _pick_login_form(soup: BeautifulSoup):
        forms = soup.find_all("form")
        if not forms:
            return None
        for f in forms:                       # 优先含密码框的
            if f.find("input", attrs={"type": "password"}):
                return f
        return forms[0]

    @staticmethod
    def _parse_fields(form) -> dict[str, str]:
        """
        抠出表单里所有 data 字段的默认值，隐藏域原样保留。

        注意 submit 也在内 —— ASP.NET 需要它。
        """
        fields: dict[str, str] = {}
        for tag in form.find_all(["input", "select", "textarea"]):
            name = tag.get("name")
            if not name:
                continue
            typ = (tag.get("type") or tag.name).lower()
            if typ in NON_DATA_TYPES:
                continue

            if tag.name == "select":
                opt = tag.find("option", selected=True) or tag.find("option")
                fields[name] = opt.get("value", "") if opt else ""
            elif typ in ("checkbox", "radio"):
                if tag.has_attr("checked"):
                    fields[name] = tag.get("value", "on")
            else:
                fields[name] = tag.get("value", "") or ""
        return fields

    @staticmethod
    def _locate_captcha(soup: BeautifulSoup, base: str) -> str:
        """
        在登录页里找验证码图片。

        打分依据：URL 含 captcha/vcode/... 得 3 分；
        带「点击换一张」的 onclick 得 3 分；属性里出现关键词得 1 分。
        """
        best, best_score = "", 0
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src:
                continue
            score = 0
            if CAPTCHA_HINT.search(src):
                score += 3
            if re.search(r"(refresh|reload|change|新|换)",
                         str(img.get("onclick") or ""), re.I):
                score += 3
            attrs = " ".join(f"{k}={v}" for k, v in img.attrs.items())
            if CAPTCHA_HINT.search(attrs):
                score += 1
            if score > best_score:
                best, best_score = urljoin(base, src), score
        return best

    # ------------------------------------------------------------ 验证码
    def fetch_captcha(self, page: LoginPage) -> bytes:
        """
        下载验证码图片。

        注意时间戳破缓存：不少门户对同一 URL 会返回同一张图。
        """
        url = self.profile.captcha_url
        if not url or url == "auto":
            url = page.captcha_url
        else:
            url = urljoin(page.base_url, url)

        if not url:
            return b""                        # 该门户不需要验证码

        import time as _t
        sep = "&" if "?" in url else "?"
        try:
            r = self.session.get(f"{url}{sep}_t={int(_t.time() * 1000)}",
                                 timeout=self.profile.timeout,
                                 headers={"Referer": page.base_url})
            r.raise_for_status()
            return r.content
        except requests.exceptions.RequestException as exc:
            raise NetworkUnreachable(str(exc), "验证码下载失败") from exc

    # ------------------------------------------------------------ 提交
    def build_payload(self, page: LoginPage, cred: Credentials,
                      captcha_text: str = "") -> dict[str, str]:
        """
        页面字段 + 我们的凭据 = 最终提交内容。

        field_map 的值支持 "a|b" 写法，一次填多个同义字段
        （例如某些门户同时有 password 和 passwordText 两个框）。
        """
        c = cred.normalized()
        fmap = self.profile.field_map or {}
        payload = dict(page.fields)
        payload.update(self.profile.extra_fields or {})

        values = {
            "operator": c.operator,
            "student_id": c.student_id,
            "account": c.account,
            "password": c.password,
            "captcha": captcha_text,
        }
        for key, value in values.items():
            if value in (None, ""):
                continue
            target = fmap.get(key)
            if not target:
                continue
            for fname in re.split(r"[|,]", target):
                fname = fname.strip()
                if fname:
                    payload[fname] = value
        return payload

    def submit(self, page: LoginPage, payload: dict[str, str]) -> requests.Response:
        action = self.profile.login_url or page.action
        method = (self.profile.method or page.method or "POST").upper()
        try:
            if method == "POST":
                return self.session.post(action, data=payload,
                                         timeout=self.profile.timeout,
                                         headers={"Referer": page.base_url})
            return self.session.get(action, params=payload,
                                    timeout=self.profile.timeout,
                                    headers={"Referer": page.base_url})
        except requests.exceptions.RequestException as exc:
            raise NetworkUnreachable(str(exc), "提交认证请求失败") from exc

    # ------------------------------------------------------------ 判定
    def evaluate(self, resp: requests.Response) -> tuple[bool, str, str]:
        """
        判断这次提交是否成功。

        返回 (ok, 面向用户的提示, 门户原样提示)
        """
        raw = extract_message(resp)
        ok = check_success(resp, self.profile.success_check)
        return ok, (raw or ("认证成功" if ok else "认证失败")), raw

    # ======================================================== 注销（2026-11-18 新增）
    def open_logout_page(self) -> LogoutPage:
        """
        打开注销页。

        与 open_login_page() 的差异：

          1. **不需要「已在线」的特殊处理**。登录页在已在线时可能不给表单，
             但注销页任何状态都正常返回（实测：未联网时也返回完整表单）。
          2. **必须解析出「当前在线状态」**。这是本次新增的权威信号。

        ═══ 关键：**不能拼 `lrl=1`**（2026-11-18 实测）═══

            GET /logoff?lrl=1   →  当前在线状态 = 「未联网」
            GET /logoff         →  当前在线状态 = 「已联网」   ← 真实状态

        带 `lrl` 参数时服务端走的是另一个分支（大概是「logoff redirect
        link」），显示的状态不可信。早期版本拼了 `lrl=1`，
        导致一条真实在线的会话被判成未联网，注销功能完全测不出来。

        实测 HTML（不带 lrl）：
            <form method="post" action="./logoff" id="ctl01">
              __VIEWSTATE / __VIEWSTATEGENERATOR / __VIEWSTATEENCRYPTED / __EVENTVALIDATION
              ctl00$MainContent$TextBoxCC     (验证码, maxlength=4)
              ctl00$MainContent$LogoffBtn     (submit, value="注销")
        """
        url = self.profile.logout_url
        if not url:
            base = self.profile.portal_url
            # portal_url 可能是 .../login，取其根
            if base.endswith("/login"):
                base = base[:-len("/login")]
            # ⚠️ 不带 lrl=1 —— 见上面的实测说明
            url = urljoin(base + "/", "logoff")

        try:
            resp = self.session.get(url, timeout=self.profile.timeout)
        except requests.exceptions.SSLError as exc:
            raise NetworkUnreachable(
                str(exc), "证书校验失败，内网门户通常需要关闭 SSL 校验") from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkUnreachable(str(exc), "注销页打不开") from exc

        resp.encoding = resp.apparent_encoding or resp.encoding
        html, base = resp.text, resp.url

        soup = BeautifulSoup(html, "lxml")
        form = self._pick_logout_form(soup)
        if form is None:
            raise PortalChanged(
                f"注销页里没有解析到表单（长度 {len(html)}）",
                "注销页面结构已变化，可能需要更新门户配置")

        return LogoutPage(
            base_url=base,
            action=urljoin(base, form.get("action") or base),
            method=(form.get("method") or "POST").upper(),
            fields=self._parse_fields(form),
            captcha_url=self._locate_captcha(soup, base),
            online_state=extract_online_state(html),
            user_ip=extract_user_ip(html),
            html=html,
        )

    @staticmethod
    def _pick_logout_form(soup: BeautifulSoup):
        """
        注销页的表单识别。

        **不能复用 _pick_login_form** —— 那里优先找含 password 框的表单，
        注销页根本没有密码框，会退化成「取第一个 form」，
        在多表单页面上可能选错。

        这里的判据：action 含 logoff，或表单里有 name 含 Logoff 的 input。
        """
        forms = soup.find_all("form")
        if not forms:
            return None

        for f in forms:
            action = str(f.get("action") or "")
            if "logoff" in action.lower():
                return f
            for tag in f.find_all("input"):
                name = str(tag.get("name") or "")
                if "Logoff" in name:
                    return f

        return forms[0]

    def fetch_logout_captcha(self, page: LogoutPage) -> bytes:
        """
        取注销验证码。

        与登录验证码的关键差异（实测）：
          · 登录走 /ACaptcha.ashx（200×60，算式，同一 session 可能返回同一张）
          · 注销走 /CheckCode    （80×24，4 位字符，**每次都是新图**）

        实测 30 张 md5 无一重复，所以这里只需要加时间戳破 HTTP 缓存，
        **不需要**登录页那套「换 session 重取」的逻辑。
        """
        url = self.profile.logout_captcha_url
        if not url or url == "auto":
            url = page.captcha_url
        if not url:
            return b""

        url = urljoin(page.base_url, url)
        sep = "&" if "?" in url else "?"

        try:
            r = self.session.get(
                f"{url}{sep}_t={int(time.time() * 1000)}",
                timeout=self.profile.timeout,
                headers={"Referer": page.base_url})
            r.raise_for_status()
            return r.content
        except requests.exceptions.RequestException as exc:
            raise NetworkUnreachable(str(exc), "注销验证码下载失败") from exc

    def build_logout_payload(self, page: LogoutPage,
                             captcha_text: str) -> dict[str, str]:
        """
        页面字段 + 验证码 + 注销按钮 = 最终提交内容。

        ⚠️ **LogoffBtn 必须带**。ASP.NET WebForms 靠 `<input type=submit>`
        的 name 判断触发了哪个回发，漏掉整个请求不生效。
        页面上它带 disabled="disabled" class="aspNetDisabled"，
        那是前端「验证码为空时禁用」的表现，**服务端不看这个属性**。
        """
        payload = dict(page.fields)
        payload.update(self.profile.extra_fields or {})

        fmap = self.profile.logout_field_map or {}
        target = fmap.get("captcha")
        if target:
            for fname in re.split(r"[|,]", target):
                fname = fname.strip()
                if fname:
                    payload[fname] = captcha_text

        if self.profile.logout_submit_name:
            payload[self.profile.logout_submit_name] = (
                self.profile.logout_submit_value or "注销")

        return payload

    def submit_logout(self, page: LogoutPage,
                      payload: dict[str, str]) -> requests.Response:
        action = self.profile.logout_url or page.action
        method = (page.method or "POST").upper()
        try:
            if method == "POST":
                return self.session.post(
                    action, data=payload, timeout=self.profile.timeout,
                    headers={"Referer": page.base_url})
            return self.session.get(
                action, params=payload, timeout=self.profile.timeout,
                headers={"Referer": page.base_url})
        except requests.exceptions.RequestException as exc:
            raise NetworkUnreachable(str(exc), "提交注销请求失败") from exc


# ------------------------------------------------------------------ 判定工具
def check_success(resp: requests.Response, rule: dict[str, Any]) -> bool:
    """
    按规则判断成功与否。

    支持的 type：
        contains / not_contains / regex / not_regex / url_contains / status / json
        all / any  —— value 是一组子规则，做与/或组合
    """
    rule = rule or {"type": "contains", "value": "成功"}
    kind = rule.get("type", "contains")
    value = rule.get("value", "")
    text = resp.text

    if kind == "all":
        return all(check_success(resp, r) for r in value)
    if kind == "any":
        return any(check_success(resp, r) for r in value)

    if kind == "contains":
        return value in text
    if kind == "not_contains":
        return value not in text
    if kind == "regex":
        return re.search(value, text) is not None
    if kind == "not_regex":
        return re.search(value, text) is None
    if kind == "url_contains":
        return value in str(resp.url)
    if kind == "status":
        return resp.status_code == int(value)
    if kind == "json":
        try:
            data = resp.json()
        except Exception:
            return False
        return all(str(data.get(k, "")) == str(v) for k, v in value.items())
    return False


def extract_message(resp: requests.Response) -> str:
    """
    抠出服务端想显示给用户的那句话。

    优先读提示容器（class 含 error/msg/tips 的元素）——
    全文扫关键词会先命中「上网账号:」这类表单标签，把真正的提示挡在后面，
    这个坑上一版踩过。
    """
    return extract_message_from_html(resp.text)


def extract_message_from_html(html: str) -> str:
    """
    与 extract_message 同逻辑，但直接吃 HTML 字符串（2026-11-18 新增）。

    为什么需要拆分：注销判定（Authenticator._judge_logout）拿到的
    是已经读出来的 HTML 文本，再包一个假的 Response 对象很别扭；
    而 extract_message 原本只接 Response。这里把核心逻辑抽出来，
    两个入口共用，行为完全一致。

    ⚠️ **无提示容器时的兜底要克制**（2026-11-18 修）：
    原实现全文扫关键词，命不中就把前 120 字符当提示返回 ——
    对注销页那是 `<title>断开网络 - ...`，等于把页面标题当报错，
    日志里全是噪音，也判不出「没问题」和「有问题」的区别。
    现在：兜底只返回**紧邻关键词**的片段，一个都没命中就返回空串。
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = None

    if soup is not None:
        for cls in MESSAGE_CLASSES:
            el = soup.find(class_=cls)
            if el:
                txt = el.get_text(" ", strip=True)
                if txt:
                    return txt[:200]

    # 先剔掉 head/script/style —— 标题和脚本不是「服务端提示」
    body = html
    for tag in ("head", "script", "style", "title"):
        body = re.sub(rf"<{tag}[\s\S]*?</{tag}>", " ", body, flags=re.I)

    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()

    # 只返回紧邻关键词的片段；一个都没命中就返回空串（不再回退到前 120 字符）
    for kw in ("验证码错误", "密码错误", "联网成功", "认证成功", "注销成功",
               "断开成功", "已注销", "已断开", "失败", "错误",
               "不存在", "已在线", "欠费", "并发"):
        idx = text.find(kw)
        if idx >= 0:
            return text[max(0, idx - 10): idx + 30].strip()
    return ""


#: 门户返回的常见错误 → 给用户的人话。
#:
#: 英文那批不是猜的 —— 该门户主页的「常见运营商服务器返回的错误信息」表格里
#: 逐条列了 AAA 系统会吐回来的英文原文。校园网后台经常直接把 RADIUS 的
#: 原始报错透传给用户，对普通人等于天书，所以这里逐条翻译。
ERROR_HINTS = (
    # --- 中文提示 ---
    ("验证码", "验证码识别有误，正在重试"),
    ("密码错", "密码错误，请检查后重新输入"),
    ("账号未开通", "账号未开通，请联系运营商"),
    ("未实名", "账号未实名注册，请带证件到信息中心办理"),
    ("欠费", "账号已欠费，请联系运营商"),
    ("并发", "账号已在其他设备登录，请先下线再试"),
    ("不存在", "账号不存在，请确认输入是否正确"),
    ("已在线", "账号已经是在线状态"),
    ("超时", "认证超时，请稍后重试"),
    ("绑定", "账号绑定信息不符，请联系运营商解绑"),

    # --- 英文原文（来自门户 FAQ 表格）---
    ("account overdue", "账号已欠费，请联系运营商"),
    ("deregistered", "账号未开通或密码错误，请确认后重试"),
    ("subscriber status is incorrect", "账号未开通，请联系运营商"),
    ("aaa authenticate terminal failed", "账号绑定信息不符，请联系运营商解绑"),
    ("authen attrib", "账号绑定信息不符，请联系运营商解绑"),
    ("passwd_err", "密码错误，请确认后重新输入"),
    ("username_err", "账号无效，请确认输入是否正确"),
    ("username invalid", "账号无效，请确认输入是否正确"),
    ("illegal charactor", "账号含非法字符，请检查输入"),
    ("noservice", "认证服务未响应，请等 5 分钟后重试"),
    ("limit users err", "账号并发数超限，请先在其他设备下线"),
    ("limit exceed", "账号并发数超限，请先在其他设备下线"),
    ("concurrency", "账号已在其他设备登录，请先下线再试"),
    ("request deny", "账号可能无效，请联系运营商确认"),
    ("timeout", "认证超时，请稍后重试"),
)


def friendly_error(raw: str) -> str:
    """把门户返回的原始提示翻译成给普通用户看的话。"""
    low = (raw or "").lower()
    for key, hint in ERROR_HINTS:
        if key.lower() in low:
            return hint
    return raw or "认证失败"
