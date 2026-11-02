# -*- coding: utf-8 -*-
"""
GeekIME Web 管理台
功能：自定义短语(增删改查+批量) / 重新部署 / 双拼方案(自创·复制·详情编辑) / 词库与配置导入导出
技术：Flask + 本地文件读写，无外部依赖
"""
import io
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

# pythonw（无窗口）运行时 stdout/stderr 为 None，print 会崩，重定向到空设备
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from flask import Flask, jsonify, request, send_file

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Rime 用户目录 F:\7.输入法
WEASEL_DIR = r"F:\12.Rime\weasel-0.17.4"  # 小狼毫安装目录

PHRASE_FILE = os.path.join(BASE_DIR, "custom_phrase.txt")
DEFAULT_CUSTOM = os.path.join(BASE_DIR, "default.custom.yaml")
# 解释说明（纯备注，不写进 Rime 词典，避免污染 tabledb 格式）
NOTES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phrase_notes.json")

app = Flask(__name__)


# ---------- 工具 ----------

def _sys_temp():
    p = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local")), "Temp")
    return p if os.path.isdir(p) else os.path.expanduser("~")


GROUP_PREFIX = "#!group\t"
DEFAULT_GROUP = "常用"


def _auto_group(word):
    """按内容启发式归组；用户可随时手动改到自己的分组"""
    w = (word or "").strip()
    if (not w) or len(w) < 2:
        return DEFAULT_GROUP
    if "@" in w or re.search(r"(https?://|www\.|\.(com|cn|net|org)\b)", w, re.I):
        return "邮箱网址"
    digits = len(re.findall(r"\d", w))
    if digits and digits >= len(w) * 0.6:
        return "号码" if digits >= 6 else "数字"
    if len(w) >= 12:
        return "长句"
    return DEFAULT_GROUP


def _read_phrases():
    """返回 (header, groups, items)；items 每项 word/code/weight/group"""
    header, groups, items = [], [], []
    cur = DEFAULT_GROUP
    with open(PHRASE_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith(GROUP_PREFIX):
                cur = line[len(GROUP_PREFIX):].strip() or DEFAULT_GROUP
                if cur not in groups:
                    groups.append(cur)
                continue
            if not line.strip():
                continue
            if line.startswith("#"):
                # 表头/注释固定是「#@/…」「# 说明」或整行没有制表符；
                # 其余带制表符的 # 行是以 # 开头的词组（之前被误当注释丢掉了）
                if line.startswith("#@/") or line.startswith("# ") or "\t" not in line:
                    header.append(line)
                    continue
            p = line.split("\t")
            if len(p) >= 2:
                items.append({"word": p[0], "code": p[1],
                              "weight": p[2] if len(p) > 2 else "1",
                              "group": cur})
    if not groups:
        # 老文件没有分组标记：首次读取时按规则自动归组（不落盘，用户保存时再写）
        for it in items:
            it["group"] = _auto_group(it["word"])
            if it["group"] not in groups:
                groups.append(it["group"])
    for it in items:
        if it["group"] not in groups:
            groups.append(it["group"])
    return header, groups, items


def _write_phrases(header, groups, items):
    groups = [g for g in groups if g]
    for d in items:
        g = d.get("group") or DEFAULT_GROUP
        if g not in groups:
            groups.append(g)
    with open(PHRASE_FILE, "w", encoding="utf-8", newline="\n") as f:
        for h in header:
            f.write(h + "\n")
        for g in groups:
            f.write(GROUP_PREFIX + g + "\n")
            for d in items:
                if (d.get("group") or DEFAULT_GROUP) == g:
                    f.write(f"{d['word']}\t{d['code']}\t{d.get('weight') or '1'}\n")


# ---------- 解释说明（备注） ----------

_notes_lock = threading.Lock()


def _note_key(word, code):
    return f"{word}\u0001{code}"


def _read_notes():
    with _notes_lock:
        try:
            with open(NOTES_FILE, encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}


def _set_note(word, code, note, old_word=None, old_code=None):
    """写入/更新备注；词或编码变更时把旧备注迁到新键"""
    note = (note or "").strip()
    with _notes_lock:
        try:
            with open(NOTES_FILE, encoding="utf-8") as f:
                notes = json.load(f) or {}
        except Exception:
            notes = {}
        if old_word is not None:
            notes.pop(_note_key(old_word, old_code), None)
        notes.pop(_note_key(word, code), None)
        if note:
            notes[_note_key(word, code)] = note
        tmp = NOTES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
        os.replace(tmp, NOTES_FILE)


def _drop_notes(keys):
    """批量删除备注，keys 为 (word, code) 列表"""
    if not keys:
        return
    with _notes_lock:
        try:
            with open(NOTES_FILE, encoding="utf-8") as f:
                notes = json.load(f) or {}
        except Exception:
            return
        changed = False
        for w, c in keys:
            if notes.pop(_note_key(w, c), None) is not None:
                changed = True
        if changed:
            tmp = NOTES_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(notes, f, ensure_ascii=False, indent=2)
            os.replace(tmp, NOTES_FILE)


# ---------- 无头部署引擎 ----------
CREATE_NO_WINDOW = 0x08000000

_deploy_lock = threading.Lock()
_deploy_state = {"running": False, "ok": None, "msg": "尚未部署", "started": 0, "finished": 0}
_deploy_timer = None


def _set_deploy_state(**kw):
    with _deploy_lock:
        _deploy_state.update(kw)


def _deploy_worker():
    ps1 = os.path.join(BASE_DIR, "do_build.ps1")
    server = os.path.join(WEASEL_DIR, "WeaselServer.exe")
    if not os.path.exists(ps1):
        _set_deploy_state(running=False, ok=False, msg="找不到 do_build.ps1",
                          finished=time.time())
        return
    env = os.environ.copy()
    env["TEMP"] = _sys_temp()
    env["TMP"] = env["TEMP"]
    try:
        subprocess.run(["taskkill", "/f", "/im", "WeaselServer.exe"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        time.sleep(0.8)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
            capture_output=True, timeout=900, env=env, cwd=BASE_DIR,
            creationflags=CREATE_NO_WINDOW)
        out = (proc.stdout or b"").decode("utf-8", "ignore")
        m = re.search(r"exit:\s*(-?\d+)", out)
        code = int(m.group(1)) if m else proc.returncode
        ok = (code == 0 and proc.returncode == 0)
        msg = "部署完成，已生效" if ok else (f"部署失败（exit {code}）")
    except subprocess.TimeoutExpired:
        ok, msg = False, "部署超时（超过 15 分钟）"
    except Exception as e:
        ok, msg = False, f"部署出错：{e}"
    finally:
        if os.path.exists(server):
            try:
                subprocess.Popen([server], cwd=WEASEL_DIR, env=env,
                                 creationflags=CREATE_NO_WINDOW)
            except OSError:
                pass
    _set_deploy_state(running=False, ok=ok, msg=msg, finished=time.time())


def _start_deploy(delay=1.0):
    """后台无头部署；delay 内的多次请求合并成一次（防抖）"""
    global _deploy_timer
    with _deploy_lock:
        if _deploy_timer is not None:
            _deploy_timer.cancel()
        _deploy_state.update(running=True, ok=None, msg="正在部署…", started=time.time())
        t = threading.Timer(delay, _deploy_worker)
        t.daemon = True
        _deploy_timer = t
        t.start()


# ---------- 方案文件读写 ----------

SCHEMA_TEMPLATE = """__include: double_pinyin.schema:/

schema:
  schema_id: {id}
  name: {name}
  version: "1.0"

speller:
  algebra/+:
{rules}

translator:
  prism: {id}
"""


def _schema_file(sid):
    return os.path.join(BASE_DIR, f"{sid}.schema.yaml")


def _read_schema_dict(sid):
    import yaml
    with open(_schema_file(sid), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _write_schema(sid, name, rules):
    """用固定模板重写方案文件；rules 为 algebra/+ 规则字符串列表"""
    block = "".join(f"    - {r}\n" for r in rules) if rules else "    # 暂无键位规则，请在下方添加\n"
    content = SCHEMA_TEMPLATE.format(id=sid, name=name, rules=block.rstrip("\n") + "\n")
    with open(_schema_file(sid), "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _list_schema_files():
    import yaml
    out = []
    for f in os.listdir(BASE_DIR):
        if not f.endswith(".schema.yaml"):
            continue
        try:
            with open(os.path.join(BASE_DIR, f), encoding="utf-8") as fh:
                d = yaml.safe_load(fh)
            sid = (d.get("schema") or {}).get("schema_id")
            name = (d.get("schema") or {}).get("name")
            if sid and sid != "double_pinyin":
                out.append({"id": sid, "name": name or sid, "file": f})
        except Exception:
            continue
    return out


def _get_active():
    with open(DEFAULT_CUSTOM, encoding="utf-8") as f:
        content = f.read()
    return list(dict.fromkeys(re.findall(r"-\s*\{schema:\s*([\w_.-]+)\}", content)))


def _set_active(ids):
    # 去重保序，避免 schema_list 里出现重复方案
    ids = list(dict.fromkeys(ids))
    with open(DEFAULT_CUSTOM, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out, i, inserted = [], 0, False
    while i < len(lines):
        if lines[i].strip() == "schema_list:":
            out.append("  schema_list:")
            i += 1
            while i < len(lines) and re.match(r"\s*- \{schema:", lines[i]):
                i += 1
            for sid in ids:
                out.append(f"    - {{schema: {sid}}}")
            inserted = True
            continue
        out.append(lines[i])
        i += 1
    if not inserted:
        out.append("  schema_list:")
        for sid in ids:
            out.append(f"    - {{schema: {sid}}}")
    with open(DEFAULT_CUSTOM, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def _valid_id(sid):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", sid or ""))


# ---------- 页面 ----------

@app.route("/")
def index():
    resp = send_file(os.path.join(os.path.dirname(__file__), "templates", "index.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- 短语 ----------

def _phrase_payload():
    _, groups, items = _read_phrases()
    notes = _read_notes()
    stats = {}
    for it in items:
        it["note"] = notes.get(_note_key(it["word"], it["code"]), "")
        stats[it["group"]] = stats.get(it["group"], 0) + 1
    return {"items": items, "groups": groups, "stats": stats, "total": len(items)}


def _commit(header, groups, items, deploy=True):
    _write_phrases(header, groups, items)
    if deploy:
        _start_deploy(1.0)
        _start_sync(3.0)


def _locate(items, key):
    """按 (词, 编码) 定位，绝不依赖列表下标"""
    if not isinstance(key, dict):
        return -1
    w = (key.get("word") or "").strip()
    c = (key.get("code") or "").strip()
    for i, it in enumerate(items):
        if it["word"] == w and it["code"] == c:
            return i
    return -1


@app.route("/api/phrases", methods=["GET"])
def api_phrases_get():
    return jsonify(_phrase_payload())


@app.route("/api/phrases", methods=["POST"])
def api_phrases_add():
    body = request.get_json(force=True) or {}
    # 制表符/换行会破坏 tabledb 行格式，统一压成空格
    word = re.sub(r"[\t\r\n]+", " ", (body.get("word") or "")).strip()
    code = re.sub(r"[\t\r\n]+", " ", (body.get("code") or "")).strip()
    weight = str(body.get("weight") or "1").strip()
    note = (body.get("note") or "").strip()
    if not word or not code:
        return jsonify({"error": "词组和编码都不能为空"}), 400
    header, groups, items = _read_phrases()
    if _locate(items, {"word": word, "code": code}) >= 0:
        return jsonify({"error": f"「{word}」({code}) 已存在"}), 409
    group = (body.get("group") or "").strip()
    if group == "__new__":  # 前端占位值，不能落盘
        group = ""
    group = group or _auto_group(word)
    if group not in groups:
        groups.append(group)
    item = {"word": word, "code": code, "weight": weight, "group": group, "note": note}
    items.append(item)
    _commit(header, groups, items)
    if note:
        _set_note(word, code, note)
    return jsonify({"ok": True, "item": item})


@app.route("/api/phrases", methods=["PUT"])
def api_phrases_update():
    body = request.get_json(force=True) or {}
    key = body.get("key") or {}
    new = body.get("item") or {}
    header, groups, items = _read_phrases()
    i = _locate(items, key)
    if i < 0:
        return jsonify({"error": "找不到这条短语（可能已被删除）"}), 404
    old = items[i]
    word = re.sub(r"[\t\r\n]+", " ", (new.get("word") or old["word"])).strip()
    code = re.sub(r"[\t\r\n]+", " ", (new.get("code") or old["code"])).strip()
    weight = str(new.get("weight") or old["weight"] or "1").strip()
    group = (new.get("group") or old["group"] or DEFAULT_GROUP).strip()
    if group == "__new__":  # 前端占位值，不能落盘
        group = old["group"] or DEFAULT_GROUP
    j = _locate(items, {"word": word, "code": code})
    if j >= 0 and j != i:
        return jsonify({"error": f"「{word}」({code}) 已存在"}), 409
    note = new.get("note")
    note = _read_notes().get(_note_key(old["word"], old["code"]), "") if note is None else note.strip()
    items[i] = {"word": word, "code": code, "weight": weight, "group": group, "note": note}
    if group not in groups:
        groups.append(group)
    _commit(header, groups, items)
    # 备注跟着词条走（词/编码变了就迁到新键，清空就删掉）
    _set_note(word, code, note, old["word"], old["code"])
    return jsonify({"ok": True, "item": items[i]})


@app.route("/api/phrases", methods=["DELETE"])
def api_phrases_delete():
    body = request.get_json(force=True, silent=True) or {}
    header, groups, items = _read_phrases()
    i = _locate(items, body.get("key") or body)
    if i < 0:
        return jsonify({"error": "找不到这条短语"}), 404
    gone = items.pop(i)
    _commit(header, groups, items)
    _drop_notes([(gone["word"], gone["code"])])
    return jsonify({"ok": True})


@app.route("/api/phrases/batch-delete", methods=["POST"])
def api_phrases_batch_delete():
    body = request.get_json(force=True) or {}
    keys = body.get("keys") or []
    header, groups, items = _read_phrases()
    drop = set()
    for k in keys:
        i = _locate(items, k)
        if i >= 0:
            drop.add(i)
    gone = [(items[n]["word"], items[n]["code"]) for n in drop]
    items = [it for n, it in enumerate(items) if n not in drop]
    _commit(header, groups, items)
    _drop_notes(gone)
    return jsonify({"ok": True, "removed": len(drop)})


# ---------- 分组 ----------

@app.route("/api/phrases/groups", methods=["POST"])
def api_group_create():
    body = request.get_json(force=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "分组名不能为空"}), 400
    if "\t" in name or "\n" in name:
        return jsonify({"error": "分组名不能含制表符或换行"}), 400
    if name.startswith("__"):
        return jsonify({"error": "分组名不能以双下划线开头"}), 400
    header, groups, items = _read_phrases()
    if name in groups:
        return jsonify({"error": f"分组「{name}」已存在"}), 409
    groups.append(name)
    # 空分组只写标记行，Rime 会忽略，无需重建词典（省掉一次十几秒部署）
    _commit(header, groups, items, deploy=False)
    return jsonify({"ok": True, "groups": groups})


@app.route("/api/phrases/groups", methods=["PUT"])
def api_group_rename():
    body = request.get_json(force=True) or {}
    old = (body.get("from") or "").strip()
    new = (body.get("to") or "").strip()
    if not new:
        return jsonify({"error": "新分组名不能为空"}), 400
    header, groups, items = _read_phrases()
    if old not in groups:
        return jsonify({"error": "原分组不存在"}), 404
    if new in groups and new != old:
        return jsonify({"error": f"分组「{new}」已存在"}), 409
    groups[groups.index(old)] = new
    for it in items:
        if it["group"] == old:
            it["group"] = new
    _commit(header, groups, items)
    return jsonify({"ok": True, "groups": groups})


@app.route("/api/phrases/groups", methods=["DELETE"])
def api_group_delete():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    header, groups, items = _read_phrases()
    if name not in groups:
        return jsonify({"error": "分组不存在"}), 404
    if len(groups) <= 1:
        return jsonify({"error": "至少要保留一个分组"}), 400
    groups.remove(name)
    target = groups[0]
    moved = 0
    for it in items:
        if it["group"] == name:
            it["group"] = target
            moved += 1
    # 只有真的移动了短语才需要重建词典
    _commit(header, groups, items, deploy=moved > 0)
    return jsonify({"ok": True, "groups": groups, "moved_to": target})


@app.route("/api/phrases/groups/move", methods=["POST"])
def api_group_move():
    body = request.get_json(force=True) or {}
    keys = body.get("keys") or []
    to = (body.get("to") or "").strip()
    if not to:
        return jsonify({"error": "目标分组不能为空"}), 400
    header, groups, items = _read_phrases()
    if to not in groups:
        groups.append(to)
    moved = 0
    for k in keys:
        i = _locate(items, k)
        if i >= 0:
            items[i]["group"] = to
            moved += 1
    _commit(header, groups, items)
    return jsonify({"ok": True, "moved": moved})


@app.route("/api/phrases/auto-group", methods=["POST"])
def api_auto_group():
    """重新按内容启发式归组，已存在同名分组则并入"""
    header, groups, items = _read_phrases()
    changed = 0
    for it in items:
        g = _auto_group(it["word"])
        if it["group"] != g:
            it["group"] = g
            changed += 1
        if g not in groups:
            groups.append(g)
    # 清掉空分组（保留至少一个）
    used = {it["group"] for it in items}
    groups = [g for g in groups if g in used] or [DEFAULT_GROUP]
    _commit(header, groups, items)
    return jsonify({"ok": True, "changed": changed, "groups": groups})


@app.route("/api/phrases/batch-import", methods=["POST"])
def api_phrases_batch_import():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "未选择文件"}), 400
    raw = f.read().decode("utf-8", errors="ignore")
    group = (request.form.get("group") or "").strip()
    header, groups, items = _read_phrases()
    existing = {(d["word"], d["code"]) for d in items}
    added, skipped = 0, 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split("\t")
        if len(p) < 2:
            continue
        word, code = p[0].strip(), p[1].strip()
        weight = p[2].strip() if len(p) > 2 else "1"
        if not word or not code:
            continue
        if (word, code) in existing:
            skipped += 1
            continue
        existing.add((word, code))
        g = group or _auto_group(word)
        if g not in groups:
            groups.append(g)
        items.append({"word": word, "code": code, "weight": weight, "group": g})
        added += 1
    _commit(header, groups, items)
    return jsonify({"ok": True, "added": added, "skipped": skipped})


@app.route("/api/phrases/export")
def api_phrases_export():
    return send_file(PHRASE_FILE, as_attachment=True, download_name="custom_phrase.txt")


# ---------- 短语库模糊检索（AI 助手用） ----------

def _is_subseq(q, s):
    """q 的每个字符是否按顺序出现在 s 中（支持 cz 命中 曹泽桐）"""
    it = iter(s)
    return all(ch in it for ch in q)


@app.route("/api/phrases/search")
def api_phrases_search():
    q = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit") or 20), 100))
    except ValueError:
        limit = 20
    if not q:
        return jsonify({"items": [], "total": 0})
    _, _, items = _read_phrases()
    notes = _read_notes()
    ql = q.lower()
    scored = []
    for it in items:
        note = notes.get(_note_key(it["word"], it["code"]), "")
        w, c = it["word"].lower(), it["code"].lower()
        if ql == w or ql == c:
            s = 1000
        elif w.startswith(ql) or c.startswith(ql):
            s = 800
        elif ql in w or ql in c:
            s = 600
        elif _is_subseq(ql, w) or _is_subseq(ql, c):
            s = 300
        elif note and ql in note.lower():
            s = 200
        else:
            continue
        scored.append((s - len(w) * 0.5, it, note))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, it, note in scored[:limit]:
        d = dict(it)
        d["note"] = note
        out.append(d)
    return jsonify({"items": out, "total": len(scored)})


# ---------- 部署 ----------

@app.route("/api/deploy", methods=["POST"])
def api_deploy():
    _start_deploy(0.2)
    return jsonify({"ok": True, "message": "部署已在后台开始"})


@app.route("/api/deploy/status")
def api_deploy_status():
    with _deploy_lock:
        st = dict(_deploy_state)
    st["elapsed"] = round(time.time() - st["started"], 1) if st.get("started") else 0
    return jsonify(st)


# ---------- AList(WebDAV) 短语库 Excel 自动同步 ----------

SYNC_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_config.json")
# 凭据放 sync_config.json（不入库），这里只留结构默认值
SYNC_DEFAULT = {
    "enabled": False,
    "url": "",
    "user": "",
    "password": "",
    "dir": "GeekIME",
    "filename": "极客输入法-短语库.xlsx",
}

_sync_lock = threading.Lock()
_sync_state = {"running": False, "ok": None, "msg": "尚未同步", "last": 0, "file": ""}
_sync_timer = None


_conf_lock = threading.Lock()
_conf_cache = {"mtime": None, "data": None}


def _sync_conf():
    """按 mtime 缓存配置，避免每次请求都读盘"""
    try:
        mtime = os.path.getmtime(SYNC_CONFIG)
    except OSError:
        return dict(SYNC_DEFAULT)
    with _conf_lock:
        if _conf_cache["mtime"] == mtime and _conf_cache["data"] is not None:
            return dict(_conf_cache["data"])
        conf = dict(SYNC_DEFAULT)
        try:
            with open(SYNC_CONFIG, encoding="utf-8") as f:
                conf.update(json.load(f) or {})
        except Exception:
            pass
        _conf_cache.update(mtime=mtime, data=conf)
        return dict(conf)


def _xlsx_bytes():
    """把当前短语库生成 Excel；返回 (bytes, 条数)"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    _, groups, items = _read_phrases()
    notes = _read_notes()
    wb = Workbook()
    ws = wb.active
    ws.title = "短语库"
    ws.append(["分组", "词组", "编码", "权重", "解释说明"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="FF7A24")
        cell.alignment = Alignment(vertical="center")
    for g in groups:
        for it in items:
            if (it.get("group") or DEFAULT_GROUP) == g:
                ws.append([g, it["word"], it["code"], it.get("weight") or "1",
                           notes.get(_note_key(it["word"], it["code"]), "")])
    for col, width in zip("ABCDE", (14, 46, 16, 8, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = f"A1:E{ws.max_row}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), len(items)


def _dav(method, path, data=None, timeout=90):
    conf = _sync_conf()
    url = conf["url"].rstrip("/") + "/" + urllib.parse.quote(path.lstrip("/"), safe="/")
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(
        f"{conf['user']}:{conf['password']}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", "Basic " + token)
    if data is not None:
        req.add_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return urllib.request.urlopen(req, timeout=timeout)


def _ensure_remote_dir(name):
    """逐级创建目录；AList 的 /dav/ 是虚拟根，MKCOL 会 405，真实存储挂载点已存在时同样 405"""
    cur = ""
    for seg in [s for s in name.strip("/").split("/") if s]:
        cur = f"{cur}/{seg}" if cur else seg
        try:
            _dav("MKCOL", cur)
        except urllib.error.HTTPError as e:
            if e.code not in (301, 405, 409):  # 已存在
                raise


def _set_sync_state(**kw):
    with _sync_lock:
        _sync_state.update(kw)


def _sync_worker():
    conf = _sync_conf()
    if not conf.get("enabled") or not conf.get("url"):
        _set_sync_state(running=False, ok=None, msg="未配置网盘同步")
        return
    try:
        data, count = _xlsx_bytes()
        _ensure_remote_dir(conf["dir"])
        remote = conf["dir"].rstrip("/") + "/" + conf["filename"]
        with _dav("PUT", remote, data) as resp:
            code = resp.status
        _set_sync_state(running=False, ok=(code in (200, 201, 204)),
                        msg=f"已同步 {count} 条短语 → {conf['filename']}",
                        last=time.time(), file=conf["filename"])
    except urllib.error.HTTPError as e:
        _set_sync_state(running=False, ok=False,
                        msg=f"同步失败：HTTP {e.code} {e.reason}",
                        last=time.time())
    except Exception as e:
        _set_sync_state(running=False, ok=False, msg=f"同步失败：{e}", last=time.time())


def _start_sync(delay=3.0):
    """短语有改动后静默推送 Excel；delay 内多次改动只传一次"""
    global _sync_timer
    conf = _sync_conf()
    if not conf.get("enabled") or not conf.get("url"):
        return
    with _sync_lock:
        if _sync_timer is not None:
            _sync_timer.cancel()
        _sync_state.update(running=True, ok=None, msg="正在同步到网盘…")
        t = threading.Timer(delay, _sync_worker)
        t.daemon = True
        _sync_timer = t
        t.start()


@app.route("/api/sync", methods=["POST"])
def api_sync():
    _start_sync(0.2)
    return jsonify({"ok": True, "message": "同步已在后台开始"})


@app.route("/api/sync/status")
def api_sync_status():
    with _sync_lock:
        st = dict(_sync_state)
    conf = _sync_conf()
    st["enabled"] = bool(conf.get("enabled"))
    st["target"] = f"{conf['dir'].rstrip('/')}/{conf['filename']}" if conf.get("enabled") else ""
    st["ago"] = round(time.time() - st["last"], 1) if st.get("last") else -1
    return jsonify(st)


# ---------- 方案 ----------

@app.route("/api/schemas", methods=["GET"])
def api_schemas():
    schemas = _list_schema_files()
    schemas.append({"id": "luna_pinyin_simp", "name": "朙月拼音·简化字", "file": None})
    return jsonify({"schemas": schemas, "active": _get_active()})


@app.route("/api/schemas/active", methods=["POST"])
def api_schemas_active():
    body = request.get_json(force=True)
    _set_active(body.get("ids") or [])
    _start_deploy()
    return jsonify({"ok": True})


@app.route("/api/schemas/<sid>", methods=["GET"])
def api_schema_detail(sid):
    if sid == "luna_pinyin_simp":
        return jsonify({"id": sid, "name": "朙月拼音·简化字", "builtin": True, "algebra": []})
    if not os.path.exists(_schema_file(sid)):
        return jsonify({"error": "方案不存在"}), 404
    d = _read_schema_dict(sid)
    algebra = (d.get("speller") or {}).get("algebra/+", [])
    return jsonify({
        "id": sid,
        "name": (d.get("schema") or {}).get("name", sid),
        "builtin": False,
        "algebra": algebra,
    })


@app.route("/api/schemas/create", methods=["POST"])
def api_schema_create():
    body = request.get_json(force=True)
    sid = (body.get("id") or "").strip()
    name = (body.get("name") or "").strip() or sid
    copy_from = (body.get("copy_from") or "").strip()
    if not _valid_id(sid):
        return jsonify({"error": "方案 ID 只能含字母数字下划线，且不能以符号开头"}), 400
    if sid == "double_pinyin" or sid == "luna_pinyin_simp":
        return jsonify({"error": "该 ID 为保留名称"}), 400
    if os.path.exists(_schema_file(sid)):
        return jsonify({"error": f"方案 {sid} 已存在"}), 409
    rules = []
    if copy_from and os.path.exists(_schema_file(copy_from)):
        rules = (_read_schema_dict(copy_from).get("speller") or {}).get("algebra/+", [])
    _write_schema(sid, name, rules)
    _start_deploy()
    return jsonify({"ok": True, "id": sid})


@app.route("/api/schemas/<sid>", methods=["PUT"])
def api_schema_update(sid):
    if sid == "luna_pinyin_simp" or not os.path.exists(_schema_file(sid)):
        return jsonify({"error": "该方案不可编辑"}), 400
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip() or sid
    rules = [r.strip() for r in (body.get("algebra") or []) if r.strip()]
    _write_schema(sid, name, rules)
    _start_deploy()
    return jsonify({"ok": True})


@app.route("/api/schemas/<sid>", methods=["DELETE"])
def api_schema_delete(sid):
    if sid == "luna_pinyin_simp":
        return jsonify({"error": "内置方案不可删除"}), 400
    p = _schema_file(sid)
    if not os.path.exists(p):
        return jsonify({"error": "方案不存在"}), 404
    os.remove(p)
    # 从启用列表移除
    _set_active([i for i in _get_active() if i != sid])
    _start_deploy()
    return jsonify({"ok": True})


# ---------- 词库 ----------

@app.route("/api/dicts", methods=["GET"])
def api_dicts():
    out = []
    for f in os.listdir(BASE_DIR):
        if f.endswith(".dict.yaml"):
            p = os.path.join(BASE_DIR, f)
            out.append({"name": f, "size": os.path.getsize(p)})
    return jsonify(out)


@app.route("/api/dicts/<name>/export")
def api_dicts_export(name):
    if not re.fullmatch(r"[\w.-]+\.dict\.yaml", name):
        return jsonify({"error": "非法文件名"}), 400
    p = os.path.join(BASE_DIR, name)
    if not os.path.exists(p):
        return jsonify({"error": "文件不存在"}), 404
    return send_file(p, as_attachment=True, download_name=name)


@app.route("/api/dicts/import", methods=["POST"])
def api_dicts_import():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "未选择文件"}), 400
    name = os.path.basename(f.filename)
    if not name.endswith(".dict.yaml"):
        return jsonify({"error": "仅支持 .dict.yaml 文件"}), 400
    f.save(os.path.join(BASE_DIR, name))
    _start_deploy()
    return jsonify({"ok": True, "name": name})


# ---------- 配置导出 ----------

@app.route("/api/export")
def api_export():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # 顶层配置与词库
        for f in sorted(os.listdir(BASE_DIR)):
            if f.endswith((".yaml", ".dict.yaml", ".txt")) or f in ("rime.lua", "custom_phrase.txt"):
                z.write(os.path.join(BASE_DIR, f), f)
        # lua 目录
        lua_dir = os.path.join(BASE_DIR, "lua")
        if os.path.isdir(lua_dir):
            for root, _, files in os.walk(lua_dir):
                for fn in files:
                    p = os.path.join(root, fn)
                    z.write(p, os.path.relpath(p, BASE_DIR))
        # 用户词典
        for d in os.listdir(BASE_DIR):
            if d.endswith(".userdb") and os.path.isdir(os.path.join(BASE_DIR, d)):
                for root, _, files in os.walk(os.path.join(BASE_DIR, d)):
                    for fn in files:
                        p = os.path.join(root, fn)
                        try:
                            with open(p, "rb") as fh:
                                z.writestr(os.path.relpath(p, BASE_DIR), fh.read())
                        except OSError:
                            pass  # 跳过被输入法占用的 WAL 日志
    buf.seek(0)
    ts = time.strftime("%Y%m%d_%H%M%S")
    return send_file(buf, as_attachment=True, download_name=f"geekime_config_{ts}.zip",
                     mimetype="application/zip")


# ---------- 配置导入 ----------

def _import_allowed(rel):
    """只允许写回导出包里出现的文件类型，并防路径穿越"""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(":" in p or p == ".." for p in parts):
        return False
    base = parts[-1]
    if len(parts) == 1:
        return base.endswith(".yaml") or base.endswith(".txt") or base == "rime.lua"
    if parts[0] == "lua" and len(parts) == 2:
        return True
    if len(parts) >= 2 and parts[0].endswith(".userdb"):
        return True
    return False


@app.route("/api/config/import", methods=["POST"])
def api_config_import():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "未选择文件"}), 400
    try:
        z = zipfile.ZipFile(io.BytesIO(f.read()))
    except zipfile.BadZipFile:
        return jsonify({"error": "不是有效的 zip 文件（请使用本工具导出的配置包）"}), 400
    entries = []
    for n in z.namelist():
        if n.endswith("/"):
            continue
        rel = "/".join(p for p in n.replace("\\", "/").split("/") if p not in ("", "."))
        if _import_allowed(rel):
            entries.append((n, rel))
    if not entries:
        return jsonify({"error": "包里没有找到可导入的 Rime 配置文件"}), 400
    # 覆盖前把将被替换的文件备份到 backups/import_时间戳/
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BASE_DIR, "backups", f"import_{ts}")
    backed_up = 0
    for _, rel in entries:
        target = os.path.join(BASE_DIR, *rel.split("/"))
        if os.path.exists(target):
            bak = os.path.join(backup_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(target, bak)
            backed_up += 1
    restored = 0
    for n, rel in entries:
        target = os.path.join(BASE_DIR, *rel.split("/"))
        os.makedirs(os.path.dirname(target) or BASE_DIR, exist_ok=True)
        try:
            with z.open(n) as src, open(target, "wb") as dst:
                dst.write(src.read())
            restored += 1
        except OSError:
            pass  # 被输入法占用的文件跳过
    _start_deploy()
    return jsonify({"ok": True, "restored": restored, "backed_up": backed_up,
                    "backup_dir": f"backups/import_{ts}" if backed_up else None})


# ─────────── AI 助手 ───────────
AI_CONFIG = os.path.join(BASE_DIR, "ai_config.json")
AI_HISTORY = os.path.join(BASE_DIR, "ai_history.log")
AI_DEFAULT_PROMPT = "你是快捷问答助手，简洁、直接地回答问题。"


def _load_ai_cfg():
    if os.path.exists(AI_CONFIG):
        try:
            with open(AI_CONFIG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


@app.route("/api/ai", methods=["GET"])
def ai_get():
    data = {"base_url": "", "api_key": "", "model": "",
            "system_prompt": AI_DEFAULT_PROMPT, "enabled": False}
    data.update(_load_ai_cfg())
    if not data.get("system_prompt"):
        data["system_prompt"] = AI_DEFAULT_PROMPT
    return jsonify(data)


@app.route("/api/ai", methods=["POST"])
def ai_post():
    data = request.get_json(force=True) or {}
    cur = _load_ai_cfg()
    for k in ("base_url", "api_key", "model", "system_prompt", "enabled"):
        if k in data:
            cur[k] = data[k]
    with open(AI_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    return jsonify({"ok": True})


@app.route("/api/ai/test", methods=["POST"])
def ai_test():
    data = request.get_json(force=True) or {}
    base = (data.get("base_url") or "").strip().rstrip("/")
    key = (data.get("api_key") or "").strip()
    model = (data.get("model") or "").strip()
    if base.endswith("/chat/completions"):
        url = base
    elif base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    payload = json.dumps({"model": model, "messages": [
        {"role": "user", "content": "请只回复两个字：正常"}]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read().decode("utf-8"))
        reply = out["choices"][0]["message"]["content"]
        return jsonify({"ok": True, "reply": (reply or "")[:200]})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        return jsonify({"ok": False, "error": "HTTP %s: %s" % (e.code, detail)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/ai/history")
def ai_history():
    if not os.path.exists(AI_HISTORY):
        return jsonify({"items": []})
    try:
        with open(AI_HISTORY, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        blocks = [b.strip() for b in text.split("────") if b.strip()]
        return jsonify({"items": blocks[-30:]})
    except Exception as e:
        return jsonify({"items": ["读取失败: %s" % e]})


# ---------- 全局设置（geek_settings.txt，输入法 Lua 侧 5 秒内自动读取） ----------

SETTINGS_FILE = os.path.join(BASE_DIR, "geek_settings.txt")
SETTING_DEFS = [  # (键, 默认值, 最小, 最大)
    ("english_min_len", 6, 2, 30),     # 英文自动记忆：最短字母数
    ("phrase_display_len", 8, 0, 50),  # 候选短语缩略：显示前 N 个字符（0=不缩略）
]


def _read_settings():
    s = {k: d for k, d, _, _ in SETTING_DEFS}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^([\w]+)\s*=\s*(-?\d+)\s*$", line.strip())
                if m and m.group(1) in s:
                    s[m.group(1)] = int(m.group(2))
    except OSError:
        pass
    return s


@app.route("/api/settings")
def api_settings_get():
    return jsonify(_read_settings())


@app.route("/api/settings", methods=["POST"])
def api_settings_set():
    data = request.get_json(force=True, silent=True) or {}
    s = _read_settings()
    for k, _d, lo, hi in SETTING_DEFS:
        if k in data:
            try:
                s[k] = max(lo, min(hi, int(data[k])))
            except (TypeError, ValueError):
                return jsonify({"error": f"{k} 不是有效数字"}), 400
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for k, _d, _lo, _hi in SETTING_DEFS:
            f.write(f"{k}={s[k]}\n")
    os.replace(tmp, SETTINGS_FILE)
    return jsonify({"ok": True, **s})


# ---------- 英文记忆词库（english_user.txt，与输入法 Lua 侧共用） ----------

ENGLISH_USER_FILE = os.path.join(BASE_DIR, "english_user.txt")


def _read_english_user():
    merged = {}
    try:
        with open(ENGLISH_USER_FILE, encoding="utf-8") as f:
            for line in f:
                p = line.split()
                if not p:
                    continue
                w = p[0].lower()
                try:
                    wt = int(p[1])
                except (IndexError, ValueError):
                    wt = 100
                if w not in merged or wt > merged[w]:
                    merged[w] = wt
    except OSError:
        pass
    return [{"word": w, "weight": v} for w, v in merged.items()]


def _write_english_user(items):
    items = sorted(items, key=lambda d: (-d["weight"], d["word"]))
    tmp = ENGLISH_USER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(f"{it['word']}\t{it['weight']}\n")
    os.replace(tmp, ENGLISH_USER_FILE)


@app.route("/api/english")
def api_english_get():
    items = _read_english_user()
    q = (request.args.get("q") or "").strip().lower()
    if q:
        items = [d for d in items if q in d["word"]]
    return jsonify({"items": items, "total": len(items)})


@app.route("/api/english", methods=["POST"])
def api_english_add():
    data = request.get_json(force=True, silent=True) or {}
    word = (data.get("word") or "").strip().lower()
    if not re.match(r"^[a-z]{2,40}$", word):
        return jsonify({"error": "只能是 2-40 个英文字母"}), 400
    try:
        weight = max(1, int(data.get("weight") or 100))
    except (TypeError, ValueError):
        weight = 100
    merged = {d["word"]: d["weight"] for d in _read_english_user()}
    merged[word] = max(merged.get(word, 0), weight)
    _write_english_user([{"word": w, "weight": v} for w, v in merged.items()])
    return jsonify({"ok": True})


@app.route("/api/english", methods=["DELETE"])
def api_english_delete():
    data = request.get_json(force=True, silent=True) or {}
    words = data.get("words") or ([data.get("word")] if data.get("word") else [])
    words = [w.strip().lower() for w in words if isinstance(w, str) and w.strip()]
    if not words:
        return jsonify({"error": "没有要删除的词"}), 400
    dropped = set(words)
    items = [d for d in _read_english_user() if d["word"] not in dropped]
    _write_english_user(items)
    return jsonify({"ok": True, "removed": len(words)})


@app.route("/api/ping")
def api_ping():
    return jsonify({"app": "GeekIME", "pid": os.getpid()})


# ---------- 启动 ----------
WEB_PORT = int(os.environ.get("GEEK_WEB_PORT") or 8765)  # 默认 8765
PORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_port.txt")


def _pick_port(start, count=20):
    """从 start 开始找第一个能绑定的端口；被占用就顺延（8765 → 8766 → …）"""
    import socket
    for p in range(start, start + count):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("端口 %d-%d 全被占用" % (start, start + count - 1))


if __name__ == "__main__":
    port = _pick_port(WEB_PORT)
    try:
        with open(PORT_FILE, "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass
    print("GeekIME Web 管理台启动: http://127.0.0.1:%d" % port)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
