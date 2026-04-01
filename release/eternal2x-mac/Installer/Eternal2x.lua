-- Eternal2x Resolve Script Panel (Workspace > Scripts)
-- Compact UI: 4 actions + 1 sensitivity slider

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
    local out = path:gsub("[/\\]+$", "")
    return out
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

local function parse_bool(value, default_value)
    if value == nil then
        return default_value
    end
    local s = tostring(value):lower()
    if s == "1" or s == "true" or s == "yes" or s == "on" then
        return true
    end
    if s == "0" or s == "false" or s == "no" or s == "off" then
        return false
    end
    return default_value
end

local function short_path(path, max_len)
    if not path then return "" end
    local limit = max_len or 56
    if #path <= limit then
        return path
    end
    return "..." .. path:sub(#path - limit + 4)
end

local function shell_quote(s)
    if not s then return "" end
    if package.config:sub(1, 1) == "\\" then
        -- cmd.exe escaping: double internal quotes
        return '"' .. s:gsub('"', '""') .. '"'
    end
    return '"' .. s:gsub('"', '\\"') .. '"'
end

local function run_command(cmd)
    print("[Eternal2x] " .. cmd)
    return os.execute(cmd)
end

local function is_windows()
    return package.config:sub(1, 1) == "\\"
end

local function get_selected_clip_path(resolve)
    local project = resolve:GetProjectManager():GetCurrentProject()
    if not project then return nil, "No active project." end
    local timeline = project:GetCurrentTimeline()
    if not timeline then return nil, "No active timeline." end

    local items = nil
    if timeline.GetSelectedItems then
        items = timeline:GetSelectedItems()
    end

    local item = nil
    if items and type(items) == "table" then
        for _, v in pairs(items) do
            item = v
            break
        end
    end
    if not item and timeline.GetCurrentVideoItem then
        item = timeline:GetCurrentVideoItem()
    end
    if not item then return nil, "No selected clip." end

    local mpi = item:GetMediaPoolItem()
    if not mpi then return nil, "No media pool item for clip." end
    local props = mpi:GetClipProperty() or {}
    local path = props["File Path"]
    if not path or path == "" then
        return nil, "Clip file path not available."
    end
    return path, nil
end

local function get_resolve()
    local ok, bmd = pcall(require, "DaVinciResolveScript")
    if not ok or not bmd then
        return nil, "Could not import DaVinciResolveScript."
    end
    local resolve = bmd.scriptapp("Resolve")
    if not resolve then
        return nil, "Could not connect to Resolve."
    end
    return resolve, nil
end

local ui = fu.UIManager
local disp = bmd.UIDispatcher(ui)

local root = trim_trailing_sep(script_dir() or "")
local conf = read_conf((root ~= "" and (root .. "/") or "") .. "Eternal2x.conf")
local REPO_ROOT = trim_trailing_sep(conf["repo_root"] or root or "")
local PYTHON = conf["python"] or (is_windows() and "python" or "python3")
local UPDATE_URL = conf["update_url"] or ""
local AUTO_UPDATE = parse_bool(conf["auto_update"], true)
local CONF_PATH = (root ~= "" and (root .. "/") or "") .. "Eternal2x.conf"

local function save_conf()
    local f = io.open(CONF_PATH, "w")
    if not f then return end
    f:write("repo_root=" .. REPO_ROOT .. "\n")
    f:write("python=" .. PYTHON .. "\n")
    f:write("update_url=" .. UPDATE_URL .. "\n")
    f:write("auto_update=" .. (AUTO_UPDATE and "true" or "false") .. "\n")
    f:close()
end

local function read_version()
    local vf = io.open((REPO_ROOT ~= "" and (REPO_ROOT .. "/") or "") .. "VERSION", "r")
    if not vf then return "?" end
    local v = vf:read("*l") or "?"
    vf:close()
    return v:match("^%s*(.-)%s*$") or "?"
end

local CURRENT_VERSION = read_version()

local win = disp:AddWindow({
    ID = "Eternal2x",
    WindowTitle = "Eternal2x  v" .. CURRENT_VERSION,
    Geometry = {100, 100, 440, 500},
    StyleSheet = [[
        QWidget {
            background-color: #0b0b0f;
            color: #e2e2ea;
            font-size: 12px;
        }
        QLabel#Title {
            font-size: 20px;
            font-weight: 700;
            color: #ededf4;
            padding-top: 4px;
        }
        QLabel#SubTitle {
            color: #6e6e82;
            font-size: 11px;
            padding-bottom: 6px;
        }
        QLabel#Section {
            color: #9a9ab0;
            font-size: 10px;
            font-weight: 700;
            padding-top: 8px;
            padding-bottom: 2px;
            letter-spacing: 0.5px;
        }
        QLabel#Meta {
            color: #6e6e82;
            background-color: #121216;
            border: 1px solid #1e1e26;
            border-radius: 6px;
            padding: 6px 8px;
            font-size: 10px;
        }
        QPushButton {
            background-color: #16161e;
            border: 1px solid #28283a;
            border-radius: 7px;
            min-height: 34px;
            padding: 6px 14px;
            font-weight: 600;
            font-size: 12px;
            color: #e2e2ea;
        }
        QPushButton:hover {
            background-color: #1e1e28;
            border-color: #7c6fef;
        }
        QPushButton:pressed { background-color: #121218; }
        QPushButton#StartBtn {
            background-color: #7c6fef;
            border: 1px solid #9b90f5;
            color: #0b0b0f;
            font-size: 13px;
            font-weight: 700;
            min-height: 38px;
        }
        QPushButton#StartBtn:hover { background-color: #9b90f5; }
        QPushButton#StartBtn:pressed { background-color: #6a5ed6; }
        QPushButton#UpdateBtn {
            background-color: #121216;
            border: 1px solid #1e1e26;
            min-height: 26px;
            font-size: 11px;
            color: #6e6e82;
        }
        QPushButton#UpdateBtn:hover {
            background-color: #1a1a22;
            color: #9a9ab0;
        }
        QCheckBox { color: #6e6e82; font-size: 11px; spacing: 6px; }
        QCheckBox::indicator {
            width: 14px; height: 14px; border-radius: 3px;
            border: 1px solid #28283a; background: #121216;
        }
        QCheckBox::indicator:checked {
            background: #7c6fef; border-color: #9b90f5;
        }
        QSlider::groove:horizontal {
            height: 6px;
            border-radius: 3px;
            background: #1e1e26;
        }
        QSlider::handle:horizontal {
            width: 16px;
            height: 16px;
            background: #7c6fef;
            border: 2px solid #9b90f5;
            border-radius: 8px;
            margin: -5px 0;
        }
        QSlider::sub-page:horizontal {
            background: #7c6fef;
            border-radius: 3px;
        }
        QLabel#Status {
            background-color: #121216;
            border: 1px solid #1e1e26;
            border-radius: 6px;
            padding: 8px;
            color: #9a9ab0;
            font-size: 11px;
        }
    ]]
}, ui:VGroup{
    ui:Label{ID="Title", Text="Eternal2x", ObjectName="Title"},
    ui:Label{ID="SubTitle", Text="Smart Upscale  \xC2\xB7  v" .. CURRENT_VERSION, ObjectName="SubTitle"},
    ui:Label{ID="WorkflowSection", Text="WORKFLOW", ObjectName="Section"},
    ui:HGroup{
        ui:Button{ID="DetectBtn", Text="\xE2\x97\x89  Detect"},
        ui:Button{ID="CutFrameBtn", Text="\xE2\x9C\x82  Sequence"},
    },
    ui:HGroup{
        ui:Button{ID="RegroupBtn", Text="\xE2\x8A\x9E  Regroup"},
        ui:Button{ID="UpscaleBtn", Text="\xE2\x96\xB2  Upscale + Interpolate", ObjectName="StartBtn"},
    },
    ui:Label{ID="SensitivitySection", Text="SENSITIVITY", ObjectName="Section"},
    ui:Label{ID="SensLabel", Text="Interpolate Sensitivity: 0.20"},
    ui:Slider{ID="SensSlider", Orientation="Horizontal", Minimum=0, Maximum=100, Value=20},
    ui:Label{ID="StatusSection", Text="STATUS", ObjectName="Section"},
    ui:Label{ID="Status", Text="Ready.", ObjectName="Status", WordWrap=true},
    ui:Label{ID="Meta", Text="Repo: (not set)", ObjectName="Meta", WordWrap=true},
    ui:HGroup{
        ui:Button{ID="UpdateBtn", Text="\xE2\x86\xBB  Check for Updates", ObjectName="UpdateBtn"},
        ui:CheckBox{ID="AutoUpdateCB", Text="Auto-update", Checked=AUTO_UPDATE},
    },
})

local items = win:GetItems()

function win.On.Eternal2x.Close(ev)
    disp:ExitLoop()
end

local function set_status(msg)
    local line = msg or ""
    if items and items.Status then
        items.Status.Text = line
    end
    print("[Eternal2x] " .. line)
end

local function sensitivity_value()
    local v = 20
    if items and items.SensSlider and items.SensSlider.Value then
        v = items.SensSlider.Value
    end
    return v / 100.0
end

function win.On.SensSlider.ValueChanged(ev)
    local v = sensitivity_value()
    if items and items.SensLabel then
        items.SensLabel.Text = string.format("Interpolate Sensitivity: %.2f", v)
    end
end

local function build_command(module_name, extra_args)
    local args = extra_args or ""
    if is_windows() then
        return "cd /d " .. shell_quote(REPO_ROOT)
            .. " && " .. shell_quote(PYTHON)
            .. " -m " .. module_name
            .. args
    end
    return "cd " .. shell_quote(REPO_ROOT)
        .. " && " .. shell_quote(PYTHON)
        .. " -m " .. module_name
        .. args
end

local function run_stage(stage_label, module_name, extra_args)
    if REPO_ROOT == "" then
        set_status("Missing repo root. Reinstall using Installer/install_eternal2x.py.")
        return
    end
    set_status(stage_label .. " running...")
    local ok = run_command(build_command(module_name, extra_args))
    if ok == true or ok == 0 then
        set_status(stage_label .. " finished.")
    else
        set_status(stage_label .. " failed. Check Console for details.")
    end
end

local function run_update(auto_mode)
    if REPO_ROOT == "" then
        set_status("Missing repo root. Reinstall using Installer/install_eternal2x.py.")
        return
    end
    if UPDATE_URL == "" then
        set_status("No update URL configured.")
        return
    end
    local args = " --meta-url " .. shell_quote(UPDATE_URL)
    if auto_mode then
        args = args .. " --auto"
    end
    if not auto_mode then
        set_status("Checking for updates...")
    end
    local ok = run_command(build_command("Stages.resolve_update", args))
    if not auto_mode then
        if ok == true or ok == 0 then
            set_status("Update check complete. See Console for details.")
        else
            set_status("Update failed. See Console for details.")
        end
    end
end

function win.On.DetectBtn.Clicked(ev)
    local resolve, err = get_resolve()
    if not resolve then
        set_status(err)
        return
    end
    local path, perr = get_selected_clip_path(resolve)
    if not path then
        set_status(perr)
        return
    end
    local v = sensitivity_value()
    local args = " --video " .. shell_quote(path)
        .. " --sensitivity " .. string.format("%.4f", v)
    run_stage("Detect", "Stages.resolve_detect_markers", args)
end

function win.On.CutFrameBtn.Clicked(ev)
    run_stage("Sequence", "Stages.resolve_cut_and_sequence", "")
end

function win.On.RegroupBtn.Clicked(ev)
    run_stage("Regroup", "Stages.resolve_regroup", "")
end

function win.On.UpscaleBtn.Clicked(ev)
    local v = sensitivity_value()
    local args = " --sensitivity " .. string.format("%.4f", v)
    run_stage("Upscale and Interpolate", "Stages.resolve_upscale_interpolate", args)
end

function win.On.UpdateBtn.Clicked(ev)
    run_update(false)
end

function win.On.AutoUpdateCB.Clicked(ev)
    AUTO_UPDATE = items.AutoUpdateCB.Checked
    save_conf()
    if AUTO_UPDATE then
        set_status("Auto-update enabled.")
    else
        set_status("Auto-update disabled. Use 'Check for Updates' manually.")
    end
end

win:Show()
if REPO_ROOT == "" then
    if items and items.Meta then
        items.Meta.Text = "Repo: not configured"
    end
    set_status("Warning: no config found. Run installer script.")
else
    if items and items.Meta then
        items.Meta.Text = "Repo: " .. short_path(REPO_ROOT, 62)
    end
    set_status("Ready.")
    if AUTO_UPDATE then
        run_update(true)
    end
end
disp:RunLoop()
