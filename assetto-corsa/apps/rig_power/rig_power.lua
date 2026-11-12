-- Boost and Power: a boost dial, live horsepower and torque, and the power curve
-- the car actually makes, filled in as you drive. On cars listed in CARS below it
-- also carries three switches: the engine map (how much boost), the rev limit and the engine sound.
--
-- Engine output is worked out the way Assetto Corsa itself does it: the car's
-- torque curve (data/power.lut, in N·m) multiplied by (1 + boost), then by how
-- far the throttle is open. Power follows from torque and revs. The curve only
-- records full-throttle moments, so each pull through the revs fills in more.

local NM_TO_LBFT = 0.7375621
local ROOT = ac.getFolder(ac.FolderID.Root)
local OUT = ROOT .. '/rig_power_telem.txt'

-- Cars with switches. A map's level is the share of the car's highest boost setting
-- (engine.ini WASTEGATE) the turbos are allowed to reach; the game keeps it in steps of 0.1.
-- A sound names the installed car whose engine sounds to borrow; turbo = true also borrows
-- its turbo whistle, and turboFrom takes the turbo sounds from a different car instead (turboLoud
-- sets how loud); top is the highest revs that sound was recorded for. revs lists the rev
-- limits the car's own script (data/script.lua) offers; the second is selected by turning on
-- the Custom Shaders Patch extra switch numbered revSwitch (0 = A ... 5 = F).
local CARS = {
  porsche_911_gt2_rs_wss_2000 = {
    maps = {
      { name = '2,000', level = 0.60 },
      { name = '2,500', level = 0.80 },
    },
    revs = { { name = '9K', rpm = 9000 }, { name = '12K', rpm = 12000 } },
    revSwitch = 5,
    sounds = {
      { name = '935 TURBO', car = 'ks_porsche_935_78_moby_dick', turbo = true, top = 9300 },
      { name = '911 RSR', car = 'ks_porsche_911_rsr_2017', turbo = false, top = 9000 },
      { name = 'GT1 TURBO', car = 'ks_porsche_911_gt1', turbo = true, top = 8000 },
      { name = '917/30', car = 'ks_porsche_917_30', turbo = true, top = 8500 },
      { name = 'STOCK', top = 7200 },
      { name = 'RSR + TURBO', car = 'ks_porsche_911_rsr_2017', turboFrom = 'ks_porsche_911_gt1', turboLoud = 2.0, top = 9000 },
    },
  },
}
-- How much louder than the sound's own level: 1 = as recorded.
local LOUD = { engine = 1.2, backfire = 1.5, turbo = 1.5 }

local curve = nil            -- the car's torque curve without boost
local curveTried = false
local peak = { boost = 0, hp = 0, tq = 0, hpRpm = 0, tqRpm = 0 }
local bins = {}              -- [rpm/100] = best full-throttle torque (lb-ft) seen there
local now = { rpm = 0, boost = 0, gas = 0, tq = 0, hp = 0, limiter = 8000, gear = 0 }
local acc, seq = 0, 0

local store = ac.storage({ map = 1, sound = 1, prevSound = 2, rev = 1, mapDev = '', mapBtn = -1, sndDev = '', sndBtn = -1, revDev = '', revBtn = -1 })
local cfg = nil              -- this car's entry in CARS, if it has one
local carId = ''
local started, startWait = false, 0
local mapTouched = false     -- the map has been set from here (until then the car's own default applies)
local mapTimer = 0
local banks = {}             -- [car] = true once its sound file is loaded
local status = { sound = '', bank = 1, err = '', note = '' }
local squeezed = false       -- the engine sound's revs are being scaled down to fit the borrowed sound
local binding, bindTimer, bindSkip = nil, 0, {}
local held = { map = false, sound = false, rev = false }
local soundLimiter = 0       -- the rev limit the engine sound was last set up for

local function hex(h, a)
  return rgbm(tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255, a or 1)
end
-- The panel is mostly see-through so the track stays visible behind it; BACKDROP is its opacity (0 = clear, 1 = solid).
local BACKDROP = 0.32
local COL = {
  panel = hex('0E1318', BACKDROP), edge = hex('8FA3B5', 0.28), text = hex('F4F7FA'), dim = hex('C3CED8'), faint = hex('93A3B2'),
  track = hex('0E1318', 0.55), tick = hex('C3CED8', 0.55), boost = hex('5DCAA5'), hp = hex('4C9BE8'), tq = hex('F2A93B'),
  red = hex('E24B4A'), live = hex('5DCAA5'), ring = hex('0E1318', 0.85), shade = hex('000000', 0.55), ink = hex('0E1318'),
}

local function num(v) return (type(v) == 'number' and v == v) and v or 0 end

local function clampIndex(i, n)
  i = math.floor(num(i))
  if i < 1 or i > n then return 1 end
  return i
end

local function resetPeaks()
  peak = { boost = 0, hp = 0, tq = 0, hpRpm = 0, tqRpm = 0 }
  bins = {}
end

-- the switches ---------------------------------------------------------------
local function fail(what, err)
  status.err = what .. ': ' .. tostring(err)
  ac.log('Boost and Power: ' .. status.err)
end

local function applyMap()
  if cfg == nil then return end
  local m = cfg.maps[clampIndex(store.map, #cfg.maps)]
  local ok, err = pcall(function() ac.setTurboWastegate(m.level, -1) end)
  if not ok then fail('map', err) end
  mapTouched = true
  mapTimer = 0
end

local function flipMap()
  if cfg == nil then return end
  store.map = clampIndex(store.map, #cfg.maps) % #cfg.maps + 1
  applyMap()
  resetPeaks()                                          -- the other map has its own curve
  ac.log('Boost and Power: map ' .. cfg.maps[store.map].name)
end

local function applyRev()
  if cfg == nil or cfg.revs == nil then return end
  local ok, err = pcall(function() ac.setExtraSwitch(cfg.revSwitch, clampIndex(store.rev, #cfg.revs) == 2) end)
  if not ok then fail('rev limit', err) end
end

local function flipRev()
  if cfg == nil or cfg.revs == nil then return end
  store.rev = clampIndex(store.rev, #cfg.revs) % #cfg.revs + 1
  applyRev()
  resetPeaks()                                          -- the same power now sits at different revs
  ac.log('Boost and Power: rev limit ' .. cfg.revs[store.rev].name)
end

local function applySound()
  if cfg == nil then return end
  local s = cfg.sounds[clampIndex(store.sound, #cfg.sounds)]
  local E = ac.CarAudioEventID
  status.bank = 1
  for _, donor in ipairs({ s.car or false, s.turboFrom or false }) do
    if donor and not banks[donor] then
      local ok, found = pcall(function()
        return ac.loadSoundbank(ac.getFolder(ac.FolderID.ContentCars) .. '/' .. donor .. '/sfx/' .. donor .. '.bank',
          ROOT .. '/content/sfx/GUIDs.txt')
      end)
      banks[donor] = ok and found ~= false
      if not ok then fail('sound file', found) elseif found == false then fail('sound file', 'not found for ' .. donor) end
    end
    if donor and not banks[donor] then status.bank = 0 end
  end
  local from = '/cars/' .. (s.car or carId) .. '/'
  local own = '/cars/' .. carId .. '/'
  local turbo = s.turboFrom and ('/cars/' .. s.turboFrom .. '/') or ((s.car ~= nil and s.turbo) and from or own)
  local swaps = {
    { E.EngineExt, from .. 'engine_ext', LOUD.engine }, { E.EngineInt, from .. 'engine_int', LOUD.engine },
    { E.BackfireExt, from .. 'backfire_ext', LOUD.backfire }, { E.BackfireInt, from .. 'backfire_int', LOUD.backfire },
    { E.Limiter, from .. 'limiter', 1 }, { E.GearExt, from .. 'gear_ext', 1 }, { E.GearInt, from .. 'gear_int', 1 },
    { E.Turbo, turbo .. 'turbo', s.turboLoud or LOUD.turbo },
  }
  for i = 1, #swaps do
    local ok, err = pcall(function()
      ac.CarAudioTweak.replaceAudioEvent(swaps[i][1], swaps[i][2])
      ac.CarAudioTweak.setVolume(swaps[i][1], swaps[i][3])
    end)
    if not ok then fail('sound', err) end
  end
  -- A sound recorded for fewer revs than this engine turns is fed scaled revs, so it tops out at the limiter
  -- instead of running off the end of its recording. Sounds that reach the limiter by themselves are left alone.
  -- The pitch is raised by the same factor (up to 40%), so the note still climbs with the real revs.
  local top = num(s.top)
  local squeeze = top > 0 and now.limiter > top + 50
  soundLimiter = now.limiter
  if squeeze or squeezed then
    local out = squeeze and top or 30000
    local inp = squeeze and now.limiter or 30000
    local pitch = squeeze and math.min(1.4, now.limiter / top) or 1
    local ok, err = pcall(function()
      local lut = ac.DataLUT11.parse(string.format('(|0=0|%d=%d|60000=%d|)', inp, out, squeeze and out or 60000))
      ac.CarAudioTweak.setParameterTransform(E.EngineExt, 'rpms', lut)
      ac.CarAudioTweak.setParameterTransform(E.EngineInt, 'rpms', lut)
      ac.CarAudioTweak.setPitch(E.EngineExt, pitch)
      ac.CarAudioTweak.setPitch(E.EngineInt, pitch)
    end)
    squeezed = squeeze and ok
    status.note = ok and (squeeze and string.format('revs scaled to %d pitch %.2f', top, pitch) or '') or ('rev scaling failed: ' .. tostring(err))
    if not ok then ac.log('Boost and Power: ' .. status.note) end
  end
  status.sound = s.name
  ac.log('Boost and Power: sound ' .. s.name)
end

local function cycleSound()                             -- on-screen click: next sound in the list
  if cfg == nil then return end
  local n = #cfg.sounds
  store.prevSound = clampIndex(store.sound, n)
  store.sound = store.prevSound % n + 1
  applySound()
end

local function flipSound()                              -- wheel button: back and forth between the last two
  if cfg == nil then return end
  local n = #cfg.sounds
  local a, b = clampIndex(store.sound, n), clampIndex(store.prevSound, n)
  if a == b then b = a % n + 1 end
  store.sound, store.prevSound = b, a
  applySound()
end

local function deviceIndex(name)
  if name == nil or name == '' then return nil end
  for d = 0, ac.getJoystickCount() - 1 do
    if ac.getJoystickName(d) == name then return d end
  end
  return nil
end

local function startBinding(which)                      -- next wheel button pressed becomes this switch
  binding, bindTimer, bindSkip = which, 0, {}
  for d = 0, ac.getJoystickCount() - 1 do
    for b = 0, ac.getJoystickButtonsCount(d) - 1 do
      if ac.isJoystickButtonPressed(d, b) then bindSkip[d * 1000 + b] = true end   -- already held: not the one being chosen
    end
  end
end

local function pollWheel(dt)
  if binding ~= nil then
    bindTimer = bindTimer + dt
    for d = 0, ac.getJoystickCount() - 1 do
      for b = 0, ac.getJoystickButtonsCount(d) - 1 do
        local down = ac.isJoystickButtonPressed(d, b)
        if not down then
          bindSkip[d * 1000 + b] = nil
        elseif not bindSkip[d * 1000 + b] then
          local name = ac.getJoystickName(d) or ''
          if binding == 'map' then store.mapDev, store.mapBtn = name, b
          elseif binding == 'rev' then store.revDev, store.revBtn = name, b
          else store.sndDev, store.sndBtn = name, b end
          held[binding] = true                          -- wait for release before it counts as a flip
          binding = nil
          return
        end
      end
    end
    if bindTimer > 8 then binding = nil end
    return
  end
  local d = store.mapBtn >= 0 and deviceIndex(store.mapDev) or nil
  local down = d ~= nil and ac.isJoystickButtonPressed(d, store.mapBtn)
  if down and not held.map then flipMap() end
  held.map = down
  d = store.sndBtn >= 0 and deviceIndex(store.sndDev) or nil
  down = d ~= nil and ac.isJoystickButtonPressed(d, store.sndBtn)
  if down and not held.sound then flipSound() end
  held.sound = down
  d = store.revBtn >= 0 and deviceIndex(store.revDev) or nil
  down = d ~= nil and ac.isJoystickButtonPressed(d, store.revBtn)
  if down and not held.rev then flipRev() end
  held.rev = down
end

-- sampling -------------------------------------------------------------------
local function loadCurve()
  curveTried = true
  local ok, lut = pcall(function() return ac.DataLUT11.carData(0, 'power.lut') end)
  if ok and lut ~= nil then curve = lut end
end

local function sample()
  local car = ac.getCar(0)
  if car == nil then return end
  if not curveTried then loadCurve() end
  now.rpm, now.boost, now.gas = num(car.rpm), math.max(0, num(car.turboBoost)), num(car.gas)
  now.gear = num(car.gear)
  local lim = num(car.rpmLimiter)
  if lim > 1000 then now.limiter = lim end
  local base = 0
  if curve ~= nil then
    local ok, v = pcall(function() return curve:get(now.rpm) end)
    if ok then base = math.max(0, num(v)) end
  end
  local nm = base * (1 + now.boost) * now.gas
  now.tq = nm * NM_TO_LBFT
  now.hp = nm * now.rpm / 7121                       -- N·m x rpm -> horsepower
  if now.boost > peak.boost then peak.boost = now.boost end
  if now.gas > 0.97 and now.rpm > 1500 then           -- full throttle: this is the engine's real output here
    if now.hp > peak.hp then peak.hp, peak.hpRpm = now.hp, now.rpm end
    if now.tq > peak.tq then peak.tq, peak.tqRpm = now.tq, now.rpm end
    local k = math.floor(now.rpm / 100)
    if bins[k] == nil or now.tq > bins[k] then bins[k] = now.tq end
  end
end

local function start(dt)                                -- once the car and the sound system are up
  local car = ac.getCar(0)
  if car == nil then return end
  local ready = true
  pcall(function() ready = ac.isAudioReady() end)
  if not ready then return end
  startWait = startWait + dt
  if startWait < 1.5 then return end
  started = true
  local ok, id = pcall(function() return ac.getCarID(0) end)
  carId = ok and tostring(id) or ''
  cfg = CARS[carId]
  if cfg == nil then return end
  store.map = clampIndex(store.map, #cfg.maps)
  store.sound = clampIndex(store.sound, #cfg.sounds)
  applyMap()
  applyRev()
  applySound()
end

function script.update(dt)
  sample()
  if not started then
    local ok, err = pcall(start, dt)
    if not ok then started = true; fail('start', err) end
  elseif cfg ~= nil then
    local ok, err = pcall(pollWheel, dt)
    if not ok then fail('wheel button', err) end
    mapTimer = mapTimer + dt
    if mapTouched and mapTimer > 2 then applyMap(); applyRev() end   -- a session restart puts the car back on its defaults
    if math.abs(now.limiter - soundLimiter) > 100 then applySound() end   -- the rev limit moved: refit the sound to it
  end
  acc = acc + dt
  if acc < 0.2 then return end                         -- 5 Hz: a line the Mac can read to check the numbers
  acc = 0
  seq = seq + 1
  local car = ac.getCar(0)
  if car == nil then return end
  local wg0, wg1 = -1, -1
  pcall(function() wg0, wg1 = num(car.turboWastegates[0]), num(car.turboWastegates[1]) end)
  local mapName, level = '-', -1
  if cfg ~= nil then
    local m = cfg.maps[clampIndex(store.map, #cfg.maps)]
    mapName, level = (m.name:gsub(',', '')), m.level
  end
  io.save(OUT, string.format('%d,%.0f,%.2f,%.3f,%.1f,%.1f,%.2f,%.2f,%d,%s,%.2f,%d,%.3f,%.3f,%s,%d,%s,%.0f,%s,%d',
    seq, now.rpm, now.gas, now.boost, now.tq, now.hp,
    num(car.drivetrainTorque), num(car.drivetrainPower), num(car.turboCount),
    mapName, level, mapTouched and 1 or 0, wg0, wg1, (status.sound:gsub(',', '')), status.bank, (status.err:gsub('[,\r\n]', ' ')), now.limiter, (status.note:gsub('[,\r\n]', ' ')), num(store.rev)), true)
end

-- drawing helpers ------------------------------------------------------------
local function label(text, size, pos, color, align)   -- align: 0 left, 0.5 centre, 1 right
  if align and align > 0 then
    local w = ui.measureDWriteText(text, size).x
    pos = vec2(pos.x - w * align, pos.y)
  end
  ui.dwriteDrawText(text, size, vec2(pos.x + 1, pos.y + 1), COL.shade)
  ui.dwriteDrawText(text, size, pos, color or COL.text)
end

local function plain(text, size, pos, color, align)   -- no shadow: for text on a filled switch
  if align and align > 0 then
    local w = ui.measureDWriteText(text, size).x
    pos = vec2(pos.x - w * align, pos.y)
  end
  ui.dwriteDrawText(text, size, pos, color)
end

local function onArc(c, r, a) return vec2(c.x + math.cos(a) * r, c.y + math.sin(a) * r) end

local function arc(c, r, a0, a1, color, thick)         -- a stroke with rounded ends
  ui.pathClear()
  ui.pathArcTo(c, r, a0, a1, 48)
  ui.pathStroke(color, false, thick)
  ui.drawCircleFilled(onArc(c, r, a0), thick / 2, color, 16)
  ui.drawCircleFilled(onArc(c, r, a1), thick / 2, color, 16)
end

local function bar(p, w, h, f, color)
  ui.drawRectFilled(p, vec2(p.x + w, p.y + h), COL.track, h / 2)
  if f > 0.01 then ui.drawRectFilled(p, vec2(p.x + w * math.min(1, f), p.y + h), color, h / 2) end
end

local function thousands(v)
  local s = string.format('%.0f', v)
  local out = s:reverse():gsub('(%d%d%d)', '%1,'):reverse()
  if out:sub(1, 1) == ',' then out = out:sub(2) end
  return out
end

-- the pieces -----------------------------------------------------------------
local function drawDial(o)
  local c, r = vec2(o.x + 85, o.y + 82), 62
  local a0, a1 = math.pi * 0.75, math.pi * 2.25
  local top = math.max(1.0, math.ceil(peak.boost * 2 + 0.2) / 2)   -- scale grows in half steps with the best boost seen
  arc(c, r, a0, a1, COL.track, 10)
  local f = math.min(1, now.boost / top)
  if f > 0.004 then arc(c, r, a0, a0 + (a1 - a0) * f, COL.boost, 10) end
  for i = 0, 8 do
    local a = a0 + (a1 - a0) * i / 8
    ui.drawLine(onArc(c, r - 9, a), onArc(c, r - 14, a), COL.tick, 1)
  end
  if peak.boost > 0 then
    local ap = a0 + (a1 - a0) * math.min(1, peak.boost / top)
    ui.drawLine(onArc(c, r - 8, ap), onArc(c, r + 8, ap), COL.text, 2)
  end
  label(string.format('%.2f', now.boost), 32, vec2(c.x, c.y - 30), COL.text, 0.5)
  label('bar boost', 11, vec2(c.x, c.y + 8), COL.dim, 0.5)
  label(string.format('%.1f psi', now.boost * 14.504), 11, vec2(c.x, c.y + 24), COL.boost, 0.5)
  label('0', 11, vec2(o.x + 26, o.y + 134), COL.faint, 0.5)
  label(string.format('%.1f', top), 11, vec2(o.x + 144, o.y + 134), COL.faint, 0.5)
end

local function drawReadout(o, w, title, value, peakValue, peakRpm, color)
  label(title, 11, vec2(o.x, o.y + 16), COL.dim, 0)
  label(thousands(value), 30, vec2(o.x + w, o.y), COL.text, 1)
  bar(vec2(o.x, o.y + 40), w, 4, peakValue > 0 and value / peakValue or 0, color)
  label(string.format('peak %s at %s rpm', thousands(peakValue), thousands(math.floor(peakRpm / 50 + 0.5) * 50)),
    11, vec2(o.x + w, o.y + 48), COL.faint, 1)
end

local function niceTop(v)
  local steps = { 100, 200, 250, 500, 1000, 2000, 2500, 5000 }
  for i = 1, #steps do
    if v / steps[i] <= 4 then return math.ceil(v / steps[i]) * steps[i], steps[i] end
  end
  return math.ceil(v / 10000) * 10000, 10000
end

local function drawCurve(o, w, h)
  local padL, padB = 34, 18
  local x0, y0, pw, ph = o.x + padL, o.y + 4, w - padL, h - padB - 4
  local rpmMax = math.max(2000, now.limiter)
  local top, step = niceTop(math.max(200, peak.hp, peak.tq))
  local function X(rpm) return x0 + pw * math.min(1, rpm / rpmMax) end
  local function Y(v) return y0 + ph * (1 - math.min(1, v / top)) end
  local v = 0
  while v <= top + 0.5 do                                         -- one scale for both: horsepower and lb-ft
    ui.drawLine(vec2(x0, Y(v)), vec2(x0 + pw, Y(v)), COL.edge, 1)
    label(string.format('%.0f', v), 11, vec2(x0 - 6, Y(v) - 7), COL.faint, 1)
    v = v + step
  end
  local rstep = rpmMax > 12000 and 4000 or 2000
  local r = 0
  while r <= rpmMax do
    label(string.format('%dk', r / 1000), 11, vec2(X(r), y0 + ph + 3), COL.faint, 0.5)
    r = r + rstep
  end
  local keys = {}
  for k, _ in pairs(bins) do keys[#keys + 1] = k end
  table.sort(keys)
  if #keys >= 2 then
    ui.pathClear()
    for i = 1, #keys do
      local rpm = keys[i] * 100 + 50
      ui.pathLineTo(vec2(X(rpm), Y(bins[keys[i]] / NM_TO_LBFT * rpm / 7121)))
    end
    ui.pathStroke(COL.hp, false, 2)
    ui.pathClear()
    for i = 1, #keys do ui.pathLineTo(vec2(X(keys[i] * 100 + 50), Y(bins[keys[i]]))) end
    ui.pathStroke(COL.tq, false, 2)
  else
    label('Full-throttle pulls fill in the curve', 11, vec2(x0 + pw / 2, y0 + ph / 2 - 7), COL.faint, 0.5)
  end
  ui.drawLine(vec2(X(now.rpm), y0), vec2(X(now.rpm), y0 + ph), COL.tick, 1)   -- where the revs are now
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.hp)), 6, COL.ring, 16)           -- a dark ring keeps the dots readable on the lines
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.hp)), 4, COL.hp, 16)
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.tq)), 6, COL.ring, 16)
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.tq)), 4, COL.tq, 16)
end

local function drawRevStrip(o, w)
  local gearText = now.gear < 0 and 'R' or (now.gear == 0 and 'N' or string.format('%d', now.gear))
  ui.drawRect(o, vec2(o.x + 34, o.y + 26), COL.edge, 6, nil, 1)
  label(gearText, 18, vec2(o.x + 17, o.y + 1), COL.text, 0.5)
  local bx, bw = o.x + 44, w - 44 - 84
  local frac = math.min(1, now.rpm / math.max(1, now.limiter))
  bar(vec2(bx, o.y + 9), bw, 8, frac, COL.dim)
  ui.drawLine(vec2(bx + bw * 0.92, o.y + 5), vec2(bx + bw * 0.92, o.y + 21), COL.red, 1)   -- redline mark
  label(thousands(math.floor(now.rpm / 10 + 0.5) * 10) .. ' rpm', 12, vec2(o.x + w, o.y + 5), COL.dim, 1)
end

-- One switch: a caption, then a pill. Left click acts; right click waits for a wheel button to tie to it.
local function switchArea(id, p, w, hgt, which, onClick)
  ui.setCursor(p)
  if ui.invisibleButton(id, vec2(w, hgt)) then onClick() end
  if ui.itemClicked(ui.MouseButton.Right) then startBinding(which) end
  if ui.itemHovered() then
    ui.setTooltip('Click to switch. Right-click, then press a wheel button, to put this switch on that button.')
  end
end

local function drawSwitches(o, w)
  local hgt = 24
  local mapI = clampIndex(store.map, #cfg.maps)
  -- engine map: one segment per map, the live one filled
  label('MAP', 11, vec2(o.x, o.y), COL.dim, 0)
  label(store.mapBtn >= 0 and ('btn ' .. (store.mapBtn + 1)) or 'hp', 9, vec2(o.x, o.y + 13), COL.faint, 0)
  local mx, seg = o.x + 36, 60
  local mw = seg * #cfg.maps
  ui.drawRectFilled(vec2(mx, o.y), vec2(mx + mw, o.y + hgt), COL.track, hgt / 2)
  ui.drawRect(vec2(mx, o.y), vec2(mx + mw, o.y + hgt), COL.edge, hgt / 2, nil, 1)
  if binding == 'map' then
    label('press a button', 11, vec2(mx + mw / 2, o.y + 5), COL.text, 0.5)
  else
    for i = 1, #cfg.maps do
      local sx = mx + seg * (i - 1)
      if i == mapI then
        local hot = i > 1
        ui.drawRectFilled(vec2(sx + 2, o.y + 2), vec2(sx + seg - 2, o.y + hgt - 2), hot and COL.red or COL.boost, (hgt - 4) / 2)
        plain(cfg.maps[i].name, 12, vec2(sx + seg / 2, o.y + 4), hot and COL.text or COL.ink, 0.5)
      else
        label(cfg.maps[i].name, 12, vec2(sx + seg / 2, o.y + 4), COL.faint, 0.5)
      end
    end
  end
  switchArea('##rigmap', vec2(mx, o.y), mw, hgt, 'map', flipMap)

  -- rev limit: the lit segment is the limit the car reports, so it only moves if the car really changed
  if cfg.revs ~= nil then
    local rx = mx + mw + 18
    label('REV', 11, vec2(rx, o.y), COL.dim, 0)
    label(store.revBtn >= 0 and ('btn ' .. (store.revBtn + 1)) or 'limit', 9, vec2(rx, o.y + 13), COL.faint, 0)
    local bx, rseg = rx + 32, 48
    local rw = rseg * #cfg.revs
    ui.drawRectFilled(vec2(bx, o.y), vec2(bx + rw, o.y + hgt), COL.track, hgt / 2)
    ui.drawRect(vec2(bx, o.y), vec2(bx + rw, o.y + hgt), COL.edge, hgt / 2, nil, 1)
    if binding == 'rev' then
      label('press', 11, vec2(bx + rw / 2, o.y + 5), COL.text, 0.5)
    else
      local live = 1
      for i = 2, #cfg.revs do
        if math.abs(now.limiter - cfg.revs[i].rpm) < math.abs(now.limiter - cfg.revs[live].rpm) then live = i end
      end
      for i = 1, #cfg.revs do
        local sx = bx + rseg * (i - 1)
        if i == live then
          local hot = i > 1
          ui.drawRectFilled(vec2(sx + 2, o.y + 2), vec2(sx + rseg - 2, o.y + hgt - 2), hot and COL.red or COL.boost, (hgt - 4) / 2)
          plain(cfg.revs[i].name, 12, vec2(sx + rseg / 2, o.y + 4), hot and COL.text or COL.ink, 0.5)
        else
          label(cfg.revs[i].name, 12, vec2(sx + rseg / 2, o.y + 4), COL.faint, 0.5)
        end
      end
    end
    switchArea('##rigrev', vec2(bx, o.y), rw, hgt, 'rev', flipRev)
  end

  -- engine sound, on its own line: the pill shows what is playing, a click moves to the next
  local sy = o.y + 30
  label('SOUND', 11, vec2(o.x, sy), COL.dim, 0)
  label(store.sndBtn >= 0 and ('btn ' .. (store.sndBtn + 1)) or 'engine', 9, vec2(o.x, sy + 13), COL.faint, 0)
  local px = o.x + 48
  local pw = math.max(60, o.x + w - px)
  ui.drawRectFilled(vec2(px, sy), vec2(px + pw, sy + hgt), COL.track, hgt / 2)
  ui.drawRect(vec2(px, sy), vec2(px + pw, sy + hgt), COL.tq, hgt / 2, nil, 1)
  if binding == 'sound' then
    label('press a wheel button', 11, vec2(px + pw / 2, sy + 5), COL.text, 0.5)
  else
    label(cfg.sounds[clampIndex(store.sound, #cfg.sounds)].name, 12, vec2(px + pw / 2, sy + 4), COL.text, 0.5)
  end
  switchArea('##rigsound', vec2(px, sy), pw, hgt, 'sound', cycleSound)
end

function windowMain(dt)
  local o = ui.getCursor()
  local space = ui.availableSpace()
  local w, h = space.x, space.y
  ui.drawRectFilled(o, vec2(o.x + w, o.y + h), COL.panel, 10)
  ui.drawRect(o, vec2(o.x + w, o.y + h), COL.edge, 10, nil, 1)
  if ac.getCar(0) == nil then
    label('Waiting for the car', 12, vec2(o.x + 16, o.y + 14), COL.dim, 0)
    return
  end
  local pad = 16
  local ix, iw = o.x + pad, w - pad * 2
  label('Boost and power', 12, vec2(ix, o.y + 12), COL.dim, 0)
  ui.drawCircleFilled(vec2(ix + iw - 30, o.y + 20), 3, COL.live, 12)
  label('live', 11, vec2(ix + iw, o.y + 13), COL.dim, 1)

  local top = o.y + 34
  drawDial(vec2(ix - 2, top))
  local nx = ix + 184
  local nw = iw - 184
  drawReadout(vec2(nx, top + 6), nw, 'Horsepower', now.hp, peak.hp, peak.hpRpm, COL.hp)
  drawReadout(vec2(nx, top + 80), nw, 'Torque lb-ft', now.tq, peak.tq, peak.tqRpm, COL.tq)

  local ly = top + 158                                            -- key: a short stroke beside each name
  ui.drawLine(vec2(ix, ly + 7), vec2(ix + 14, ly + 7), COL.hp, 2)
  label('Horsepower', 11, vec2(ix + 20, ly), COL.dim, 0)
  ui.drawLine(vec2(ix + 104, ly + 7), vec2(ix + 118, ly + 7), COL.tq, 2)
  label('Torque', 11, vec2(ix + 124, ly), COL.dim, 0)
  if status.err ~= '' then
    label('switch problem: see the log', 11, vec2(ix + iw, ly), COL.red, 1)
  elseif curve == nil then
    label("no power curve for this car: boost only", 11, vec2(ix + iw, ly), COL.faint, 1)
  else
    label('fills in at full throttle', 11, vec2(ix + iw, ly), COL.faint, 1)
  end

  local row = cfg ~= nil and 64 or 0                              -- room for the two lines of switches on cars that have them
  local cy = ly + 18
  local ch = math.max(70, h - (cy - o.y) - 46 - row)
  drawCurve(vec2(ix, cy), iw, ch)
  if cfg ~= nil then drawSwitches(vec2(ix, o.y + h - 38 - row), iw) end
  drawRevStrip(vec2(ix, o.y + h - 38), iw)

  ui.setCursor(vec2(ix + 150, o.y + 8))                           -- a small reset in the header
  if ui.button('Reset', vec2(52, 20)) then resetPeaks() end
end
