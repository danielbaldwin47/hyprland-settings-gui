local name = "default.lua"
local variant = "layouts"
name = name:gsub(".lua", "")
require("conf." .. variant .. "." .. name)