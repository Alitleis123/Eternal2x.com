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

function H.make_resolve(clip_file_path)
    local mpi = {
        GetClipProperty = function(_self)
            return { ["File Path"] = clip_file_path, FPS = H.clip_fps or "24" }
        end,
    }
    local item = { GetMediaPoolItem = function(_self) return mpi end }
    -- Where the clip sits in its media, given in seconds from the start of
    -- the file. Resolve reports frames from the start of the file, but
    -- seconds from the media's start timecode, H.start_tc. Left unset, the
    -- item has no such methods, as on a build that predates them, and the
    -- whole file is used.
    if H.source_range then
        local fps = tonumber(H.clip_fps or "24")
        local tc = H.start_tc or 0
        item.GetSourceStartTime = function(_self) return H.source_range[1] + tc end
        item.GetSourceEndTime = function(_self) return H.source_range[2] + tc end
        item.GetSourceStartFrame = function(_self)
            return math.floor(H.source_range[1] * fps + 0.5)
        end
        item.GetSourceEndFrame = function(_self)
            return math.floor(H.source_range[2] * fps + 0.5)
        end
    end
    if H.source_frames then
        item.GetSourceStartFrame = function(_self) return H.source_frames[1] end
        item.GetSourceEndFrame = function(_self) return H.source_frames[2] end
    end
    -- No GetSelectedItems: Resolve's API has no timeline selection.
    local timeline = {
        GetCurrentVideoItem = function(_self) return H.under_playhead and item or nil end,
    }
    local pool = {
        ImportMedia = function(_self, paths)
            if not H.import_works then return {} end
            local made = {}
            for _, path in ipairs(paths) do
                -- A clipInfo table imports a sequence, as Blackmagic documents.
                if type(path) == "table" then
                    if not H.sequence_import_works then return {} end
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
                        if k == "FPS" then return H.clip_properties.FPS or "24" end
                        return H.clip_properties[k]
                    end,
                })
            end
            return made
        end,
        AppendToTimeline = function(_self, clips)
            H.appended = H.appended + #clips
            return clips
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
            local f = io.open(script, "rb")
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
