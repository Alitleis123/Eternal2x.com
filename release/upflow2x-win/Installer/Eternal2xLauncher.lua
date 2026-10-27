-- Upflow2x launcher script installed into Resolve Scripts/Utility.
-- It forwards execution to the main UI script at repo_root/Installer/Eternal2x.lua.

local function script_dir()
    local info = debug.getinfo(1, "S")
    local src = info.source or ""
    if src:sub(1, 1) == "@" then
        src = src:sub(2)
    end
    local norm = src:gsub("\\", "/")
    return norm:match("(.*/)")
end

local function trim_trailing_sep(path)
    if not path then return "" end
    return path:gsub("[/\\]+$", "")
end

local function join_path(a, b)
    if not a or a == "" then return b end
    local sep = a:sub(-1)
    if sep == "/" or sep == "\\" then
        return a .. b
    end
    return a .. "/" .. b
end

local function read_conf(path)
    local conf = {}
    local f = io.open(path, "r")
    if not f then
        return conf
    end
    for line in f:lines() do
        local k, v = line:match("^%s*([^=]+)%s*=%s*(.-)%s*$")
        if k and v then
            conf[k] = v
        end
    end
    f:close()
    return conf
end

local function file_exists(path)
    local f = io.open(path, "r")
    if not f then
        return false
    end
    f:close()
    return true
end

-- Where Installer/install_eternal2x.py puts this script, for when Resolve
-- reports the running script under a path map such as Scripts: that io.open
-- cannot follow.
local function installed_dir()
    local appdata = os.getenv("APPDATA")
    if package.config:sub(1, 1) == "\\" and appdata then
        return appdata .. "/Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility"
    end
    local home = os.getenv("HOME")
    if not home then return nil end
    local mac = home .. "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
    if file_exists(mac .. "/Eternal2x.conf") then return mac end
    return home .. "/.local/share/DaVinciResolve/Fusion/Scripts/Utility"
end

local root = trim_trailing_sep(script_dir() or "")
local conf_path = join_path(root, "Eternal2x.conf")
if not file_exists(conf_path) and installed_dir() then
    conf_path = join_path(installed_dir(), "Eternal2x.conf")
end
local conf = read_conf(conf_path)
local repo_root = trim_trailing_sep(conf["repo_root"] or "")

if repo_root == "" then
    print("[Upflow2x] Missing repo_root in Eternal2x.conf. Re-run installer.")
    return
end

local ui_script = join_path(repo_root:gsub("\\", "/"), "Installer/Eternal2x.lua")
if not file_exists(ui_script) then
    print("[Upflow2x] UI script not found: " .. ui_script)
    print("[Upflow2x] Re-run installer to repair.")
    return
end

-- The UI script lives in the plugin folder, but the config lives here next to
-- the launcher. Hand both down so the UI does not have to guess from its own
-- location and end up treating Installer/ as the repo root.
_G.ETERNAL2X_CONF = conf_path
_G.ETERNAL2X_ROOT = repo_root

local ok, err = pcall(dofile, ui_script)
if not ok then
    print("[Upflow2x] Failed to launch UI: " .. tostring(err))
end
