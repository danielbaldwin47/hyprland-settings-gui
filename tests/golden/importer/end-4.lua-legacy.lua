-- Imported by hyprtweaker from <entry>.
--
-- Constructs the settings app cannot represent, kept exactly as they were. The app
-- lists these read-only and never rewrites this file: it is yours to edit.

-- script from hyprland/execs.lua:2
hl.on("hyprland.start", function ()

    -- Bar, wallpaper
    hl.exec_cmd("$HOME/.config/hypr/hyprland/scripts/start_geoclue_agent.sh")
    hl.exec_cmd("qs -c $qsConfig")
    hl.exec_cmd("$HOME/.config/hypr/custom/scripts/__restore_video_wallpaper.sh")

    -- Core components (authentication, lock screen, notification daemon)
    hl.exec_cmd("gnome-keyring-daemon --start --components=secrets")
    hl.exec_cmd("hypridle")
    hl.exec_cmd("dbus-update-activation-environment --all")
    hl.exec_cmd("sleep 1 && dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP") -- Some fix idk

    -- Audio
    --hl.exec_cmd("easyeffects --hide-window --service-mode")

    -- Clipboard: history
    --hl.exec_cmd("wl-paste --watch cliphist store")
    hl.exec_cmd("wl-paste --type text --watch bash -c 'cliphist store && qs -c $qsConfig ipc call cliphistService update'")
    hl.exec_cmd("wl-paste --type image --watch bash -c 'cliphist store && qs -c $qsConfig ipc call cliphistService update'")

    -- Cursor
    hl.exec_cmd("hyprctl setcursor Bibata-Modern-Classic 24")

    -- Fix dock pinned apps not launching properly (https://github.com/end-4/dots-hyprland/issues/2200)
    -- This causes https://github.com/end-4/dots-hyprland/issues/2427
    -- hl.exec_cmd("sleep 3.5 && hyprctl reload && sleep 0.5 && touch ~/.config/quickshell/ii/shell.qml")

end)

-- inline function from hyprland/general.lua:12
hl.gesture({ action = function() hl.dsp.global("quickshell:overviewWorkspacesToggle") end, direction = "up", fingers = 4 })

-- inline function from hyprland/general.lua:13
hl.gesture({ action = function() hl.dsp.global("quickshell:overviewWorkspacesClose") end, direction = "down", fingers = 4 })

-- inline function from hyprland/keybinds.lua:218
hl.bind("SUPER + ALT + F1", function()
        local currentsubmap =                       hl.get_current_submap()
        if currentsubmap == "virtual-machine" then
                                                    hl.dispatch(hl.dsp.exec_cmd("notify-send 'Exited Virtual Machine submap' 'Keybinds re-enabled' -a 'Hyprland'")                              ) -- # [hidden]
                                                    hl.dispatch(hl.dsp.submap("reset")                                                                                                          )
        elseif currentsubmap == "" then
                                                    hl.dispatch(hl.dsp.exec_cmd("notify-send 'Entered Virtual Machine submap' 'Keybinds disabled. hit SUPER+ALT+F1 to escape' -a 'Hyprland'")   ) -- Disable keybinds
                                                    hl.dispatch(hl.dsp.submap("virtual-machine")                                                                                                )
        end
    end, { submap_universal = true })

-- inline function from hyprland/keybinds.lua:259
do
  local zoomfunction = function(value)
    local zoomvalue =                               hl.get_config("cursor:zoom_factor")
    if (zoomvalue + value) > 3.0 then
        hl.config({cursor = {zoom_factor = 3.0}})
    elseif (zoomvalue + value) < 1.0 then
        hl.config({cursor = {zoom_factor = 1.0}})
    else
        hl.config({cursor = {zoom_factor = zoomvalue + value}})
    end

end
  hl.bind("SUPER + Minus", function() zoomfunction(-0.3)  end, { repeating = true })
end

-- inline function from hyprland/keybinds.lua:260
do
  local zoomfunction = function(value)
    local zoomvalue =                               hl.get_config("cursor:zoom_factor")
    if (zoomvalue + value) > 3.0 then
        hl.config({cursor = {zoom_factor = 3.0}})
    elseif (zoomvalue + value) < 1.0 then
        hl.config({cursor = {zoom_factor = 1.0}})
    else
        hl.config({cursor = {zoom_factor = zoomvalue + value}})
    end

end
  hl.bind("SUPER + Equal", function() zoomfunction(0.3)   end, { repeating = true })
end

-- inline function from hyprland/keybinds.lua:263
do
  local zoomfunction = function(value)
    local zoomvalue =                               hl.get_config("cursor:zoom_factor")
    if (zoomvalue + value) > 3.0 then
        hl.config({cursor = {zoom_factor = 3.0}})
    elseif (zoomvalue + value) < 1.0 then
        hl.config({cursor = {zoom_factor = 1.0}})
    else
        hl.config({cursor = {zoom_factor = zoomvalue + value}})
    end

end
  hl.bind("SUPER + code:82", function() zoomfunction(-0.1)  end, { repeating = true })
end

-- inline function from hyprland/keybinds.lua:264
do
  local zoomfunction = function(value)
    local zoomvalue =                               hl.get_config("cursor:zoom_factor")
    if (zoomvalue + value) > 3.0 then
        hl.config({cursor = {zoom_factor = 3.0}})
    elseif (zoomvalue + value) < 1.0 then
        hl.config({cursor = {zoom_factor = 1.0}})
    else
        hl.config({cursor = {zoom_factor = zoomvalue + value}})
    end

end
  hl.bind("SUPER + code:86", function() zoomfunction(0.1)   end, { repeating = true })
end

