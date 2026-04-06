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

local function run_command(cmd, status_prefix)
    print("[Eternal2x] " .. cmd)
    if is_windows() then
        -- Run via a temp .bat that redirects output to a temp file,
        -- launched hidden through wscript so no cmd window appears.
        local tmp = os.getenv("TEMP") or "."
        local out_file = tmp .. "\\eternal2x_output.log"
        local rc_file = tmp .. "\\eternal2x_rc.txt"
        local bat_file = tmp .. "\\eternal2x_run.bat"
        local vbs_file = tmp .. "\\eternal2x_run.vbs"

        -- Write .bat that runs the command and saves the exit code
        local bf = io.open(bat_file, "w")
        if bf then
            bf:write("@echo off\r\n")
            bf:write(cmd .. " > " .. shell_quote(out_file) .. " 2>&1\r\n")
            bf:write("echo %errorlevel% > " .. shell_quote(rc_file) .. "\r\n")
            bf:close()
        end

        -- Write .vbs that launches the .bat hidden (no window)
        local vf = io.open(vbs_file, "w")
        if vf then
            vf:write('Set ws = CreateObject("WScript.Shell")\r\n')
            vf:write('ws.Run "cmd /c """ & "' .. bat_file:gsub("\\", "\\\\") .. '" & """", 0, True\r\n')
            vf:close()
        end

        -- Delete old output files
        os.remove(out_file)
        os.remove(rc_file)

        -- Run the vbs (wscript is always available, runs hidden)
        os.execute('wscript //nologo "' .. vbs_file .. '"')

        -- Read output
        local output = ""
        local of = io.open(out_file, "r")
        if of then
            output = of:read("*a") or ""
            of:close()
        end
        if output ~= "" then print(output) end

        -- Read return code
        local code = 1
        local rf = io.open(rc_file, "r")
        if rf then
            local rc_str = rf:read("*l") or "1"
            rf:close()
            code = tonumber(rc_str:match("%d+")) or 1
        end

        -- Clean up temp files
        os.remove(bat_file)
        os.remove(vbs_file)

        if code == 0 then return 0, output end
        return code, output
    end

    -- macOS / Linux: io.popen is fine, no window issues
    local handle = io.popen(cmd .. " 2>&1")
    if not handle then return false, "" end
    local lines = {}
    for line in handle:lines() do
        table.insert(lines, line)
        print(line)
        if status_prefix then
            local pct = line:match("%[PROGRESS%] (%d+)%%")
            if pct and items and items.Status then
                items.Status.Text = status_prefix .. " " .. pct .. "%"
            end
        end
    end
    local ok, _, code = handle:close()
    local output = table.concat(lines, "\n")
    if ok then return 0, output end
    return code or 1, output
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
    if not item then return nil, nil, "Please select a clip on the timeline." end

    local mpi = item:GetMediaPoolItem()
    if not mpi then return nil, nil, "Please select a clip with a valid media source." end
    local props = mpi:GetClipProperty() or {}
    local path = props["File Path"]
    if not path or path == "" then
        return nil, nil, "Please select a clip with a valid media source."
    end
    local name = props["Clip Name"] or props["File Name"] or path:match("([^/\\]+)$") or path
    return path, name, nil
end

local function get_resolve()
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

local function find_resolve_install_dir()
    if is_windows() then
        -- Try Fusion's app path first (no subprocess needed)
        local app_path = nil
        if app and app.GetPath then
            app_path = app:GetPath()
        end
        if app_path and app_path ~= "" then
            return trim_trailing_sep(app_path:gsub("\\", "/"))
        end
        -- Fallback: scan common install locations by checking for fusionscript.dll
        local candidates = {
            "C:\\Program Files\\Blackmagic Design\\DaVinci Resolve",
            "E:\\Davinchi resolve",
            "D:\\Program Files\\Blackmagic Design\\DaVinci Resolve",
        }
        for _, dir in ipairs(candidates) do
            local f = io.open(dir .. "\\fusionscript.dll", "r")
            if f then
                f:close()
                return dir
            end
        end
    else
        return "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion"
    end
    return ""
end

local function resolve_script_modules_dir()
    if is_windows() then
        local pd = os.getenv("PROGRAMDATA") or "C:\\ProgramData"
        return pd .. "\\Blackmagic Design\\DaVinci Resolve\\Support\\Developer\\Scripting\\Modules"
    end
    return "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
end

local RESOLVE_DIR = find_resolve_install_dir()

local function build_command(module_name, extra_args)
    local args = extra_args or ""
    local modules = resolve_script_modules_dir()
    if is_windows() then
        local resolve_lib = RESOLVE_DIR .. "\\fusionscript.dll"
        -- Use set "VAR=value" syntax so paths with spaces don't leak quotes into the value
        return "chcp 65001 >nul && cd /d " .. shell_quote(REPO_ROOT)
            .. ' && set "PYTHONPATH=' .. modules .. ';%PYTHONPATH%"'
            .. ' && set "RESOLVE_SCRIPT_LIB=' .. resolve_lib .. '"'
            .. ' && set "PATH=' .. RESOLVE_DIR .. ';%PATH%"'
            .. " && " .. shell_quote(PYTHON)
            .. " -m " .. module_name
            .. args
    end
    return "cd " .. shell_quote(REPO_ROOT)
        .. " && PYTHONPATH=" .. shell_quote(modules) .. ":$PYTHONPATH"
        .. " RESOLVE_SCRIPT_LIB=" .. shell_quote(RESOLVE_DIR .. "/fusionscript.so")
        .. " " .. shell_quote(PYTHON)
        .. " -m " .. module_name
        .. args
end

local function run_stage(stage_label, module_name, extra_args)
    if REPO_ROOT == "" then
        set_status("Plugin not configured. Please reinstall.")
        return
    end
    set_status(stage_label .. " running...")
    local code, output = run_command(build_command(module_name, extra_args), stage_label)
    if code == true or code == 0 then
        set_status(stage_label .. " complete.")
    else
        -- Extract a useful error from Python output
        local err_msg = nil
        if output then
            err_msg = output:match("([A-Z]%w+Error: [^\n]+)")
                   or output:match("Error: ([^\n]+)")
        end
        if err_msg then
            set_status(stage_label .. " failed: " .. err_msg)
        else
            set_status(stage_label .. " failed. See Resolve Console for details.")
        end
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
    local code = run_command(build_command("Stages.resolve_update", args))
    if not auto_mode then
        if code == true or code == 0 then
            set_status("You're up to date.")
        else
            set_status("Update check failed. Check your connection.")
        end
    end
end

function win.On.DetectBtn.Clicked(ev)
    local resolve, err = get_resolve()
    if not resolve then
        set_status(err)
        return
    end
    local path, clip_name, perr = get_selected_clip_path(resolve)
    if not path then
        set_status(perr)
        return
    end
    -- Write video path to temp file (UTF-8) to avoid cmd.exe encoding issues
    local tmp = os.getenv("TEMP") or os.getenv("TMPDIR") or "/tmp"
    local argfile = tmp .. "/eternal2x_video_path.txt"
    local af = io.open(argfile, "wb")
    if af then
        af:write(path)
        af:close()
    end
    set_status("Detecting motion in: " .. (clip_name or "clip") .. "...")
    local v = sensitivity_value()
    local args = " --video-file " .. shell_quote(argfile)
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
