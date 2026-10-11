-- Eternal2x  --  smooth 2x for hand-drawn animation
-- Resolve script panel: Workspace > Scripts > Eternal2x

-- ---------------------------------------------------------------------------
-- paths and config
-- ---------------------------------------------------------------------------

local function script_dir()
    local info = debug.getinfo(1, "S")
    local src = info.source or ""
    if src:sub(1, 1) == "@" then src = src:sub(2) end
    return (src:gsub("\\", "/")):match("(.*/)")
end

local function trim_trailing_sep(path)
    if not path then return "" end
    local out = path:gsub("[/\\]+$", "")
    return out
end

local function join_path(a, b)
    if not a or a == "" then return b end
    local last = a:sub(-1)
    if last == "/" or last == "\\" then return a .. b end
    return a .. "/" .. b
end

local function read_conf(path)
    local conf = {}
    local f = io.open(path, "r")
    if not f then return conf end
    for line in f:lines() do
        local k, v = line:match("^%s*([^=]+)%s*=%s*(.-)%s*$")
        if k and v then conf[k] = v end
    end
    f:close()
    return conf
end

local function parse_bool(value, default_value)
    if value == nil then return default_value end
    local s = tostring(value):lower()
    if s == "1" or s == "true" or s == "yes" or s == "on" then return true end
    if s == "0" or s == "false" or s == "no" or s == "off" then return false end
    return default_value
end

local function is_windows()
    return package.config:sub(1, 1) == "\\"
end

local function shell_quote(s)
    if not s then return "" end
    if is_windows() then
        return '"' .. s:gsub('"', '""') .. '"'
    end
    -- Single quotes, so $, backticks and backslashes in a path stay literal.
    -- Inside double quotes the shell would expand `$HOME` in a file name, or
    -- run a backticked command.
    return "'" .. s:gsub("'", "'\\''") .. "'"
end

local function short_path(path, max_len)
    if not path then return "" end
    local limit = max_len or 56
    if #path <= limit then return path end
    return "..." .. path:sub(#path - limit + 4)
end

local function basename(path)
    if not path then return "" end
    return (path:gsub("\\", "/")):match("([^/]+)$") or path
end

local root = trim_trailing_sep(script_dir() or "")
-- Launched through Eternal2xLauncher.lua the config lives in Resolve's Utility
-- folder rather than beside this file, so the launcher passes its path down.
local CONF_PATH = _G.ETERNAL2X_CONF or join_path(root, "Eternal2x.conf")
local conf = read_conf(CONF_PATH)
local REPO_ROOT = trim_trailing_sep(conf["repo_root"] or _G.ETERNAL2X_ROOT or root or "")
local PYTHON = conf["python"] or (is_windows() and "python" or "python3")
local UPDATE_URL = conf["update_url"] or ""
local AUTO_UPDATE = parse_bool(conf["auto_update"], true)
local OUTPUT_DIR = conf["output_dir"] or ""
local QUALITY = conf["quality"] or "better"
local DO_UPSCALE = parse_bool(conf["upscale"], true)
local DO_INTERPOLATE = parse_bool(conf["interpolate"], true)
local BASE_HOLD = tonumber(conf["base_hold"] or "0") or 0
local FORMAT = conf["format"] or "png"

local STATUS_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_status.json")
local LOG_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_last_run.log")
local RUN_SCRIPT = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_run.cmd")

local function save_conf()
    local f = io.open(CONF_PATH, "w")
    if not f then return end
    f:write("repo_root=" .. REPO_ROOT .. "\n")
    f:write("python=" .. PYTHON .. "\n")
    f:write("update_url=" .. UPDATE_URL .. "\n")
    f:write("auto_update=" .. (AUTO_UPDATE and "true" or "false") .. "\n")
    f:write("output_dir=" .. OUTPUT_DIR .. "\n")
    f:write("quality=" .. QUALITY .. "\n")
    f:write("upscale=" .. (DO_UPSCALE and "true" or "false") .. "\n")
    f:write("interpolate=" .. (DO_INTERPOLATE and "true" or "false") .. "\n")
    f:write("base_hold=" .. tostring(BASE_HOLD) .. "\n")
    f:write("format=" .. FORMAT .. "\n")
    f:close()
end

local function read_version()
    local vf = io.open(join_path(REPO_ROOT, "VERSION"), "r")
    if not vf then return "?" end
    local v = vf:read("*l") or "?"
    vf:close()
    -- Tolerate a UTF-8 BOM left by a Windows editor.
    v = v:gsub("^\239\187\191", "")
    return v:match("^%s*(.-)%s*$") or "?"
end

local CURRENT_VERSION = read_version()

-- ---------------------------------------------------------------------------
-- Resolve
-- ---------------------------------------------------------------------------

local function get_resolve()
    local ok, mod = pcall(require, "DaVinciResolveScript")
    if not ok or not mod then
        return nil, "Could not reach Resolve. Run this from Workspace > Scripts."
    end
    local r = mod.scriptapp("Resolve")
    if not r then return nil, "Could not connect to Resolve." end
    return r, nil
end

local function selected_clip_path()
    local resolve, err = get_resolve()
    if not resolve then return nil, err end
    local project = resolve:GetProjectManager():GetCurrentProject()
    if not project then return nil, "No project is open." end
    local timeline = project:GetCurrentTimeline()
    if not timeline then return nil, "No timeline is open." end

    local item = nil
    if timeline.GetSelectedItems then
        local items = timeline:GetSelectedItems()
        if items and type(items) == "table" then
            for _, v in pairs(items) do item = v break end
        end
    end
    if not item and timeline.GetCurrentVideoItem then
        item = timeline:GetCurrentVideoItem()
    end
    if not item then return nil, "No clip selected. Click a clip on the timeline." end

    local mpi = item:GetMediaPoolItem()
    if not mpi then return nil, "That item has no source media." end
    local props = mpi:GetClipProperty() or {}
    local path = props["File Path"]
    if not path or path == "" then return nil, "Could not read the clip's file path." end
    return path, nil
end

-- ---------------------------------------------------------------------------
-- running stages
-- ---------------------------------------------------------------------------

local function build_command(module_name, args, background)
    local cd = (is_windows() and "cd /d " or "cd ") .. shell_quote(REPO_ROOT)
    local run = shell_quote(PYTHON) .. " -m " .. module_name .. (args or "")
    local redirect = " > " .. shell_quote(LOG_PATH) .. " 2>&1"
    if not background then
        return cd .. " && " .. run .. redirect
    end
    if is_windows() then
        -- Nesting quoted paths inside `cmd /c "..."` flips cmd's quote state,
        -- so the inner paths end up unquoted and an & in a file name splits
        -- the command. Write the command to a script and start that instead.
        -- % is doubled because a batch file expands it. The redirection sits
        -- inside the script, so it applies to the child process and not to
        -- `start`, which would leave the log empty. chcp 65001 makes cmd read
        -- the rest of the script as UTF-8, so non-ASCII paths survive.
        -- Binary mode keeps Lua from turning \r\n into \r\r\n.
        local f = io.open(RUN_SCRIPT, "wb")
        if f then
            f:write("@echo off\r\nchcp 65001 >nul\r\n", (cd:gsub("%%", "%%%%")), "\r\n",
                    ((run .. redirect):gsub("%%", "%%%%")), "\r\n")
            f:close()
        end
        return "start \"Eternal2x\" /B cmd /c " .. shell_quote((RUN_SCRIPT:gsub("/", "\\")))
    end
    -- `A && B &` backgrounds the whole compound, which is what we want here.
    return cd .. " && " .. run .. redirect .. " &"
end

local function run_blocking(module_name, args)
    local cmd = build_command(module_name, args, false)
    print("[Eternal2x] " .. cmd)
    local ok = os.execute(cmd)
    return (ok == true or ok == 0)
end

local function run_background(module_name, args)
    local cmd = build_command(module_name, args, true)
    print("[Eternal2x] " .. cmd)
    os.execute(cmd)
end

local function read_log()
    local f = io.open(LOG_PATH, "r")
    if not f then return "" end
    local s = f:read("*a") or ""
    f:close()
    return s
end

-- Encode a code point as UTF-8, for \u escapes in the status file.
local function utf8_char(cp)
    if cp < 0x80 then return string.char(cp) end
    if cp < 0x800 then
        return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40)
    end
    if cp < 0x10000 then
        return string.char(0xE0 + math.floor(cp / 0x1000),
                           0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
    end
    return string.char(0xF0 + math.floor(cp / 0x40000),
                       0x80 + math.floor(cp / 0x1000) % 0x40,
                       0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
end

local JSON_ESCAPES = { ['"'] = '"', ["\\"] = "\\", ["/"] = "/", b = "\b",
                       f = "\f", n = "\n", r = "\r", t = "\t" }

-- Read one string value out of a flat JSON object, decoding its escapes.
-- File paths can hold quotes, backslashes and non-ASCII letters, and a plain
-- pattern match would hand Resolve the escaped form, which it cannot find.
local function json_string(s, key)
    local start = select(2, s:find('"' .. key .. '"%s*:%s*"'))
    if not start then return nil end
    local out, i = {}, start + 1
    while i <= #s do
        local c = s:sub(i, i)
        if c == '"' then return table.concat(out) end
        if c == "\\" then
            local e = s:sub(i + 1, i + 1)
            if e == "u" then
                local cp = tonumber(s:sub(i + 2, i + 5), 16) or 0xFFFD
                i = i + 6
                -- A surrogate pair carries a character outside the BMP.
                if cp >= 0xD800 and cp <= 0xDBFF and s:sub(i, i + 1) == "\\u" then
                    local low = tonumber(s:sub(i + 2, i + 5), 16) or 0
                    cp = 0x10000 + (cp - 0xD800) * 0x400 + (low - 0xDC00)
                    i = i + 6
                end
                out[#out + 1] = utf8_char(cp)
            else
                out[#out + 1] = JSON_ESCAPES[e] or e
                i = i + 2
            end
        else
            out[#out + 1] = c
            i = i + 1
        end
    end
    return nil
end

-- The status file is written by Stages/resolve_smooth.py as a flat JSON
-- object with a fixed shape, so the panel reads it without a full parser.
local function read_status()
    local f = io.open(STATUS_PATH, "r")
    if not f then return nil end
    local s = f:read("*a") or ""
    f:close()
    if s == "" then return nil end
    return {
        stage    = json_string(s, "stage") or "",
        message  = json_string(s, "message") or "",
        fraction = tonumber(s:match('"fraction"%s*:%s*([%-%d%.eE]+)')) or 0,
        import_path = json_string(s, "import_path") or "",
        done     = s:match('"done"%s*:%s*(%a+)') == "true",
        ok       = s:match('"ok"%s*:%s*(%a+)') == "true",
    }
end

local function clear_status()
    os.remove(STATUS_PATH)
end

-- ---------------------------------------------------------------------------
-- window
-- ---------------------------------------------------------------------------

local ui = fu.UIManager
local disp = bmd.UIDispatcher(ui)

local QUALITIES = { "fast", "better", "best" }
local QUALITY_LABELS = { "Fast", "Better", "Best" }
-- No "On 1s": planning on 1s gives every frame its own slot, so it would
-- change nothing. A saved 1 from an older version falls back to Auto detect.
local HOLDS = { 0, 2, 3 }
local HOLD_LABELS = { "Auto detect", "On 2s", "On 3s" }
local FORMATS = { "png", "mp4", "avi" }
local FORMAT_LABELS = { "Image sequence (lossless)", "MP4 (compact)", "AVI (lossless)" }

local function index_of(list, value)
    for i, v in ipairs(list) do
        if v == value then return i - 1 end
    end
    return 0
end

local win = disp:AddWindow({
    ID = "Eternal2x",
    WindowTitle = "Eternal2x  v" .. CURRENT_VERSION,
    Geometry = { 100, 100, 460, 660 },
    StyleSheet = [[
        QWidget { background-color: #0b0b0f; color: #e2e2ea; font-size: 12px; }
        QLabel#Title { font-size: 22px; font-weight: 700; color: #ededf4; padding-top: 2px; }
        QLabel#SubTitle { color: #6e6e82; font-size: 11px; padding-bottom: 8px; }
        QLabel#Section {
            color: #9a9ab0; font-size: 10px; font-weight: 700;
            padding-top: 10px; padding-bottom: 2px; letter-spacing: 0.6px;
        }
        QLabel#Card {
            background-color: #121216; border: 1px solid #1e1e26;
            border-radius: 6px; padding: 8px 10px; color: #c8c8d6; font-size: 11px;
        }
        QLabel#Meta { color: #5c5c70; font-size: 10px; padding-top: 2px; }
        QLabel#Progress {
            color: #7c6fef; font-size: 11px; font-family: monospace;
            padding: 2px 0px;
        }
        QPushButton {
            background-color: #16161e; border: 1px solid #28283a; border-radius: 7px;
            min-height: 32px; padding: 6px 14px; font-weight: 600; color: #e2e2ea;
        }
        QPushButton:hover { background-color: #1e1e28; border-color: #7c6fef; }
        QPushButton:pressed { background-color: #121218; }
        QPushButton:disabled { color: #4a4a5a; border-color: #1e1e26; background-color: #101014; }
        QPushButton#StartBtn {
            background-color: #7c6fef; border: 1px solid #9b90f5; color: #0b0b0f;
            font-size: 13px; font-weight: 700; min-height: 40px;
        }
        QPushButton#StartBtn:hover { background-color: #9b90f5; }
        QPushButton#StartBtn:disabled { background-color: #2a2740; color: #6a6a80; border-color: #2a2740; }
        QPushButton#GhostBtn {
            background-color: #121216; border: 1px solid #1e1e26;
            min-height: 26px; font-size: 11px; color: #6e6e82; font-weight: 500;
        }
        QPushButton#GhostBtn:hover { background-color: #1a1a22; color: #9a9ab0; }
        QComboBox {
            background-color: #16161e; border: 1px solid #28283a; border-radius: 6px;
            padding: 5px 8px; min-height: 24px; color: #e2e2ea;
        }
        QComboBox:hover { border-color: #7c6fef; }
        QComboBox QAbstractItemView {
            background-color: #16161e; border: 1px solid #28283a;
            selection-background-color: #7c6fef; selection-color: #0b0b0f;
        }
        QCheckBox { color: #c8c8d6; font-size: 11px; spacing: 7px; }
        QCheckBox::indicator {
            width: 15px; height: 15px; border-radius: 3px;
            border: 1px solid #28283a; background: #121216;
        }
        QCheckBox::indicator:checked { background: #7c6fef; border-color: #9b90f5; }
    ]],
}, ui:VGroup{
    Spacing = 2,

    ui:Label{ ID = "Title", Text = "Eternal2x", ObjectName = "Title" },
    ui:Label{ ID = "SubTitle", ObjectName = "SubTitle",
              Text = "Smooth 2x for hand-drawn animation  \xC2\xB7  v" .. CURRENT_VERSION },

    ui:Label{ ID = "SourceSection", Text = "SOURCE CLIP", ObjectName = "Section" },
    ui:Label{ ID = "SourceLabel", Text = "No clip selected.", ObjectName = "Card",
              WordWrap = true },
    ui:HGroup{ Spacing = 6,
        ui:Button{ ID = "RefreshBtn", Text = "Use Selected Clip", ObjectName = "GhostBtn" },
        ui:Button{ ID = "AnalyseBtn", Text = "Analyse", ObjectName = "GhostBtn" },
    },
    ui:Label{ ID = "AnalysisLabel", Text = "Analyse to see how this clip is animated.",
              ObjectName = "Card", WordWrap = true },

    ui:Label{ ID = "SettingsSection", Text = "SETTINGS", ObjectName = "Section" },
    ui:HGroup{ Spacing = 6,
        ui:Label{ Text = "Quality", Weight = 0.4 },
        ui:ComboBox{ ID = "QualityCombo", Weight = 0.6 },
    },
    ui:HGroup{ Spacing = 6,
        ui:Label{ Text = "Hold pattern", Weight = 0.4 },
        ui:ComboBox{ ID = "HoldCombo", Weight = 0.6 },
    },
    ui:HGroup{ Spacing = 6,
        ui:Label{ Text = "Output", Weight = 0.4 },
        ui:ComboBox{ ID = "FormatCombo", Weight = 0.6 },
    },
    ui:HGroup{ Spacing = 6,
        ui:CheckBox{ ID = "UpscaleCB", Text = "Upscale 2x", Checked = DO_UPSCALE },
        ui:CheckBox{ ID = "InterpCB", Text = "Interpolate", Checked = DO_INTERPOLATE },
    },

    ui:Label{ ID = "RunSection", Text = "RUN", ObjectName = "Section" },
    ui:Button{ ID = "SmoothBtn", Text = "\xE2\x96\xB2  Smooth Clip", ObjectName = "StartBtn" },
    ui:Label{ ID = "Progress", Text = "", ObjectName = "Progress" },
    ui:Label{ ID = "Status", Text = "Ready.", ObjectName = "Card", WordWrap = true },
    ui:HGroup{ Spacing = 6,
        ui:Button{ ID = "RefreshStatusBtn", Text = "Refresh Progress", ObjectName = "GhostBtn" },
        ui:Button{ ID = "CancelBtn", Text = "Reset", ObjectName = "GhostBtn" },
    },

    ui:Label{ ID = "Meta", Text = "", ObjectName = "Meta", WordWrap = true },
    ui:HGroup{ Spacing = 6,
        ui:Button{ ID = "UpdateBtn", Text = "\xE2\x86\xBB  Check for Updates", ObjectName = "GhostBtn" },
        ui:CheckBox{ ID = "AutoUpdateCB", Text = "Auto-update", Checked = AUTO_UPDATE },
    },
})

local items = win:GetItems()

for _, label in ipairs(QUALITY_LABELS) do items.QualityCombo:AddItem(label) end
for _, label in ipairs(HOLD_LABELS) do items.HoldCombo:AddItem(label) end
for _, label in ipairs(FORMAT_LABELS) do items.FormatCombo:AddItem(label) end
items.QualityCombo.CurrentIndex = index_of(QUALITIES, QUALITY)
items.HoldCombo.CurrentIndex = index_of(HOLDS, BASE_HOLD)
items.FormatCombo.CurrentIndex = index_of(FORMATS, FORMAT)

-- ---------------------------------------------------------------------------
-- state
-- ---------------------------------------------------------------------------

local SOURCE_PATH = nil
local RUNNING = false
local IMPORTED = false
local ANALYSING = false

local function set_status(msg)
    items.Status.Text = msg or ""
    print("[Eternal2x] " .. (msg or ""))
end

local function set_progress(fraction, stage)
    if not fraction or fraction <= 0 then
        items.Progress.Text = ""
        return
    end
    local width = 24
    local filled = math.floor(fraction * width + 0.5)
    if filled > width then filled = width end
    local bar = string.rep("\xE2\x96\x88", filled) .. string.rep("\xE2\x96\x91", width - filled)
    items.Progress.Text = string.format("%s  %d%%  %s", bar, math.floor(fraction * 100 + 0.5),
                                        stage or "")
end

local function set_busy(busy)
    RUNNING = busy
    items.SmoothBtn.Enabled = not busy
    items.AnalyseBtn.Enabled = not busy
    items.RefreshBtn.Enabled = not busy
    items.SmoothBtn.Text = busy and "Working..." or "\xE2\x96\xB2  Smooth Clip"
end

local function refresh_source()
    local path, err = selected_clip_path()
    if not path then
        SOURCE_PATH = nil
        items.SourceLabel.Text = err or "No clip selected."
        return false
    end
    SOURCE_PATH = path
    items.SourceLabel.Text = basename(path)
    items.Meta.Text = short_path(path, 68)
    return true
end

local function current_args(extra)
    local args = ""
    if SOURCE_PATH and SOURCE_PATH ~= "" then
        args = args .. " --video " .. shell_quote(SOURCE_PATH)
    end
    local quality = QUALITIES[(items.QualityCombo.CurrentIndex or 1) + 1] or "better"
    args = args .. " --quality " .. quality
    local hold = HOLDS[(items.HoldCombo.CurrentIndex or 0) + 1] or 0
    if hold and hold > 0 then
        args = args .. " --base-hold " .. tostring(hold)
    end
    local fmt = FORMATS[(items.FormatCombo.CurrentIndex or 0) + 1] or "png"
    args = args .. " --format " .. fmt
    if not items.UpscaleCB.Checked then args = args .. " --no-upscale" end
    if not items.InterpCB.Checked then args = args .. " --no-interpolate" end
    if OUTPUT_DIR ~= "" then args = args .. " --output-dir " .. shell_quote(OUTPUT_DIR) end
    args = args .. " --status-file " .. shell_quote(STATUS_PATH)
    return args .. (extra or "")
end

-- The render is finished on disk; bring it in from here rather than from the
-- Python process, which would need Resolve's external scripting enabled.
local function import_result(path)
    if not path or path == "" then return false, "Nothing to import." end
    local resolve, err = get_resolve()
    if not resolve then return false, err end
    local project = resolve:GetProjectManager():GetCurrentProject()
    if not project then return false, "No project is open." end
    if not project.GetMediaPool then return false, "Could not reach the media pool." end
    local pool = project:GetMediaPool()
    if not pool or not pool.ImportMedia then
        return false, "This Resolve version cannot import from a script."
    end

    local ok, items = pcall(function() return pool:ImportMedia({ path }) end)
    if not ok or not items or type(items) ~= "table" or #items == 0 then
        return false, "Resolve would not import " .. basename(path)
    end
    local mpi = items[1]

    local notes = "Imported " .. basename(path)
    if DO_UPSCALE and mpi.SetClipProperty then
        -- Resolve accepts 1, 2, 3, 4 or Auto here. "2x" is rejected.
        -- pcall's first value only says nothing raised. Resolve reports a
        -- refusal, as on the free edition, by returning false, so check that.
        local called, scaled = pcall(function() return mpi:SetClipProperty("Super Scale", "2") end)
        notes = notes .. ((called and scaled) and ", upscaled 2x"
                          or ", upscale not applied (Super Scale needs Resolve Studio)")
    end
    if pool.AppendToTimeline then
        local called, appended = pcall(function() return pool:AppendToTimeline({ mpi }) end)
        local added = called and appended and (type(appended) ~= "table" or #appended > 0)
        notes = notes .. (added and ", added to the timeline."
                                or ". Drag it onto your timeline.")
    end
    return true, notes
end

local function apply_status(st)
    if not st then return false end
    set_progress(st.fraction, st.stage)
    if st.message ~= "" then set_status(st.message) end
    if st.done then
        set_busy(false)
        if not st.ok then
            set_progress(0, nil)
            local why = st.message ~= "" and st.message
                or "Failed. See the console for details."
            if ANALYSING then items.AnalysisLabel.Text = why end
            ANALYSING = false
            set_status(why)
            return true
        end
        set_progress(1.0, "Finished")
        if ANALYSING then
            ANALYSING = false
            if st.message ~= "" then items.AnalysisLabel.Text = st.message end
            set_status("Analysis complete.")
            return true
        end
        if st.import_path ~= "" and not IMPORTED then
            IMPORTED = true
            local ok, notes = import_result(st.import_path)
            if ok then
                set_status(st.message .. " " .. notes)
            else
                set_status(st.message .. " " .. notes ..
                           " The render is finished, so you can drag it in.")
            end
        end
        return true
    end
    return false
end

-- ---------------------------------------------------------------------------
-- polling
-- ---------------------------------------------------------------------------

-- ui:Timer is not present on every Resolve build, so it is created defensively
-- and the Refresh Progress button covers the case where it is missing.
local timer = nil
local have_timer = pcall(function()
    timer = ui:Timer({ ID = "PollTimer", Interval = 500 })
end)

if have_timer and timer then
    win:AddChild(timer)
    function win.On.PollTimer.Timeout(ev)
        if not RUNNING then return end
        apply_status(read_status())
    end
end

local function start_polling()
    if have_timer and timer and timer.Start then
        pcall(function() timer:Start() end)
    end
end

-- ---------------------------------------------------------------------------
-- handlers
-- ---------------------------------------------------------------------------

function win.On.Eternal2x.Close(ev)
    save_conf()
    disp:ExitLoop()
end

function win.On.RefreshBtn.Clicked(ev)
    if refresh_source() then
        set_status("Ready.")
        items.AnalysisLabel.Text = "Analyse to see how this clip is animated."
    else
        set_status(items.SourceLabel.Text)
    end
end

function win.On.AnalyseBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if not SOURCE_PATH and not refresh_source() then
        set_status(items.SourceLabel.Text)
        return
    end
    -- Backgrounded like the render: analysis is quick on a short clip but can
    -- take tens of seconds on a long one, and Resolve must not freeze for it.
    clear_status()
    ANALYSING = true
    IMPORTED = false
    set_busy(true)
    set_progress(0.01, "Starting")
    set_status("Analysing. Resolve stays usable while this runs.")
    items.AnalysisLabel.Text = "Analysing..."
    run_background("Stages.resolve_smooth", current_args(" --analyse"))
    start_polling()
end

function win.On.SmoothBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if not SOURCE_PATH and not refresh_source() then
        set_status(items.SourceLabel.Text)
        return
    end
    clear_status()
    IMPORTED = false
    ANALYSING = false
    set_busy(true)
    set_progress(0.01, "Starting")
    set_status("Working. Resolve stays usable while this runs.")
    run_background("Stages.resolve_smooth", current_args(""))
    start_polling()
end

function win.On.RefreshStatusBtn.Clicked(ev)
    local st = read_status()
    if not st then
        set_status("No run in progress.")
        return
    end
    apply_status(st)
end

function win.On.CancelBtn.Clicked(ev)
    clear_status()
    ANALYSING = false
    IMPORTED = false
    set_busy(false)
    set_progress(0, nil)
    set_status("Ready.")
end

function win.On.QualityCombo.CurrentIndexChanged(ev)
    QUALITY = QUALITIES[(items.QualityCombo.CurrentIndex or 1) + 1] or "better"
    save_conf()
end

function win.On.HoldCombo.CurrentIndexChanged(ev)
    BASE_HOLD = HOLDS[(items.HoldCombo.CurrentIndex or 0) + 1] or 0
    save_conf()
end

function win.On.FormatCombo.CurrentIndexChanged(ev)
    FORMAT = FORMATS[(items.FormatCombo.CurrentIndex or 0) + 1] or "png"
    save_conf()
end

function win.On.UpscaleCB.Clicked(ev)
    DO_UPSCALE = items.UpscaleCB.Checked
    save_conf()
end

function win.On.InterpCB.Clicked(ev)
    DO_INTERPOLATE = items.InterpCB.Checked
    save_conf()
end

function win.On.AutoUpdateCB.Clicked(ev)
    AUTO_UPDATE = items.AutoUpdateCB.Checked
    save_conf()
    set_status(AUTO_UPDATE and "Auto-update enabled." or "Auto-update disabled.")
end

local function run_update(auto_mode)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if UPDATE_URL == "" then
        set_status("No update URL configured.")
        return
    end
    local args = " --meta-url " .. shell_quote(UPDATE_URL)
    if auto_mode then args = args .. " --auto" end
    if not auto_mode then set_status("Checking for updates...") end
    local ok = run_blocking("Stages.resolve_update", args)
    if auto_mode then return end
    local log = read_log()
    local line = log:match("([^\n]*Updat[^\n]*)") or log:match("([^\n]*No update[^\n]*)")
    if line and line ~= "" then
        set_status(line)
    elseif ok then
        set_status("Update check complete.")
    else
        set_status("Update check failed. See the console for details.")
    end
end

function win.On.UpdateBtn.Clicked(ev)
    run_update(false)
end

-- ---------------------------------------------------------------------------
-- start
-- ---------------------------------------------------------------------------

win:Show()

if REPO_ROOT == "" then
    items.Meta.Text = "Plugin folder not configured."
    set_status("Run Installer/install_eternal2x.py, then restart Resolve.")
else
    items.Meta.Text = short_path(REPO_ROOT, 68)
    refresh_source()
    set_status(SOURCE_PATH and "Ready." or items.SourceLabel.Text)
    if AUTO_UPDATE then run_update(true) end
end

disp:RunLoop()
