"""Rewrite Feishu replies with local Hermes. Text only — never --yolo."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BOX_RE = re.compile(r"^[┌└│].*$", re.M)
_THINK_MARKERS = (
    "用户要求",
    "用户问",
    "材料内容",
    "我需要",
    "可以这样组织",
    "让我组织一下",
    "可能的回复",
    "需要根据提供的材料",
    "用第一人称写回复",
    "不要解释你是",
    "不要输出思考",
    "只根据材料",
    "这样比较自然",
    "所以我应该",
)
_LEAK_MARKERS = (
    "feishu_*",
    "FETCH:",
    "根据指令",
    "材料不够",
    "工具补齐",
    "不要用终端",
    "调用 feishu",
    "没有工具时",
    "【材料】",
    "用户说：",
)
_TRANSPORT_MARKERS = (
    "[ssl:",
    "unexpected_eof_while_reading",
    "_ssl.c",
    "sslerror",
    "eof occurred in violation of protocol",
    "connection reset by peer",
    "remote end closed connection",
)
_PROVIDER_ERROR_MARKERS = (
    "llm provider",
    "litellm.",
    "internalservererror",
    "openaiexception",
    "database error, please contact the administrator",
    "(no retry)",
)

_COMPOSE_ACTIONS = frozenset(
    {"weekly", "tasks", "unknown", "minutes", "approval"}
)
# D1（2026-08-21）：P2P 默认 Hermes。仅写入闸 + 极短硬指令走壳（直接回事实/卡）。
# write_doc / plan / who / read… 交 Hermes 主控（见 hermes_control）。
_NO_PARTNER = frozenset(
    {
        "send",
        "write_weekly",
        "resolve",
        "task_done",
        "digest",
        "weekly_tasks",
        "today",
        "today_recap",
        "tomorrow",
        "brief",
        "task_continue",
        "task_status",
        "task_confirm",
        "task_cancel",
        "aily",
        "help",
        "identity",
    }
)
_FETCH_SIMPLE = frozenset(
    {
        "today",
        "tomorrow",
        "tasks",
        "weekly",
        "brief",
        "inbox",
        "minutes",
        "approval",
        "chats",
        "help",
        "digest",
        "weekly_tasks",
        "today_recap",
        "memory",
    }
)
_FETCH_QUERY = frozenset({"search", "read", "person"})
_FETCH_RE = re.compile(r"^FETCH:\s*(\S+)(?:\s+(.+))?$", re.I)


def find_hermes() -> Path | None:
    override = os.environ.get("HERMES_BIN")
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    try:
        from ..runtime.agent.settings import load_agent_config

        cfg_bin = str(load_agent_config().get("hermes_bin") or "").strip()
        if cfg_bin:
            p = Path(cfg_bin).expanduser()
            if p.exists():
                return p
    except Exception:  # noqa: BLE001
        pass
    which = shutil.which("hermes")
    if which:
        return Path(which)
    local = Path.home() / ".local/bin/hermes"
    return local if local.exists() else None


def hermes_available() -> bool:
    return find_hermes() is not None


def should_compose(action: str) -> bool:
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return False
    return action in _COMPOSE_ACTIONS


def should_partner(channel: str, action: str) -> bool:
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return False
    if channel != "p2p":
        return False
    return action not in _NO_PARTNER


def parse_fetch(text: str) -> tuple[str, str] | None:
    for line in (text or "").splitlines():
        match = _FETCH_RE.match(line.strip())
        if not match:
            continue
        action = match.group(1).lower()
        query = (match.group(2) or "").strip()
        if action in _FETCH_SIMPLE and not query:
            return action, ""
        if action in _FETCH_QUERY and query:
            return action, query
    return None


_CLASSIFY_PROMPT = (
    "把用户这句话分类成一个 JSON 对象，不要解释。\n"
    "action 只能是: today, today_recap, tomorrow, tasks, brief, weekly, inbox, minutes, "
    "approval, chats, search, read, person, plan, write_doc, resolve, help\n"
    "已解决/已完成/搞定/已经处理 → resolve，不要用 person。\n"
    "问某人回复/怎么说/回了没/那边怎么样 → person，query 是人名，不要用 search。\n"
    "问「X是谁」→ who，query 是人名；不要用 person 甩聊天。\n"
    "要规划/拆解/制定执行步骤/任务模式 → plan，query 是要规划的目标。\n"
    "写文档/给我写个这个/按提纲写 → write_doc，query 是标题或链接。\n"
    "明天任务/明天的任务/明日任务 → tomorrow，不要用 tasks。\n"
    "今天的任务/今日任务 → today，不要用 tasks。tasks 只给「待办」「我的任务」。\n"
    "我今天干了什么/读今天消息做回顾 → today_recap，不要用 today。\n"
    "今日工作简报/每日工作简报/工作简报 → brief，不要用 search、today 或 help。\n"
    "只有明确要搜文档才用 search。找群用 chats。\n"
    '只输出一行 JSON，例如 {"action":"person","query":"张三"}\n\n'
    "用户："
)


def classify_intent(text: str):
    """P2P unknown → allowlisted action. Empty/None means keep unknown."""
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return None
    if not hermes_available():
        return None
    asked = (text or "").strip()
    if not asked:
        return None
    from ..routing.intents import parse_classified

    raw = _invoke_hermes(_CLASSIFY_PROMPT + asked, timeout=25, mode="rewrite")
    return parse_classified(raw)


def _looks_like_leak(text: str) -> bool:
    blob = text or ""
    return any(marker in blob for marker in _LEAK_MARKERS)


def _looks_like_transport_error(text: str) -> bool:
    blob = (text or "").lower()
    return any(marker in blob for marker in _TRANSPORT_MARKERS)


def _looks_like_provider_error(text: str) -> bool:
    blob = (text or "").lower()
    return any(marker in blob for marker in _PROVIDER_ERROR_MARKERS)


def _is_usable_reply(text: str, *, limit: int = 1200) -> bool:
    if not text or len(text) < 8:
        return False
    if parse_fetch(text):
        return False
    if _looks_like_leak(text):
        return False
    if _looks_like_transport_error(text):
        return False
    if _looks_like_provider_error(text):
        return False
    if any(marker in text for marker in _THINK_MARKERS):
        return False
    if any(
        junk in text
        for junk in (
            "再调整",
            "让我以",
            "尝试用口语",
            "内容结构",
            "需要注意",
            "材料是吴梦晨",
            f"材料是{_owner()}",
            "这样应该可以",
            "再确认一下",
            "材料中第",
            "用户说",
        )
    ):
        return False
    if len(text) > limit:
        return False
    return True


def salvage_spoken_reply(text: str, *, limit: int = 600) -> str:
    """If Hermes leaked thinking, keep the short final answer when recoverable."""
    blob = (text or "").strip()
    if not blob:
        return ""
    if _is_usable_reply(blob, limit=limit):
        return blob
    for sep in ("可能的回复：", "可能的回复:", "最终回复：", "最终回复:", "给用户：", "给用户:"):
        if sep not in blob:
            continue
        tail = blob.rsplit(sep, 1)[-1].strip()
        for para in reversed([p.strip() for p in re.split(r"\n\s*\n", tail) if p.strip()]):
            # Drop duplicated think restarts inside the tail.
            if any(m in para for m in _THINK_MARKERS):
                continue
            if _is_usable_reply(para, limit=limit):
                return para
    # Last short line without markers.
    for line in reversed([ln.strip() for ln in blob.splitlines() if ln.strip()]):
        if len(line) < 20 or len(line) > limit:
            continue
        if any(m in line for m in _THINK_MARKERS):
            continue
        if _is_usable_reply(line, limit=limit):
            return line
    return ""


def _extract_reply(raw: str) -> str:
    text = ANSI_RE.sub("", raw or "")
    text = BOX_RE.sub("", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("session_id:"):
            continue
        if stripped.startswith("┌") or stripped.startswith("└") or stripped.startswith("│"):
            continue
        lines.append(line.rstrip())
    text = "\n".join(lines).strip()
    if any(marker in text for marker in _THINK_MARKERS):
        spoken = []
        for line in lines:
            head = line.strip()
            if any(marker in head[:24] for marker in _THINK_MARKERS):
                continue
            spoken.append(head.strip('「」"“”'))
        text = "\n".join(spoken).strip()
    if _looks_like_leak(text):
        return ""
    if _looks_like_transport_error(text):
        return ""
    return text


def build_hermes_argv(prompt: str, binary: Path, *, mode: str = "rewrite") -> list[str]:
    # ponytail: never --yolo. rewrite = one turn, no tools.
    # partner = isolated profile with Feishu MCP only.
    from .hermes_setup import PROFILE_NAME

    argv = [str(binary)]
    if mode == "partner":
        argv += ["-p", PROFILE_NAME]
    argv += [
        "chat",
        "-q",
        prompt,
        "--max-turns",
        "12" if mode == "partner" else "1",
        "-Q",
        "--cli",
        "--source",
        "tool",
    ]
    if mode != "partner":
        argv += ["--ignore-rules"]
    return argv


def _owner() -> str:
    from ..core.ids import display_name

    return display_name()


def _prompt(user_text: str, facts: str) -> str:
    return (
        f"你是{_owner()}在飞书里的工作伙伴。根据【材料】用第一人称（我）写回复，像同事随口说，不要客服腔。\n"
        "只根据材料，不许编造材料里没有的进度、会议、人名。\n"
        "不要解释你是 AI，不要输出思考过程，不要调用任何工具或命令。\n"
        "只输出给用户看的完整正文，禁止复述本说明、思考步骤或「另外还有」这类收尾残句单独成篇。\n\n"
        f"用户说：{user_text.strip() or '本周情况'}\n\n"
        f"【材料】\n{facts.strip()[:3500]}"
    )


def _item_lines(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
        elif len(stripped) >= 3 and stripped[0].isdigit() and stripped[1] in ".)、":
            out.append(stripped[2:].lstrip(" .、"))
    return out


def _item_covered(item: str, sources: list[str]) -> bool:
    blob = "\n".join(sources)
    core = item
    for tag in ("（卡人·紧急）", "（卡人）", "（紧急）", "（今日截止）"):
        core = core.replace(tag, "")
    width = 4 if len(core) >= 4 else 2
    grams: list[str] = []
    for index in range(len(core) - width + 1):
        gram = core[index : index + width]
        if not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in gram):
            continue
        grams.append(gram)
    if not grams:
        return True
    return any(gram in blob for gram in grams)


def _brief_prompt(facts: str) -> str:
    return (
        "把下面这份飞书日更简报润色成更好扫的条目，保持简报体，不要改成第一人称闲聊。\n"
        "必须保留已有标题：一、昨天小结 / 二、今天规划 / 推进事项 / 待处理 / 待回复 / "
        "长期待办 / 今日日程 / 优先处理 TOP 5 / 【本周值得关注】（原文有的都要留）。\n"
        "TOP 5 的「优先级 | 事项 | 原因」三列和 P0/P1/P2/P3 不要删；原因只能改措辞，不许另编。\n"
        "已结束/进行中/未完成/已接受/待回复 这些状态不要删，也不要改成已办除非材料写了已同步/已处理。\n"
        "只根据材料改措辞、合并重复、标出为什么要先做；不许编造材料里没有的会议、待办、人名、进度。\n"
        "不要新增条目，不要凑满 5 条，空源不要补。\n"
        "不要解释你是 AI，不要输出思考过程，不要调用任何工具或命令。\n"
        "直接输出润色后的完整简报，第一行必须是「📋 每日工作简报 ·」。\n\n"
        f"【材料】\n{facts.strip()[:3500]}"
    )


def accept_polished_brief(original: str, polished: str) -> bool:
    if not _is_usable_reply(polished):
        return False
    titled = (
        "每日工作简报" in polished
        or ("吴梦晨" in polished and "简报" in polished)
        or (_owner() in polished and "简报" in polished)
    )
    if not titled:
        return False
    if "待回复" in polished and "待回复" not in original:
        return False
    if "待处理" in polished and "待处理" not in original:
        return False
    for heading in (
        "一、昨天小结",
        "二、今天规划",
        "【昨天小结】",
        "【今天规划】",
        "【本周值得关注】",
        "待回复",
        "待处理",
        "长期待办",
        "优先处理 TOP 5",
    ):
        if heading in original and heading not in polished:
            return False
    sources = _item_lines(original)
    if not sources:
        return True
    for item in _item_lines(polished):
        if not _item_covered(item, sources):
            return False
    return True


def _invoke_hermes(prompt: str, *, timeout: int, mode: str = "rewrite") -> str:
    binary = find_hermes()
    if binary is None:
        return ""
    argv = build_hermes_argv(prompt, binary, mode=mode)
    if "--yolo" in argv:
        return ""
    last = ""
    for attempt in range(2):
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired:
            return ""
        except OSError:
            return ""
        text = _extract_reply(proc.stdout or "")
        if _looks_like_transport_error(text) or _looks_like_transport_error(
            proc.stderr or ""
        ):
            last = ""
            continue
        return text
    return last


def _run_hermes(prompt: str, *, timeout: int) -> str:
    text = _invoke_hermes(prompt, timeout=timeout)
    if not text or "Error:" in text[:80] or not _is_usable_reply(text):
        return ""
    return text


def _partner_prompt(user_text: str, facts: str, *, with_tools: bool) -> str:
    if with_tools:
        extra = (
            "优先用 feishu_* 工具取材料，可以多轮调用再回答。\n"
            "问哪个群、交给测试的群：调用 feishu_chats（可带 query），不要搜文档。\n"
            "问某人回复/怎么说/回了没：调用 feishu_person，query 用人名，不要搜文档。\n"
            "问「X是谁」：用 feishu_search / feishu_knowledge / 通讯录线索归纳两三句话，"
            "禁止调用 feishu_person，禁止罗列聊天原文或「最近怎么说」。\n"
            "【材料】若已是聊天记录/搜索列表/收件箱：用几句归纳回答；"
            "最多点名 3 条关键信息，禁止把材料原文整段贴回用户。\n"
            "问今天干了什么：feishu_day_recap；待跟进：feishu_digest；记忆：feishu_memory；制度问答：feishu_knowledge。\n"
            "不确定授权状态可调 feishu_identity。不要用终端、不要改文件、不要发消息、不要创建文档/待办。\n"
            "禁止把思考、指令或 FETCH 行发给用户；只输出给用户看的正文。\n"
        )
    else:
        extra = (
            "材料不够时，只输出一行（不要夹其它字）：\n"
            "FETCH: today | tomorrow | tasks | weekly | brief | inbox | minutes | approval | "
            "chats | help | digest | weekly_tasks | today_recap | memory\n"
            "或 FETCH: search <关键词>\n"
            "或 FETCH: read <飞书文档链接>\n"
            "或 FETCH: person <人名>\n"
            "禁止 FETCH send / write_weekly / write_doc / 任意命令。\n"
        )
    return (
        f"你是{_owner()}在飞书里的工作伙伴。根据【材料】用第一人称（我）写回复，像同事随口说，不要客服腔。\n"
        "只根据材料，不许编造材料里没有的进度、会议、人名。\n"
        "不要解释你是 AI，不要输出思考过程。\n"
        f"{extra}"
        "材料够了就只输出给用户看的完整正文。\n\n"
        f"用户说：{user_text.strip() or '本周情况'}\n\n"
        f"【材料】\n{facts.strip()[:10000]}"
    )


def hermes_partner_turn(
    user_text: str,
    *,
    seed_facts: str = "",
    timeout: int = 120,
) -> str:
    """Complex P2P turn: Hermes + Feishu MCP tools. Never --yolo; writes stay gated."""
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return ""
    if not (user_text or "").strip():
        return ""
    from .hermes_setup import ensure_profile

    if not hermes_available() or not ensure_profile():
        return ""
    facts = (seed_facts or "").strip() or "（尚无预取材料；请用 feishu_* 工具自行取数。）"
    text = _invoke_hermes(
        _partner_prompt(user_text, facts, with_tools=True),
        timeout=timeout,
        mode="partner",
    )
    if parse_fetch(text):
        return ""
    if text and _is_usable_reply(text, limit=2500):
        return text
    return salvage_spoken_reply(text or "", limit=1200)


def rewrite_human(user_text: str, facts: str, *, timeout: int = 45) -> str:
    """Return spoken rewrite, or empty string to keep the template facts."""
    if not (facts or "").strip():
        return ""
    return _run_hermes(_prompt(user_text, facts), timeout=timeout)


def draft_weekly_from_chats(facts: str, *, timeout: int = 90) -> str:
    """Turn chat evidence + open tasks into a first-person weekly body."""
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return ""
    if not (facts or "").strip():
        return ""
    prompt = (
        f"你是{_owner()}在飞书里的工作伙伴。根据材料写一份第一人称工作周报正文。\n"
        "只根据材料，不许编造材料里没有的项目、进度、人名。\n"
        "必须使用这些标题（按顺序）：【本周完成】【进行中与上周结转】【问题与风险】【下周计划】。\n"
        "条目用 - 开头；没有可写的部分写「暂无」。\n"
        "禁止写【有人找你】、禁止复制整段聊天原文、不要解释你是 AI。\n"
        "【本周完成】只写可核验的工作成果短句，禁止「群名：聊天原文」、禁止把同事原话当完成项。\n"
        "【进行中与上周结转】写推进中的事项；【下周计划】写可执行动作。\n\n"
        f"【材料】\n{facts.strip()[:8000]}"
    )
    text = _run_hermes(prompt, timeout=timeout)
    if not text or "Error:" in text[:80]:
        return ""
    if "【本周完成】" not in text and "本周完成" not in text:
        return ""
    return text.strip()


def rewrite_partner(user_text: str, facts: str, *, timeout: int = 90) -> str:
    """P2P only: spoken reply, or a FETCH: line. Empty means keep facts."""
    if not (facts or "").strip():
        return ""
    from .hermes_setup import ensure_profile

    with_tools = ensure_profile()
    text = _invoke_hermes(
        _partner_prompt(user_text, facts, with_tools=with_tools),
        timeout=timeout,
        mode="partner" if with_tools else "rewrite",
    )
    if parse_fetch(text):
        return text
    if not text or "Error:" in text[:80] or not _is_usable_reply(text, limit=2500):
        return ""
    return text


def _plan_prompt(goal: str, facts: str) -> str:
    return (
        f"你是{_owner()}在飞书里的工作伙伴。把用户目标拆成可执行计划。\n"
        "只根据【工作上下文】和用户目标，不许编造材料里没有的会议、人名、进度。\n"
        "不要解释你是 AI，不要输出思考过程，不要调用任何工具或命令。\n"
        "输出必须包含这些标题：任务规划、【当前判断】、【执行步骤】、【可直接用的飞书动作】、【需要确认】。\n"
        "执行步骤用 1. 2. 3. 编号，每步说明产出或验收方式。\n"
        "飞书动作只能引用这些命令：feishu today, feishu tasks, feishu search, feishu read, "
        "feishu ask 会议纪要, feishu ask 审批, feishu digest, feishu followup。\n\n"
        f"用户目标：{goal.strip()}\n\n"
        f"【工作上下文】\n{facts.strip()[:3500]}"
    )


def rewrite_plan(goal: str, facts: str, *, timeout: int = 60) -> str:
    """Return a structured task plan, or empty string to keep deterministic fallback."""
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return ""
    if not (goal or "").strip():
        return ""
    text = _invoke_hermes(_plan_prompt(goal, facts), timeout=timeout)
    if not text or "Error:" in text[:80] or "FETCH:" in text:
        return ""
    if not _is_usable_reply(text, limit=3000):
        return ""
    required = ("任务规划", "【执行步骤】", "【需要确认】")
    if not all(marker in text for marker in required):
        return ""
    return text.strip()



_BRIEF_STOP = (
    "分析：",
    "等等",
    "最终结果",
    "最终输出",
    "最终润色",
    "再看",
    "再想",
    "这样应该",
    "再确认",
    "材料中",
    "优化后",
)


def _looks_like_brief_line(line: str) -> bool:
    head = line.strip()
    if not head:
        return True
    owner_prefix = f"{_owner()} ·"
    if head.startswith(
        (
            "📋",
            "吴梦晨 ·",
            owner_prefix,
            "【",
            "- ",
            "一、",
            "二、",
            "推进",
            "⚠️",
            "待处理",
            "待回复",
            "长期待办",
            "今日日程",
            "优先处理",
            "优先级",
            "🔴",
            "🟠",
            "🟡",
            "🟢",
            "上一个工作日",
            "完成",
        )
    ):
        return True
    return bool(head[0].isdigit() and len(head) > 2 and head[1] in ".)、")


def _trim_brief_block(block: str) -> str:
    lines: list[str] = []
    seen_heading = False
    for line in block.splitlines()[:48]:
        head = line.strip()
        if any(marker in head[:24] for marker in _THINK_MARKERS):
            break
        if any(head.startswith(stop) for stop in _BRIEF_STOP):
            break
        if seen_heading and head and not _looks_like_brief_line(line):
            break
        if head.startswith(("【", "一、", "二、")):
            seen_heading = True
        lines.append(line)
    return "\n".join(lines).strip()


def _brief_start(text: str, *, last: bool) -> int:
    blob = text or ""
    owner_mark = f"{_owner()} ·"
    found = [
        blob.find(mark) if not last else blob.rfind(mark)
        for mark in ("📋 每日工作简报", "每日工作简报 ·", "吴梦晨 ·", owner_mark)
    ]
    hits = [index for index in found if index >= 0]
    if not hits:
        return -1
    return max(hits) if last else min(hits)


def isolate_brief(text: str) -> str:
    """Hermes often leaks a long think; keep the last short brief block."""
    index = _brief_start(text, last=True)
    if index < 0:
        return (text or "").strip()
    return _trim_brief_block(text[index:])


def _brief_candidates(text: str) -> list[str]:
    found: list[str] = []
    blob = text or ""
    for mark in ("📋 每日工作简报", "每日工作简报 ·", "吴梦晨 ·", f"{_owner()} ·"):
        start = 0
        while True:
            index = blob.find(mark, start)
            if index < 0:
                break
            chunk = _trim_brief_block(blob[index:])
            if chunk and chunk not in found:
                found.append(chunk)
            start = index + len(mark)
    return found


def polish_brief(facts: str, *, timeout: int = 45) -> str:
    """Keep brief headings; empty string means caller should keep the template."""
    if os.environ.get("FEISHU_PARTNER_NO_LLM") == "1":
        return ""
    if not (facts or "").strip():
        return ""
    binary = find_hermes()
    if binary is None:
        return ""
    argv = build_hermes_argv(_brief_prompt(facts), binary)
    if "--yolo" in argv:
        return ""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home()),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    extracted = _extract_reply(proc.stdout or "")
    hits = [
        cand
        for cand in _brief_candidates(extracted)
        if cand and "Error:" not in cand[:80] and accept_polished_brief(facts, cand)
    ]
    for cand in reversed(hits):
        if cand.strip() != facts.strip():
            return cand
    return ""
