-- GeekIME English mode translator
-- 输入 e + 英文字母 → 英文单词候选（主词典 + 用户记忆，前缀补全）
-- 直接打一串字母按回车上屏 → 自动记忆（长度达到阈值才记，阈值在 Web 管理台调）
-- 记忆过的词在普通双拼输入时也会直接出现在候选里（搜狗式英文回显）
-- 性能版：击键路径零磁盘 IO（词典/用户表/设置常驻内存，mtime 节流探测），
--         学习逻辑由 commit_notifier 驱动（仅上屏时触发）
local M = {}

local settings = require("lua/geek_settings")

-- ── 路径解析 ──────────────────────────────────────────
local function script_dir()
  local info = debug.getinfo(1, "S")
  local path = info.source:sub(2)
  return path:match("(.+)[/\\]")
end

local DATA_DIR = script_dir() .. "/.."
local DICT_PATH = DATA_DIR .. "/english.dict.yaml"
local USER_DICT_PATH = DATA_DIR .. "/english_user.txt"

-- ── 安全获取 lfs ─────────────────────────────────────────
local lfs_ok, lfs = pcall(require, "lfs")

local MAX_LEARN_LEN = 40  -- 超长纯字母串基本是误触/粘贴，不记

-- ── 主词典（内存缓存，mtime 节流探测：最多每 5 秒查一次修改时间）──
local dict_cache = nil
local dict_mtime = 0
local last_probe = 0

local function load_dict()
  if dict_cache then
    local now = os.time()
    if now - last_probe < 5 then
      return dict_cache
    end
    last_probe = now
    if lfs_ok then
      local attr = lfs.attributes(DICT_PATH)
      local mt = attr and attr.modification or 0
      if mt == dict_mtime then
        return dict_cache
      end
      dict_mtime = mt
    else
      return dict_cache
    end
  end

  local d = {}
  local f = io.open(DICT_PATH, "r")
  if not f then
    dict_cache = d
    return d
  end
  local past_header = false
  for line in f:lines() do
    if past_header then
      if line:sub(1, 1) ~= "#" then
        local word, code, weight = line:match("^(%S+)%s+(%S+)%s+(%d+)")
        if word and code then
          d[word] = tonumber(weight) or 100
        end
      end
    elseif line:match("^%.%.%.%s*$") then
      past_header = true
    end
  end
  f:close()
  dict_cache = d
  return d
end

-- ── 用户记忆表（内存缓存 + 2 秒 mtime 节流；管理台增删后约 2 秒内同步）──
local user_cache = nil
local user_mtime = 0
local user_probe = 0

local function user_file_mtime()
  if lfs_ok then
    local attr = lfs.attributes(USER_DICT_PATH)
    return attr and attr.modification or 0
  end
  return user_mtime
end

local function load_user()
  local now = os.time()
  if user_cache and now - user_probe < 2 then return user_cache end
  user_probe = now
  local mt = user_file_mtime()
  if user_cache and mt == user_mtime then return user_cache end
  user_mtime = mt
  local t = {}
  local f = io.open(USER_DICT_PATH, "r")
  if not f then
    user_cache = t
    return t
  end
  for line in f:lines() do
    local word, weight = line:match("^(%S+)%s+(%d+)")
    if word then t[word] = tonumber(weight) or 100 end
  end
  f:close()
  user_cache = t
  return t
end

local function save_user()
  if not user_cache then return end
  -- 原子写：先写临时文件再改名，避免写一半崩溃损坏词库
  local tmp = USER_DICT_PATH .. ".tmp"
  local f = io.open(tmp, "w")
  if not f then return end
  for word, weight in pairs(user_cache) do
    f:write(word, "\t", tostring(weight), "\n")
  end
  f:close()
  os.remove(USER_DICT_PATH)
  os.rename(tmp, USER_DICT_PATH)
  user_probe = os.time()
  user_mtime = user_file_mtime()
end

-- ── 学习逻辑：commit_notifier 回调（仅在上屏时执行一次）──────
local function learn_from_commit(text, ctx)
  if not text or text == "" then return end
  -- 仅学习「纯字母」上屏内容（英文模式选词、或直接打字母回车）
  local word = text:match("^([%a]+)$")
  if not word then return end
  word = word:lower()
  if #word > MAX_LEARN_LEN then return end

  -- e 模式下回车上屏会带 e 前缀：若去掉 e 后是已知词，按已知词处理
  local input = nil
  if ctx then pcall(function() input = ctx.input end) end
  if input and input == text and #input >= 2
     and input:sub(1, 1) == "e" and input:sub(2):match("^[a-z]+$") then
    local rest = input:sub(2)
    if load_dict()[rest] or load_user()[rest] then
      word = rest
    end
  end

  local dict = load_dict()
  local user = load_user()
  if dict[word] or user[word] then
    -- 已知词：重复使用加权
    user[word] = (user[word] or 100) + 50
    save_user()
  else
    -- 新词：直接打字母回车上屏的，达到阈值长度才记（阈值在 Web 管理台调）
    local min_len = tonumber(settings.get("english_min_len")) or 6
    if min_len < 2 then min_len = 2 end
    if #word >= min_len then
      user[word] = 100
      save_user()
    end
  end
end

-- ── 挂接 commit 通知（每个 translator 实例只挂一次）─────────
local function attach(env)
  if env._geek_english_attached then return end
  env._geek_english_attached = true
  env.engine.context.commit_notifier:connect(function(ctx)
    local ok, text = pcall(function() return ctx:get_commit_text() end)
    if ok then
      pcall(learn_from_commit, text, ctx)
    end
  end)
end

-- ── 主入口 ────────────────────────────────────────────
function M.func(input, seg, env)
  if env then attach(env) end

  -- 记忆过的英文词：普通输入直接打同一串字母即可再次上屏（不用进 e 模式）
  local lw = input:lower()
  if #lw >= 2 and lw:match("^[a-z]+$") then
    local user = load_user()
    if user[lw] then
      yield(Candidate("english_user", seg.start, seg._end, lw, "英文"))
    end
  end

  -- 仅处理 e + 字母 的输入
  if input:sub(1, 1) ~= "e" then return end
  local rest = input:sub(2)
  if rest == "" or not rest:match("^[a-z]+$") then return end

  local dict = load_dict()
  local user = load_user()

  -- 合并权重：用户记忆的词获得额外加权
  local function get_weight(w)
    return (dict[w] or 0) + (user[w] or 0) + (1000 - #w)
  end

  -- 精确匹配
  if dict[rest] or user[rest] then
    yield(Candidate("english", seg.start, seg._end, rest, "English"))
  end

  -- 前缀补全（主词典 + 用户记忆词，去重）
  local seen = { [rest] = true }
  local matches = {}
  for w in pairs(dict) do
    if not seen[w] and w:sub(1, #rest) == rest then
      seen[w] = true
      matches[#matches + 1] = w
    end
  end
  for w in pairs(user) do
    if not seen[w] and w:sub(1, #rest) == rest then
      seen[w] = true
      matches[#matches + 1] = w
    end
  end
  table.sort(matches, function(a, b) return get_weight(a) > get_weight(b) end)

  local count = 0
  for _, w in ipairs(matches) do
    yield(Candidate("english", seg.start, seg._end, w, "English"))
    count = count + 1
    if count >= 30 then break end
  end
end

return M
