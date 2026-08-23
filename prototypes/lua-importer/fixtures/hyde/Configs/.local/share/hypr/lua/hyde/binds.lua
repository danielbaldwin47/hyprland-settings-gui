-- Bind dedup wrapper for Hyprland Lua keybinds.
--
-- This module normalizes keycombo strings and tracks active binds in
-- hyde.binds._active. It deduplicates bindings only when
-- hyde.binds.dedup is enabled, and only for bindings with the same
-- normalized key combo and the same values for configured dedup fields.
-- Description-only metadata is ignored for dedup signature generation.
--
-- Default dedup fields are based on Hyprland bind flags:
--   https://wiki.hypr.land/Configuring/Basics/Binds/#bind-flags
--
-- Default fields:
--   locked, release, click, drag, long_press, repeating,
--   non_consuming, auto_consuming, transparent, ignore_mods,
--   separate, bypass, submap_universal, devices
--
-- TODO: check the Hyprland bind flags documentation periodically and
-- update the dedup field list when new relevant bind flags are added.

local function trim(str)
    return str and str:gsub("^%s+", ""):gsub("%s+$", "") or ""
end

local function normalize(keycombo)
    if type(keycombo) ~= "string" then
        return ""
    end
    return trim(keycombo:gsub("%s*%+%s*", " + "))
end

local function has_dedup_field(opts)
    if type(opts) ~= "table" then
        return false
    end

    local fields = hyde.binds.dedup_fields
    if type(fields) ~= "table" then
        return false
    end

    for _, field in ipairs(fields) do
        if opts[field] ~= nil then
            return true
        end
    end

    return false
end

local function find_options(...)
    for i = select("#", ...), 1, -1 do
        local arg = select(i, ...)
        if type(arg) == "table" and has_dedup_field(arg) then
            return arg
        end
    end
    return nil
end

local function serialize_flags(opts)
    if type(opts) ~= "table" then
        return ""
    end

    local fields = hyde.binds.dedup_fields
    if type(fields) ~= "table" or #fields == 0 then
        return ""
    end

    local parts = {}
    for _, field in ipairs(fields) do
        local value = opts[field]
        if value ~= nil then
            parts[#parts + 1] = field .. "=" .. tostring(value)
        end
    end

    if #parts == 0 then
        return ""
    end

    return table.concat(parts, "|")
end

hyde = hyde or {}
hyde.binds = hyde.binds or {}

hyde.binds.dedup = hyde.binds.dedup == nil and false or hyde.binds.dedup
hyde.binds.dedup_fields =
    hyde.binds.dedup_fields or
    {
        "locked",
        "release",
        "click",
        "drag",
        "long_press",
        "repeating",
        "non_consuming",
        "auto_consuming",
        "transparent",
        "ignore_mods",
        "separate",
        "bypass",
        "submap_universal",
        "devices"
    }

hyde.binds.normalize = normalize

hyde.binds._active = hyde.binds._active or {}

local orig_add = hl.bind

hl.bind = function(keycombo, action, ...)
    local normalized = hyde.binds.normalize(keycombo)
    local opts = find_options(...)
    local signature = serialize_flags(opts)
    local dedup_id = normalized .. "|" .. signature

    if normalized ~= "" and hyde.binds.dedup and hyde.binds._active[dedup_id] then
        hl.unbind(normalized)
    end

    if normalized ~= "" then
        hyde.binds._active[dedup_id] = true
        keycombo = normalized
    end

    return orig_add(keycombo, action, ...)
end
