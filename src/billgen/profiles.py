"""档案库：人物 / 企业事实的 JSON 文件后端（无数据库）。

决策 3：档案放在 ``/data/billgen/profiles.json``，能直接看、直接改、直接备份。
决策 7：档案绑一家**首选银行**（模板 id），批量时「人 × 银行」才是可无人值守的配对。
决策 8：个人与企业都支持 —— 两种形状只在 ``kind`` 与必填字段上有差别。
决策 10：录入 = 表单 + 批量导入（粘 JSON / 上传 CSV），导入里**校验失败的行要报出来**，
          不能静默丢掉（:class:`ImportResult` 的 ``errors`` 就是给页面标红用的）。
决策 12：账户事实（路由号 / 账号 / 尾号 / 开户日）首次生成时派生并**写回**档案；
          之后一律以档案里的值为准（可手改），:func:`ensure_accounts` 只补缺、绝不覆盖。
决策 15：收支档次是**可选**提示（``monthly_income_hint`` / ``monthly_revenue_hint``），
          默认不填、不干扰模型。

这里只做「读到内存 → 校验 → 原样写回」，不碰 LLM、不碰渲染。
"""

from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import io
import json
import os
import random
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import registry
from .synth.ledger import routing_for

__all__ = [
    "ACCOUNT_KEYS",
    "DEFAULT_STORE",
    "ENTITY_TYPES",
    "ImportResult",
    "Profile",
    "ProfileError",
    "ProfileNotFound",
    "ProfileStore",
    "VALID_KINDS",
    "ensure_accounts",
    "import_csv",
    "import_json",
    "new_business",
    "new_person",
    "validate_profile",
]

#: 档案库默认位置。环境变量在**导入时**读一次；运行期换库直接给 ``ProfileStore(path)``。
#: 机器上没有 ``/data`` 也不报错 —— 只有真的去写这份文件时才需要那个目录。
DEFAULT_STORE = Path(os.environ.get("BILLGEN_PROFILES", "/data/billgen/profiles.json"))

#: 两种主体（决策 8）。
VALID_KINDS = ("person", "business")

#: 企业形态，与月 JSON / 账单上的 entityType 用同一套写法。
ENTITY_TYPES = ("LLC", "S-Corp", "Sole Prop")

#: 每种主体要派生哪几个账户（决策 12）：个人是支票 + 信用卡，企业是商业支票 + 信用卡。
ACCOUNT_KEYS: dict[str, tuple[str, ...]] = {
    "person": ("checking", "credit"),
    "business": ("business_checking", "credit"),
}

_STATE = re.compile(r"^[A-Z]{2}$")
_ZIP = re.compile(r"^\d{5}$")
#: 收支档次：``4000-6000``、``$4,000 – $6,000``、``4000`` 都认；空串 = 不填。
_HINT = re.compile(r"^\$?\d[\d,]*(?:\s*(?:-|–|—|to)\s*\$?\d[\d,]*)?$")
_ADDRESS_KEYS = ("street", "city", "state", "zip")


class ProfileError(ValueError):
    """档案库读写失败：文件损坏、导入文本解析不出来。"""


class ProfileNotFound(ProfileError):
    """按 id 找不到档案。"""


def _now() -> str:
    """档案时间戳：UTC、秒级、带 Z（和月 JSON 里的 ``createdAt`` 同一个写法）。"""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class Profile:
    """一条档案：一个主体 × 一家首选银行。

    ``payload`` 只存**事实**（姓名/地址/职业/企业形态…），不存渲染用字段 ——
    那些由 ``synth`` 层层派生，档案不背这个包袱。
    """

    id: str = ""
    kind: str = ""  # person | business
    bank: str = ""  # 模板 id，如 chase-personal，必须在 registry.SPECS 里
    display_name: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    accounts: dict[str, dict] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_business(self) -> bool:
        return self.kind == "business"

    def to_dict(self) -> dict[str, Any]:
        """落盘形态：字段一一对应，嵌套结构深拷贝（别把库里的对象交给调用方改）。"""
        return {
            "id": self.id,
            "kind": self.kind,
            "bank": self.bank,
            "display_name": self.display_name,
            "payload": copy.deepcopy(self.payload),
            "accounts": copy.deepcopy(self.accounts),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> Profile:
        """容忍缺字段（手写的、别的版本写回来的都能读）：缺什么给空默认值。"""
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise ProfileError("档案条目必须是对象")
        payload = raw.get("payload")
        accounts = raw.get("accounts")
        return cls(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or ""),
            bank=str(raw.get("bank") or ""),
            display_name=str(raw.get("display_name") or raw.get("displayName") or ""),
            payload=dict(payload) if isinstance(payload, Mapping) else {},
            accounts={str(k): dict(v) for k, v in accounts.items() if isinstance(v, Mapping)}
            if isinstance(accounts, Mapping)
            else {},
            created_at=str(raw.get("created_at") or raw.get("createdAt") or ""),
            updated_at=str(raw.get("updated_at") or raw.get("updatedAt") or ""),
        )


class ProfileStore:
    """档案库的读写：一个 JSON 文件 = 一张表。

    * 文件不存在 = 空库（第一次写入才落盘），不在构造/导入时就建目录；
    * 写入是原子的（``.tmp`` → fsync → ``os.replace``），掉电不会留半截库；
    * 增删改**立即落盘**：表单一次一改，忘调 :meth:`save` 就等于丢数据。
    """

    def __init__(self, path: Path | str = DEFAULT_STORE) -> None:
        self.path = Path(path)
        self._profiles: dict[str, Profile] = {}
        self._loaded = False

    # ------------------------------------------------------------ 读写

    def load(self) -> None:
        """从磁盘重读（文件被手改过也跟上）。文件不存在视为空库。"""
        self._loaded = True
        if not self.path.exists():
            self._profiles = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ProfileError(f"档案库文件损坏: {self.path}（{exc}）") from exc
        except OSError as exc:
            raise ProfileError(f"档案库读不出来: {self.path}（{exc}）") from exc
        entries: Any = raw.get("profiles") if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            raise ProfileError(f"档案库文件损坏: {self.path}（profiles 不是数组）")
        profiles: dict[str, Profile] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise ProfileError(f"档案库文件损坏: {self.path}（第 {index} 条不是对象）")
            profile = Profile.from_dict(entry)
            if not profile.id:
                raise ProfileError(f"档案库文件损坏: {self.path}（第 {index} 条缺 id）")
            profiles[profile.id] = profile
        self._profiles = profiles

    def save(self) -> None:
        """原子写：``.tmp`` → fsync → ``os.replace``，再 fsync 目录。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                {"version": 1, "profiles": [p.to_dict() for p in self.all()]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        temporary = self.path.with_name(self.path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------ 增删查改

    def all(self) -> list[Profile]:
        """全部档案，按创建时间排序（批量导入的一批人保持表里的先后）。"""
        self._ensure_loaded()
        return sorted(self._profiles.values(), key=lambda p: (p.created_at, p.id))

    def get(self, pid: str) -> Profile:
        self._ensure_loaded()
        try:
            return self._profiles[pid]
        except KeyError:
            raise ProfileNotFound(
                f"档案 {pid!r} 不存在；库里现有 {len(self._profiles)} 条"
            ) from None

    def upsert(self, profile: Profile) -> Profile:
        """插入或更新；给空 id 分配一个，``created_at`` 只在第一次写入时定下。"""
        self._ensure_loaded()
        now = _now()
        existing = self._profiles.get(profile.id) if profile.id else None
        if not profile.id:
            profile.id = self._fresh_id()
        profile.created_at = (existing.created_at if existing else "") or profile.created_at or now
        profile.updated_at = now
        self._profiles[profile.id] = profile
        self.save()
        return profile

    def delete(self, pid: str) -> None:
        """删掉一条；删不存在的 id 当没事（页面上连点两次不该报错）。"""
        self._ensure_loaded()
        if self._profiles.pop(pid, None) is None:
            return
        self.save()

    def _fresh_id(self) -> str:
        while True:
            candidate = uuid.uuid4().hex[:12]
            if candidate not in self._profiles:
                return candidate


# ------------------------------------------------------------------ 校验


def _address_issues(address: Any, label: str = "address") -> list[str]:
    if not isinstance(address, Mapping):
        return [f"{label} 必须是 {{street, city, state, zip}} 字典"]
    problems: list[str] = []
    for key in _ADDRESS_KEYS:
        if not str(address.get(key) or "").strip():
            problems.append(f"{label} 缺少 {key}")
    state = str(address.get("state") or "").strip()
    if state and not _STATE.fullmatch(state):
        problems.append(f"{label}.state 必须是两位大写州缩写（如 CA），实际 {state!r}")
    zip5 = str(address.get("zip") or "").strip()
    if zip5 and not _ZIP.fullmatch(zip5):
        problems.append(f"{label}.zip 必须是 5 位数字（如 94107），实际 {zip5!r}")
    return problems


def _hint_issues(value: Any, label: str) -> list[str]:
    """收支档次只校验「像不像区间」，空值一律放行（决策 15：默认不填）。"""
    if value is None or value == "":
        return []
    if not isinstance(value, str | int | float):
        return [f'{label} 需是 "4000-6000" 这样的区间']
    text = str(value).strip()
    if text and not _HINT.fullmatch(text):
        return [f'{label} 需是 "4000-6000" 这样的区间，实际 {value!r}']
    return []


def _count_issues(value: Any, label: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, bool):
        return [f"{label} 需是非负整数"]
    if isinstance(value, int):
        return [] if value >= 0 else [f"{label} 需是非负整数"]
    if isinstance(value, str) and value.strip().isdigit():
        return []
    return [f"{label} 需是非负整数，实际 {value!r}"]


def _person_issues(payload: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if not str(payload.get("name") or "").strip():
        problems.append("人物缺少 name（全名）")
    problems.extend(_address_issues(payload.get("address")))
    problems.extend(_hint_issues(payload.get("monthly_income_hint"), "monthly_income_hint"))
    return problems


def _business_issues(payload: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if not str(payload.get("legalName") or "").strip():
        problems.append("企业缺少 legalName（法定名称）")
    entity = str(payload.get("entityType") or "").strip()
    if not entity:
        problems.append("企业缺少 entityType")
    elif entity not in ENTITY_TYPES:
        problems.append(f"entityType 必须是 LLC / S-Corp / Sole Prop 之一，实际 {entity!r}")
    problems.extend(_address_issues(payload.get("address")))
    problems.extend(_count_issues(payload.get("employeeCount"), "employeeCount"))
    problems.extend(_hint_issues(payload.get("monthly_revenue_hint"), "monthly_revenue_hint"))
    return problems


def validate_profile(profile: Profile) -> list[str]:
    """档案能不能拿去生成：返回中文错误串列表，空列表 = 通过。

    一次把问题报全（页面要一次标红所有格子），不要碰到第一个错就返回。
    """
    errors: list[str] = []
    kind = (profile.kind or "").strip()
    if kind not in VALID_KINDS:
        errors.append(f"kind 必须是 person 或 business，实际 {profile.kind!r}")
    bank = (profile.bank or "").strip()
    spec = registry.SPEC_BY_ID.get(bank)
    if spec is None:
        errors.append(f"未知模板 {bank!r}；可用：{', '.join(registry.SPEC_BY_ID)}")
    elif spec.kind == "static-web":
        errors.append(f"模板 {bank} 是纯前端模板（static-web），没有 Python 渲染器，档案不能绑定")
    if not (profile.display_name or "").strip():
        errors.append("display_name 不能为空")
    if isinstance(profile.accounts, Mapping):
        for key, value in profile.accounts.items():
            if not isinstance(value, Mapping):
                errors.append(f"accounts.{key} 必须是账户字典")
    else:
        errors.append("accounts 必须是 {账户名: 账户字典} 的字典")
    payload = profile.payload if isinstance(profile.payload, Mapping) else {}
    if kind == "person":
        errors.extend(_person_issues(payload))
    elif kind == "business":
        errors.extend(_business_issues(payload))
    return errors


# -------------------------------------------------------------- 账户派生


def _seed_int(key: str) -> int:
    """字符串 → 稳定的整数种子（``random.Random`` 直接用字符串会依赖哈希实现）。"""
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16)


def _derived_number(seed: str) -> str:
    """10–12 位账号：首位 1–9，保证不会派生出 ``000000000`` 这种号。"""
    rng = random.Random(_seed_int(f"account-number:{seed}"))
    length = 10 + rng.randrange(3)
    head = str(rng.randrange(1, 10))
    return head + "".join(str(rng.randrange(10)) for _ in range(length - 1))


def _derived_opened(seed: str) -> str:
    """开户日：相对今天 1–6 年前（真账单的账户不会昨天才开）。"""
    rng = random.Random(_seed_int(f"opened:{seed}"))
    return (dt.date.today() - dt.timedelta(days=rng.randrange(365, 365 * 6 + 1))).isoformat()


def _bank_name_of(profile: Profile) -> str:
    """档案绑的是模板 id，行名要按模板族查 ``TEMPLATE_BANK``（如 chase → JPMorgan Chase）。

    懒导入：``bridge`` 会拖进整条 PDF 栈，档案库（表单/导入）不该为它付启动成本。
    """
    from .synth.bridge import TEMPLATE_BANK

    root = (profile.bank or "").split("-")[0]
    return TEMPLATE_BANK.get(root, root)


def _derive_account(kind: str, bank_name: str, seed: str) -> dict[str, Any]:
    number = _derived_number(f"{seed}:{kind}")
    return {
        "routing": routing_for(bank_name, f"{seed}:{kind}"),
        "number": number,
        "last4": number[-4:],
        "opened": _derived_opened(f"{seed}:{kind}"),
    }


def ensure_accounts(profile: Profile) -> dict[str, dict]:
    """补齐该主体应有的账户，并写回 ``profile.accounts``（决策 12）。

    * 缺哪个账户键就派生哪个：路由号走 ``routing_for``（真行有真路由），
      账号/开户日由档案 id 派生，**同一个档案永远同一套号**；
    * 已经存在的字段一个都不覆盖 —— 手改过的号必须留住，这是明确要求；
    * 只补缺：手改了 ``number`` 却没改 ``last4`` 时，尾号跟着新号走。
    """
    if not isinstance(profile.accounts, dict):
        profile.accounts = {}
    keys = ACCOUNT_KEYS.get(profile.kind, ACCOUNT_KEYS["person"])
    bank_name = _bank_name_of(profile)
    seed = profile.id or f"{profile.bank}:{profile.display_name}"
    for kind in keys:
        derived = _derive_account(kind, bank_name, seed)
        existing = profile.accounts.get(kind)
        if not isinstance(existing, dict):
            profile.accounts[kind] = derived
            continue
        number = str(existing.get("number") or "").strip() or derived["number"]
        existing.setdefault("number", number)
        existing.setdefault("last4", number[-4:] if len(number) >= 4 else number)
        existing.setdefault("routing", derived["routing"])
        existing.setdefault("opened", derived["opened"])
    return profile.accounts


# ------------------------------------------------------------------ 导入


@dataclass(slots=True)
class ImportResult:
    """批量导入的结果：能用的档案 + 每一行的错误（页面按 ``row`` 标红）。"""

    profiles: list[Profile] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


#: CSV 表头 → 档案字段。大小写、空格、下划线、连字符都不敏感（``_csv_key`` 先归一）。
_CSV_ALIASES: dict[str, str] = {
    "kind": "kind",
    "type": "kind",
    "subject_type": "kind",
    "bank": "bank",
    "template": "bank",
    "bank_template": "bank",
    "display_name": "display_name",
    "displayname": "display_name",
    "label": "display_name",
    "name": "name",
    "full_name": "name",
    "fullname": "name",
    "person_name": "name",
    "legal_name": "legalName",
    "legalname": "legalName",
    "company": "legalName",
    "company_name": "legalName",
    "entity_type": "entityType",
    "entitytype": "entityType",
    "street": "street",
    "address": "street",
    "street_address": "street",
    "address1": "street",
    "city": "city",
    "state": "state",
    "zip": "zip",
    "zip5": "zip",
    "zip_code": "zip",
    "postal_code": "zip",
    "occupation": "occupation",
    "job": "occupation",
    "job_title": "occupation",
    "household": "household",
    "interests": "interests",
    "hobbies": "interests",
    "monthly_income_hint": "monthly_income_hint",
    "income": "monthly_income_hint",
    "income_hint": "monthly_income_hint",
    "industry": "industry",
    "description": "description",
    "notes": "description",
    "employee_count": "employeeCount",
    "employeecount": "employeeCount",
    "employees": "employeeCount",
    "monthly_revenue_hint": "monthly_revenue_hint",
    "revenue": "monthly_revenue_hint",
    "revenue_hint": "monthly_revenue_hint",
}

#: 企业形态的宽容写法 → 规范值（表格里 "llc"、"S Corp" 太常见，不该因此整行报废）。
_ENTITY_CANON: dict[str, str] = {
    "llc": "LLC",
    "l l c": "LLC",
    "limited liability company": "LLC",
    "s corp": "S-Corp",
    "scorp": "S-Corp",
    "s corporation": "S-Corp",
    "sole prop": "Sole Prop",
    "sole proprietor": "Sole Prop",
    "sole proprietorship": "Sole Prop",
}

#: 平铺条目里可以直接抬进 ``payload`` 的键（JSON 与 CSV 都走这一套）。
_FLAT_FACTS = (
    "name",
    "legalName",
    "entityType",
    "occupation",
    "household",
    "interests",
    "monthly_income_hint",
    "industry",
    "description",
    "employeeCount",
    "monthly_revenue_hint",
)


def _csv_key(name: str) -> str:
    text = (name or "").replace("\ufeff", "").strip().lower()
    return re.sub(r"[\s\-]+", "_", text)


def _entity_value(value: Any) -> str:
    """``llc`` / ``S Corp`` / ``sole proprietor`` → 规范值；不认识的写法原样留着让校验去报。"""
    text = str(value or "").strip()
    if not text:
        return ""
    key = re.sub(r"[\s_\-]+", " ", text.lower()).strip()
    return _ENTITY_CANON.get(key, text)


def _profile_from_mapping(raw: Mapping[str, Any]) -> Profile:
    """一条平铺/带 payload 的原始条目 → :class:`Profile`（不做校验，校验在外面统一做）。"""
    if not isinstance(raw, Mapping):
        raise ProfileError("条目必须是对象")
    facts: dict[str, Any] = {}
    nested = raw.get("payload")
    if isinstance(nested, Mapping):
        facts.update(nested)
    for key in _FLAT_FACTS:
        value = raw.get(key)
        if key not in facts and value not in (None, ""):
            facts[key] = value
    if "entityType" in facts:
        facts["entityType"] = _entity_value(facts["entityType"])
    address = facts.get("address")
    if not isinstance(address, Mapping):
        address = raw.get("address")
    if not isinstance(address, Mapping):
        address = {key: raw.get(key) for key in _ADDRESS_KEYS}
    address = {str(k): v for k, v in address.items() if v not in (None, "")}
    if address:
        facts["address"] = address
    else:
        facts.pop("address", None)  # 空地址当没填，交给校验去报「缺少 address」
    kind = str(raw.get("kind") or "").strip().lower()
    if not kind:
        # 没写 kind 就按字段判：有法定名/企业形态的是企业
        kind = "business" if ("legalName" in facts or "entityType" in facts) else "person"
    bank = str(raw.get("bank") or raw.get("template") or "").strip()
    display = str(raw.get("display_name") or raw.get("displayName") or "").strip()
    if not display:
        display = str(facts.get("legalName") or facts.get("name") or "").strip()
    accounts = raw.get("accounts")
    return Profile(
        id=str(raw.get("id") or "").strip(),
        kind=kind,
        bank=bank,
        display_name=display,
        payload=facts,
        accounts={str(k): dict(v) for k, v in accounts.items() if isinstance(v, Mapping)}
        if isinstance(accounts, Mapping)
        else {},
        created_at=str(raw.get("created_at") or raw.get("createdAt") or ""),
        updated_at=str(raw.get("updated_at") or raw.get("updatedAt") or ""),
    )


def _collect(entries: list[Any], first_row: int) -> ImportResult:
    """逐条建档 + 校验；坏行只记错误，不拖累其它行，也不静默丢弃。"""
    result = ImportResult()
    for offset, entry in enumerate(entries):
        row = first_row + offset
        try:
            profile = _profile_from_mapping(entry)
        except ProfileError as exc:
            result.errors.append({"row": row, "error": str(exc)})
            continue
        problems = validate_profile(profile)
        if problems:
            result.errors.append({"row": row, "error": "；".join(problems)})
        else:
            result.profiles.append(profile)
    return result


def import_json(text: str) -> ImportResult:
    """粘进来的 JSON：``{"profiles": [...]}`` 或裸数组都认，下标从 0 起算。"""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ProfileError(f"JSON 解析失败: {exc}") from exc
    if isinstance(raw, dict):
        entries = raw.get("profiles")
        if entries is None:
            entries = raw.get("items", [])
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ProfileError("JSON 顶层必须是对象（含 profiles 数组）或数组")
    if not isinstance(entries, list):
        raise ProfileError("profiles 必须是数组")
    return _collect(entries, 0)


def import_csv(text: str) -> ImportResult:
    """上传的 CSV：表头同时兼容人物与企业两套列，缺列忽略，行号从 2 起算（表头是 1）。"""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    fieldnames = [name for name in (reader.fieldnames or []) if name is not None]
    if not fieldnames:
        raise ProfileError("CSV 缺少表头（至少要有 kind,bank,display_name 之类）")
    headers = {name: _csv_key(name) for name in fieldnames}
    entries: list[dict[str, Any]] = []
    rows: list[int] = []
    for line_no, row in enumerate(reader, start=2):
        entry: dict[str, Any] = {}
        for name, value in row.items():
            if name is None or isinstance(value, list):
                continue
            canonical = _CSV_ALIASES.get(headers.get(name, ""))
            if canonical is None:
                continue
            text_value = str(value or "").strip()
            if text_value:
                entry[canonical] = text_value
        if not entry:
            continue  # 整行空白 / 只有逗号的行，不算一条档案
        for key in ("employeeCount", "household"):
            if isinstance(entry.get(key), str) and entry[key].isdigit():
                entry[key] = int(entry[key])
        entries.append(entry)
        rows.append(line_no)
    result = ImportResult()
    for entry, line_no in zip(entries, rows, strict=True):
        try:
            profile = _profile_from_mapping(entry)
        except ProfileError as exc:
            result.errors.append({"row": line_no, "error": str(exc)})
            continue
        problems = validate_profile(profile)
        if problems:
            result.errors.append({"row": line_no, "error": "；".join(problems)})
        else:
            result.profiles.append(profile)
    return result


# ------------------------------------------------------------------ 构造


def _compact(facts: dict[str, Any]) -> dict[str, Any]:
    """空值不落档案：可选字段（决策 15）没填就是没填，别留一堆空串。"""
    return {key: value for key, value in facts.items() if value not in (None, "", [], {})}


def new_person(
    *,
    name: str,
    bank: str = "chase-personal",
    street: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
    display_name: str = "",
    occupation: str = "",
    household: int | str | None = None,
    interests: str = "",
    monthly_income_hint: str = "",
) -> Profile:
    """组一个个人档案（还没入库：id/时间戳由 :meth:`ProfileStore.upsert` 分配）。"""
    payload = _compact(
        {
            "name": name,
            "address": {"street": street, "city": city, "state": state, "zip": zip_code},
            "occupation": occupation,
            "household": household,
            "interests": interests,
            "monthly_income_hint": monthly_income_hint,
        }
    )
    return Profile(
        kind="person",
        bank=bank,
        display_name=display_name or name,
        payload=payload,
    )


def new_business(
    *,
    legal_name: str = "",
    name: str = "",
    entity_type: str = "LLC",
    bank: str = "chase-business",
    street: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
    display_name: str = "",
    industry: str = "",
    description: str = "",
    employee_count: int | str | None = None,
    monthly_revenue_hint: str = "",
) -> Profile:
    """组一个企业档案；``name`` 是 ``legal_name`` 的别名（表单里两个名字都常见）。"""
    legal = legal_name or name
    payload = _compact(
        {
            "legalName": legal,
            "entityType": _entity_value(entity_type),
            "address": {"street": street, "city": city, "state": state, "zip": zip_code},
            "industry": industry,
            "description": description,
            "employeeCount": employee_count,
            "monthly_revenue_hint": monthly_revenue_hint,
        }
    )
    return Profile(
        kind="business",
        bank=bank,
        display_name=display_name or legal,
        payload=payload,
    )
