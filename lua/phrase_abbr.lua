-- GeekIME 长短语候选缩略 filter
-- 自定义短语（table/user_table 类型）内容很长时，候选栏只显示前 N 个字符 + …
-- 实际上屏仍是完整内容（librime-lua 的 ShadowCandidate 影子候选：
-- 显示用缩略文本、提交原候选）。
-- 显示长度在 Web 管理台「英文·设置」里调（phrase_display_len，0 = 不缩略）；
-- 本机 librime-lua 不支持 ShadowCandidate 时自动跳过缩略，保证上屏永远完整。
local M = {}

local settings = require("lua/geek_settings")

-- 运行时探测 ShadowCandidate（旧版 librime-lua 没有）
local Shadow = ShadowCandidate

-- ── UTF-8 字符数与头部截取 ──────────────────────────────
local function utf8_iter(s)
  return s:gmatch("[\1-\127\192-\255][\128-\191]*")
end

local function utf8_len(s)
  local n = 0
  for _ in utf8_iter(s) do n = n + 1 end
  return n
end

local function utf8_head(s, n)
  local out = {}
  local i = 0
  for c in utf8_iter(s) do
    i = i + 1
    if i > n then break end
    out[#out + 1] = c
  end
  return table.concat(out)
end

-- ── filter 主入口 ──────────────────────────────────────
function M.func(translation, env)
  local display_len = tonumber(settings.get("phrase_display_len")) or 8
  for cand in translation:iter() do
    local t = cand.type
    if Shadow and display_len > 0 and (t == "table" or t == "user_table") then
      local text = cand.text or ""
      if utf8_len(text) > display_len then
        yield(Shadow(cand, t, utf8_head(text, display_len) .. "…", cand.comment or "", false))
      else
        yield(cand)
      end
    else
      yield(cand)
    end
  end
end

return M
