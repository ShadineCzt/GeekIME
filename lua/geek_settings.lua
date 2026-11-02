-- GeekIME 共享设置读取
-- 设置文件：用户目录/geek_settings.txt（key=value，Web 管理台写入）
-- Lua 侧带 mtime 节流缓存：击键路径零磁盘 IO，最多每 5 秒探测一次文件修改
local M = {}

local function script_dir()
  local info = debug.getinfo(1, "S")
  local path = info.source:sub(2)
  return path:match("(.+)[/\\]")
end

M.PATH = script_dir() .. "/../geek_settings.txt"

M.DEFAULTS = {
  english_min_len = 6,     -- 英文自动记忆：最短字母数
  phrase_display_len = 8,  -- 候选短语缩略：显示前 N 个字符（0=不缩略）
}

local lfs_ok, lfs = pcall(require, "lfs")
local cache, mtime, probe = nil, nil, 0

-- 返回全部设置（含默认值兜底）
function M.all()
  local now = os.time()
  if cache and now - probe < 5 then return cache end
  probe = now
  local mt = 0
  if lfs_ok then
    local attr = lfs.attributes(M.PATH)
    mt = attr and attr.modification or 0
  elseif cache then
    return cache  -- 无 lfs 无法探测，只能用首次加载的缓存
  end
  if cache and mt == mtime then return cache end
  mtime = mt
  local t = {}
  for k, v in pairs(M.DEFAULTS) do t[k] = v end
  local f = io.open(M.PATH, "r")
  if f then
    for line in f:lines() do
      local k, n = line:match("^([%w_]+)%s*=%s*(-?%d+)%s*$")
      if k and M.DEFAULTS[k] ~= nil then t[k] = tonumber(n) end
    end
    f:close()
  end
  cache = t
  return t
end

-- 读取单个设置项
function M.get(key)
  return M.all()[key]
end

return M
