-- The recording stub ADR-0009 specifies: a foreign hyprland.lua evaluated under a
-- fake `hl` and a stdlib that refuses to touch the world, dumping what it saw as JSON.
--
-- Usage: lua5.5 runner.lua <entry.lua> <basedir> <out.json> <policy>
--   policy "block"       -- side effects intercepted and faked; the default
--   policy "passthrough" -- side effects really happen, and are still recorded
--
-- Why evaluation at all: a hyprland.lua is a program, and the only thing that knows what
-- `for _, m in ipairs(monitors) do hl.monitor(m) end` declares is Lua. So the file runs --
-- but against this table, not the real API, and with the world held away from it.
--
-- The sandbox boundary is one thing: the ENV table below, passed to `loadfile(..., "t", ENV)`.
-- Nothing in this file leaks into it except what ENV names, so user code never reaches the
-- real `io`/`os`, never reaches `debug` (the recorder uses it from outside), and never
-- reaches the real `require`. Blocking is the default because an import must be safe to run
-- on a config the user has not read; passthrough exists because some configs (theme engines
-- that shell out to discover their own files) produce nothing useful without it, and needs
-- the user's consent every time.

local entry, basedir, outpath, policy = arg[1], arg[2], arg[3], arg[4] or "block"
local passthrough = policy == "passthrough"

----------------------------------------------------------------------
-- record state
----------------------------------------------------------------------
local record = {
  calls = {},     -- ordered stream: {call=, args=, src=, line=, argc=, submap=?}
  scripts = {},   -- captured functions: {id=, source=, from=, to=, upvalues={}, context=}
  queries = {},   -- hl.* live-state queries answered with a stand-in
  shell = {},     -- os.execute / io.popen / os.remove / os.rename
  iowrites = {},  -- io.open in a write mode
  reads = {},     -- io.open in a read mode: state the imported copy will not re-read
  requires = {},  -- files evaluated, in order
  prints = {},
  errors = {},
  exited = false, -- the config called os.exit and we trapped it
  policy = policy,
}

local script_id = 0
local current_submap = nil

local function relsrc(s)
  if type(s) ~= "string" then return tostring(s) end
  s = s:gsub("^@", "")
  if s:sub(1, #basedir) == basedir then s = s:sub(#basedir + 2) end
  return s
end

-- deep copy of a captured value; functions become script refs
local capture_fn
local fn_seen = {} -- function -> script id (dedup, and the cycle guard)
local snap         -- forward declared: mutually recursive with capture_fn

capture_fn = function(f, context)
  if fn_seen[f] then return { __fn = fn_seen[f] } end
  script_id = script_id + 1
  local id = script_id
  fn_seen[f] = id
  local info = debug.getinfo(f, "S")
  local entry_rec = {
    id = id,
    source = relsrc(info.source),
    from = info.linedefined,
    to = info.lastlinedefined,
    upvalues = {},
    context = context,
  }
  record.scripts[#record.scripts + 1] = entry_rec
  -- Upvalues are why a closure can be lifted out of its file at all: the source text
  -- alone is not self-contained, and these are the names it closed over.
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
      entry_rec.upvalues[#entry_rec.upvalues + 1] = uv
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
  -- walk out of this file's frames to the first user-code frame
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
-- the hl stub
----------------------------------------------------------------------
local hl = {}

-- Declarative entry points: whatever they were handed, recorded verbatim. Interpreting
-- the table is Python's job -- this side must not have opinions it could be wrong about.
local DECL = {
  "config", "monitor", "bind", "unbind", "gesture", "animation", "curve",
  "device", "workspace_rule", "window_rule", "layer_rule", "env", "permission",
}
for _, name in ipairs(DECL) do
  hl[name] = function(...)
    local n = select("#", ...)
    local args
    -- argc travels separately: hl.config({}) and hl.config() are otherwise identical
    -- once the arguments have been flattened into a table.
    if n == 1 then args = (...) else args = { ... } end
    rec(name, args, n)
  end
end

hl.define_submap = function(name, reset_or_fn, maybe_fn)
  -- Real signature is (name, fn) or (name, reset_target, fn).
  local reset, fn = "", reset_or_fn
  if type(reset_or_fn) == "string" then reset, fn = reset_or_fn, maybe_fn end
  rec("define_submap", { name = name, reset = reset })
  local prev = current_submap
  current_submap = name
  if type(fn) == "function" then
    -- The body runs now, as the engine runs it, so binds inside land in this submap.
    local ok, err = pcall(fn)
    if not ok then
      record.errors[#record.errors + 1] =
        "define_submap(" .. tostring(name) .. "): " .. tostring(err)
    end
  end
  current_submap = prev
end

hl.on = function(event, fn)
  local src, line = callsite()
  record.calls[#record.calls + 1] = {
    call = "on",
    args = { event = event, handler = capture_fn(fn, "on:" .. tostring(event)) },
    src = src, line = line,
  }
end

hl.timer = function(spec) rec("timer", spec, 1) end

-- hl.dsp.*: lazy markers. `hl.dsp.window.resize({...})` is three __index hops and a
-- __call, and what we keep is the path plus the arguments -- ADR-0007's typed Action.
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

-- Same argument convention as DECL above: argc says how many there were, and args is the
-- single argument when there was one. Wrapping a lone argument in a table here instead
-- would make `hl.exec_cmd("waybar")` indistinguishable from `hl.exec_cmd({"waybar"})`.
hl.dispatch = function(...)
  local n = select("#", ...)
  rec("dispatch_immediate", n == 1 and (...) or { ... }, n)
end
hl.exec_cmd = function(cmd) rec("exec_cmd", cmd, 1) end

hl.layout = {
  register = function(name, provider)
    local src, line = callsite()
    record.calls[#record.calls + 1] = {
      call = "layout_register",
      args = { name = name, provider = snap(provider, "layout:" .. tostring(name)) },
      src = src, line = line,
    }
  end,
}

-- A plugin namespace is nil unless a matching hl.plugin.load ran first, exactly as the
-- engine has it. Configs guard on that (`if hl.plugin.hyprbars ~= nil then ... end`), and
-- a stub that answered truthy would bake the wrong branch and emit config the engine
-- rejects -- the prototype hit this for real.
local plugins_loaded = {}
hl.plugin = setmetatable({
  load = function(path)
    rec("plugin_load", path, 1)
    plugins_loaded[#plugins_loaded + 1] = tostring(path)
  end,
}, {
  __index = function(_, ns)
    local hit = false
    for _, p in ipairs(plugins_loaded) do
      if p:lower():find(tostring(ns):lower(), 1, true) then hit = true break end
    end
    if not hit then
      local src, line = callsite()
      record.queries[#record.queries + 1] =
        { fn = "plugin." .. tostring(ns), src = src, line = line }
      return nil
    end
    return setmetatable({}, {
      __newindex = function(_, k, v)
        rec("plugin_set", { ns = ns, key = k, value = snap(v, "plugin") }, 1)
      end,
      __index = function() return nil end,
      __call = function(_, ...) rec("plugin_call", { ns = ns, args = snap({ ... }, "plugin") }, 1) end,
    })
  end,
})

hl.notification = setmetatable({}, {
  __index = function(_, name)
    return function(...) rec("notification_" .. name, snap({ ... }, "notification"), select("#", ...)) end
  end,
})

-- Live-state queries: a stand-in answer, and a note that the config asked. There is no
-- compositor here, so anything the config decided from these answers is baked.
local QUERIES = {
  version = "0.56.2",
  get_monitors = {}, get_windows = {}, get_workspaces = {}, get_layers = {},
  get_loaded_plugins = {}, get_workspace_windows = {},
  get_current_submap = "",
  get_cursor_pos = { x = 0, y = 0 },
  is_key_down = false,
  clear_crashed_lockscreen = true,
  exec_scheduled_prop_refresh_immediately = true,
}
local NIL_QUERIES = {
  "get_active_monitor", "get_active_window", "get_active_workspace",
  "get_active_special_workspace", "get_last_window", "get_last_workspace",
  "get_urgent_window", "get_monitor", "get_monitor_at", "get_monitor_at_cursor",
  "get_window", "get_workspace", "get_config",
}
local function query_fn(name, ret)
  return function()
    local src, line = callsite()
    record.queries[#record.queries + 1] = { fn = name, src = src, line = line }
    return ret
  end
end
for name, ret in pairs(QUERIES) do hl[name] = query_fn(name, ret) end
for _, name in ipairs(NIL_QUERIES) do hl[name] = query_fn(name, nil) end

----------------------------------------------------------------------
-- sandboxed stdlib
----------------------------------------------------------------------
local real_io, real_os = io, os

local function fake_pipe(text)
  return {
    read = function() if text ~= nil then local t = text; text = nil; return t end return nil end,
    lines = function() return function() return nil end end,
    close = function() return true, "exit", 0 end,
  }
end

local function note_shell(kind, cmd)
  record.shell[#record.shell + 1] =
    { kind = kind, cmd = tostring(cmd), policy = policy, src = select(1, callsite()) }
end

local sandbox_os = {
  -- Reading the environment is allowed under both policies: it cannot change anything,
  -- and refusing it breaks configs that only want $HOME. That the answer is baked into
  -- the import is a loss-report matter, not a sandbox one.
  getenv = real_os.getenv,
  time = real_os.time, date = real_os.date, clock = real_os.clock,
  difftime = real_os.difftime, tmpname = real_os.tmpname,
  getpid = function() return 4242 end,   -- Hyprland extensions some configs call
  geteuid = function() return 1000 end,
  -- Trapped under every policy, consent or not: a config that exits mid-file would
  -- otherwise take the import with it and leave a half-read model looking complete.
  exit = function(code)
    record.exited = true
    record.errors[#record.errors + 1] = "os.exit(" .. tostring(code) .. ") trapped"
    error("__importer_os_exit__", 0)
  end,
  execute = function(cmd)
    note_shell("os.execute", cmd)
    if passthrough then return real_os.execute(cmd) end
    return true, "exit", 0
  end,
  remove = function(p)
    note_shell("os.remove", p)
    if passthrough then return real_os.remove(p) end
    return true
  end,
  rename = function(a, b)
    note_shell("os.rename", tostring(a) .. " -> " .. tostring(b))
    if passthrough then return real_os.rename(a, b) end
    return true
  end,
}

local sandbox_io = {
  open = function(path, mode)
    mode = mode or "r"
    if mode:find("[wa+]") then
      record.iowrites[#record.iowrites + 1] =
        { path = tostring(path), mode = mode, policy = policy }
      if passthrough then return real_io.open(path, mode) end
      -- Pretend it worked and swallow the writes: a config that checks whether its log
      -- file opened should take the same branch it takes on the user's machine.
      return {
        write = function(self) return self end,
        close = function() return true end,
        read = function() return nil end,
        lines = function() return function() return nil end end,
      }
    end
    -- Reads are allowed under both policies -- refusing them would not make anything
    -- safer, only make the import wrong. But a config that reads state outside its own
    -- tree (a theme cache, a generated colour file) has baked that state into the import
    -- and will not re-read it afterwards, so the read is recorded for the Loss report.
    record.reads[#record.reads + 1] = { path = tostring(path), src = select(1, callsite()) }
    return real_io.open(path, mode)
  end,
  lines = function(...) return real_io.lines(...) end,
  read = function() return nil end,
  write = function(...) record.prints[#record.prints + 1] = table.concat({ ... }, "") return sandbox_io end,
  popen = function(cmd, mode)
    note_shell("io.popen", cmd)
    if passthrough then return real_io.popen(cmd, mode) end
    return fake_pipe("")
  end,
  stderr = { write = function(_, ...) record.prints[#record.prints + 1] = table.concat({ ... }, "") return sandbox_io.stderr end },
  stdout = { write = function(_, ...) record.prints[#record.prints + 1] = table.concat({ ... }, "") return sandbox_io.stdout end },
}

----------------------------------------------------------------------
-- Hyprland-style require, resolved against the sandbox env
----------------------------------------------------------------------
local ENV -- forward
local loaded = {}
local pkg = { path = "./?.lua;./?/init.lua", cpath = "", loaded = loaded, preload = {} }
pkg.searchpath = function(name, path, sep, rep)
  sep, rep = sep or ".", rep or "/"
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
  cands[#cands + 1] = basedir .. "/" .. name .. ".lua"
  cands[#cands + 1] = basedir .. "/" .. name
  cands[#cands + 1] = basedir .. "/" .. slashed .. ".lua"
  cands[#cands + 1] = basedir .. "/" .. slashed .. "/init.lua"
  for pat in tostring(pkg.path):gmatch("[^;]+") do
    cands[#cands + 1] = pat:gsub("%?", slashed)
  end
  for _, c in ipairs(cands) do
    local f = real_io.open(c, "r")
    if f then f:close() return c end
  end
  return nil, cands
end

-- Wildcard require -- `require("./stuff/*")` -- needs a directory listing, which Lua
-- cannot do without help. The listing runs regardless of policy because it is the
-- importer's own machinery rather than the config's, so the name is checked against a
-- conservative character set first: the string comes from the config being imported, and
-- interpolating it into a shell command unchecked is a command injection.
local SAFE_WILDCARD = "^[%w%._/%-]*$"
local function list_lua_modules(dir)
  if not dir:match(SAFE_WILDCARD) or dir:find("%.%.") then
    record.errors[#record.errors + 1] = "wildcard require refused for path: " .. dir
    return {}
  end
  local names = {}
  -- Recorded like any other shell-out: this one is the importer's own, not the config's,
  -- but an unrecorded process start is exactly the thing this file promises not to do.
  note_shell("importer.listdir", dir)
  local p = real_io.popen("ls -1 '" .. basedir .. "/" .. dir .. "' 2>/dev/null")
  if not p then return names end
  for f in p:lines() do
    if f:sub(-4) == ".lua" then names[#names + 1] = f:sub(1, -5) end
  end
  p:close()
  return names
end

local function sandbox_require(name)
  if loaded[name] ~= nil then return loaded[name] end
  if name:find("%*") then
    local dir = name:gsub("%*", ""):gsub("^%./", "")
    local results = {}
    for _, mod in ipairs(list_lua_modules(dir)) do
      results[#results + 1] = sandbox_require(dir .. mod)
    end
    return results
  end
  local path = try_paths(name)
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
-- the environment the config sees -- this table is the sandbox
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
  -- Anything the config loads itself loads into this same environment, so there is no
  -- way out through `load`/`dofile`.
  load = function(chunk, name, _, env) return load(chunk, name, "t", env or ENV) end,
  loadstring = function(s, n) return load(s, n, "t", ENV) end,
  dofile = function(p) local c = loadfile(p, "t", ENV) return c and c() end,
  collectgarbage = function() return 0 end,
  arg = {},
}
ENV._G = ENV
-- Deliberately absent: debug (this file uses the real one from outside ENV), the real
-- io/os, and every C-loading path.

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
-- JSON out (hand-rolled: no library may be assumed present)
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
  return (s:gsub("[%z\1-\31]", function(c) return string.format("\\u%04x", c:byte()) end))
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
      -- keys sorted, so two runs of the same config produce the same bytes
      local kvs = {}
      for k, val in pairs(v) do kvs[#kvs + 1] = { tostring(k), val } end
      table.sort(kvs, function(a, b) return a[1] < b[1] end)
      out[#out + 1] = "{"
      for i, kv in ipairs(kvs) do
        if i > 1 then out[#out + 1] = "," end
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
