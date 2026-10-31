-- GeekIME English mode translator
-- 输入 e + 英文字母 → 英文单词候选
-- 选过的英文单词自动记忆，下次输入时优先展示
-- 性能版：击键路径零磁盘 IO（词典/用户表常驻内存），
--         学习逻辑改由 commit_notifier 驱动（低频，仅上屏时触发）
local M = {}

-- ── 路径解析 ──────────────────────────────────────────
local function script_dir()
  local info = debug.getinfo(1, "S")
  local path = info.source:sub(2)
  return path:match("(.+)[/\\]")
end

local DATA_DIR = script_dir() .. "/.."
local DICT_PATH = DATA_DIR .. "/english.dict.yaml"
local USER_DICT_PATH = DATA_DIR .. "/english_user.txt"

-- ── 安全获取 lfs ─────────────────────────────────────
local lfs_ok, lfs = pcall(require, "lfs")

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

-- ── 用户记忆表（内存缓存；仅在 commit 时写盘）──────────────
local user_cache = nil

local function load_user()
  if user_cache then return user_cache end
  user_cache = {}
  local f = io.open(USER_DICT_PATH, "r")
  if not f then return user_cache end
  for line in f:lines() do
    local word, weight = line:match("^(%S+)%s+(%d+)")
    if word then user_cache[word] = tonumber(weight) or 100 end
  end
  f:close()
  return user_cache
end

local function save_user()
  if not user_cache then return end
  local f = io.open(USER_DICT_PATH, "w")
  if not f then return end
  for word, weight in pairs(user_cache) do
    f:write(word, "\t", tostring(weight), "\n")
  end
  f:close()
end

-- ── 学习逻辑：commit_notifier 回调（仅在上屏时执行一次）──────
local function learn_from_commit(text)
  if not text or text == "" then return end
  -- 仅学习「纯字母词」（即英文模式上屏的内容）
  local word = text:match("^([%a]+)$")
  if not word then return end
  word = word:lower()
  local dict = load_dict()
  local user = load_user()
  -- 只学主词典或用户表中已有的词，避免误学任意字符串
  if dict[word] or user[word] then
    user[word] = (user[word] or 100) + 50
    save_user()
  end
end

-- ── 挂接 commit 通知（每个 translator 实例只挂一次）─────────
local function attach(env)
  if env._geek_english_attached then return end
  env._geek_english_attached = true
  env.engine.context.commit_notifier:connect(function(ctx)
    local ok, text = pcall(function() return ctx:get_commit_text() end)
    if ok then
      pcall(learn_from_commit, text)
    end
  end)
end

-- ── 主入口 ────────────────────────────────────────────
function M.func(input, seg, env)
  if env then attach(env) end

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
