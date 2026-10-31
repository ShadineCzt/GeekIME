-- GeekIME 标点直达处理器
-- 背景：; 被登记在 speller.alphabet 里（搜狗双拼的 ing 韵母键），
--       所以按键总是先被拼写器吞进编码，轮不到标点表直接上屏，
--       导致按 ; 只出现一个孤零零的候选。
-- 策略：没有正在输入的编码时，; 直接上屏全角分号；
--       正在编码时放行，仍作为 ing 韵母键使用。
--       西文/临时英文模式下放行，交给 ASCII 标点表处理。
local M = {}

local kAccepted = 1
local kNoop = 2

function M.func(key_event, env)
  local ok, ret = pcall(function()
    -- 只处理分号按下事件
    if key_event:repr() ~= ";" then
      return kNoop
    end

    local ctx = env.engine.context
    -- 西文模式（含 Shift 临时英文）不干预
    if ctx:get_option("ascii") or ctx:get_option("ascii_mode") then
      return kNoop
    end
    -- 正在编码：放行给拼写器，; 仍作 ing 韵母
    if ctx:is_composing() then
      return kNoop
    end

    env.engine:commit_text("；")
    return kAccepted
  end)

  if ok then return ret end
  return kNoop
end

return M
