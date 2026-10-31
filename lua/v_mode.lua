-- GeekIME v 模式 translator
-- 功能：rq 日期 / sj 时间 / xq 星期 / v+数字 中文读法与人民币大写 / v+日期 / v+符号
local M = {}

local CN = { "零", "一", "二", "三", "四", "五", "六", "七", "八", "九" }
local CU = { "零", "壹", "贰", "叁", "肆", "伍", "陆", "柒", "捌", "玖" }
local CU_UNIT_POS = { "仟", "佰", "拾", "" }
local CN_UNIT_POS = { "千", "百", "十", "" }

-- 4 位段转中文（pos 顺序与 digits 的千/百/十/个一一对应）
local function sec2cn(sec, digit, upper)
  local pos = upper and CU_UNIT_POS or CN_UNIT_POS
  local digs = {
    math.floor(sec / 1000) % 10,
    math.floor(sec / 100) % 10,
    math.floor(sec / 10) % 10,
    sec % 10,
  }
  local s = {}
  local pending_zero = false
  for i = 1, 4 do
    local d = digs[i]
    if d > 0 then
      if pending_zero then
        s[#s + 1] = digit[1]
        pending_zero = false
      end
      if d == 1 and i == 3 and #s == 0 and not upper then
        s[#s + 1] = pos[i]            -- 小写 10~19 省略“一”（十/十一/十五…）；大写保留“壹”
      else
        s[#s + 1] = digit[d + 1]
        s[#s + 1] = pos[i]
      end
    else
      if #s > 0 then
        pending_zero = true
      end
    end
  end
  return table.concat(s)
end

-- 非负整数转中文
local function int2cn(n, digit, upper)
  if n == 0 then
    return digit[1]
  end
  local units = { "", "万", "亿", "万亿" }
  local blocks = {}  -- 低段在前：{ text, unit, pad_zero }
  local ui = 0
  while n > 0 do
    local sec = n % 10000
    n = math.floor(n / 10000)
    if sec > 0 then
      blocks[#blocks + 1] = {
        text = sec2cn(sec, digit, upper),
        unit = ui > 0 and units[((ui - 1) % 4) + 2] or "",
        pad_zero = (sec < 1000),  -- 段内千位空缺且存在更高段时，拼接时补零
      }
    end
    ui = ui + 1
  end
  local out = {}
  for i = #blocks, 1, -1 do
    local b = blocks[i]
    if b.pad_zero and i < #blocks then
      out[#out + 1] = digit[1]
    end
    out[#out + 1] = b.text
    out[#out + 1] = b.unit
  end
  return table.concat(out)
end

local function num2cn(n, upper)
  local digit = upper and CU or CN
  local int_part = math.floor(n)
  local frac = math.floor((n - int_part) * 100 + 0.5)  -- 两位小数四舍五入
  local s = int2cn(int_part, digit, upper)
  if upper then
    s = s .. "元"
    if frac == 0 then
      s = s .. "整"
    else
      local jiao = math.floor(frac / 10)
      local fen = frac % 10
      if jiao > 0 then
        s = s .. digit[jiao + 1] .. "角"
      elseif fen > 0 then
        s = s .. "零"
      end
      if fen > 0 then
        s = s .. digit[fen + 1] .. "分"
      end
    end
  elseif frac > 0 then
    local dec = string.format("%02d", frac)
    s = s .. "点"
    for ch in dec:gmatch(".") do
      s = s .. CN[tonumber(ch) + 1]
    end
  end
  return s
end

-- 轻量符号表（v 前缀触发，可自行扩充）
local SYM = {
  jt = "→", xj = "←", js = "↑", xz = "↓",
  du = "°", c = "℃", xh = "×", ch = "÷", pm = "±",
  a = "α", b = "β", g = "γ", d = "δ",
  wq = "√", dd = "·", fh = "Φ",
}

local WEEK = { "日", "一", "二", "三", "四", "五", "六" }

function M.func(input, seg)
  -- matcher 传入的是带 v 前缀的完整串（如 vrq / v123 / vjt），先剥离前缀
  if input:sub(1, 1) ~= "v" then
    return
  end
  local r = input:sub(2)
  if r == "rq" then
    local today = os.date("*t")
    for _, f in ipairs({ "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日" }) do
      yield(Candidate("v", seg.start, seg._end, os.date(f), "日期"))
    end
    today = nil
  elseif r == "sj" then
    yield(Candidate("v", seg.start, seg._end, os.date("%H:%M"), "时间"))
    yield(Candidate("v", seg.start, seg._end, os.date("%H:%M:%S"), "时间"))
  elseif r == "xq" then
    yield(Candidate("v", seg.start, seg._end, "星期" .. WEEK[os.date("%w") + 1], "星期"))
  else
    local num, denom
    num, denom = r:match("^(%d+)%.(%d%d)$")
    local y, m, d = r:match("^(%d%d%d%d)(%d%d)(%d%d)$")
    if r:match("^%d+$") or num then
      local value
      if num then
        value = tonumber(num) + tonumber(denom) / 100
      else
        value = tonumber(r)
      end
      yield(Candidate("v", seg.start, seg._end, num2cn(value, false), "数字"))
      yield(Candidate("v", seg.start, seg._end, num2cn(value, true), "金额"))
    elseif y then
      m = tonumber(m)
      d = tonumber(d)
      if m >= 1 and m <= 12 and d >= 1 and d <= 31 then
        yield(Candidate("v", seg.start, seg._end, string.format("%s-%02d-%02d", y, m, d), "日期"))
        yield(Candidate("v", seg.start, seg._end, string.format("%s年%d月%d日", y, m, d), "日期"))
      end
    elseif SYM[r] then
      yield(Candidate("v", seg.start, seg._end, SYM[r], "符号"))
    end
  end
end

return M