-- 测试用 translator 样例（不挂载）：输入 zz 返回提示
local function geek_translator(input, seg)

    if input == "zz" then

        yield(Candidate(
            "geek",
            seg.start,
            seg._end,
            "Lua插件成功加载",
            "测试"
        ))

    end

end


return geek_translator