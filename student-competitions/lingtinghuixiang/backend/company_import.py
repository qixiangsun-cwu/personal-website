# -*- coding: utf-8 -*-
"""新公司信息多格式解析器（07 新公司信息导入专用， Qin 11/11，A 方案移植）。

对外只暴露 parse_input(filename, data, text) -> {"rows": [...], "meta": {...}}。

支持格式：xlsx / xlsm / csv / docx / txt / md / 直接粘贴文字；
统一输出 6 字段：company_name / shop_name / contact_name / phone / audit_no / reject_reason。

解析策略：
  * 规则优先 —— 表头别名 + 正则抽字段；
  * 抽不全才交智谱 GLM 兜底（只补空字段、绝不编造，失败明确报 llm_error）；
  * 零新增第三方依赖 —— openpyxl / zhipuai / dotenv 全部延迟导入；
  * 老式 .xls / .doc 明确报错提示「另存为」，不静默读成乱码。
"""

import csv
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET

# openpyxl / zhipuai / dotenv 延迟导入（见 _read_xlsx / _llm_extract），
# 避免没装包时本模块加载失败拖垮整个 server。

# 全系统统一字段口径（与 import_data.COMPANY_FIELDS 一致）
FIELDS = ("company_name", "shop_name", "contact_name", "phone",
          "audit_no", "reject_reason")

# 每个字段的中文别名表：用于表头识别与「键:值」行识别
_ALIASES = {
    "company_name": ("公司全称", "公司名称", "企业名称", "公司", "企业",
                     "商家名称", "商家全称", "客户名称"),
    "shop_name": ("店铺名称", "店铺", "京东店铺", "旗舰店", "店名",
                  "店铺名", "商家店铺"),
    "contact_name": ("联系人", "联系人姓名", "姓名", "负责人", "对接人",
                     "联系方式人"),
    "phone": ("联系电话", "电话", "手机号", "手机", "联系方式", "号码",
              "座机", "电话号码"),
    "audit_no": ("资审单号", "资质单号", "单号", "资审编号", "资质编号",
                 "审核单号", "工单号"),
    "reject_reason": ("驳回场景", "驳回原因", "驳回", "驳回理由",
                      "不符合原因", "资质驳回"),
}

_KEY_RE = re.compile(r"^\s*([^：:]{2,12})[：:]\s*(.+)$")          # 「公司全称：xxx」键值行
_PHONE_RE = re.compile(r"(?:1[3-9]\d{9})|(?:0\d{2,3}-?\d{7,8})")  # 手机号 / 座机
_COMPANY_HINT = ("有限公司", "有限责任公司", "股份有限公司", "集团",
                 "商行", "工作室", "旗舰店", "专卖店", "专营店", "个体")
_SHOP_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}(?:旗舰店|专卖店|专营店|自营|京东店|店))")
_CONTACT_RE = re.compile(r"([\u4e00-\u9fa5]{1,3}(?:经理|主管|总监|女士|先生|总|老师))")
_NAME_NOISE_RE = re.compile(  # 「新商家是XX有限公司」这类叙述句开头
    r"^(?:新商家|商家|客户|该公司|企业)(?:是|为|叫|名(?:为|字)?[为是]?)")

# ---------------- 通用工具 ----------------


def _norm(name: str) -> str:
    """归一化公司名：去空格、冒号、括号、顿号、逗号，用于判重。"""
    return re.sub(r"[\s：:（）()、，,．.·]", "", name or "")


def _cell(v) -> str:
    """单元格清洗：None→空串、去 \r、去首尾空格。"""
    if v is None:
        return ""
    return str(v).replace("\r", "").strip()


def _match_field(name: str) -> str:
    """表头/键名 → 字段名。先按高频词直判，再按别名最长优先匹配。"""
    n = (name or "").replace(" ", "").lower()
    if not n:
        return ""
    # 高频词直判（优先级最高，避免「联系方式」误归店铺等）
    if "联系人" in n or n in ("姓名", "负责人", "对接人"):
        return "contact_name"
    if "电话" in n or "手机" in n or "号码" in n:
        return "phone"
    if "店铺" in n or n.endswith("店名"):
        return "shop_name"
    if "单号" in n or "编号" in n:
        return "audit_no"
    if "驳回" in n:
        return "reject_reason"
    best, best_len = "", 0
    for field, aliases in _ALIASES.items():
        for a in aliases:
            if a in n and len(a) > best_len:
                best, best_len = field, len(a)
    return best


def _blank() -> dict:
    """生成 6 个空字段的字典。"""
    return {k: "" for k in FIELDS}


def _suspicious_name(name: str) -> bool:
    """公司名可疑判定：空 / 过长 / 命中叙述句噪声 → 需要大模型修正。"""
    n = (name or "").strip()
    if not n or len(n) > 40:
        return True
    return bool(_NAME_NOISE_RE.match(n))


def _need_llm(row: dict) -> bool:
    """触发兜底的条件：公司名可疑，或除公司全称外一个字段都没抽到。"""
    if _suspicious_name(row.get("company_name", "")):
        return True
    return all(not row.get(k) for k in FIELDS if k != "company_name")


def _dedup(rows: list) -> list:
    """同批同名合并：按归一化名建索引，重复行只补空字段，不新增公司。"""
    out, idx = [], {}
    for r in rows:
        key = _norm(r.get("company_name", ""))
        if key and key in idx:
            base = idx[key]
            for k in FIELDS:
                if not base.get(k) and r.get(k):
                    base[k] = r[k]
            continue
        row = dict(r)
        if key:
            idx[key] = row
        out.append(row)
    return out


def _decode(data: bytes) -> str:
    """文本编码探测：依次试常见中文编码，全失败才 errors=replace。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


# ---------------- 文件读取 ----------------


def _read_xlsx(data: bytes) -> list:
    """xlsx/xlsm → 行列表（活动工作表全部行）。openpyxl 延迟导入。"""
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("解析 xlsx 需要 openpyxl：python -m pip install openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.active
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _read_csv(data: bytes) -> list:
    """csv → 行列表。Sniffer 猜分隔符（, ; \t |），失败回退逗号。"""
    text = _decode(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    return [row for row in reader if any((c or "").strip() for c in row)]


def _read_docx(data: bytes) -> list:
    """docx → 行列表：段落一行 + 表格一行一格。纯标准库 zipfile + ElementTree。"""
    NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    rows = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml_bytes = z.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    body = root.find(f"{NS_W}body")
    if body is None:
        return rows

    def text_of(el) -> str:
        # 兼容「文本不在 w:t 里」的生成文档：收集全部后代文本
        return "".join(t.text or "" for t in el.iter(f"{NS_W}t")).strip()

    for child in body:
        tag = child.tag
        if tag == f"{NS_W}p":
            t = text_of(child)
            if t:
                rows.append([t])
        elif tag == f"{NS_W}tbl":
            for tr in child.findall(f"{NS_W}tr"):
                row = [text_of(tc) for tc in tr.findall(f"{NS_W}tc")]
                if any(row):
                    rows.append(row)
    return rows


# ---------------- 规则解析 ----------------


def _map_header(header_row: list) -> dict:
    """表头行 → {列号: 字段名}。同一字段只取第一列。"""
    mapping = {}
    for i, name in enumerate(header_row):
        field = _match_field(_cell(name))
        if field and field not in mapping:
            mapping[i] = field
    return mapping


def _parse_table(rows: list) -> list:
    """表格解析：前 12 行里找表头；找不到就按 FIELDS 顺序硬映射；
    首列非空才算一家公司，脏数据行自动忽略。"""
    header_map = None
    head_i = -1
    for i, row in enumerate(rows[:12]):
        fields = {_match_field(_cell(c)) for c in row}
        fields.discard("")
        if len(fields) >= 3:  # 至少认出 3 个字段才算表头
            header_map = _map_header(row)
            head_i = i
            break
    out = []
    if header_map is None:
        # 无表头：按 FIELDS 顺序硬映射（公司/店铺/联系人/电话/单号/驳回）
        order = FIELDS
        for row in rows:
            if not row or not _cell(row[0] if row else None):
                continue
            r = _blank()
            for i, k in enumerate(order):
                if i < len(row):
                    r[k] = _cell(row[i])
            if r["company_name"]:
                out.append(r)
        return _dedup(out)
    for row in rows[head_i + 1:]:
        if not row or not _cell(row[0] if row else None):
            continue
        r = _blank()
        for i, field in header_map.items():
            if i < len(row):
                r[field] = _cell(row[i])
        if r["company_name"]:
            out.append(r)
    return _dedup(out)


def _split_blocks(text: str) -> list:
    """文本分段：空行分段；若只有一段且每行都不含「键:值」，按每行一家公司拆。"""
    blocks = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= 1:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if lines and not any(_KEY_RE.match(ln) for ln in lines):
            return [[ln] for ln in lines]
    return blocks


def _parse_text_block(block: str) -> dict:
    """逐行键值匹配 + 正则补抽电话 / 联系人 / 店铺名 / 公司名。"""
    r = _blank()
    for ln in block.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = _KEY_RE.match(ln)
        if m:
            field = _match_field(m.group(1))
            if field and not r[field]:
                r[field] = m.group(2).strip()
                continue
        # 正则补抽
        if not r["phone"]:
            pm = _PHONE_RE.search(ln)
            if pm:
                r["phone"] = pm.group(0)
        if not r["contact_name"]:
            cm = _CONTACT_RE.search(ln)
            if cm:
                r["contact_name"] = cm.group(1)
        if not r["shop_name"]:
            sm = _SHOP_RE.search(ln)
            if sm:
                # 剥掉「店铺」引导词
                val = sm.group(1)
                val = re.sub(r"^(店铺名?称?[：:]?)", "", val)
                r["shop_name"] = val
        if not r["company_name"]:
            for hint in _COMPANY_HINT:
                pos = ln.find(hint)
                if pos >= 0:
                    start = 0
                    for j in range(pos, -1, -1):
                        ch = ln[j]
                        if ch.isspace() or ch in "，。；：:、（）()":
                            start = j + 1
                            break
                    cand = ln[start:pos + len(hint)]
                    # 遇叙述句只取干净短串，完整修正交给大模型
                    nm = _NAME_NOISE_RE.match(cand)
                    if nm:
                        cand = cand[nm.end():]
                    if cand and len(cand) >= 4:
                        r["company_name"] = cand
                    break
    return r


# ---------------- 大模型兜底 ----------------

_LLM_SYS = (
    "你是公司资料抽取助手。从文本中抽取出公司信息，返回 JSON："
    '{"companies":[{"company_name":"","shop_name":"","contact_name":"",'
    '"phone":"","audit_no":"","reject_reason":""}]}。'
    "六个字段依次是：公司全称、店铺名称、联系人、联系电话、资审单号、驳回场景。"
    "文本里没有的字段一律留空字符串，禁止编造、禁止猜测。只返回 JSON，不要解释。"
)


def _llm_extract(text: str) -> list:
    """调智谱 GLM 抽取。任何失败都 raise，由上层明确提示，不静默降级。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    api_key = os.environ.get("ZHIPU_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 ZHIPU_API_KEY，无法进行大模型兜底")
    try:
        from zhipuai import ZhipuAI
    except ImportError:
        raise RuntimeError("未安装 zhipuai：python -m pip install zhipuai")
    model = os.environ.get("ZHIPU_MODEL", "glm-4-flash")
    client = ZhipuAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        temperature=0.1,
        messages=[{"role": "system", "content": _LLM_SYS},
                  {"role": "user", "content": text[:6000]}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    # 剥掉 ```json 围栏后截取 JSON 解析
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("大模型返回内容不是 JSON")
    data = json.loads(raw[start:end + 1])
    companies = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(companies, list):
        raise RuntimeError("大模型返回缺少 companies 数组")
    out = []
    for c in companies:
        if not isinstance(c, dict):
            continue
        row = _blank()
        for k in FIELDS:
            row[k] = _cell(c.get(k, ""))
        if row["company_name"]:
            out.append(row)
    return out


def _merge_llm(rule_rows: list, llm_rows: list) -> tuple:
    """大模型只补规则抽到的空字段；规则完全漏掉的公司才追加。
    返回 (rows, llm_filled, llm_added)。"""
    filled = added = 0
    idx = {_norm(r.get("company_name", "")): r for r in rule_rows
           if r.get("company_name")}
    used = set()
    for lr in llm_rows:
        key = _norm(lr.get("company_name", ""))
        target = idx.get(key)
        if target is None:
            # 用「包含关系」把「新商家是XX有限公司」与「XX有限公司」认成同一家
            for k2, r2 in idx.items():
                if k2 and key and (k2 in key or key in k2):
                    target = r2
                    break
        if target is not None:
            changed = False
            for k in FIELDS:
                if not target.get(k) and lr.get(k):
                    target[k] = lr[k]
                    changed = True
            if changed:
                filled += 1
            used.add(id(target))
        elif key:
            # 用大模型的干净公司名追加规则完全漏掉的公司
            rule_rows.append(dict(lr))
            idx[key] = rule_rows[-1]
            added += 1
    # 用大模型的干净公司名覆盖可疑名字，避免产生重复公司
    for r in rule_rows:
        if _suspicious_name(r.get("company_name", "")):
            key = _norm(r.get("company_name", ""))
            for lr in llm_rows:
                lk = _norm(lr.get("company_name", ""))
                if lk and (key in lk or lk in key) and not _suspicious_name(lk):
                    r["company_name"] = lr["company_name"]
                    break
    return rule_rows, filled, added


# ---------------- 对外主入口 ----------------


def parse_input(filename: str = None, data: bytes = None, text: str = "") -> dict:
    """统一解析入口。上传文件与粘贴文本可同时给。只解析、不写库。"""
    rows, source = [], "text"
    fname = (filename or "").lower()

    if data and fname:
        if fname.endswith((".xlsx", ".xlsm")):
            source = "excel"
            rows = _parse_table(_read_xlsx(data))
        elif fname.endswith(".xls"):
            raise RuntimeError("不支持老式 .xls 格式，请另存为 .xlsx 后重新上传")
        elif fname.endswith(".docx"):
            source = "docx"
            rows = _parse_table(_read_docx(data))
        elif fname.endswith(".doc"):
            raise RuntimeError("不支持老式 .doc 格式，请另存为 .docx 后重新上传")
        elif fname.endswith(".csv"):
            source = "csv"
            rows = _parse_table(_read_csv(data))
        elif fname.endswith((".txt", ".md", ".text")) or "." not in fname:
            source = "text"
            rows = [_parse_text_block(b) for b in _split_blocks(_decode(data))]
            rows = [r for r in rows if r.get("company_name") or any(r.values())]
            rows = [r for r in rows if any(r.values())]
        else:
            raise RuntimeError("不支持的文件格式，请使用 xlsx / csv / docx / txt / md，或直接粘贴文字")

    text = (text or "").strip()
    if text:
        trows = [_parse_text_block(b) for b in _split_blocks(text)]
        trows = [r for r in trows if any(r.values())]
        if source == "text" and not rows:
            rows = trows
            if fname.endswith((".txt", ".md", ".text")):
                source = "text_file"
        else:
            rows = rows + trows
            source = source + "+text"

    if not rows and not text:
        raise RuntimeError("请先选择文件，或在文本框里粘贴公司信息")

    rows = _dedup(rows)

    # 只有存在「抽不全」的行且是纯文本来源时才触发 GLM；
    # 表格类文件不调大模型（只标 manual_needed 让人手改）。
    llm_used = llm_filled = llm_added = 0
    llm_error = ""
    need = [r for r in rows if _need_llm(r)]
    if need and source in ("text", "text_file", "text+text", "csv+text"):
        llm_used = 1
        try:
            llm_rows = _llm_extract(text or "\n".join(
                " ".join(v for v in r.values() if v) for r in need))
            rows, llm_filled, llm_added = _merge_llm(rows, llm_rows)
        except Exception as e:  # 兜底异常写入 meta，规则结果仍保留
            llm_error = str(e)

    manual_needed = sum(1 for r in rows if _need_llm(r))
    return {"rows": rows,
            "meta": {"source": source, "rule_rows": len(rows),
                     "llm_used": bool(llm_used), "llm_filled": llm_filled,
                     "llm_added": llm_added, "llm_error": llm_error,
                     "manual_needed": manual_needed}}
