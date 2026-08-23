-- PROTOTYPE (wayfinder #30) — throwaway. Evaluation-based importer runner.
-- Usage: lua5.5 runner.lua <entry.lua> <basedir> <out.json> <policy>
--   policy: "block"    — os.execute/io.popen/io write intercepted, benign stubs
--           "record"   — passthrough to the real thing, but recorded
-- Evaluates a foreign hyprland.lua under a recording hl.* stub and dumps the
-- captured call stream + script-extract info as JSON.

local entry, basedir, outpath, policy = arg[1], arg[2], arg[3], arg[4] or "block"

----------------------------------------------------------------------
-- record state
----------------------------------------------------------------------
local record = {
  calls = {},      -- ordered stream: {call=, args=, src=, line=, submap=?}
  scripts = {},    -- functions captured: {id=, source=, from=, to=, upvalues={}, context=}
  queries = {},    -- hl query fns invoked at config time
  shell = {},      -- os.execute / io.popen commands seen
  iowrites = {},   -- io.open write-mode attempts
  requires = {},   -- files loaded, in order
  prints = {},
  errors = {},
}

local script_id = 0
local current_submap = nil

local function relsrc(s)
  if type(s) ~= "string" then return tostring(s) end
  s = s:gsub("^@", "")
  if s:sub(1, #basedir) == basedir then s = s:sub(#basedir + 2) end
  return s
end

-- deep copy args; functions become script refs
local capture_fn
local fn_seen = {} -- function -> script id (dedup + cycle guard)
local snap     -- forward (mutual recursion with capture_fn via upvalue capture)

capture_fn = function(f, context)
  if fn_seen[f] then return { __fn = fn_seen[f] } end
  script_id = script_id + 1
  local id = script_id
  fn_seen[f] = id
  local info = debug.getinfo(f, "S")
  local entry = {
    id = id,
    source = relsrc(info.source),
    from = info.linedefined,
    to = info.lastlinedefined,
    upvalues = {},
    context = context,
  }
  record.scripts[#record.scripts + 1] = entry
  local i = 1
  while true do
    local name, val = debug.getupvalue(f, i)
    if not name then break end
    if name ~= "_ENV" then
      local t = type(val)
      local uv = { name = name, type = t }
      if t == "string" or t == "number" or t == "boolean" then
        uv.value = val
      elseif t == "table" then
        uv.value = snap(val, "upvalue:" .. name)
      elseif t == "function" then
        uv.value = capture_fn(val, "upvalue:" .. name)
      end
      entry.upvalues[#entry.upvalues + 1] = uv
    end
    i = i + 1
  end
  return { __fn = id }
end

snap = function(v, context, depth, seen)
  depth = depth or 0
  if depth > 12 then return "<deep>" end
  local t = type(v)
  if t == "function" then return capture_fn(v, context) end
  if t == "table" then
    local node = rawget(v, "__dsp_node")
    if node then return { __dsp = node, args = {} } end
    local dsp = rawget(v, "__dsp")
    if type(dsp) == "string" then return { __dsp = dsp, args = rawget(v, "args") } end
    seen = seen or {}
    if seen[v] then return "<cycle>" end
    seen[v] = true
    local out = {}
    for k, val in pairs(v) do
      out[k] = snap(val, context, depth + 1, seen)
    end
    seen[v] = nil
    return out
  end
  if t == "userdata" or t == "thread" then return "<" .. t .. ">" end
  return v
end

local function callsite()
  -- walk out of runner.lua frames to the first user-code frame
  local here = debug.getinfo(1, "S").source
  for level = 2, 12 do
    local info = debug.getinfo(level, "Sl")
    if not info then break end
    if info.source ~= here and info.what ~= "C" then
      return relsrc(info.source), info.currentline
    end
  end
  return nil, nil
end

local function rec(name, args, argc)
  local src, line = callsite()
  local e = { call = name, args = snap(args, name), src = src, line = line, argc = argc }
  if current_submap then e.submap = current_submap end
  record.calls[#record.calls + 1] = e
  return e
end

----------------------------------------------------------------------
-- hl stub
----------------------------------------------------------------------
local hl = {}

-- declarative, table-or-varargs entry points: record verbatim
local DECL = {
  "config", "monitor", "bind", "unbind", "gesture", "animation", "curve",
  "device", "workspace_rule", "window_rule", "layer_rule", "env", "permission",
}
for _, name in ipairs(DECL) do
  hl[name] = function(...)
    local n = select("#", ...)
    local args
    if n == 1 then args = (...) else args = { ... } end
    rec(name, args, n)
  end
end

hl.define_submap = function(name, fn)
  rec("define_submap", { name = name })
  local prev = current_submap
  current_submap = name
  local ok, err = pcall(fn)
  current_submap = prev
  if not ok then
    record.errors[#record.errors + 1] = "define_submap(" .. tostring(name) .. "): " .. tostring(err)
  end
end

hl.on = function(event, fn)
  local src, line = callsite()
  local e = { call = "on", args = { event = event, handler = capture_fn(fn, "on:" .. tostring(event)) },
              src = src, line = line }
  record.calls[#record.calls + 1] = e
end

hl.timer = function(spec)
  rec("timer", spec)
end

-- dsp: lazy action markers; namespaces nest (hl.dsp.window.drag)
local function dsp_node(path)
  local node = { __dsp_node = path }
  return setmetatable(node, {
    __index = function(_, k)
      if k == "__dsp_node" then return path end
      return dsp_node(path .. "." .. tostring(k))
    end,
    __call = function(_, ...)
      return { __dsp = path, args = snap({ ... }, "dsp:" .. path) }
    end,
  })
end
hl.dsp = setmetatable({}, {
  __index = function(_, name) return dsp_node(tostring(name)) end,
})

hl.dispatch = function(...)
  rec("dispatch_immediate", { ... })
end
hl.exec_cmd = function(cmd)
  rec("exec_cmd", { cmd })
end

hl.layout = {
  register = function(name, provider)
    local src, line = callsite()
    local e = { call = "layout_register",
      args = { name = name, provider = snap(provider, "layout:" .. tostring(name)) },
      src = src, line = line }
    record.calls[#record.calls + 1] = e
  end,
}

-- plugin namespaces are nil unless a matching hl.plugin.load ran this reload
-- (mirrors the real engine: an unloaded plugin's namespace doesn't exist, and
-- configs guard on that — `if hl.plugin.hyprbars ~= nil then … end`)
local plugins_loaded = {}
hl.plugin = setmetatable({
  load = function(path)
    rec("plugin_load", { path })
    plugins_loaded[#plugins_loaded + 1] = tostring(path)
  end,
}, {
  __index = function(_, ns)
    local hit = false
    for _, p in ipairs(plugins_loaded) do
      if p:lower():find(tostring(ns):lower(), 1, true) then hit = true break end
    end
    if not hit then
      record.queries[#record.queries + 1] = { fn = "plugin." .. tostring(ns), src = select(1, callsite()) }
      return nil
    end
    return setmetatable({}, {
      __newindex = function(_, k, v) rec("plugin_set", { ns = ns, key = k, value = snap(v, "plugin") }) end,
      __index = function() return nil end,
      __call = function(_, ...) rec("plugin_call", { ns = ns, args = snap({ ... }, "plugin") }) end,
    })
  end,
})

hl.notification = setmetatable({}, {
  __index = function(_, name)
    return function(...) rec("notification_" .. name, snap({ ... }, "notification")) end
  end,
})

-- queries: canned answers, recorded
local QUERIES = {
  version = "0.56.2",
  get_monitors = {},
  get_windows = {},
  get_workspaces = {},
  get_layers = {},
  get_loaded_plugins = {},
  get_active_monitor = nil,
  get_active_window = nil,
  get_active_workspace = nil,
  get_active_special_workspace = nil,
  get_current_submap = "",
  get_cursor_pos = { x = 0, y = 0 },
  is_key_down = false,
  get_last_window = nil,
  get_last_workspace = nil,
  get_urgent_window = nil,
  get_monitor = nil,
  get_monitor_at = nil,
  get_monitor_at_cursor = nil,
  get_window = nil,
  get_workspace = nil,
  get_workspace_windows = {},
  get_config = nil,
  get_option = nil,
  getoption = nil,
  clear_crashed_lockscreen = true,
  exec_scheduled_prop_refresh_immediately = true,
}
for name, ret in pairs(QUERIES) do
  hl[name] = function(...)
    local src, line = callsite()
    record.queries[#record.queries + 1] = { fn = name, src = src, line = line }
    return ret
  end
end

----------------------------------------------------------------------
-- sandboxed stdlib
----------------------------------------------------------------------
local real_io, real_os = io, os

local function fake_pipe(text)
  return {
    read = function(_, ...) if text ~= nil then local t = text; text = nil; return t end return nil end,
    lines = function() return function() return nil end end,
    close = function() return true, "exit", 0 end,
  }
end

local sandbox_os = {
  getenv = real_os.getenv,
  time = real_os.time,
  date = real_os.date,
  clock = real_os.clock,
  difftime = real_os.difftime,
  tmpname = real_os.tmpname,
  getpid = function() return 4242 end,   -- Hyprland extension used by HyDE
  geteuid = function() return 1000 end,  -- ditto
  exit = function(code)
    record.errors[#record.errors + 1] = "os.exit(" .. tostring(code) .. ") trapped"
    error("__importer_os_exit__", 0)
  end,
  execute = function(cmd)
    record.shell[#record.shell + 1] = { kind = "execute", cmd = cmd, policy = policy }
    if policy == "record" then return real_os.execute(cmd) end
    return true, "exit", 0
  end,
  remove = function(p)
    record.shell[#record.shell + 1] = { kind = "os.remove", cmd = tostring(p), policy = policy }
    if policy == "record" then return real_os.remove(p) end
    return true
  end,
  rename = function(a, b)
    record.shell[#record.shell + 1] = { kind = "os.rename", cmd = tostring(a) .. " -> " .. tostring(b), policy = policy }
    if policy == "record" then return real_os.rename(a, b) end
    return true
  end,
}

local sandbox_io = {
  open = function(path, mode)
    mode = mode or "r"
    if mode:find("[wa+]") then
      record.iowrites[#record.iowrites + 1] = { path = tostring(path), mode = mode, policy = policy }
      if policy == "record" then return real_io.open(path, mode) end
      -- pretend success, swallow writes
      return { write = function(self) return self end, close = function() return true end,
               read = function() return nil end, lines = function() return function() return nil end end }
    end
    return real_io.open(path, mode) -- reads allowed under both policies
  end,
  lines = function(...) return real_io.lines(...) end,
  read = function() return nil end,
  write = function(...) record.prints[#record.prints + 1] = table.concat({ ... }, "") return sandbox_io end,
  popen = function(cmd, mode)
    record.shell[#record.shell + 1] = { kind = "popen", cmd = cmd, policy = policy }
    if policy == "record" then return real_io.popen(cmd, mode) end
    return fake_pipe("")
  end,
  stderr = { write = function(_, ...) record.prints[#record.prints + 1] = table.concat({ ... }, "") return sandbox_io.stderr end },
  stdout = { write = function(_, ...) record.prints[#record.prints + 1] = table.concat({ ... }, "") return sandbox_io.stdout end },
}

----------------------------------------------------------------------
-- Hyprland-style require against the sandbox env
----------------------------------------------------------------------
local ENV -- forward
local loaded = {}
local pkg = { path = "./?.lua;./?/init.lua", cpath = "", loaded = loaded, preload = {} }
pkg.searchpath = function(name, path, sep, rep)
  sep = sep or "."
  rep = rep or "/"
  local slashed = tostring(name):gsub("%" .. sep, rep)
  local tried = {}
  for pat in tostring(path):gmatch("[^;]+") do
    local cand = pat:gsub("%?", slashed)
    local f = real_io.open(cand, "r")
    if f then f:close() return cand end
    tried[#tried + 1] = cand
  end
  return nil, "\n\tno file '" .. table.concat(tried, "'\n\tno file '") .. "'"
end

local function try_paths(name)
  local cands = {}
  local slashed = name:gsub("%.", "/")
  -- config-dir relative, both spellings, with and without .lua
  cands[#cands + 1] = basedir .. "/" .. name .. ".lua"
  cands[#cands + 1] = basedir .. "/" .. name
  cands[#cands + 1] = basedir .. "/" .. slashed .. ".lua"
  cands[#cands + 1] = basedir .. "/" .. slashed .. "/init.lua"
  -- package.path patterns (config may have appended to it)
  for pat in tostring(pkg.path):gmatch("[^;]+") do
    cands[#cands + 1] = pat:gsub("%?", slashed)
  end
  for _, c in ipairs(cands) do
    local f = real_io.open(c, "r")
    if f then f:close() return c end
  end
  return nil, cands
end

local function sandbox_require(name)
  if loaded[name] ~= nil then return loaded[name] end
  -- wildcard require: require("./stuff/*")
  if name:find("%*") then
    local dir = name:gsub("%*", ""):gsub("^%./", "")
    local p = real_io.popen("ls '" .. basedir .. "/" .. dir .. "' 2>/dev/null")
    local results = {}
    if p then
      for f in p:lines() do
        if f:sub(-4) == ".lua" then
          results[#results + 1] = sandbox_require(dir .. f:sub(1, -5))
        end
      end
      p:close()
    end
    return results
  end
  local path, cands = try_paths(name)
  if not path then
    record.errors[#record.errors + 1] = "require('" .. name .. "') not found"
    error("module '" .. name .. "' not found", 2)
  end
  record.requires[#record.requires + 1] = relsrc(path)
  local chunk, err = loadfile(path, "t", ENV)
  if not chunk then
    record.errors[#record.errors + 1] = "loadfile " .. path .. ": " .. tostring(err)
    error(err, 2)
  end
  local ok, ret = pcall(chunk)
  if not ok then
    record.errors[#record.errors + 1] = "require('" .. name .. "'): " .. tostring(ret)
    error(ret, 2)
  end
  if ret == nil then ret = true end
  loaded[name] = ret
  return ret
end

----------------------------------------------------------------------
-- environment
----------------------------------------------------------------------
ENV = {
  hl = hl,
  require = sandbox_require,
  __require = sandbox_require,
  package = pkg,
  os = sandbox_os,
  io = sandbox_io,
  print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
    record.prints[#record.prints + 1] = table.concat(parts, "\t")
  end,
  string = string, table = table, math = math, utf8 = utf8, coroutine = coroutine,
  ipairs = ipairs, pairs = pairs, next = next, type = type, tostring = tostring,
  tonumber = tonumber, select = select, error = error, assert = assert,
  pcall = pcall, xpcall = xpcall, setmetatable = setmetatable, getmetatable = getmetatable,
  rawget = rawget, rawset = rawset, rawequal = rawequal, rawlen = rawlen,
  load = function(chunk, name, mode, env) return load(chunk, name, "t", env or ENV) end,
  loadstring = function(s, n) return load(s, n, "t", ENV) end,
  dofile = function(p) local c = loadfile(p, "t", ENV) return c and c() end,
  collectgarbage = function() return 0 end,
  arg = {},
}
ENV._G = ENV

----------------------------------------------------------------------
-- run
----------------------------------------------------------------------
local chunk, lerr = loadfile(entry, "t", ENV)
if not chunk then
  record.errors[#record.errors + 1] = "entry loadfile: " .. tostring(lerr)
else
  record.requires[#record.requires + 1] = relsrc(entry)
  local ok, rerr = pcall(chunk)
  if not ok and tostring(rerr) ~= "__importer_os_exit__" then
    record.errors[#record.errors + 1] = "entry: " .. tostring(rerr)
  end
end

----------------------------------------------------------------------
-- JSON dump
----------------------------------------------------------------------
local function is_array(t)
  local n = 0
  for k in pairs(t) do
    if type(k) ~= "number" then return false end
    n = n + 1
  end
  for i = 1, n do if t[i] == nil then return false end end
  return true, n
end

local function jesc(s)
  s = s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t")
  s = s:gsub("[%z\1-\31]", function(c) return string.format("\\u%04x", c:byte()) end)
  return s
end

local function tojson(v, out)
  local t = type(v)
  if v == nil then out[#out + 1] = "null"
  elseif t == "boolean" then out[#out + 1] = tostring(v)
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then out[#out + 1] = "null"
    elseif math.type(v) == "integer" then out[#out + 1] = string.format("%d", v)
    else out[#out + 1] = string.format("%.17g", v) end
  elseif t == "string" then out[#out + 1] = '"' .. jesc(v) .. '"'
  elseif t == "table" then
    local arr, n = is_array(v)
    if arr then
      out[#out + 1] = "["
      for i = 1, n do
        if i > 1 then out[#out + 1] = "," end
        tojson(v[i], out)
      end
      out[#out + 1] = "]"
    else
      out[#out + 1] = "{"
      local first = true
      local kvs = {}
      for k, val in pairs(v) do kvs[#kvs + 1] = { tostring(k), val } end
      table.sort(kvs, function(a, b) return a[1] < b[1] end)
      for _, kv in ipairs(kvs) do
        if not first then out[#out + 1] = "," end
        first = false
        out[#out + 1] = '"' .. jesc(kv[1]) .. '":'
        tojson(kv[2], out)
      end
      out[#out + 1] = "}"
    end
  else out[#out + 1] = '"<' .. t .. '>"' end
end

local out = {}
tojson(record, out)
local f = assert(real_io.open(outpath, "w"))
f:write(table.concat(out))
f:close()
