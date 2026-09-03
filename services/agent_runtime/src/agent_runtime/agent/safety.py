"""Shared text safety checks for model-facing, read-only boundaries."""

from __future__ import annotations

import re
import unicodedata

_UNSAFE_LITERAL_PATTERN = re.compile(
    r"(?i)(?:purchase\.submit|material_request\.create|purchase_order\.(?:create|submit)|"
    r"approve\s*the\s*order|expand\s*capability|(?:runtime\s*)?token\s*[:=]|"
    r"api[_-]?key\s*[:=]|authorization\s*[:=]|cookie\s*[:=]|secret\s*[:=])"
)
_UNSAFE_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:secret|api[_-]?key|authorization|cookie|runtime\s+token)\b"
    r"\s*(?:[:=]\s*[^\s,;]{3,}|\s+(?-i:[A-Z0-9][A-Z0-9_-]{5,}))"
)
_EXTERNAL_SCOPE_PATTERN = re.compile(
    r"(?i)(?:\b(?:other|another)[-_ ](?:company|warehouse|user)\b|"
    r"\b(?:other|another)[-_ ](?:company|warehouse)\s+(?:inventory|stock|data|facts?)\b|"
    r"\b(?:OTHER-COMPANY|OTHER-WAREHOUSE)\b|"
    r"(?:其他|别的|另一(?:个|家)?|不同的)(?:公司|仓库|用户)|"
    r"(?:跨公司|跨仓库|跨用户)|(?:公司|仓库|用户)(?:之外|以外|范围外))"
)
_EXTERNAL_SCOPE_FACT_PATTERN = re.compile(
    r"(?i)(?:inventory|stock|quantity|data|facts?|records?|access|"
    r"库存|数量|事实|记录|访问|仓库|公司|用户)"
)
_EXTERNAL_SCOPE_NEGATION_PATTERN = re.compile(
    r"(?i)(?:\b(?:cannot|can't|unable|refuse|reject|not\s+provide|read[- ]?only)\b|"
    r"不能|不可|无法|拒绝|不提供|只读|仅限|当前范围|没有|无|不含|不存在|未提供|"
    r"未包含|不涉及)"
)
_UNSAFE_CAPABILITY_PATTERN = re.compile(
    r"(?ix)(?:"
    r"开放(?:写(?:入)?|调用|工具|权限|能力)|申请扩大(?:写入)?(?:权限|能力)|"
    r"(?:扩大|提升|获取|授予|开启|启用)(?:写入|写|调用|工具|权限|能力)|"
    r"授权(?:调用|工具|能力|写入|执行)|授予(?:访问|调用|工具|写入|权限|能力)|"
    r"申请(?:扩大|更高|更大|额外|新增)(?:写入)?(?:权限|能力)|"
    r"绕(?:过|开)(?:权限|审批|校验|策略|限制)|"
    r"规避(?:权限|审批|校验|策略|限制)|(?:提升|切换|成为)(?:管理员|特权|超级用户)|"
    r"解除(?:权限|写入)?限制|(?:忽略|跳过|无视)审批|覆盖审批|"
    r"open\s*(?:write|tool|capability|permission|access)|"
    r"(?:request|expand|raise|get|grant|enable)\s*(?:write|tool|capability|permission|access)|"
    r"(?:bypass|circumvent|override)\s*(?:permission|permissions|authorization|access|accesscontrol|"
    r"approval|policy|validation|restriction|guard)|(?:escalate|elevate)\s*(?:privilege|privileges|access)|"
    r"privilege\s*escalation|elevated\s*privilege|"
    r"(?:switch|become|act\s*as)\s*(?:admin|administrator|superuser)|"
    r"allow\s*(?:write|tool|capability|permission|access)\s*(?:operation|operations|access|permission|"
    r"actions?)?|"
    r"(?:remove|disable|ignore|skip|override)\s*(?:write|approval|access)\s*(?:restriction|limit|guard|"
    r"approval|requirement)?"
    r")"
)
_UNSAFE_DOMAIN_ACTION_PATTERN = re.compile(
    r"(?ix)(?:开票|入库|出库|调拨|收货|付款|收款|结算|核销|预留库存|占用库存|"
    r"reserve\s*(?:stock|inventory)|allocate\s*(?:stock|inventory)|hold\s*(?:stock|inventory)|"
    r"(?:receive|post|issue|book)\s*(?:goods|stock|inventory|invoice|payment)|"
    r"(?:move|transfer|relocate)\s*(?:stock|inventory)|(?:stock|inventory)\s*(?:reserve|allocate|hold|"
    r"move|transfer|entry))"
)

# The object is intentionally limited to ERP capabilities.  Generic verbs such
# as "执行检查" remain valid read-only guidance.
_UNSAFE_ACTION_PATTERN = re.compile(
    r"(?ix)(?:"
    r"(?:提交|创建|新增|发起|取消|审批|批准|执行|调用|运行|写入|修改|删除|更新|"
    r"生成|建立|发送|推送|同步|保存|确认|核准|处理|操作|申请|授权|录入|核定|审核|"
    r"同意|签署|撤回|退回|锁定|下发|流转|填写|编辑|配置)"
    r"(?:采购订单|采购单|订单|采购|物料需求|物料申请|业务单据|记录|工具|ERP|单据|po|mr|pr)|"
    r"(?:自动)?下单|重试|"
    r"(?:submit|create|approve|cancel|execute|invoke|run|write|update|delete|retry|send|"
    r"confirm|release|issue|dispatch|generate|publish|post|book|commit|apply|process|"
    r"schedule|trigger|authorize|enter|review|sign|withdraw|retract|lock|route|fill|edit|"
    r"configure|accept|reject|return|finalize)"
    r"\s*(?:the\s*)?(?:erp|purchase(?:\s*order)?|order|record|"
    r"material\s*request|tool|po|mr|pr)|"
    r"(?:erp|purchase(?:\s*order)?|order|record|material\s*request|tool|po|mr|pr)"
    r"\s*(?:submit|create|approve|cancel|execute|invoke|run|write|update|delete|retry|send|"
    r"confirm|release|issue|dispatch|generate|publish|post|book|commit|apply|process|"
    r"schedule|trigger|authorize|enter|review|sign|withdraw|retract|lock|route|fill|edit|"
    r"configure|accept|reject|return|finalize|提交|创建|新增|发起|取消|审批|批准|执行|调用|运行|"
    r"写入|修改|删除|更新|生成|建立|发送|推送|同步|保存|确认|核准|处理|操作|申请|授权|录入|"
    r"核定|审核|同意|签署|撤回|退回|锁定|下发|流转|填写|编辑|配置)|"
    r"place\s*(?:a\s*)?(?:purchase\s*)?order"
    r")"
)

_ACTION_WORDS = (
    "提交",
    "创建",
    "新增",
    "发起",
    "取消",
    "审批",
    "批准",
    "执行",
    "生成",
    "建立",
    "发送",
    "推送",
    "同步",
    "保存",
    "确认",
    "核准",
    "处理",
    "操作",
    "申请",
    "授权",
    "录入",
    "核定",
    "审核",
    "同意",
    "签署",
    "撤回",
    "退回",
    "锁定",
    "下发",
    "流转",
    "填写",
    "编辑",
    "配置",
    "调用",
    "运行",
    "写入",
    "修改",
    "删除",
    "更新",
    "重试",
    "下单",
    "submit",
    "create",
    "approve",
    "cancel",
    "execute",
    "invoke",
    "run",
    "write",
    "update",
    "delete",
    "retry",
    "place",
    "send",
    "confirm",
    "release",
    "issue",
    "dispatch",
    "generate",
    "publish",
    "post",
    "book",
    "commit",
    "apply",
    "process",
    "schedule",
    "trigger",
    "authorize",
    "enter",
    "review",
    "sign",
    "withdraw",
    "retract",
    "lock",
    "route",
    "fill",
    "edit",
    "configure",
    "accept",
    "reject",
    "return",
    "finalize",
)
_ERP_OBJECT_WORDS = (
    "采购订单",
    "采购单",
    "采购申请",
    "采购需求",
    "采购单据",
    "物料需求",
    "物料申请",
    "业务单据",
    "订单",
    "记录",
    "工具",
    "erp",
    "purchaseorder",
    "materialrequest",
    "purchaserequest",
    "order",
    "record",
    "tool",
    "供应商",
    "库存",
    "仓库",
    "发票",
    "库存调拨",
    "收货",
    "付款",
    "供应商",
    "supplier",
    "inventory",
    "warehouse",
    "invoice",
    "stockentry",
    "stocktransfer",
    "receipt",
    "payment",
)
_REVERSE_ACTION_OBJECT_WINDOW = 4
_BUSINESS_OBJECT_WORDS = frozenset(
    {
        "供应商",
        "库存",
        "仓库",
        "发票",
        "库存调拨",
        "收货",
        "付款",
        "supplier",
        "inventory",
        "warehouse",
        "invoice",
        "stockentry",
        "stocktransfer",
        "receipt",
        "payment",
    }
)
_BUSINESS_OBJECT_RE = re.compile(
    "|".join(sorted((re.escape(item) for item in _BUSINESS_OBJECT_WORDS), key=len, reverse=True)),
    re.IGNORECASE,
)
_BUSINESS_FACT_CONTEXT_PATTERN = re.compile(
    r"(?ix)(?:\d+(?:\.\d+)?|充足|缺货|在途|需求|缺口|事实|当前|现有|已有|核对|检查|分析|"
    r"read[- ]?only|fact|current|existing|available|sufficient|shortage|demand|no\s*demand|"
    r"on\s*hand)"
)
_ACTION_PAIR_WINDOW = 16
_DOCUMENT_PATTERN = (
    r"采购订单|采购单|采购申请|采购需求|采购单据|物料需求|物料申请|业务单据|订单|记录|工具|"
    r"erp|purchaseorder|materialrequest|purchaserequest|order|record|tool"
)
_DOCUMENT_RE = re.compile(_DOCUMENT_PATTERN, re.IGNORECASE)
_DOCUMENT_ALIAS_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9-])(?:p[\s./]*o|m[\s./]*r|p[\s./]*r)(?![A-Za-z0-9-])"
)
_READONLY_WORD_PATTERN = re.compile(
    r"(?ix)(?:只读|只提供|仅提供|无法|不能|不可|不得|禁止|不应|不该|不建议|无需|不需要|"
    r"不要|拒绝|未授权|未获授权|仅可|只可|只能|不会|未|查看|查询|读取|核对|检查|分析|对账|"
    r"确认事实|验证|比较|现有|已有|当前|历史|相关|引用)"
)
_READONLY_CONTEXT_PATTERN = re.compile(
    rf"(?ix)(?:(?:只读|只提供|仅提供|无法|不能|不可|不得|禁止|不应|不该|不建议|无需|不需要|"
    rf"不要|拒绝|未授权|未获授权|仅可|只可|只能|不会|未|查看|查询|读取|核对|检查|分析|对账|"
    rf"确认事实|验证|比较|现有|已有|当前|历史|相关|引用).{{0,24}}(?:{_DOCUMENT_PATTERN})|"
    rf"(?:{_DOCUMENT_PATTERN}).{{0,24}}(?:未|无需|不需要|不要|不能|不可|不得|禁止|不建议|只读)|"
    rf"(?:在途|库存|需求|缺口|现有|已有|当前|事实|数量).{{0,4}}(?:{_DOCUMENT_PATTERN}))"
)
_TEXT_BOUNDARY_PATTERN = re.compile(r"[\u3002\uff01\uff1f\uff1b;,.!?\n]+")
_ACTION_BOUNDARY_MARKER = "\u241e"
_ACTION_BOUNDARY_CHARS = frozenset({",", ";", "!", "?", "\u3002"})
_CONFUSABLE_TRANSLATION = str.maketrans(
    {
        "\u041e": "O",
        "\u043e": "o",
        "\u0420": "P",
        "\u0440": "p",
        "\u041c": "M",
        "\u043c": "m",
        "\u039f": "O",
        "\u03bf": "o",
        "\u03a1": "P",
        "\u03c1": "p",
        "\u039c": "M",
        "\u03bc": "m",
    }
)

_NEGATION_PREFIXES = (
    "不能",
    "不可",
    "不得",
    "禁止",
    "不应",
    "不该",
    "无需",
    "不需要",
    "不要",
    "不建议",
    "拒绝",
    "未授权",
    "未获授权",
    "仅可",
    "只可",
    "只能",
    "仅提供",
    "只提供",
    "无法",
    "不会",
    "cannot",
    "can't",
    "must not",
    "should not",
    "shouldn't",
    "do not",
    "don't",
    "never",
    "not authorized to",
    "no permission to",
)
_NEGATION_FLIP_MARKERS = ("不是", "并非", "未必", "并不", "不一定", "不曾", "not necessarily")


def _compact(value: str) -> str:
    """Normalize confusable width and remove whitespace/format characters."""
    normalized = unicodedata.normalize("NFKC", value).translate(_CONFUSABLE_TRANSLATION)
    return "".join(
        character
        for character in normalized
        if not character.isspace() and unicodedata.category(character) != "Cf"
    )


def _without_format(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_CONFUSABLE_TRANSLATION)
    return "".join(character for character in normalized if unicodedata.category(character) != "Cf")


def _action_compact(value: str) -> str:
    """Also remove punctuation that can be inserted between action characters."""
    return "".join(
        _ACTION_BOUNDARY_MARKER if character in _ACTION_BOUNDARY_CHARS else character
        for character in value
        if character.isalnum()
        or "\u3400" <= character <= "\u9fff"
        or character in _ACTION_BOUNDARY_CHARS
    )


_COMPACT_NEGATION_PREFIXES = tuple(_action_compact(_compact(item)) for item in _NEGATION_PREFIXES)
_COMPACT_NEGATION_FLIPS = tuple(_action_compact(_compact(item)) for item in _NEGATION_FLIP_MARKERS)


def _find_positions(text: str, words: tuple[str, ...]) -> list[tuple[int, int]]:
    positions: list[tuple[int, int]] = []
    for word in words:
        offset = 0
        while True:
            start = text.find(word, offset)
            if start < 0:
                break
            positions.append((start, start + len(word)))
            offset = start + 1
    return positions


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 32) : start].casefold()
    prefix = prefix.rsplit(_ACTION_BOUNDARY_MARKER, 1)[-1]
    if any(marker in prefix for marker in _COMPACT_NEGATION_FLIPS):
        return False
    if any(prefix.endswith(negation) for negation in _COMPACT_NEGATION_PREFIXES):
        return True
    return any(
        prefix.endswith(negation + marker)
        for negation in _COMPACT_NEGATION_PREFIXES
        for marker in ("对", "将", "去", "自动", "再", "继续", "进行", "执行")
    )


def _is_negated_capability(text: str, start: int) -> bool:
    prefix = text[max(0, start - 32) : start]
    prefix = prefix.rsplit(_ACTION_BOUNDARY_MARKER, 1)[-1]
    return _is_negated(text, start) or (
        any(negation in prefix for negation in _COMPACT_NEGATION_PREFIXES)
        and not any(flip in prefix for flip in _COMPACT_NEGATION_FLIPS)
    )


def _has_unsafe_action_pair(text: str) -> bool:
    text = text.casefold()
    actions = _find_positions(text, _ACTION_WORDS)
    objects = _find_positions(text, _ERP_OBJECT_WORDS)
    for action_start, action_end in actions:
        if action_start == action_end:
            continue
        if text[action_start:action_end] in {"重试", "retry", "下单"}:
            if not _is_negated(text, action_start):
                return True
        for object_start, object_end in objects:
            if min(action_start, object_start) == action_start:
                distance = object_start - action_end
            else:
                distance = action_start - object_end
            if 0 <= distance <= _ACTION_PAIR_WINDOW:
                between_start = min(action_end, object_end)
                between_end = max(action_start, object_start)
                if _ACTION_BOUNDARY_MARKER in text[between_start:between_end]:
                    continue
                object_text = text[object_start:object_end]
                if (
                    object_text in _BUSINESS_OBJECT_WORDS
                    and action_start > object_end
                    and distance > _REVERSE_ACTION_OBJECT_WINDOW
                ):
                    # A generic data fact such as "库存 20.0 ... 新增需求"
                    # must not be treated as an ERP write.  Keep a short
                    # reverse window for direct forms such as "库存更新".
                    continue
                phrase_start = min(action_start, object_start)
                if not _is_negated(text, phrase_start):
                    return True
    return False


def _has_external_scope_leak(value: str) -> bool:
    """Reject an affirmative reference to another company/user/warehouse."""
    for match in _EXTERNAL_SCOPE_PATTERN.finditer(value):
        context = value[max(0, match.start() - 48) : match.end() + 48]
        if _EXTERNAL_SCOPE_FACT_PATTERN.search(context) is None:
            continue
        prefix = value[max(0, match.start() - 48) : match.start()]
        if _EXTERNAL_SCOPE_NEGATION_PATTERN.search(prefix) is None:
            return True
    return False


def _documents_have_readonly_context(value: str) -> bool:
    """Require ERP references to be read-only or part of a deterministic fact."""
    normalized = _without_format(value)
    # Check aliases before sentence splitting: dots and slashes are valid
    # separators in obfuscated forms such as ``P.O.`` and ``P/O``.
    for alias in _DOCUMENT_ALIAS_RE.finditer(normalized):
        context = normalized[max(0, alias.start() - 48) : alias.end() + 48]
        compact_context = _action_compact(_compact(context))
        if _READONLY_WORD_PATTERN.search(compact_context) is None:
            return False
    for segment in _TEXT_BOUNDARY_PATTERN.split(normalized):
        compact_segment = _action_compact(_compact(segment))
        for match in _BUSINESS_OBJECT_RE.finditer(compact_segment):
            context = compact_segment[max(0, match.start() - 12) : match.end() + 12]
            if (
                _READONLY_WORD_PATTERN.search(context) is None
                and _BUSINESS_FACT_CONTEXT_PATTERN.search(context) is None
            ):
                return False
        for match in _DOCUMENT_RE.finditer(compact_segment):
            context = compact_segment[max(0, match.start() - 32) : match.end() + 32]
            if _READONLY_CONTEXT_PATTERN.search(context) is None:
                return False
    return True


def contains_unsafe_text(value: str) -> bool:
    """Return whether text exposes a capability or affirmative ERP action."""
    compact = _compact(value)
    if _UNSAFE_LITERAL_PATTERN.search(compact):
        return True
    if _UNSAFE_SECRET_VALUE_PATTERN.search(value):
        return True
    if _has_external_scope_leak(value):
        return True
    action_compact = _action_compact(compact)
    if any(
        not _is_negated_capability(action_compact, match.start())
        for match in _UNSAFE_CAPABILITY_PATTERN.finditer(action_compact)
    ):
        return True
    if any(
        not _is_negated(action_compact, match.start())
        for match in _UNSAFE_DOMAIN_ACTION_PATTERN.finditer(action_compact)
    ):
        return True
    if not _documents_have_readonly_context(value):
        return True
    if _has_unsafe_action_pair(action_compact):
        return True
    return any(
        not _is_negated(action_compact, match.start())
        for match in _UNSAFE_ACTION_PATTERN.finditer(action_compact)
    )


def check_safe_text(value: str, *, field_name: str) -> str:
    if contains_unsafe_text(value):
        raise ValueError(f"{field_name} contains an unsafe capability or action")
    return value
