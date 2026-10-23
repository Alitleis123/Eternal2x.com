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
-- The update check runs each time the panel opens, so it logs apart from
-- renders. Sharing a log wiped the render's, the one worth sending in.
local UPDATE_LOG_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_update.log")
local RUN_SCRIPT = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_run.cmd")
-- Written by the shell once Python exits, however it exits. A Python that
-- cannot start never writes a status file, so this is how the panel tells a
-- run that is still going from one that died.
local EXIT_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_exit")

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

-- A script run from Workspace > Scripts gets Resolve as a global, the way
-- Blackmagic's Lua examples use it: `resolve`, or `Resolve()`. There is no Lua
-- DaVinciResolveScript module, so require is only a last resort.
local function global(name)
    -- A plain lookup, so globals Fusion provides through a metatable count.
    local ok, value = pcall(function() return _G[name] end)
    if ok then return value end
    return nil
end

local function get_resolve()
    local r = global("resolve")
    if not r and global("Resolve") then
        local ok, got = pcall(global("Resolve"))
        if ok then r = got end
    end
    local app = global("bmd")
    if not r and app and app.scriptapp then
        local ok, got = pcall(app.scriptapp, "Resolve")
        if ok then r = got end
    end
    if not r then
        local ok, mod = pcall(require, "DaVinciResolveScript")
        if ok and type(mod) == "table" and mod.scriptapp then r = mod.scriptapp("Resolve") end
    end
    if not r then
        return nil, "Could not reach Resolve. Run this from Workspace > Scripts."
    end
    return r, nil
end

-- Where the timeline clip starts and ends within its media, in seconds from
-- the start of the file, so only that part is rebuilt. Seconds mean the same
-- whatever the timeline's rate. nil means use the whole file.
--
-- Resolve counts the source frame from the start of the file, at the media's
-- own rate, but the source time from the media's start timecode, so on
-- footage that starts at 01:00:00:00 the times are an hour out. Only their
-- difference is used, anchored on the start frame.
local function clip_range(item, props)
    local function call(name)
        local ok, value = pcall(function() return item[name](item) end)
        return ok and tonumber(value) or nil
    end
    local fps = tonumber(tostring(props["FPS"] or ""):match("^%s*([%d%.]+)"))
    local f0 = call("GetSourceStartFrame")
    if not (fps and fps > 0 and f0) then return nil end
    local start = f0 / fps
    local t0, t1 = call("GetSourceStartTime"), call("GetSourceEndTime")
    if t0 and t1 and t1 > t0 then return { start = start, finish = start + (t1 - t0) } end
    local f1 = call("GetSourceEndFrame")
    if f1 and f1 > f0 then return { start = start, finish = f1 / fps } end
    return nil
end

local function find_clip_path()
    local resolve, err = get_resolve()
    if not resolve then return nil, err end
    local project = resolve:GetProjectManager():GetCurrentProject()
    if not project then return nil, "No project is open." end
    local timeline = project:GetCurrentTimeline()
    if not timeline then return nil, "No timeline is open." end

    -- Resolve's API has no timeline selection. GetCurrentVideoItem is the clip
    -- under the playhead, so that is the one Eternal2x works on.
    local item = nil
    if timeline.GetCurrentVideoItem then
        item = timeline:GetCurrentVideoItem()
    end
    if not item then
        return nil, "No clip under the playhead. Move the playhead over a clip on the timeline."
    end

    local mpi = item:GetMediaPoolItem()
    if not mpi then return nil, "That item has no source media." end
    local props = mpi:GetClipProperty() or {}
    local path = props["File Path"]
    if not path or path == "" then return nil, "Could not read the clip's file path." end
    return path, nil, clip_range(item, props)
end

-- Any API call above can raise, and this runs while the panel opens, where an
-- error would stop the window from ever appearing.
local function selected_clip_path()
    local ok, path, err, range = pcall(find_clip_path)
    if not ok then return nil, "Could not read the clip from Resolve: " .. tostring(path) end
    return path, err, range
end

-- ---------------------------------------------------------------------------
-- running stages
-- ---------------------------------------------------------------------------

local function build_command(module_name, args, background, log)
    local cd = (is_windows() and "cd /d " or "cd ") .. shell_quote(REPO_ROOT)
    local run = shell_quote(PYTHON) .. " -m " .. module_name .. (args or "")
    local redirect = " > " .. shell_quote(log or LOG_PATH) .. " 2>&1"
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
        -- Binary mode keeps Lua from turning \r\n into \r\r\n. The exit code
        -- line puts the redirection first, because `echo 2> file` would
        -- redirect stderr instead of writing 2.
        local f = io.open(RUN_SCRIPT, "wb")
        if f then
            f:write("@echo off\r\nchcp 65001 >nul\r\n", (cd:gsub("%%", "%%%%")), "\r\n",
                    ((run .. redirect):gsub("%%", "%%%%")), "\r\n",
                    ">", (shell_quote(EXIT_PATH):gsub("%%", "%%%%")), " echo %ERRORLEVEL%\r\n")
            f:close()
        end
        -- /MIN gives the run its own minimised window. With /B it shared the
        -- console os.execute opens, which stayed on screen for the whole
        -- render, and closing it killed the render without a word.
        -- It starts in the plugin folder and runs the script by name. Given
        -- the full path, `cmd /c` strips its quotes when it holds & and
        -- splits there, and `call` would double a ^ in it.
        local dir = (REPO_ROOT ~= "" and REPO_ROOT or root):gsub("/", "\\")
        return "start \"Eternal2x\" /D " .. shell_quote(dir) .. " /MIN cmd /c .\\"
            .. basename(RUN_SCRIPT)
    end
    -- The subshell backgrounds the whole run, and records how it ended.
    return "(" .. cd .. " && " .. run .. redirect .. "; echo $? > "
        .. shell_quote(EXIT_PATH) .. ") &"
end

local function run_blocking(module_name, args, log)
    local cmd = build_command(module_name, args, false, log)
    print("[Eternal2x] " .. cmd)
    local ok = os.execute(cmd)
    return (ok == true or ok == 0)
end

local function run_background(module_name, args)
    local cmd = build_command(module_name, args, true)
    print("[Eternal2x] " .. cmd)
    os.execute(cmd)
end

local function read_log(path)
    local f = io.open(path or LOG_PATH, "r")
    if not f then return "" end
    local s = f:read("*a") or ""
    f:close()
    return s
end

-- The last line of the run log worth showing, such as Python's final error.
local function last_log_line(path)
    local last = nil
    for line in read_log(path):gmatch("[^\r\n]+") do
        if line:match("%S") then last = line end
    end
    return last and (last:match("^%s*(.-)%s*$")) or nil
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
        sequence_pattern = json_string(s, "sequence_pattern") or "",
        sequence_frames = tonumber(s:match('"sequence_frames"%s*:%s*(%d+)')) or 0,
        updated  = tonumber(s:match('"updated"%s*:%s*([%d%.eE]+)')),
        fps      = tonumber(s:match('"fps"%s*:%s*([%d%.]+)')) or 0,
        done     = s:match('"done"%s*:%s*(%a+)') == "true",
        ok       = s:match('"ok"%s*:%s*(%a+)') == "true",
    }
end

local function clear_status()
    os.remove(STATUS_PATH)
    os.remove(EXIT_PATH)
end

-- The exit code of the last background run, or nil while it is still going.
local function read_exit_code()
    local f = io.open(EXIT_PATH, "r")
    if not f then return nil end
    local s = f:read("*a") or ""
    f:close()
    return s:match("%-?%d+") or "?"
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
    ui:Label{ ID = "SourceLabel", Text = "No clip under the playhead.", ObjectName = "Card",
              WordWrap = true },
    ui:HGroup{ Spacing = 6,
        ui:Button{ ID = "RefreshBtn", Text = "Use Clip at Playhead", ObjectName = "GhostBtn" },
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
local SOURCE_RANGE = nil
local RUNNING = false
-- When this panel started its run, which counts as the first heartbeat until
-- Python, a second or two later, writes its own.
local LAUNCHED_AT = nil
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
    items.UpdateBtn.Enabled = not busy
    items.SmoothBtn.Text = busy and "Working..." or "\xE2\x96\xB2  Smooth Clip"
end

-- Seconds as text that Python's float() reads, whatever the locale.
local function seconds(value)
    return (string.format("%.6f", value):gsub(",", "."))
end

local function range_text(range)
    if not range then return "Whole file." end
    return (string.format("%.2f s to %.2f s of the file, %.2f s long.",
                          range.start, range.finish, range.finish - range.start)
            :gsub("(%d),(%d)", "%1.%2"))
end

local function refresh_source()
    local path, err, range = selected_clip_path()
    if not path then
        SOURCE_PATH = nil
        SOURCE_RANGE = nil
        items.SourceLabel.Text = err or "No clip under the playhead."
        return false
    end
    SOURCE_PATH = path
    SOURCE_RANGE = range
    items.SourceLabel.Text = basename(path) .. "\n" .. range_text(range)
    items.Meta.Text = short_path(path, 68)
    return true
end

local function current_args(extra)
    local args = ""
    if SOURCE_PATH and SOURCE_PATH ~= "" then
        args = args .. " --video " .. shell_quote(SOURCE_PATH)
        if SOURCE_RANGE then
            args = args .. " --start-time " .. seconds(SOURCE_RANGE.start)
                        .. " --end-time " .. seconds(SOURCE_RANGE.finish)
        end
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
-- An image sequence has no frame rate of its own, so Resolve gives it the
-- project's, and a 60 fps render in a 24 fps project plays at 40% speed.
-- Resolve lets a sequence's rate be changed, so set it, then read it back,
-- because a refusal is not always reported. Done before the clip is appended,
-- since its length on the timeline depends on it.
local function set_sequence_fps(mpi, fps)
    if not mpi.SetClipProperty then return "" end
    local text = (math.abs(fps - math.floor(fps + 0.5)) < 0.001)
        and tostring(math.floor(fps + 0.5)) or string.format("%.3f", fps)
    local called, set = pcall(function() return mpi:SetClipProperty("FPS", text) end)
    if called and set and mpi.GetClipProperty then
        local read, got = pcall(function() return mpi:GetClipProperty("FPS") end)
        if read and type(got) == "table" then got = got["FPS"] end
        if read and math.abs((tonumber(got) or 0) - fps) > 0.01 then set = false end
    end
    if called and set then return " at " .. text .. " fps" end
    return " (set its frame rate to " .. text .. " in Clip Attributes, or it plays at the project's)"
end

local function import_result(st)
    local path = st.import_path
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

    local function try_import(what)
        local ok, got = pcall(function() return pool:ImportMedia({ what }) end)
        if ok and type(got) == "table" and #got > 0 then return got[1] end
        return nil
    end

    -- Handed a single frame, Resolve imports a single still. A sequence needs
    -- the clipInfo form from Blackmagic's README: a printf pattern plus the
    -- first and last frame numbers. If that is refused there is no fallback,
    -- since the first frame alone would go on the timeline as a still.
    local mpi = nil
    if st.sequence_pattern ~= "" and st.sequence_frames > 0 then
        mpi = try_import({ FilePath = st.sequence_pattern, StartIndex = 0,
                           EndIndex = st.sequence_frames - 1 })
    else
        mpi = try_import(path)
    end
    if not mpi then
        -- The whole path, so the render can be found and dragged in by hand.
        -- A sequence is its folder, not its first frame.
        local where = st.sequence_pattern ~= "" and path:match("^(.+)[/\\]") or path
        return false, "Resolve would not import " .. where .. "."
    end

    local notes = "Imported " .. basename(path)
    if st.sequence_pattern ~= "" and st.fps > 0 then
        notes = notes .. set_sequence_fps(mpi, st.fps)
    end
    if DO_UPSCALE and mpi.SetClipProperty then
        -- Resolve accepts 1, 2, 3, 4 or Auto here. "2x" is rejected. The
        -- README calls the value an integer, so try a number if text is refused.
        -- pcall's first value only says nothing raised. Resolve reports a
        -- refusal, as on the free edition, by returning false, so check that.
        local called, scaled = pcall(function() return mpi:SetClipProperty("Super Scale", "2") end)
        if not (called and scaled) then
            called, scaled = pcall(function() return mpi:SetClipProperty("Super Scale", 2) end)
        end
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
        -- Handled now. Left in place, Refresh Progress in a reopened panel
        -- would import the same render again.
        os.remove(STATUS_PATH)
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
            local ok, notes = import_result(st)
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

-- Check on the background run. The exit code is read before the status, so a
-- run that wrote its final status and then exited is never taken for a crash.
-- Python updates the status at least once a frame. This long without a word,
-- and no exit code, means the run was killed from outside, so nothing could
-- report it, as when its window is closed.
local STALLED_AFTER = 180

local function poll()
    local exited = read_exit_code()
    local st = read_status()
    if apply_status(st) then return true end
    if not exited then
        local quiet = (st and st.updated and st.updated > 0) and (os.time() - st.updated) or 0
        if quiet > STALLED_AFTER then
            set_status(string.format("No progress for %d minutes. If the Eternal2x window "
                .. "was closed, the render stopped: press Reset and run it again.",
                math.floor(quiet / 60 + 0.5)))
        end
        return false
    end

    -- Python exited without saying it was done: it could not start, could not
    -- load a package, or crashed. Its last words are the end of the log.
    local why = last_log_line() or ("Python exited with code " .. exited .. ".")
    local msg = "Stopped without finishing: " .. why .. " Full log: " .. LOG_PATH
    set_busy(false)
    set_progress(0, nil)
    if ANALYSING then items.AnalysisLabel.Text = msg end
    ANALYSING = false
    set_status(msg)
    return true
end

-- A run may still be going, even one started from an earlier panel. Python
-- writes its status at least once a frame, so a recent unfinished one means
-- it is. Only one run at a time, since runs share the script, status and
-- exit files.
local function run_in_progress()
    if read_exit_code() then return false end
    local st = read_status()
    if st and st.done then return false end
    local last = (st and st.updated) or (RUNNING and LAUNCHED_AT)
    -- Either way of the clock: a heartbeat from before the clock was moved
    -- back is in the future, and is no more alive than an old one.
    return last and math.abs(os.time() - last) < STALLED_AFTER or false
end

-- ---------------------------------------------------------------------------
-- polling
-- ---------------------------------------------------------------------------

-- ui:Timer is not present on every Resolve build, so it is created defensively
-- and the Refresh Progress button covers the case where it is missing. Its
-- Timeout reaches the dispatcher, as in Blackmagic's UIManager examples, and
-- the window handler is kept for builds that route it there instead.
local timer = nil
local have_timer = pcall(function()
    timer = ui:Timer({ ID = "PollTimer", Interval = 500 })
end)

local function on_timeout(ev)
    if not RUNNING then return end
    poll()
end

if have_timer and timer then
    pcall(function() win:AddChild(timer) end)
    pcall(function() disp.On.Timeout = on_timeout end)
    pcall(function() win.On.PollTimer.Timeout = on_timeout end)
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

-- Work on the clip under the playhead now, not the one there when the panel
-- opened. If the playhead has moved off every clip, keep the last one.
local function pick_source()
    local previous, label, range = SOURCE_PATH, items.SourceLabel.Text, SOURCE_RANGE
    if refresh_source() then return true end
    if previous then
        SOURCE_PATH = previous
        SOURCE_RANGE = range
        items.SourceLabel.Text = label
        return true
    end
    set_status(items.SourceLabel.Text)
    return false
end

function win.On.AnalyseBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if not pick_source() then return end
    -- Backgrounded like the render: analysis is quick on a short clip but can
    -- take tens of seconds on a long one, and Resolve must not freeze for it.
    clear_status()
    ANALYSING = true
    IMPORTED = false
    set_busy(true)
    set_progress(0.01, "Starting")
    set_status("Analysing. Resolve stays usable while this runs.")
    items.AnalysisLabel.Text = "Analysing..."
    LAUNCHED_AT = os.time()
    run_background("Stages.resolve_smooth", current_args(" --analyse"))
    start_polling()
end

function win.On.SmoothBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if not pick_source() then return end
    clear_status()
    IMPORTED = false
    ANALYSING = false
    set_busy(true)
    set_progress(0.01, "Starting")
    set_status("Working. Resolve stays usable while this runs.")
    LAUNCHED_AT = os.time()
    run_background("Stages.resolve_smooth", current_args(""))
    start_polling()
end

function win.On.RefreshStatusBtn.Clicked(ev)
    -- A finished run's status is gone once handled, but the shell can still
    -- write the exit code after it, so that alone is not a run to report on.
    if not read_status() and not (RUNNING and read_exit_code()) then
        set_status("No run in progress.")
        return
    end
    poll()
end

function win.On.CancelBtn.Clicked(ev)
    if run_in_progress() then
        set_busy(true)
        start_polling()
        set_status("It is still running, so Reset leaves it alone. If its window was closed, "
            .. "Reset works once it has been quiet for 3 minutes.")
        return
    end
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
    -- An update replaces the files a running render is using.
    if run_in_progress() then
        if not auto_mode then
            set_status("A render is still running. Check for updates once it finishes.")
        end
        return
    end
    local args = " --meta-url " .. shell_quote(UPDATE_URL)
    if auto_mode then args = args .. " --auto" end
    if not auto_mode then set_status("Checking for updates...") end
    -- A check that never starts, say because cd failed, must not report the
    -- verdict of the one before.
    os.remove(UPDATE_LOG_PATH)
    local ok = run_blocking("Stages.resolve_update", args, UPDATE_LOG_PATH)
    if auto_mode then return end
    -- The updater's last line is its verdict. An earlier "Updating..." line is
    -- followed by whether it was applied, or why not.
    local line = last_log_line(UPDATE_LOG_PATH)
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
    if run_in_progress() then
        -- Closed mid-run and reopened: follow that run, and import it when done.
        set_busy(true)
        start_polling()
        set_status("A run started earlier is still going. Its progress shows here.")
    elseif AUTO_UPDATE then
        run_update(true)
    end
end

disp:RunLoop()
