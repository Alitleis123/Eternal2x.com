-- Upflow2x  --  upscale and smooth hand-drawn animation
-- Resolve script panel: Workspace > Scripts > Upflow2x

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

-- A file name cut to one line of the Source card, keeping its start and end.
-- Long scene pack names wrapped and pushed the clip's range out of the card.
-- CJK characters count double, and the cut never splits a character.
local function short_name(name, width)
    width = width or 64
    local chars, total = {}, 0
    for ch in name:gmatch("[\1-\127\194-\244][\128-\191]*") do
        chars[#chars + 1] = ch
        total = total + (#ch >= 3 and 2 or 1)
    end
    if total <= width then return name end
    local function take(from, to, step, room)
        local out, used = {}, 0
        for k = from, to, step do
            local w = #chars[k] >= 3 and 2 or 1
            if used + w > room then break end
            used = used + w
            out[#out + 1] = chars[k]
        end
        return out
    end
    local tail = take(#chars, 1, -1, 20)
    local head = take(1, #chars - #tail, 1, width - 23)
    local rest = {}
    for k = #tail, 1, -1 do rest[#rest + 1] = tail[k] end
    return table.concat(head) .. "..." .. table.concat(rest)
end

local root = trim_trailing_sep(script_dir() or "")
-- Launched through Eternal2xLauncher.lua the config lives in Resolve's Utility
-- folder rather than beside this file, so the launcher passes its path down.
local CONF_PATH = _G.ETERNAL2X_CONF or join_path(root, "Eternal2x.conf")
local conf = read_conf(CONF_PATH)
local REPO_ROOT = trim_trailing_sep(conf["repo_root"] or _G.ETERNAL2X_ROOT or root or "")
local PYTHON = conf["python"] or (is_windows() and "python" or "python3")
local UPDATE_URL = conf["update_url"] or ""
-- The repo moved to sidebandstudio, then was renamed Upflow2x. An install
-- keeps the feed it was set up with through every update, and the old ones
-- only work while GitHub redirects them, so they are swapped for the new one.
if UPDATE_URL == "https://raw.githubusercontent.com/Alitleis123/Eternal2x.com/main/update/latest.json"
        or UPDATE_URL == "https://raw.githubusercontent.com/sidebandstudio/Eternal2x.com/main/update/latest.json" then
    UPDATE_URL = "https://raw.githubusercontent.com/sidebandstudio/Upflow2x/main/update/latest.json"
end

-- Workspace > Scripts lists the launcher by its file name. Installs from
-- before the rename have it as Eternal2x.lua beside the config, and updates
-- never touch that file, so the panel renames it once, to Upflow2x.lua.
-- The old one only goes once a whole new one is in place, so a failure along
-- the way leaves the menu entry that works.
local function read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local body = f:read("*a")
    f:close()
    return body
end

-- Every launcher ever installed opens with this line.
local function is_launcher(body)
    local first = body and body:match("^[^\r\n]*") or ""
    return first:find("^%-%- %w+ launcher script installed into Resolve Scripts/") ~= nil
end

local function same_path(a, b)
    local function norm(p)
        p = p:gsub("\\", "/"):gsub("/+", "/")
        return is_windows() and p:lower() or p
    end
    return norm(a) == norm(b)
end

-- A launcher that would run: the header, the call into the panel, and Lua
-- that compiles, so one cut short anywhere is refused.
local function complete_launcher(body)
    return is_launcher(body) and body:find("pcall(dofile, ui_script)", 1, true) ~= nil
        and (loadstring or load)(body) ~= nil
end

local function exists(path)
    if read_file(path) then return true end
    -- An unreadable file is still there. Renaming a file onto itself only
    -- succeeds when it exists, on macOS and Linux.
    return not is_windows() and os.rename(path, path) ~= nil
end

local function rename_launcher()
    if not _G.ETERNAL2X_CONF then return end
    local dir = CONF_PATH:match("^(.*)[/\\][^/\\]*$")
    if not dir then return end
    local old_path, new_path = join_path(dir, "Eternal2x.lua"), join_path(dir, "Upflow2x.lua")
    -- With the config beside this panel, Eternal2x.lua there is the panel.
    if same_path(old_path, join_path(REPO_ROOT, "Installer/Eternal2x.lua")) then return end
    if not is_launcher(read_file(old_path)) then return end
    local existing = read_file(new_path)
    if existing then
        -- Someone else's script, or one cut short: keep the old launcher.
        if not complete_launcher(existing) then return end
    elseif exists(new_path) then
        return
    else
        local launcher = read_file(join_path(REPO_ROOT, "Installer/Eternal2xLauncher.lua"))
        if not complete_launcher(launcher) then return end
        -- Written whole under a name of its own first, so a full disk or a
        -- second Resolve doing the same never finds half a launcher.
        local temp = new_path .. "." .. os.time() .. (tostring({}):gsub("%W", "")) .. ".part"
        local out = io.open(temp, "wb")
        if not out then return end
        local wrote = out:write(launcher)
        local closed = out:close()
        -- Windows refuses to rename onto a file that exists, but macOS
        -- replaces it, so look once more just before.
        if not (wrote and closed) or read_file(temp) ~= launcher or exists(new_path)
                or not os.rename(temp, new_path) then
            os.remove(temp)
            return
        end
        if read_file(new_path) ~= launcher then return end
    end
    os.remove(old_path)
end
pcall(rename_launcher)
local AUTO_UPDATE = parse_bool(conf["auto_update"], true)
local OUTPUT_DIR = conf["output_dir"] or ""
local DO_UPSCALE = parse_bool(conf["upscale"], true)
local DO_INTERPOLATE = parse_bool(conf["interpolate"], true)
-- How big a change Analyse counts as a new drawing, 1 to 9, 5 the default.
local THRESHOLD = math.max(1, math.min(9, math.floor(tonumber(conf["threshold"] or "5") or 5)))
local FORMAT = conf["format"] or "png"

local STATUS_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_status.json")
-- Stop creates this, and the run ends at its next frame.
local STOP_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_status.stop")
-- The frames marked on the clip go to the stage in this file. A long clip's
-- list does not fit on a Windows command line.
local MARKS_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_marks.txt")
local LOG_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_last_run.log")
-- The update check runs each time the panel opens, so it logs apart from
-- renders. Sharing a log wiped the render's, the one worth sending in.
local UPDATE_LOG_PATH = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_update.log")
local RUN_SCRIPT = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_run.cmd")
-- Opens Explorer at a render, for the same reason the run goes through a
-- script: see Show in Folder.
local REVEAL_SCRIPT = join_path(REPO_ROOT ~= "" and REPO_ROOT or root, ".eternal2x_reveal.cmd")
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
    f:write("upscale=" .. (DO_UPSCALE and "true" or "false") .. "\n")
    f:write("interpolate=" .. (DO_INTERPOLATE and "true" or "false") .. "\n")
    f:write("threshold=" .. tostring(THRESHOLD) .. "\n")
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
-- Resolve counts source frames from the start of the file, at the media's
-- own rate, but the source time from the media's start timecode, so on
-- footage that starts at 01:00:00:00 the times are an hour out. The frames
-- are used, and without an end frame the times' difference, anchored on the
-- start frame. The end frame is the last one the cut uses: on Resolve Studio
-- 20.3.1 a 119 frame cut from 1200 reported 1318. Where the timeline's rate
-- differs from the media's, the end time fell short of it, and a render from
-- the times came out a timeline frame short of the cut.
local function clip_range(item, props, rate)
    local function call(name)
        local ok, value = pcall(function() return item[name](item) end)
        return ok and tonumber(value) or nil
    end
    local fps = tonumber(tostring(props["FPS"] or ""):match("^%s*([%d%.]+)"))
    local f0 = call("GetSourceStartFrame")
    if not (fps and fps > 0 and f0) then return nil end
    local start = f0 / fps
    local f1 = call("GetSourceEndFrame")
    -- At the timeline's own rate Resolve reports the end frame as the last one
    -- used at 60 fps but one past it at 23.976 (20.3.1), so the clip's length
    -- settles it. A retimed clip's length is no guide and keeps the end frame.
    local length = call("GetDuration")
    if f1 and length and rate and math.abs(rate - fps) < 0.001 and math.abs(f1 - f0 - length) <= 1 then
        return { start = start, finish = (f0 + length) / fps }
    end
    if f1 and f1 >= f0 then return { start = start, finish = (f1 + 1) / fps } end
    local t0, t1 = call("GetSourceStartTime"), call("GetSourceEndTime")
    if t0 and t1 and t1 > t0 then return { start = start, finish = start + (t1 - t0) } end
    return nil
end

-- A file's name without its folder or extension.
local function stem_of(path)
    return ((path:match("([^/\\]+)$") or path):gsub("%.[^.]*$", ""))
end

-- What a render is called: its folder for a sequence of frames, its file
-- name for one file. nil for a path Eternal2x does not write.
local function render_name(path)
    return path:match("([^/\\]+)[/\\]frame_[%d%[%]%-]+%.png$")
        or path:match("([^/\\]+)%.mkv$") or path:match("([^/\\]+)%.mp4$")
end

-- Whether `name` is what Stages/resolve_smooth.py calls a render of a file
-- whose name is `stem`: <stem>, a range _f<a>-<b> if it was trimmed, _<n>x
-- if it was slowed, then _smooth, then _<nn> for the second render and on.
local function rendered_from(name, stem)
    if stem == "" or name:sub(1, #stem) ~= stem then return false end
    local rest = name:sub(#stem + 1):gsub("^_f%d+%-%d+", "", 1):gsub("^_%dx", "", 1)
    return rest == "_smooth" or rest:match("^_smooth_%d%d+$") ~= nil
end

-- How many times longer than its clip a render `name` of a file `stem` is,
-- from the _<n>x that rendered_from passes over.
local function slowed_by(name, stem)
    local rest = name:sub(#stem + 1):gsub("^_f%d+%-%d+", "", 1)
    return tonumber(rest:match("^_(%d)x_smooth")) or 1
end

-- The clip a render under the playhead was made from: on a track below it,
-- starting on the same frame, its file the one the render is named after.
-- Earlier renders stacked in between are passed over. nil if there is none.
local function original_under(timeline, item, name)
    local ok, found = pcall(function()
        local start = item:GetStart()
        for track = tonumber(item:GetTrackTypeAndIndex()[2]) - 1, 1, -1 do
            for _, it in ipairs(timeline:GetItemListInTrack("video", track) or {}) do
                local mpi = it:GetStart() == start and it:GetMediaPoolItem()
                local file = mpi and (mpi:GetClipProperty() or {})["File Path"]
                if file and rendered_from(name, stem_of(file)) then return it end
            end
        end
    end)
    return ok and found or nil
end

-- The playhead as a timeline frame, the unit GetStart counts in. Resolve gives
-- it only as timecode, which numbers frames at the nominal rate, 24 for
-- 23.976. Drop-frame timecode, written with ;, skips two numbers a minute
-- (four at 59.94) except every tenth minute.
local function playhead_frame(timeline)
    local h, m, s, sep, f = tostring(timeline:GetCurrentTimecode() or "")
        :match("^(%d+):(%d+):(%d+)([:;])(%d+)$")
    if not h then return nil end
    local rate = tonumber(tostring(timeline:GetSetting("timelineFrameRate") or ""):match("[%d.]+"))
    local nominal = math.floor((rate or 24) + 0.5)
    local minutes = tonumber(h) * 60 + tonumber(m)
    local frame = (minutes * 60 + tonumber(s)) * nominal + tonumber(f)
    if sep == ";" then
        frame = frame - math.floor(nominal / 15) * (minutes - math.floor(minutes / 10))
    end
    return frame
end

local function clip_is_on(it)
    return not (it.GetClipEnabled and it:GetClipEnabled() == false)
end

local function track_is_on(timeline, track)
    return not (timeline.GetIsTrackEnabled and timeline:GetIsTrackEnabled("video", track) == false)
end

-- GetCurrentVideoItem is meant to be the clip that shows under the playhead,
-- and in Resolve 20.3.1 it passed over a video clip or track switched off.
-- It still returned a render switched off on the track above its original.
-- So a clip it returns switched off is passed over for the top one switched
-- on, on a track switched on. With none, the top clip is kept.
local function shown_item(timeline, top)
    local kind = top.GetTrackTypeAndIndex and top:GetTrackTypeAndIndex()
    local track = kind and tonumber(kind[2])
    if clip_is_on(top) and (not track or track_is_on(timeline, track)) then return top end
    local frame = playhead_frame(timeline)
    if not frame then return top end
    for n = timeline:GetTrackCount("video"), 1, -1 do
        if track_is_on(timeline, n) then
            for _, it in ipairs(timeline:GetItemListInTrack("video", n) or {}) do
                if it:GetStart() <= frame and frame < it:GetEnd() and clip_is_on(it) then
                    return it
                end
            end
        end
    end
    return top
end

-- The rates Resolve shows for 24000/1001 and its kin. It counts with the
-- exact rate, which over a long clip comes out a frame different. Any other
-- rate, 23.98 say, is what it says.
local NTSC = { { 23.976, 24000 / 1001 }, { 29.97, 30000 / 1001 }, { 47.952, 48000 / 1001 },
               { 59.94, 60000 / 1001 }, { 119.88, 120000 / 1001 } }

local function exact_rate(text)
    local r = tonumber(tostring(text or ""):match("^%s*([%d%.]+)"))
    if not r then return nil end
    for _, pair in ipairs(NTSC) do
        if math.abs(r - pair[1]) < 1e-6 then return pair[2] end
    end
    return r
end

-- How the clip's markers line up with its frames. Resolve counts them in
-- timeline frames from the start of the media, not from the clip's left edge,
-- so a trimmed clip's first frame is at its left offset. A clip at another
-- rate than the timeline has `per` source frames to each timeline frame.
-- Measured on Resolve 20.3.1. Guessed bounds would read every marker as the
-- wrong frame, so a clip whose bounds Resolve will not give is `unreadable`.
local function marked_clip(item, timeline, props)
    local ok, clip = pcall(function()
        local fps, rate = exact_rate(props["FPS"]), exact_rate(timeline:GetSetting("timelineFrameRate"))
        if not (fps and rate and fps > 0 and rate > 0) then error("no frame rates") end
        local per = fps / rate
        local origin
        if item.GetLeftOffset then
            origin = tonumber(item:GetLeftOffset())
        else
            origin = tonumber(item:GetSourceStartFrame()) / per
        end
        local length = tonumber(item:GetDuration())
        if not (origin and length) then error("no clip bounds") end
        return { item = item, origin = math.floor(origin + 0.5), per = per, length = length }
    end)
    return ok and clip or { item = item, unreadable = true }
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
    -- A failed lookup keeps the top clip, as before it was made.
    local looked, shown = pcall(shown_item, timeline, item)
    if looked and shown then item = shown end

    local mpi = item:GetMediaPoolItem()
    if not mpi then return nil, "That item has no source media." end
    local props = mpi:GetClipProperty() or {}
    local path = props["File Path"]
    if not path or path == "" then return nil, "Could not read the clip's file path." end

    -- A render placed above its original is the top clip under the playhead
    -- from then on, even switched off. Smoothing it would smooth the render
    -- again, so the clip it was made from is used instead.
    local name = render_name(path)
    local below = name and original_under(timeline, item, name)
    local render = nil
    if below then
        -- Markers an editor puts on the render, the clip they see, count too.
        -- A slowed one shows each frame of the original that many frames on.
        render = marked_clip(item, timeline, props)
        item = below
        props = below:GetMediaPoolItem():GetClipProperty() or {}
        path = props["File Path"]
        render.slow = slowed_by(name, stem_of(path or ""))
    end

    -- Where the clip sits, so the result can go on a track above it, lined up
    -- with it. Without these calls, as before Resolve 18, the result goes at
    -- the end of the timeline instead.
    local place = nil
    pcall(function()
        local kind = item:GetTrackTypeAndIndex()
        place = { track = tonumber(kind[2]), start = tonumber(item:GetStart()),
                  duration = tonumber(item:GetDuration()), timeline = timeline:GetUniqueId(),
                  item = item:GetUniqueId() }
        place.source_start = tonumber(item:GetSourceStartFrame())
    end)
    if place and not (place.track and place.start and place.duration and place.timeline
                      and place.item) then
        place = nil
    end
    local rate = nil
    pcall(function() rate = tonumber(timeline:GetSetting("timelineFrameRate")) end)
    local range = clip_range(item, props, rate)
    local clip = marked_clip(item, timeline, props)
    -- Without the part of the file a trimmed clip uses, the stage reads the
    -- whole file from its first frame, and every marker would land on the
    -- wrong frame of it.
    if not range and not clip.unreadable and clip.origin ~= 0 then clip.unreadable = true end
    clip.also = render
    return path, nil, range, place, clip
end

-- Any API call above can raise, and this runs while the panel opens, where an
-- error would stop the window from ever appearing.
local function selected_clip_path()
    local ok, path, err, range, place, item = pcall(find_clip_path)
    if not ok then return nil, "Could not read the clip from Resolve: " .. tostring(path) end
    return path, err, range, place, item
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
        -- redirect stderr instead of writing 2. Unwritten, the script left
        -- from the last run would start in its place, so nothing starts.
        local f = io.open(RUN_SCRIPT, "wb")
        if not f then return nil end
        f:write("@echo off\r\nchcp 65001 >nul\r\n", (cd:gsub("%%", "%%%%")), "\r\n",
                ((run .. redirect):gsub("%%", "%%%%")), "\r\n",
                ">", (shell_quote(EXIT_PATH):gsub("%%", "%%%%")), " echo %ERRORLEVEL%\r\n")
        f:close()
        -- /MIN gives the run its own minimised window. With /B it shared the
        -- console os.execute opens, which stayed on screen for the whole
        -- render, and closing it killed the render without a word.
        -- It starts in the plugin folder and runs the script by name. Given
        -- the full path, `cmd /c` strips its quotes when it holds & and
        -- splits there, and `call` would double a ^ in it.
        local dir = (REPO_ROOT ~= "" and REPO_ROOT or root):gsub("/", "\\")
        return "start \"Upflow2x\" /D " .. shell_quote(dir) .. " /MIN cmd /c .\\"
            .. basename(RUN_SCRIPT)
    end
    -- The subshell backgrounds the whole run, and records how it ended.
    return "(" .. cd .. " && " .. run .. redirect .. "; echo $? > "
        .. shell_quote(EXIT_PATH) .. ") &"
end

-- cmd expands %NAME% in a command line even inside quotes, and nothing
-- escapes it there, so a plugin folder whose path holds a % cannot be handed
-- to it. The batch file the render runs from doubles them, but `start /D` and
-- `cd /d` see the path as typed.
local function launch_problem()
    if is_windows() and (REPO_ROOT ~= "" and REPO_ROOT or root):find("%%") then
        return "The plugin folder's path contains %, which Windows cannot start Python "
            .. "from. Move the Upflow2x folder to a path without %, then re-run the installer."
    end
    return nil
end

-- On Windows, os.execute runs its command in a console window that flashes up
-- for as long as the command takes, as on every update check. Started with
-- CreateProcessW and CREATE_NO_WINDOW, cmd gets a console nobody sees, and
-- the programs it runs share it. The command line is the one os.execute
-- builds, so quoting is unchanged, and a render still opens its own
-- minimised window through `start`. UTF-16 also carries non-ASCII paths that
-- os.execute's ANSI code page cannot. Without LuaJIT's ffi, os.execute runs.
local hidden_execute = nil
local hidden_start = nil
if is_windows() then
    pcall(function()
        local ffi = require("ffi")
        -- Declared once per Lua state; a second cdef of the same names fails.
        if not pcall(ffi.typeof, "E2X_STARTUPINFOW") then
            ffi.cdef[[
                typedef struct {
                    uint32_t cb; void *reserved, *desktop, *title;
                    uint32_t x, y, xsize, ysize, xchars, ychars, fill, flags;
                    uint16_t show, reserved2; void *reserved3, *input, *output, *error;
                } E2X_STARTUPINFOW;
                typedef struct { void *process, *thread; uint32_t pid, tid; } E2X_PROCESS_INFORMATION;
                int CreateProcessW(const wchar_t *app, wchar_t *cmdline, void *pattr, void *tattr,
                                   int inherit, uint32_t flags, void *env, const wchar_t *cwd,
                                   E2X_STARTUPINFOW *si, E2X_PROCESS_INFORMATION *pi);
                uint32_t WaitForSingleObject(void *handle, uint32_t ms);
                int GetExitCodeProcess(void *process, uint32_t *code);
                int CloseHandle(void *handle);
                int MultiByteToWideChar(uint32_t page, uint32_t flags, const char *s, int n,
                                        wchar_t *out, int size);
            ]]
        end
        local k32 = ffi.load("kernel32")
        hidden_execute = function(cmd)
            local line = (os.getenv("ComSpec") or "cmd.exe") .. " /c " .. cmd
            local size = k32.MultiByteToWideChar(65001, 0, line, #line, nil, 0)
            if size <= 0 then return nil end
            local wide = ffi.new("wchar_t[?]", size + 1)
            k32.MultiByteToWideChar(65001, 0, line, #line, wide, size)
            local si = ffi.new("E2X_STARTUPINFOW")
            si.cb = ffi.sizeof(si)
            local pi = ffi.new("E2X_PROCESS_INFORMATION")
            local code = ffi.new("uint32_t[1]")
            if k32.CreateProcessW(nil, wide, nil, nil, 0, 0x08000000, nil, nil, si, pi) == 0 then
                return nil
            end
            -- Started, so it must not run a second time through os.execute,
            -- whatever goes wrong while waiting for it.
            pcall(function()
                k32.WaitForSingleObject(pi.process, 0xFFFFFFFF)
                k32.GetExitCodeProcess(pi.process, code)
            end)
            k32.CloseHandle(pi.thread)
            k32.CloseHandle(pi.process)
            return true, tonumber(code[0])
        end
        -- Starts `cmd` in `dir` and leaves it running, with no window.
        hidden_start = function(cmd, dir)
            local function wide(s)
                local size = k32.MultiByteToWideChar(65001, 0, s, #s, nil, 0)
                if size <= 0 then return nil end
                local out = ffi.new("wchar_t[?]", size + 1)
                k32.MultiByteToWideChar(65001, 0, s, #s, out, size)
                return out
            end
            local line = wide((os.getenv("ComSpec") or "cmd.exe") .. " /c " .. cmd)
            local cwd = wide(dir)
            if not line or not cwd then return false end
            local si = ffi.new("E2X_STARTUPINFOW")
            si.cb = ffi.sizeof(si)
            local pi = ffi.new("E2X_PROCESS_INFORMATION")
            if k32.CreateProcessW(nil, line, nil, nil, 0, 0x08000000, nil, cwd, si, pi) == 0 then
                return false
            end
            k32.CloseHandle(pi.thread)
            k32.CloseHandle(pi.process)
            return true
        end
    end)
end

-- Runs a command and returns its exit code, like os.execute.
local function execute(cmd)
    if hidden_execute then
        local ok, started, code = pcall(hidden_execute, cmd)
        if ok and started then return code end
    end
    return os.execute(cmd)
end

local function run_blocking(module_name, args, log)
    local cmd = build_command(module_name, args, false, log)
    print("[Upflow2x] " .. cmd)
    local ok = execute(cmd)
    return (ok == true or ok == 0)
end

-- Runs a script in the plugin folder by name, as a render does. With no
-- window, there is none to close by mistake, which would end the run without
-- a word. Where that cannot start, `fallback` opens a minimised one.
local function start_script(script, fallback)
    if hidden_start then
        local dir = (REPO_ROOT ~= "" and REPO_ROOT or root):gsub("/", "\\")
        local ok, started = pcall(hidden_start, ".\\" .. basename(script), dir)
        if ok and started then return true end
    end
    local ok = execute(fallback)
    return (ok == true or ok == 0)
end

-- True once the run is on its way. Python reports from then on.
local function run_background(module_name, args)
    local cmd = build_command(module_name, args, true)
    if not cmd then return false end
    print("[Upflow2x] " .. cmd)
    if is_windows() then return start_script(RUN_SCRIPT, cmd) end
    local ok = execute(cmd)
    return (ok == true or ok == 0)
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
        marks    = json_string(s, "marks") or "",
        upscaled = s:match('"upscaled"%s*:%s*(%a+)') == "true",
        slow_motion = tonumber(s:match('"slow_motion"%s*:%s*(%d+)')) or 1,
        updated  = tonumber(s:match('"updated"%s*:%s*([%d%.eE]+)')),
        fps      = tonumber(s:match('"fps"%s*:%s*([%d%.]+)')) or 0,
        done     = s:match('"done"%s*:%s*(%a+)') == "true",
        ok       = s:match('"ok"%s*:%s*(%a+)') == "true",
    }
end

local function clear_status()
    os.remove(STATUS_PATH)
    os.remove(EXIT_PATH)
    os.remove(STOP_PATH)
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

-- No AVI: Resolve refuses its lossless FFV1 there, so that file is MKV. A
-- saved avi from an older version falls back to the image sequence.
local FORMATS = { "png", "mp4", "mkv" }
local FORMAT_LABELS = { "Image sequence", "MP4", "MKV" }

local function index_of(list, value)
    for i, v in ipairs(list) do
        if v == value then return i - 1 end
    end
    return 0
end

-- Resolve's own greys, so the panel sits beside the Inspector without
-- standing out, and the amber from upflow2x.com for what is pressed or on.
local C = {
    panel = "#28282e", field = "#1d1d21", raise = "#37363c", hover = "#423f47",
    line = "#45444b", hair = "#35343a",
    text = "#dcdcd8", soft = "#a3a2a8", mute = "#7a7b80",
    amber = "#f2a93b", amber_hover = "#f6bd63", amber_down = "#d9932c", on_amber = "#17171a",
}
-- "$name" in a style sheet becomes that colour.
local function themed(sheet)
    return (sheet:gsub("%$([%w_]+)", C))
end

-- Resolve's UI Manager ignores #name selectors in a window's style sheet, so
-- every widget that looks different from the rest carries its own.
local TITLE = themed("QLabel { font-size: 17px; font-weight: 600; color: $text; }")
local BADGE = themed([[QLabel { background-color: $amber; color: $on_amber; border-radius: 4px;
    padding: 1px 5px; font-family: Consolas, Menlo, monospace; font-size: 10px; font-weight: 700; }]])
local VERSION = themed("QLabel { color: $mute; font-size: 11px; }")
local HAIRLINE = themed("QLabel { background-color: $hair; }")
local INFO = themed([[QLabel { color: $soft; border: 1px solid $line; border-radius: 8px;
    font-family: Georgia, serif; font-size: 10px; font-weight: 700; }]])
local CARD = themed([[QLabel { background-color: $field; border: 1px solid $hair;
    border-radius: 6px; padding: 7px 10px; color: $text; font-size: 12px; }]])
local NOTE = themed("QLabel { color: $soft; font-size: 11px; }")
local FIELD_LABEL = themed("QLabel { color: $soft; font-size: 12px; }")
local META = themed("QLabel { color: $mute; font-size: 10px; }")
local PROGRESS = themed("QLabel { color: $soft; font-size: 11px; font-family: Consolas, Menlo, monospace; }")
local START = themed([[
    QPushButton { background-color: $amber; border: none; border-radius: 8px;
        color: $on_amber; font-size: 15px; font-weight: 700; }
    QPushButton:hover { background-color: $amber_hover; }
    QPushButton:pressed { background-color: $amber_down; }
    QPushButton:disabled { background-color: $raise; color: $mute; }
]])
-- On or off at a glance: the whole tile lights up when it is on. They are
-- checkable buttons, since a checkbox only takes a click on its dot and words.
local TOGGLE = themed([[
    QPushButton { background-color: $raise; border: 1px solid $line; border-radius: 8px;
        padding: 0px 12px; min-height: 34px; font-size: 13px; color: $soft; }
    QPushButton:hover { background-color: $hover; }
    QPushButton:checked { border-color: $amber; color: $text; background-color: rgba(242, 169, 59, 40); }
    QPushButton:checked:hover { background-color: rgba(242, 169, 59, 64); }
]])
local BUTTON = themed([[
    QPushButton { background-color: $raise; border: 1px solid $line; border-radius: 6px;
        min-height: 30px; padding: 3px 10px; font-size: 12px; color: $text; }
    QPushButton:hover { background-color: $hover; }
    QPushButton:pressed { background-color: $field; }
    QPushButton:disabled { background-color: $panel; border-color: $hair; color: $mute; }
]])
local LINK = themed([[
    QPushButton { background-color: transparent; border: none; padding: 2px 4px;
        min-height: 18px; font-size: 11px; color: $soft; }
    QPushButton:hover { color: $amber; }
    QPushButton:disabled { color: $mute; }
]])
local WINDOW = themed([[
    QWidget { background-color: $panel; color: $text; font-size: 12px; }
    QComboBox {
        background-color: $field; border: 1px solid $hair; border-radius: 5px;
        padding: 4px 8px; min-height: 18px; color: $text;
    }
    QComboBox:hover { border-color: $line; }
    QComboBox QAbstractItemView {
        background-color: $field; border: 1px solid $line; outline: none;
        selection-background-color: $amber; selection-color: $on_amber;
    }
    QSlider::groove:horizontal { height: 3px; border-radius: 1px; background-color: $field; }
    QSlider::sub-page:horizontal { border-radius: 1px; background-color: $amber; }
    QSlider::handle:horizontal {
        width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background-color: $text;
    }
    QSlider::handle:horizontal:hover { background-color: #ffffff; }
    QToolTip { background-color: $field; color: $text; border: 1px solid $line; padding: 5px; }
]])
-- A thin bar, amber up to how far the run has got. Empty, it keeps its
-- space but shows nothing, so the panel doesn't jump when a run starts.
local function bar_style(fraction)
    if not fraction then
        return "QLabel { background-color: transparent; }"
    end
    local f = math.max(0.001, math.min(0.998, fraction))
    -- Qt reads only a decimal point, and some locales format with a comma.
    local function stop(value) return (string.format("%.3f", value):gsub(",", ".")) end
    return themed(string.format([[QLabel { border-radius: 2px; background-color: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 $amber, stop: %s $amber,
        stop: %s $field, stop: 1 $field); }]], stop(f), stop(f + 0.001)))
end

local RENDER_LABEL = "\xE2\x96\xB6  Render"
local ANALYSIS_HINT = "Mark the drawings with M, or let Analyse do it."

-- What each part does, shown when the pointer rests on it, so the panel
-- itself only needs names.
local TIPS = {
    clip = "Upflow2x works on the clip under the playhead, since Resolve doesn't tell scripts which clip is selected. This box follows the playhead. With the playhead in a gap, it keeps the last clip.",
    detection = "Analyse finds the real drawings and puts a marker on each one. Delete a marker to leave its frame out, or add one with M. Render uses the marked frames, and finds the drawings itself on a clip with none.",
    threshold = "How big a change Analyse counts as a new drawing. Lower catches small moves, like only a mouth. Higher counts only big ones.",
    render = "Render draws on your GPU and puts the result on the track above the clip. Resolve stays usable while it runs.",
    upscale = "Draw every frame at 2x with Real-ESRGAN's anime model, on the GPU on Windows. Without a GPU, Resolve's Super Scale does it.\nIt shows on a timeline bigger than the clip.",
    interp = "Draw the missing in-betweens with the RIFE network, on the GPU on Windows. Its first use downloads about 50 MB.\nThe render plays at a third of the clip's speed, ready to time remap.\nOff keeps every drawing on its frame and the clip's length, and draws nothing, to upscale only.",
    format = "Image sequence is lossless and imports most reliably. MP4 is one small file but lossy, and MKV is one lossless file. All are 8-bit RGB.",
}

local function info(tip)
    return ui:Label{ Text = "i", ToolTip = tip, StyleSheet = INFO, Weight = 0,
                     Alignment = { AlignCenter = true },
                     MinimumSize = { 16, 16 }, MaximumSize = { 16, 16 } }
end

-- Name, control, (i): the same three columns on every row.
local function row(name, control, tip)
    return ui:HGroup{ Weight = 0, Spacing = 8,
        ui:Label{ Text = name, StyleSheet = FIELD_LABEL, Weight = 0.2,
                  Alignment = { AlignLeft = true, AlignVCenter = true } },
        control,
        info(tip),
    }
end

-- Top to bottom in the order you use it: the clip, Analyse and its
-- threshold, the two switches and the output, then Render. All in one
-- window, so nothing opens on top of it.
local win = disp:AddWindow({
    ID = "Eternal2x",
    WindowTitle = "Upflow2x  v" .. CURRENT_VERSION,
    Geometry = { 100, 100, 380, 506 },
    StyleSheet = WINDOW,
}, ui:VGroup{
    Spacing = 8,

    ui:HGroup{ Weight = 0, Spacing = 6,
        ui:Label{ ID = "Title", Text = "Upflow", StyleSheet = TITLE, Weight = 0 },
        ui:Label{ ID = "Badge", Text = "2X", StyleSheet = BADGE, Weight = 0,
                  Alignment = { AlignCenter = true } },
        ui:HGap(0, 1),
        ui:Label{ ID = "SubTitle", Text = "v" .. CURRENT_VERSION, StyleSheet = VERSION, Weight = 0,
                  Alignment = { AlignRight = true, AlignVCenter = true } },
        ui:Button{ ID = "UpdateBtn", Text = "Check for Updates", StyleSheet = LINK, Weight = 0 },
    },

    ui:Label{ ID = "SourceLabel", Text = "No clip under the playhead.", StyleSheet = CARD,
              WordWrap = true, MinimumSize = { 0, 46 }, ToolTip = TIPS.clip },

    ui:Button{ ID = "AnalyseBtn", Text = "Analyse", StyleSheet = BUTTON, ToolTip = TIPS.detection },
    row("Threshold", ui:HGroup{ Weight = 0.8, Spacing = 8,
        ui:Slider{ ID = "ThresholdSlider", Weight = 1, Minimum = 1, Maximum = 9, PageStep = 1,
                   Value = THRESHOLD, Orientation = "Horizontal" },
        ui:Label{ ID = "ThresholdValue", Text = tostring(THRESHOLD), StyleSheet = FIELD_LABEL,
                  Weight = 0, MinimumSize = { 10, 0 },
                  Alignment = { AlignRight = true, AlignVCenter = true } },
    }, TIPS.threshold),
    ui:Label{ ID = "AnalysisLabel", Text = ANALYSIS_HINT, StyleSheet = NOTE, WordWrap = true },

    ui:Label{ StyleSheet = HAIRLINE, MinimumSize = { 0, 1 }, MaximumSize = { 16777215, 1 } },

    ui:HGroup{ Weight = 0, Spacing = 8,
        ui:Button{ ID = "UpscaleCB", Text = "Upscaling", Checkable = true, Checked = DO_UPSCALE,
                   StyleSheet = TOGGLE, ToolTip = TIPS.upscale },
        ui:Button{ ID = "InterpCB", Text = "Interpolate", Checkable = true,
                   Checked = DO_INTERPOLATE, StyleSheet = TOGGLE, ToolTip = TIPS.interp },
    },
    row("Output", ui:ComboBox{ ID = "FormatCombo", Weight = 0.8 }, TIPS.format),
    ui:Button{ ID = "SmoothBtn", Text = RENDER_LABEL, StyleSheet = START, ToolTip = TIPS.render,
               MinimumSize = { 0, 44 }, MaximumSize = { 16777215, 44 } },

    ui:Label{ ID = "ProgressBar", Text = "", StyleSheet = bar_style(nil),
              MinimumSize = { 0, 4 }, MaximumSize = { 16777215, 4 } },
    ui:Label{ ID = "Progress", Text = "", StyleSheet = PROGRESS, WordWrap = true },
    -- Three lines, as long as a finished render's message runs. Resolve does
    -- not grow a wrapped label to fit, so two lines cut it off top and bottom.
    ui:Label{ ID = "Status", Text = "Ready.", StyleSheet = NOTE, WordWrap = true,
              MinimumSize = { 0, 46 } },

    ui:HGroup{ Weight = 0, Spacing = 2,
        ui:Button{ ID = "CompareBtn", Text = "Show Original", StyleSheet = LINK, Enabled = false, Weight = 0,
                   ToolTip = "Switch the render off and on to compare it with the original under it." },
        ui:Button{ ID = "FolderBtn", Text = "Show in Folder", StyleSheet = LINK, Enabled = false, Weight = 0,
                   ToolTip = "Open the folder the render was saved to." },
        ui:HGap(0, 1),
        ui:Button{ ID = "RefreshStatusBtn", Text = "Refresh Progress", StyleSheet = LINK, Weight = 0,
                   ToolTip = "Read how far the run has got, if the panel stopped updating." },
        ui:Button{ ID = "CancelBtn", Text = "Reset", StyleSheet = LINK, Weight = 0,
                   ToolTip = "Clear a run that stopped without finishing." },
    },
    ui:Label{ ID = "Meta", Text = "", StyleSheet = META, WordWrap = true },
})


local items = win:GetItems()

for _, label in ipairs(FORMAT_LABELS) do items.FormatCombo:AddItem(label) end
items.FormatCombo.CurrentIndex = index_of(FORMATS, FORMAT)

-- ---------------------------------------------------------------------------
-- state
-- ---------------------------------------------------------------------------

local SOURCE_PATH = nil
local SOURCE_RANGE = nil
-- The clip on the timeline, whose markers say which frames count, and the one
-- the running analysis will mark, as marked_clip describes them.
local SOURCE_ITEM = nil
local ANALYSED_ITEM = nil
-- Where the source clip sits on the timeline, and where the running render's
-- clip sat when it started, since the playhead can move while it renders.
local SOURCE_PLACE = nil
local RUN_PLACE = nil
-- The last render on disk, and the clip it became on the timeline.
local LAST_OUTPUT = nil
local LAST_ITEM = nil
local RUNNING = false
-- When this panel started its run, which counts as the first heartbeat until
-- Python, a second or two later, writes its own.
local LAUNCHED_AT = nil
local IMPORTED = false
local ANALYSING = false

local function set_status(msg)
    items.Status.Text = msg or ""
    -- The whole message, for one too long for the box.
    items.Status.ToolTip = msg or ""
    print("[Upflow2x] " .. (msg or ""))
end

-- A stage at 0% is still shown: installing the AI runtime on first use stays
-- at 0% for as long as pip takes, and a blank bar then looks stuck.
local function set_progress(fraction, stage)
    if not fraction or (fraction <= 0 and (stage or "") == "") then
        items.Progress.Text = ""
        items.ProgressBar.StyleSheet = bar_style(nil)
        return
    end
    fraction = math.min(1, fraction)
    items.ProgressBar.StyleSheet = bar_style(fraction)
    items.Progress.Text = string.format("%d%%  %s", math.floor(fraction * 100 + 0.5), stage or "")
end

local function set_busy(busy)
    RUNNING = busy
    items.SmoothBtn.Enabled = not busy
    items.AnalyseBtn.Enabled = not busy
    items.UpdateBtn.Enabled = not busy
    items.SmoothBtn.Text = busy and "Working..." or RENDER_LABEL
    items.CancelBtn.Text = busy and "Stop" or "Reset"
    items.CancelBtn.ToolTip = busy and "End the run now. Frames it has written stay in its folder."
        or "Clear a run that stopped without finishing."
end

-- Seconds as text that Python's float() reads, whatever the locale.
local function seconds(value)
    return (string.format("%.6f", value):gsub(",", "."))
end

local function range_text(range)
    if not range then return "Whole file." end
    return (string.format("%.2f s long, from %.2f s of the file.",
                          range.finish - range.start, range.start)
            :gsub("(%d),(%d)", "%1.%2"))
end

-- The timer reads the clip twice a second, and a tip set again closes under
-- the pointer, so the card is only written when it changes.
local function show_source(text, tip)
    if items.SourceLabel.Text ~= text then items.SourceLabel.Text = text end
    if items.SourceLabel.ToolTip ~= tip then items.SourceLabel.ToolTip = tip end
end

-- With keep, a playhead in a gap leaves the last clip, and the card, as they are.
local function refresh_source(keep)
    local path, err, range, place, item = selected_clip_path()
    if not path then
        if keep and SOURCE_PATH then return false end
        SOURCE_PATH = nil
        SOURCE_RANGE = nil
        SOURCE_PLACE = nil
        SOURCE_ITEM = nil
        show_source(err or "No clip under the playhead.", TIPS.clip)
        return false
    end
    -- Another clip, even one cut from the same part of the same file, or
    -- another part of the file, makes the last analysis say nothing about it.
    local was, at = SOURCE_RANGE, SOURCE_PLACE
    if path ~= SOURCE_PATH or (range and range.start) ~= (was and was.start)
            or (range and range.finish) ~= (was and was.finish)
            or (place and place.item) ~= (at and at.item)
            or (place and place.timeline) ~= (at and at.timeline) then
        items.AnalysisLabel.Text = ANALYSIS_HINT
        items.AnalysisLabel.ToolTip = ""
    end
    SOURCE_PATH = path
    SOURCE_RANGE = range
    SOURCE_PLACE = place
    SOURCE_ITEM = item
    show_source(short_name(basename(path)) .. "\n" .. range_text(range), TIPS.clip .. "\n\n" .. path)
    return true
end

-- Eternal2x's own markers carry this, so analysing again replaces them and
-- leaves every marker the editor placed.
local MARK_TAG = "eternal2x"

-- The frames marked on the clip, as source frames from its first frame,
-- sorted, with those marked on a render of it stacked above. Every marker on
-- the clip counts, whatever its colour. Markers on the timeline's ruler do
-- not, since editors keep beat markers there. A marker reads as the source
-- frame its timeline frame shows. nil if Resolve would not say where they are.
local function clip_marks(clip)
    if not clip then return {} end
    local out, seen = {}, {}
    for _, c in ipairs({ clip, clip.also }) do
        local ok = pcall(function()
            local markers = c.item:GetMarkers()
            if type(markers) ~= "table" then error("no markers table") end
            for key in pairs(markers) do
                local t = tonumber(key)
                if t then
                    if clip.unreadable or c.unreadable then error("no clip bounds") end
                    -- In timeline frames from the clip's first frame, which the
                    -- render shares with its original, then in the original's frames.
                    -- A slowed render runs its own length, longer than the
                    -- original's, and shows each of its frames that many on.
                    local slow = c.slow or 1
                    t = math.floor(t + 0.5) - c.origin
                    if t >= 0 and t < (slow > 1 and c.length or clip.length) then
                        local f = math.floor(t / slow * clip.per + 1e-6)
                        if not seen[f] then
                            seen[f] = true
                            out[#out + 1] = f
                        end
                    end
                end
            end
        end)
        if not ok then return nil end
    end
    table.sort(out)
    return out
end

-- Put a marker on each frame Analyse found, given as source frames from the
-- clip's first frame, after taking down the ones it put there last time. Each
-- goes on the first timeline frame that shows it, so it reads back as that
-- frame or a later one of the same drawing, never the drawing before. On a
-- timeline slower than the clip, two found frames can fall on one timeline
-- frame, which then reads as the later one. The earlier is counted instead of
-- marked. Returns how many it placed and how many shared a frame.
local function mark_drawings(clip, marks)
    local placed, shared, used = 0, 0, {}
    if clip.unreadable then return placed, shared end
    pcall(function()
        local item = clip.item
        local markers = item:GetMarkers()
        if type(markers) ~= "table" then return end
        for key, info in pairs(markers) do
            if type(info) == "table" and info.customData == MARK_TAG then
                item:DeleteMarkerAtFrame(tonumber(key))
            end
        end
        for f in marks:gmatch("%d+") do
            local t = math.ceil(tonumber(f) / clip.per - 1e-6)
            if used[t] then
                shared = shared + 1
            elseif t < clip.length then
                used[t] = true
                if item:AddMarker(clip.origin + t, "Yellow", "Upflow2x",
                                  "Delete this marker to leave the frame out.", 1, MARK_TAG) then
                    placed = placed + 1
                end
            end
        end
    end)
    return placed, shared
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
    local fmt = FORMATS[(items.FormatCombo.CurrentIndex or 0) + 1] or "png"
    args = args .. " --format " .. fmt
    -- --network-upscale tells the stage this panel skips Super Scale for a
    -- render that comes back already 2x.
    if items.UpscaleCB.Checked then
        args = args .. " --network-upscale"
    else
        args = args .. " --no-upscale"
    end
    -- Every render plays at a third of the clip's speed, with all the new
    -- frames drawn, so there is more to time remap. With no in-betweens
    -- there is nothing new to draw, so an upscale alone keeps the length.
    if items.InterpCB.Checked then
        args = args .. " --slow-motion 3"
    else
        args = args .. " --no-interpolate"
    end
    if THRESHOLD ~= 5 then
        -- Each step doubles or halves how big a change counts, around the default.
        args = args .. " --threshold " .. string.format("%.6f", 0.004 * 2 ^ ((THRESHOLD - 5) / 2)):gsub(",", ".")
    end
    if OUTPUT_DIR ~= "" then args = args .. " --output-dir " .. shell_quote(OUTPUT_DIR) end
    args = args .. " --status-file " .. shell_quote(STATUS_PATH)
    return args .. (extra or "")
end

-- One clip property. Asked for a single key, Resolve can hand back the value or
-- a table holding it, depending on the build.
local function clip_property(mpi, key)
    if not mpi.GetClipProperty then return nil end
    local ok, got = pcall(function() return mpi:GetClipProperty(key) end)
    if not ok then return nil end
    if type(got) == "table" then got = got[key] end
    return got
end

-- The render is finished on disk; bring it in from here rather than from the
-- Python process, which would need Resolve's external scripting enabled.
-- An image sequence has no frame rate of its own, so Resolve gives it the
-- project's, and a 60 fps render in a 24 fps project plays at 40% speed.
-- Resolve lets a sequence's rate be changed, so set it, then read it back,
-- because a refusal is not always reported. Done before the clip is appended,
-- since its length on the timeline depends on it.
local function set_sequence_fps(mpi, fps)
    if not mpi.SetClipProperty then return "", false end
    local text = (math.abs(fps - math.floor(fps + 0.5)) < 0.001)
        and tostring(math.floor(fps + 0.5)) or string.format("%.3f", fps)
    local called, set = pcall(function() return mpi:SetClipProperty("FPS", text) end)
    -- Only a rate read back as set counts. A yes from SetClipProperty alone
    -- is not enough, and a clip placed at the wrong rate plays at the wrong
    -- speed and covers more of the timeline.
    local confirmed = false
    if called and set then
        local got = tonumber(tostring(clip_property(mpi, "FPS") or ""):match("^%s*([%d%.]+)"))
        confirmed = got ~= nil and math.abs(got - fps) <= 0.01
    end
    if confirmed then return " at " .. text .. " fps", true end
    return " (set its frame rate to " .. text .. " in Clip Attributes, or it plays at the project's)",
        false
end

-- The first entry of a list from Resolve, whatever its keys. Resolve 20.3
-- adds a number under __flags to every list, which is not an entry.
local function first_of(list)
    if type(list) ~= "table" then return nil end
    if list[1] ~= nil then return list[1] end
    for k, v in pairs(list) do
        if k ~= "__flags" then return v end
    end
    return nil
end

-- The lowest video track above the source clip with nothing over `length`
-- frames from its start, so no clip of the edit is covered or cut. Adds a
-- track when every one has something there.
local function free_track(timeline, at, length)
    local finish = at.start + length
    for track = at.track + 1, tonumber(timeline:GetTrackCount("video")) or 0 do
        local clear = true
        for _, other in ipairs(timeline:GetItemListInTrack("video", track) or {}) do
            if other:GetStart() < finish and other:GetEnd() > at.start then
                clear = false
                break
            end
        end
        if clear then return track, false end
    end
    if not timeline:AddTrack("video") then return nil end
    return tonumber(timeline:GetTrackCount("video")), true
end

-- Where the source clip is now. It can be moved while the render runs, so it
-- is found again by its id. nil if it is gone, or now uses another part of
-- its media, since the render would then not match it.
local function find_source(timeline, place)
    for track = 1, tonumber(timeline:GetTrackCount("video")) or 0 do
        for _, it in ipairs(timeline:GetItemListInTrack("video", track) or {}) do
            if it:GetUniqueId() == place.item then
                local source_start = nil
                pcall(function() source_start = tonumber(it:GetSourceStartFrame()) end)
                if tonumber(it:GetDuration()) ~= place.duration
                    or source_start ~= place.source_start then
                    return nil
                end
                return { track = track, start = tonumber(it:GetStart()) }
            end
        end
    end
    return nil
end

-- The timeline's frame rate and the clip's. nil when either is unknown.
local function rates(timeline, mpi)
    local ours, theirs = exact_rate(timeline:GetSetting("timelineFrameRate")), exact_rate(clip_property(mpi, "FPS"))
    if not (ours and theirs and ours > 0 and theirs > 0) then return nil end
    return ours, theirs
end

-- How many of the render's frames to place over the source clip's
-- `duration` timeline frames: as many as fit without running past it.
-- Resolve rounds a clip's length on the timeline down, so that is the most
-- whose length at the timeline's rate is under duration + 1. The render can
-- have a frame more than that, as Resolve reports where a clip ends
-- inconsistently. On a timeline faster than the clip it can fall a frame
-- short, since no whole number of the clip's frames fits exactly.
local function frames_covering(timeline, mpi, duration, frames)
    local ours, theirs = rates(timeline, mpi)
    if not ours then return nil end
    return math.min(frames, math.ceil((duration + 1) * theirs / ours - 1e-6) - 1)
end

-- How many timeline frames the clip covers: its frames at its own rate,
-- counted at the timeline's. nil when either rate is unknown.
local function timeline_length(timeline, mpi, frames)
    local ours, theirs = rates(timeline, mpi)
    if not ours then return nil end
    return math.ceil(frames * ours / theirs - 0.001)
end

-- Put the render on a track above the clip it came from, starting on the same
-- frame, so it lines up with the edit and the audio. nil when that cannot be
-- done: another timeline is open, the source clip is gone or was re-trimmed,
-- or a call failed. Each step is guarded on its own, so a failure after the
-- clip went in cannot lead to a second copy at the end of the timeline.
-- `slow` is how many times longer than the clip the render is.
local function place_above(pool, project, mpi, frames, slow)
    local place = RUN_PLACE
    if not (place and frames and frames > 0) then return nil end
    local ok, timeline, at, count, length = pcall(function()
        local timeline = project:GetCurrentTimeline()
        if not timeline or timeline:GetUniqueId() ~= place.timeline then return nil end
        local at = find_source(timeline, place)
        if not at then return nil end
        -- A slowed render runs past the original anyway, so all of it goes on.
        local count = slow > 1 and frames
            or frames_covering(timeline, mpi, place.duration, frames)
        return timeline, at, count, count and timeline_length(timeline, mpi, count)
    end)
    if not (ok and timeline and at and count and count > 0 and length) then return nil end

    local made, track, added = pcall(free_track, timeline, at, math.max(length, place.duration))
    if not (made and track) then return nil end
    -- Resolve 20.3 places frames startFrame to endFrame - 1: asked for 10 to
    -- 20, it puts 10 frames on the timeline.
    local appended, item = pcall(function()
        return first_of(pool:AppendToTimeline({ {
            mediaPoolItem = mpi, startFrame = 0, endFrame = count,
            trackIndex = track, recordFrame = at.start, mediaType = 1,
        } }))
    end)
    if not (appended and item) then
        if added then pcall(function() timeline:DeleteTrack("video", track) end) end
        return nil
    end
    if added then pcall(function() timeline:SetTrackName("video", track, "Upflow2x") end) end
    -- Where it really ends, in case this Resolve counts frames differently.
    local over = 0
    if slow == 1 then
        pcall(function() over = tonumber(item:GetEnd()) - (at.start + place.duration) end)
    end
    return track, item, over
end

-- The upscale only shows on a timeline bigger than the original clip. On one
-- the same size it is scaled back down. `factor` is how much bigger than its
-- file the clip plays: 2 with Super Scale, 1 for a render already drawn at 2x.
local function upscale_hint(project, mpi, factor)
    local ok, width = pcall(function()
        return project:GetCurrentTimeline():GetSetting("timelineResolutionWidth")
    end)
    local w, h = tostring(clip_property(mpi, "Resolution") or ""):match("(%d+)%s*[xX]%s*(%d+)")
    w, h, width = tonumber(w), tonumber(h), ok and tonumber(width) or nil
    if not (w and h and width) or width * 2 > w * factor then return nil end
    return string.format("To see the 2x upscale, set the timeline to %dx%d.", w * factor, h * factor)
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
        -- Where it is, so it can be dragged in by hand. A sequence is its
        -- folder, not its first frame.
        local where = st.sequence_pattern ~= "" and path:match("^(.+)[/\\]") or path
        return false, "Resolve would not import it.", where
    end

    local notes = "Imported " .. basename(st.sequence_pattern ~= "" and path:match("^(.+)[/\\]")
                                         or path)
    local hint = nil
    local rate_ok = true
    if st.sequence_pattern ~= "" and st.fps > 0 then
        local said
        said, rate_ok = set_sequence_fps(mpi, st.fps)
        notes = notes .. said
    end
    if st.upscaled then
        -- Drawn at 2x already, even if Upscale was unticked since. Super
        -- Scale on top would make it 4x.
        notes = notes .. ", upscaled 2x"
        hint = upscale_hint(project, mpi, 1)
    elseif DO_UPSCALE and mpi.SetClipProperty then
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
        if called and scaled then hint = upscale_hint(project, mpi, 2) end
    end
    -- At the wrong rate it would play at the wrong speed and cover more of
    -- the timeline than the original, so it stays in the media pool.
    if not rate_ok then
        return true, notes .. " It is in the media pool, not on the timeline.", nil, hint
    end
    if pool.AppendToTimeline then
        local frames = st.sequence_frames > 0 and st.sequence_frames
            or tonumber(clip_property(mpi, "Frames"))
        local track, item, over = place_above(pool, project, mpi, frames, st.slow_motion)
        if track then
            LAST_ITEM = item
            notes = notes .. ", placed on V" .. track .. " above the original"
            if over > 0 then
                notes = notes .. string.format(", but it runs %d frame%s past it. Trim its end.",
                                               over, over == 1 and "" or "s")
            else
                notes = notes .. "."
            end
        else
            local called, appended = pcall(function() return pool:AppendToTimeline({ mpi }) end)
            local added = called and appended and (type(appended) ~= "table" or #appended > 0)
            if added then LAST_ITEM = first_of(appended) end
            notes = notes .. (added and ", added to the end of the timeline."
                                    or ". Drag it onto your timeline.")
        end
    end
    return true, notes, nil, hint
end

-- How long the current stage has left, from its pace since the panel first
-- saw it. Each stage counts from 0 to 1 on its own, so its start is reset.
local STAGE = { name = nil, at = 0, from = 0 }

local function time_left(st)
    if st.stage ~= STAGE.name or st.fraction < STAGE.from then
        STAGE.name, STAGE.at, STAGE.from = st.stage, os.time(), st.fraction
        return ""
    end
    local spent, done = os.time() - STAGE.at, st.fraction - STAGE.from
    if st.done or spent < 3 or done < 0.02 or st.fraction >= 1 then return "" end
    local left = spent * (1 - st.fraction) / done
    if left < 60 then return string.format("  about %d s left", math.ceil(left)) end
    return string.format("  about %d min left", math.ceil(left / 60))
end

-- The folder holding a render: a sequence is its folder, a file is itself.
local function render_location(st)
    if st.sequence_pattern ~= "" then return st.import_path:match("^(.+)[/\\]") end
    return st.import_path
end

local function apply_status(st)
    if not st then return false end
    set_progress(st.fraction, st.stage .. time_left(st))
    -- Until the run is done its message only ever says Starting, and shown on
    -- every poll it would hide what the panel itself has to say.
    if st.done and st.message ~= "" then set_status(st.message) end
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
            local placed, shared = 0, 0
            if ANALYSED_ITEM then placed, shared = mark_drawings(ANALYSED_ITEM, st.marks) end
            local report = st.message
            if placed > 0 then
                report = report .. " Marked " .. placed .. " frames on the clip."
                if shared > 0 then
                    report = report .. " " .. shared
                        .. (shared == 1 and " more found frame shares a timeline frame with another"
                            or " more found frames share timeline frames with others")
                        .. ", as the clip runs faster than the timeline."
                end
                local on = st.message:match("[Oo]n (%d)s")
                items.AnalysisLabel.Text = "Placed " .. placed .. " markers."
                    .. (on and (" Animated on " .. on .. "s.") or "")
            elseif st.message ~= "" then
                items.AnalysisLabel.Text = st.message
            end
            items.AnalysisLabel.ToolTip = report
            if placed > 0 then
                set_status("Check the markers, then press Render.")
            elseif st.marks ~= "" and ANALYSED_ITEM and ANALYSED_ITEM.unreadable then
                set_status("Analysis complete. Resolve would not say where the clip's frames are, so no markers were placed.")
            else
                set_status("Analysis complete.")
            end
            return true
        end
        if st.import_path ~= "" and not IMPORTED then
            IMPORTED = true
            LAST_OUTPUT = render_location(st)
            items.FolderBtn.Enabled = LAST_OUTPUT ~= nil
            local ok, notes, where, hint = import_result(st)
            -- A slowed render runs past its original, so off it shows the next shot.
            items.CompareBtn.Enabled = LAST_ITEM ~= nil and st.slow_motion == 1
            items.CompareBtn.Text = "Show Original"
            if ok then
                set_status(st.message .. " " .. notes)
                -- Three lines of status leave no room for a tip as well.
                if hint then items.Meta.Text = hint end
            elseif where then
                -- The status box fits three lines, too few for a path as well.
                items.Meta.Text = short_path(where, 68)
                print("[Upflow2x] The render is at " .. where)
                set_status(st.message .. " " .. notes .. " Drag it in from the path below.")
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
        -- With no status yet, the quiet counts from the launch: a run whose
        -- window closed before Python started leaves no file at all.
        local last = (st and st.updated and st.updated > 0) and st.updated or LAUNCHED_AT
        local quiet = last and (os.time() - last) or 0
        -- Quiet this long, the button clears the run, so it says Reset.
        if quiet > STALLED_AFTER and not st then
            items.CancelBtn.Text = "Reset"
            set_status(string.format("Python has not started after %d minutes. Press Reset "
                .. "and run it again. Log: %s", math.floor(quiet / 60 + 0.5), LOG_PATH))
        elseif quiet > STALLED_AFTER then
            items.CancelBtn.Text = "Reset"
            set_status(string.format("No progress for %d minutes, so the run has stopped. "
                .. "Press Reset and run it again.", math.floor(quiet / 60 + 0.5)))
        elseif (items.Status.Text or ""):match("^No progress for")
            or (items.Status.Text or ""):match("^Python has not started") then
            -- It is reporting again, so the warning no longer holds.
            items.CancelBtn.Text = "Stop"
            set_status("Working. Resolve stays usable while this runs.")
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

-- Where the playhead is, or nil. Cheap enough to read every tick.
local function playhead_spot()
    local ok, spot = pcall(function()
        local timeline = get_resolve():GetProjectManager():GetCurrentProject():GetCurrentTimeline()
        return timeline:GetUniqueId() .. " " .. tostring(timeline:GetCurrentTimecode())
    end)
    return ok and spot or nil
end

-- Between runs the card follows the playhead, so there is nothing to press.
-- During playback the playhead moves every tick, and reading the clip under
-- it each time would cost Resolve, so the card catches up once it stops.
local pick_source
local last_spot = nil
local function on_timeout(ev)
    if RUNNING then
        poll()
        return
    end
    local spot = playhead_spot()
    local still = spot == last_spot
    last_spot = spot
    if not still then return end
    local missing = not SOURCE_PATH and items.SourceLabel.Text
    if pick_source(true) and missing and items.Status.Text == missing then
        set_status("Ready.")
    end
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

-- A run that never started has nothing to poll, so the panel says so now.
local function start_run(args)
    LAUNCHED_AT = os.time()
    if run_background("Stages.resolve_smooth", args) then
        start_polling()
        return
    end
    local msg = "Could not start the run. Check that Upflow2x's folder can be written to, "
        .. "then try again."
    set_busy(false)
    set_progress(0, nil)
    if ANALYSING then items.AnalysisLabel.Text = msg end
    ANALYSING = false
    set_status(msg)
end

-- ---------------------------------------------------------------------------
-- handlers
-- ---------------------------------------------------------------------------

function win.On.Eternal2x.Close(ev)
    save_conf()
    disp:ExitLoop()
end

-- Work on the clip under the playhead now, not the one there when the panel
-- opened. If the playhead has moved off every clip, keep the last one.
-- With quiet, as the timer calls it, a gap says nothing.
function pick_source(quiet)
    if refresh_source(true) or SOURCE_PATH then return true end
    if not quiet then set_status(items.SourceLabel.Text) end
    return false
end

function win.On.AnalyseBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if launch_problem() then
        set_status(launch_problem())
        return
    end
    if not pick_source() then return end
    -- Backgrounded like the render: analysis is quick on a short clip but can
    -- take tens of seconds on a long one, and Resolve must not freeze for it.
    clear_status()
    ANALYSING = true
    ANALYSED_ITEM = SOURCE_ITEM
    IMPORTED = false
    STAGE.name = nil
    set_busy(true)
    set_progress(0.01, "Starting")
    set_status("Analysing...")
    items.AnalysisLabel.Text = "Analysing..."
    items.AnalysisLabel.ToolTip = ""
    start_run(current_args(" --analyse"))
end

function win.On.SmoothBtn.Clicked(ev)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if launch_problem() then
        set_status(launch_problem())
        return
    end
    if not pick_source() then return end
    -- Read before anything starts. Rendering as if unmarked would throw away
    -- the frames the editor chose.
    local marks = clip_marks(SOURCE_ITEM)
    if not marks then
        set_status("Could not read the markers on the clip from Resolve, so nothing was rendered.")
        return
    end
    if #marks > 0 then
        -- A write can look fine and still fail as the file closes.
        local f = io.open(MARKS_PATH, "w")
        local wrote = f and f:write(table.concat(marks, ","))
        local closed = f and f:close()
        if not (wrote and closed) then
            set_status("Could not write the marked frames to " .. MARKS_PATH .. ".")
            return
        end
    end
    clear_status()
    IMPORTED = false
    ANALYSING = false
    STAGE.name = nil
    RUN_PLACE = SOURCE_PLACE
    LAST_ITEM = nil
    items.CompareBtn.Enabled = false
    set_busy(true)
    set_progress(0.01, "Starting")
    if #marks > 0 then
        set_status("Rendering the " .. #marks .. " marked frames.")
        start_run(current_args(" --marks-file " .. shell_quote(MARKS_PATH)))
    else
        set_status("Rendering. No markers, so Upflow2x finds the drawings.")
        start_run(current_args(""))
    end
end

-- Switch the smoothed clip off and on, so the original under it shows through.
function win.On.CompareBtn.Clicked(ev)
    local ok, showing = pcall(function()
        local now = not LAST_ITEM:GetClipEnabled()
        if not LAST_ITEM:SetClipEnabled(now) then error("refused") end
        return now
    end)
    if not ok then
        LAST_ITEM = nil
        items.CompareBtn.Enabled = false
        set_status("The smoothed clip is no longer on the timeline.")
        return
    end
    items.CompareBtn.Text = showing and "Show Original" or "Show Upflow2x"
    set_status(showing and "Showing the Upflow2x version." or "Showing the original.")
end

function win.On.FolderBtn.Clicked(ev)
    if not LAST_OUTPUT then return end
    local cmd
    if is_windows() then
        if launch_problem() then
            set_status(launch_problem())
            return
        end
        -- /select opens the parent with the render picked out. Explorer
        -- reports 1 even when it worked, so its answer is not checked. cmd
        -- expands %NAME% in a command line, so the path goes in a script,
        -- where doubling % keeps it literal, started as the render is.
        local f = io.open(REVEAL_SCRIPT, "wb")
        if not f then return end
        local target = shell_quote((LAST_OUTPUT:gsub("/", "\\")))
        f:write("@echo off\r\nchcp 65001 >nul\r\nexplorer /select,",
                (target:gsub("%%", "%%%%")), "\r\n")
        f:close()
        local dir = (REPO_ROOT ~= "" and REPO_ROOT or root):gsub("/", "\\")
        cmd = "start \"Upflow2x\" /D " .. shell_quote(dir) .. " /MIN cmd /c .\\"
            .. basename(REVEAL_SCRIPT)
    else
        local q = shell_quote(LAST_OUTPUT)
        cmd = "open -R " .. q .. " 2>/dev/null || xdg-open " ..
              shell_quote(LAST_OUTPUT:match("^(.+)/") or ".") .. " >/dev/null 2>&1 &"
    end
    print("[Upflow2x] " .. cmd)
    if is_windows() then
        start_script(REVEAL_SCRIPT, cmd)
    else
        execute(cmd)
    end
end

function win.On.RefreshStatusBtn.Clicked(ev)
    -- A finished run's status is gone once handled, but the shell can still
    -- write the exit code after it, so that alone is not a run to report on.
    -- A run this panel started is polled even before Python reports, so one
    -- that never started is caught.
    if not read_status() and not RUNNING then
        -- Without a timer, this is also how the card catches up.
        pick_source(true)
        set_status("No run in progress.")
        return
    end
    poll()
end

function win.On.CancelBtn.Clicked(ev)
    if run_in_progress() then
        -- The run sees this at its next frame, then reports Stopped.
        local f = io.open(STOP_PATH, "w")
        if f then f:close() end
        set_busy(true)
        start_polling()
        set_status(f and "Stopping..." or "Could not stop the run.")
        return
    end
    clear_status()
    ANALYSING = false
    IMPORTED = false
    set_busy(false)
    set_progress(0, nil)
    set_status("Ready.")
end

function win.On.ThresholdSlider.ValueChanged(ev)
    THRESHOLD = math.max(1, math.min(9, math.floor((tonumber(items.ThresholdSlider.Value) or 5) + 0.5)))
    items.ThresholdValue.Text = tostring(THRESHOLD)
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

local function run_update(auto_mode)
    if REPO_ROOT == "" then
        set_status("Plugin folder not configured. Re-run the installer.")
        return
    end
    if UPDATE_URL == "" then
        set_status("No update URL configured.")
        return
    end
    if launch_problem() then
        if not auto_mode then set_status(launch_problem()) end
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
    refresh_source()
    start_polling()
    set_status(SOURCE_PATH and "Ready." or items.SourceLabel.Text)
    local waiting = read_status()
    if run_in_progress() then
        -- Closed mid-run and reopened: follow that run, and import it when done.
        set_busy(true)
        start_polling()
        set_status("A run started earlier is still going. Its progress shows here.")
    elseif waiting and waiting.done and waiting.ok and waiting.import_path ~= "" then
        -- It finished after its panel closed, so nothing has imported it, and
        -- the next run would delete its status unseen.
        LAST_OUTPUT = render_location(waiting)
        items.FolderBtn.Enabled = LAST_OUTPUT ~= nil
        set_status("A render finished while the panel was closed. "
            .. "Press Refresh Progress to add it to the timeline.")
    elseif AUTO_UPDATE then
        run_update(true)
    end
end

disp:RunLoop()
