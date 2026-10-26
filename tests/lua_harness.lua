-- Stubs enough of Fusion's UIManager / UIDispatcher and Resolve to load and
-- drive Installer/Eternal2x.lua outside of DaVinci Resolve.
--
-- Everything the script does to the outside world -- os.execute calls, print
-- output, config writes -- is captured for inspection.

local H = {}

H.commands = {}   -- every shell command the script ran, in order
H.prints = {}     -- every print() line
H.widgets = nil     -- widget table, keyed by ID
H.window = nil
H.exit_code = 0      -- what os.execute reports back
H.real_execute = false  -- when true, commands are genuinely run

-- Widgets -----------------------------------------------------------------

local function new_widget(spec)
    local w = {}
    for k, v in pairs(spec) do
        if type(k) ~= "number" then w[k] = v end
    end
    w.__children = {}
    for _, child in ipairs(spec) do
        table.insert(w.__children, child)
    end

    -- Widget methods the panel uses.
    w.__entries = {}
    function w:AddItem(label)
        table.insert(self.__entries, label)
        if self.CurrentIndex == nil then self.CurrentIndex = 0 end
    end
    function w:GetItem(i) return self.__entries[i + 1] end
    function w:Count() return #self.__entries end
    function w:Clear() self.__entries = {} end
    function w:Start() self.__running = true end
    function w:Stop() self.__running = false end

    if w.Enabled == nil then w.Enabled = true end
    return w
end

local function collect(widget, out)
    if widget.ID then out[widget.ID] = widget end
    for _, child in ipairs(widget.__children or {}) do
        collect(child, out)
    end
end

-- UIManager: ui:VGroup{...}, ui:Label{...} etc. all just build descriptors.
local ui = setmetatable({}, {
    __index = function(_, kind)
        return function(_self, spec)
            local w = new_widget(spec or {})
            w.__kind = kind
            return w
        end
    end,
})

-- Dispatcher --------------------------------------------------------------

local function auto_table()
    return setmetatable({}, {
        __index = function(t, k)
            local sub = {}
            rawset(t, k, sub)
            return sub
        end,
    })
end

local dispatcher = {}
dispatcher.__index = dispatcher

function dispatcher:AddWindow(attrs, layout)
    local win = {
        __attrs = attrs,
        __layout = layout,
        __shown = false,
        __extra = {},
        On = auto_table(),
    }
    local items = {}
    collect(layout, items)
    function win:GetItems() return items end
    function win:Show() self.__shown = true end
    function win:Hide() self.__shown = false end
    function win:AddChild(child)
        if child and child.ID then items[child.ID] = child end
        table.insert(self.__extra, child)
    end
    H.widgets = items
    H.window = win
    return win
end

function dispatcher:RunLoop() end
function dispatcher:ExitLoop() end

local bmd = {}
function bmd.UIDispatcher(_ui)
    return setmetatable({}, dispatcher)
end

-- Resolve stub ------------------------------------------------------------
-- Mirrors the shape Eternal2x.lua walks: project -> timeline -> item -> mpi.

H.imported = {}         -- paths handed to ImportMedia
H.appended = 0          -- how many clips reached the timeline
H.clip_properties = {}  -- properties set on imported clips
H.import_works = true   -- flip to false to simulate a build that refuses
H.sequence_import_works = true  -- false: refuse the clipInfo form only
H.under_playhead = true  -- false: the playhead is not over a clip

-- Every list Resolve 20.3 hands to Lua also has a number under __flags, so a
-- loop over it with pairs meets that number as well as the items.
local function resolve_list(t)
    t.__flags = 4194304
    return t
end

-- A frame rate as Resolve counts with it: 23.976 is 24000/1001, but 23.98
-- is 23.98.
local NTSC = { { 23.976, 24000 / 1001 }, { 29.97, 30000 / 1001 }, { 47.952, 48000 / 1001 },
               { 59.94, 60000 / 1001 }, { 119.88, 120000 / 1001 } }
local function exact_rate(text)
    local r = tonumber(tostring(text or "24"):match("[%d%.]+"))
    for _, pair in ipairs(NTSC) do
        if math.abs(r - pair[1]) < 1e-6 then return pair[2] end
    end
    return r
end

function H.make_resolve(clip_file_path)
    local mpi = {
        GetClipProperty = function(_self)
            return { ["File Path"] = clip_file_path, FPS = H.clip_fps or "24" }
        end,
    }
    local item = { GetMediaPoolItem = function(_self) return mpi end }
    -- H.item_off switches the source clip off, as Resolve's clip enable does.
    item.GetClipEnabled = function(_self) return not H.item_off end
    -- Where the clip sits in its media, given in seconds from the start of
    -- the file. Resolve reports frames from the start of the file, the end
    -- frame being the last one used, but seconds from the media's start
    -- timecode, H.start_tc. Left unset, the item has no such methods, as on a
    -- build that predates them, and the whole file is used.
    if H.source_range then
        local fps = tonumber(H.clip_fps or "24")
        local tc = H.start_tc or 0
        item.GetSourceStartTime = function(_self) return H.source_range[1] + tc end
        item.GetSourceEndTime = function(_self) return H.source_range[2] + tc end
        item.GetSourceStartFrame = function(_self)
            return math.floor(H.source_range[1] * fps + 0.5)
        end
        item.GetSourceEndFrame = function(_self)
            return math.floor(H.source_range[2] * fps + 0.5) - 1
        end
    end
    if H.source_frames then
        item.GetSourceStartFrame = function(_self) return H.source_frames[1] end
        item.GetSourceEndFrame = function(_self) return H.source_frames[2] end
    end
    -- Where the clip sits on the timeline. H.no_place_api models a build
    -- without these calls.
    -- H.src_span is where the source clip sits, { start, end }. A test can
    -- change it to move or re-trim the clip, or take it off its track.
    if not H.src_span then
        local start = H.item_start or 86400
        H.src_span = { start, start + (H.item_duration or 48) }
    end
    -- Markers on the clip, keyed in timeline frames from the start of its
    -- media, as Resolve keeps them, so a clip trimmed H.left_offset frames
    -- into its media has its first frame there. Each table carries the
    -- __flags entry every Resolve table does.
    H.markers = H.markers or {}
    item.GetLeftOffset = function(_self) return H.left_offset or 0 end
    item.GetMarkers = function(_self)
        if H.markers_fail then error("GetMarkers failed") end
        local out = { __flags = 4194304 }
        for frame, info in pairs(H.markers) do out[frame] = info end
        return out
    end
    item.AddMarker = function(_self, frame, color, name, note, duration, custom)
        if H.markers[frame] then return false end
        H.markers[frame] = { color = color, name = name, note = note,
                             duration = duration, customData = custom or "" }
        return true
    end
    item.DeleteMarkerAtFrame = function(_self, frame)
        if not H.markers[frame] then return false end
        H.markers[frame] = nil
        return true
    end
    if not H.no_place_api then
        item.GetStart = function(_self) return H.src_span[1] end
        item.GetEnd = function(_self) return H.src_span[2] end
        item.GetDuration = function(_self) return H.src_span[2] - H.src_span[1] end
        item.GetUniqueId = function(_self) return "source-item" end
        item.GetTrackTypeAndIndex = function(_self)
            return resolve_list({ "video", H.item_track or 1 })
        end
    end
    -- H.tracks[n] lists the spans already on video track n. By default only
    -- the source clip, on track 1.
    if not H.tracks then
        H.tracks = {}
        for n = 1, (H.item_track or 1) do H.tracks[n] = {} end
        table.insert(H.tracks[H.item_track or 1], H.src_span)
    end
    -- H.under_paths lists files lined up under the source clip on tracks 1,
    -- 2 and up, as an original and earlier renders are under the newest
    -- render. H.item_track should be the track above the last of them.
    local under = {}
    if H.under_paths and not H.no_place_api then
        H.under_spans = H.under_spans or {}
        for i, file in ipairs(H.under_paths) do
            if not H.under_spans[i] then
                H.under_spans[i] = { H.src_span[1], H.src_span[2] }
                table.insert(H.tracks[i], H.under_spans[i])
            end
            local span = H.under_spans[i]
            local file_mpi = {
                GetClipProperty = function(_self)
                    return { ["File Path"] = file, FPS = H.clip_fps or "24" }
                end,
            }
            local it = {}
            for k, v in pairs(item) do it[k] = v end
            it.GetMediaPoolItem = function(_self) return file_mpi end
            it.GetClipEnabled = function(_self) return true end
            it.GetStart = function(_self) return span[1] end
            it.GetEnd = function(_self) return span[2] end
            it.GetDuration = function(_self) return span[2] - span[1] end
            it.GetUniqueId = function(_self) return "under-" .. i end
            -- Each clip underneath keeps its own markers, in H.under_markers[i].
            H.under_markers = H.under_markers or {}
            H.under_markers[i] = H.under_markers[i] or {}
            local own = H.under_markers[i]
            it.GetMarkers = function(_self)
                local out = { __flags = 4194304 }
                for frame, info in pairs(own) do out[frame] = info end
                return out
            end
            it.AddMarker = function(_self, frame, color, name, note, duration, custom)
                if own[frame] then return false end
                own[frame] = { color = color, customData = custom or "" }
                return true
            end
            it.DeleteMarkerAtFrame = function(_self, frame)
                if not own[frame] then return false end
                own[frame] = nil
                return true
            end
            it.GetTrackTypeAndIndex = function(_self) return resolve_list({ "video", i }) end
            under[span] = it
        end
    end
    H.track_names = H.track_names or {}
    H.placements = H.placements or {}
    local function span_item(span, n) return {
        GetStart = function(_self) return span[1] end,
        GetEnd = function(_self) return span[2] end,
        GetDuration = function(_self) return span[2] - span[1] end,
        GetUniqueId = function(_self) return "item-" .. n end,
    } end
    -- No GetSelectedItems: Resolve's API has no timeline selection.
    local timeline = {
        GetCurrentVideoItem = function(_self) return H.under_playhead and item or nil end,
        GetUniqueId = function(_self) return H.timeline_id or "timeline-1" end,
        -- H.playhead_tc is the playhead's timecode, nil until a test sets it.
        -- H.tracks_off[n] switches video track n off.
        GetCurrentTimecode = function(_self) return H.playhead_tc end,
        GetIsTrackEnabled = function(_self, kind, n)
            return not (H.tracks_off and H.tracks_off[n])
        end,
        GetTrackCount = function(_self, kind) return kind == "video" and #H.tracks or 0 end,
        GetItemListInTrack = function(_self, kind, n)
            local out = {}
            for i, span in ipairs(H.tracks[n] or {}) do
                table.insert(out, span == H.src_span and item or under[span]
                                  or span_item(span, n * 100 + i))
            end
            return resolve_list(out)
        end,
        AddTrack = function(_self, kind)
            if H.add_track_works == false then return false end
            table.insert(H.tracks, {})
            return true
        end,
        DeleteTrack = function(_self, kind, n) table.remove(H.tracks, n) return true end,
        SetTrackName = function(_self, kind, n, name)
            if H.rename_throws then error("SetTrackName failed") end
            H.track_names[n] = name
            return true
        end,
        GetSetting = function(_self, key)
            if key == "timelineResolutionWidth" then return H.timeline_width or "1920" end
            if key == "timelineFrameRate" then return H.timeline_fps or "24" end
            return nil
        end,
    }
    local pool = {
        ImportMedia = function(_self, paths)
            if not H.import_works then return resolve_list({}) end
            local made = {}
            for _, path in ipairs(paths) do
                -- A clipInfo table imports a sequence, as Blackmagic documents.
                if type(path) == "table" then
                    if not H.sequence_import_works then return resolve_list({}) end
                    H.sequence_imports = (H.sequence_imports or 0) + 1
                    H.last_sequence = path
                    path = path.FilePath
                end
                table.insert(H.imported, path)
                table.insert(made, {
                    SetClipProperty = function(_s, k, v)
                        if k == "Super Scale" and H.super_scale_works == false then return false end
                        -- Resolve can say yes to a frame rate and keep the old one.
                        if k == "FPS" and H.fps_sticks == false then return true end
                        H.clip_properties[k] = v
                        return true
                    end,
                    GetClipProperty = function(_s, k)
                        if k == "FPS" and H.fps_read_throws then error("GetClipProperty failed") end
                        if k == "FPS" then return H.clip_properties.FPS or "24" end
                        if k == "Frames" then return H.media_frames or "48" end
                        if k == "Resolution" then return H.media_resolution or "1920x1080" end
                        return H.clip_properties[k]
                    end,
                })
            end
            return resolve_list(made)
        end,
        AppendToTimeline = function(_self, clips)
            local out = {}
            for _, clip in ipairs(clips) do
                local placed = { enabled = true }
                -- A clipInfo says where on the timeline it goes.
                if clip.mediaPoolItem then
                    if H.place_throws then error("AppendToTimeline failed") end
                    if H.place_works == false then return resolve_list({}) end
                    table.insert(H.placements, clip)
                    local track = H.tracks[clip.trackIndex]
                    -- Resolve 20.3 places frames startFrame to endFrame - 1, at
                    -- the timeline's rate, and rounds the length down.
                    -- H.place_rounds models a build that rounds to nearest.
                    local raw = (clip.endFrame - clip.startFrame) * exact_rate(H.timeline_fps or "24")
                        / exact_rate(clip.mediaPoolItem:GetClipProperty("FPS"))
                    local length = H.place_rounds and math.floor(raw + 0.5) or math.floor(raw + 1e-9)
                    local span = { clip.recordFrame, clip.recordFrame + length }
                    table.insert(track, span)
                    function placed:GetStart() return span[1] end
                    function placed:GetEnd() return span[2] end
                end
                H.appended = H.appended + 1
                function placed:GetClipEnabled() return self.enabled end
                function placed:SetClipEnabled(on) self.enabled = on return true end
                H.last_placed = placed
                table.insert(out, placed)
            end
            return resolve_list(out)
        end,
    }
    local project = {
        GetCurrentTimeline = function(_self) return timeline end,
        GetMediaPool = function(_self) return pool end,
    }
    local pm = { GetCurrentProject = function(_self) return project end }
    return { GetProjectManager = function(_self) return pm end }
end

-- Execution ---------------------------------------------------------------

-- Commands are recorded, and optionally really run. Running them for real is
-- what makes an end-to-end test end-to-end: the panel builds a command string,
-- the shell runs it, and the panel reads back what it produced.
function H.make_execute(real_execute)
    return function(cmd)
        -- On Windows the panel writes the real command to a script and starts
        -- that, so record the script too, or every check of the arguments
        -- would only see `start ... cmd /c "script"`.
        local dir, name = cmd:match('/D "(.-)" /MIN cmd /c %.\\(.-%.cmd)$')
        local script = dir and (dir .. "\\" .. name)
        local recorded = cmd
        if script then
            -- A test can make the panel take its Windows path on macOS or
            -- Linux, where the script is only found with forward slashes.
            local f = io.open(script, "rb") or io.open((script:gsub("\\", "/")), "rb")
            if f then
                recorded = cmd .. "\n" .. f:read("*a")
                f:close()
            end
        end
        table.insert(H.commands, recorded)
        -- Lets a test play the part of the command, such as writing its log.
        if H.on_execute then H.on_execute(cmd) end
        if H.real_execute then
            return real_execute(cmd)
        end
        return H.exit_code == 0, "exit", H.exit_code
    end
end

-- Loading -----------------------------------------------------------------

function H.load(script_path, clip_file_path)
    H.commands = {}
    H.prints = {}

    _G.fu = { UIManager = ui }
    _G.bmd = bmd

    -- Resolve hands Lua scripts a Resolve() global, as in Blackmagic's
    -- examples. There is no Lua DaVinciResolveScript module to require.
    _G.Resolve = function() return H.make_resolve(clip_file_path) end
    package.preload["DaVinciResolveScript"] = nil
    package.loaded["DaVinciResolveScript"] = nil

    local real_execute = os.execute
    local real_print = print

    os.execute = H.make_execute(real_execute)
    print = function(...)
        local parts = {}
        for i = 1, select("#", ...) do
            parts[#parts + 1] = tostring((select(i, ...)))
        end
        table.insert(H.prints, table.concat(parts, "\t"))
    end

    local ok, err = pcall(dofile, script_path)

    os.execute = real_execute
    print = real_print

    if not ok then error(err) end
    return H.window
end

-- Inspection helpers used from the Python tests ---------------------------

function H.fire(id, event)
    local handler = H.window.On[id] and H.window.On[id][event]
    if not handler then
        error("no " .. tostring(event) .. " handler for " .. tostring(id))
    end
    local real_execute = os.execute
    local real_print = print
    os.execute = H.make_execute(real_execute)
    print = function(...) end
    local ok, err = pcall(handler, {})
    os.execute = real_execute
    print = real_print
    if not ok then error(err) end
end

function H.click(id)
    local handler = H.window.On[id] and H.window.On[id].Clicked
    if not handler then error("no Clicked handler for " .. tostring(id)) end
    local real_execute = os.execute
    local real_print = print
    os.execute = H.make_execute(real_execute)
    print = function(...) end
    local ok, err = pcall(handler, {})
    os.execute = real_execute
    print = real_print
    if not ok then error(err) end
end

function H.set_slider(id, value)
    H.widgets[id].Value = value
    local handler = H.window.On[id] and H.window.On[id].ValueChanged
    if handler then handler({}) end
end

function H.set_combo(id, index)
    H.widgets[id].CurrentIndex = index
    local handler = H.window.On[id] and H.window.On[id].CurrentIndexChanged
    if handler then handler({}) end
end

function H.set_checkbox(id, value)
    H.widgets[id].Checked = value
    local handler = H.window.On[id] and H.window.On[id].Clicked
    if handler then handler({}) end
end

function H.combo_items(id)
    return table.concat(H.widgets[id].__entries or {}, ",")
end

function H.enabled(id)
    local w = H.widgets[id]
    return w ~= nil and w.Enabled ~= false
end

function H.set_source_range(a, b) H.source_range = { a, b } end
function H.add_marker(frame, custom)
    H.markers = H.markers or {}
    H.markers[frame] = { color = "Blue", name = "", note = "", duration = 1, customData = custom or "" }
end
function H.marker_frames()
    local out = {}
    for frame in pairs(H.markers or {}) do out[#out + 1] = frame end
    table.sort(out)
    return table.concat(out, ",")
end
function H.tagged_markers()
    local n = 0
    for _, info in pairs(H.markers or {}) do
        if info.customData == "eternal2x" then n = n + 1 end
    end
    return n
end
function H.imported_count() return #H.imported end
function H.sequence_import_count() return H.sequence_imports or 0 end
function H.last_sequence_field(k) return H.last_sequence and H.last_sequence[k] end
function H.imported_at(i) return H.imported[i] end
function H.property_of(key) return H.clip_properties[key] end
function H.command_count() return #H.commands end
function H.command_at(i) return H.commands[i] end
function H.last_command() return H.commands[#H.commands] end
function H.status() return H.widgets.Status and H.widgets.Status.Text or nil end
function H.text_of(id) return H.widgets[id] and H.widgets[id].Text or nil end
function H.widget_ids()
    local ids = {}
    for id, _ in pairs(H.widgets) do table.insert(ids, id) end
    table.sort(ids)
    return table.concat(ids, ",")
end
function H.handler_ids()
    local ids = {}
    for id, events in pairs(H.window.On) do
        for ev, _ in pairs(events) do
            table.insert(ids, id .. "." .. ev)
        end
    end
    table.sort(ids)
    return table.concat(ids, ",")
end

return H
